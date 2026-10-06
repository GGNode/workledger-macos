from __future__ import annotations

import glob
import io
import json
import os
import re
from pathlib import Path
from .config import Config
from .store import Store
from .util import now
from .adapters import PARSERS, import_opencode_db, parse_opencode_export
from .adapters.bridge import ingest_bridge, import_chatgpt_export


def expand_paths(patterns: list[str]) -> list[Path]:
    paths = set()
    for raw in patterns:
        expanded = os.path.expandvars(os.path.expanduser(raw))
        for p in glob.iglob(expanded, recursive=True):
            path = Path(p)
            if path.is_file():
                paths.add(path.resolve())
            elif path.is_dir():
                if (path / "opencode.db").is_file():
                    paths.add((path / "opencode.db").resolve())
                for ext in ("*.jsonl", "*.jsonl.zstd", "*.jsonl.zst"):
                    paths.update(v.resolve() for v in path.rglob(ext) if v.is_file())
    return sorted(paths)


def read_rows(path: Path, limit: int) -> tuple[list[dict], list[str]]:
    if path.stat().st_size > limit:
        raise ValueError(f"Source exceeds {limit // 1024**2} MiB import limit")
    raw = path.read_bytes()
    if path.suffix in {".zstd", ".zst"}:
        try:
            import zstandard
        except ImportError as e:
            raise ValueError("DSH Zstd log needs optional decoder: python -m pip install 'zstandard>=0.22,<1'; or export raw JSONL") from e
        # A concatenation of frames is valid. Refuse truncated frames; don't invent tail data.
        try:
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw), read_across_frames=True) as reader:
                raw = reader.read(limit + 1)
        except zstandard.ZstdError as e:
            raise ValueError(f"Zstd frame incomplete/corrupt; retry after log flush: {e}") from e
        if len(raw) > limit:
            raise ValueError("Decompressed log exceeds limit")
    rows, warnings = [], []
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if i == len(lines) - 1 and not raw.endswith(b"\n"):
            warnings.append(f"Trailing record at line {i+1} lacks newline; waiting for writer commit")
            break
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError("row is not an object")
            obj["__workledger_line_number"] = i + 1
            rows.append(obj)
        except (ValueError, UnicodeDecodeError) as e:
            if i == len(lines) - 1 and not raw.endswith(b"\n"):
                warnings.append(f"Incomplete trailing record at line {i+1}; waiting for writer")
            else:
                warnings.append(f"Malformed record at line {i+1}: {e}")
    return rows, warnings


def file_signature(path: Path):
    s = path.stat()
    wal = Path(str(path) + "-wal")
    ws = wal.stat() if wal.exists() else None
    return [s.st_ino, s.st_size, s.st_mtime_ns, ws.st_size if ws else 0, ws.st_mtime_ns if ws else 0]


def collect(config: Config, store: Store, *, force=False) -> dict:
    c = config.data
    if c["capture_paused"]:
        return {"paused": True, "files": 0}
    total = 0
    sources = dict(c["sources"])
    sources["bridge"] = {**sources.get("bridge", {}), "paths": sources.get("bridge", {}).get("paths", []) + [str(config.home / "inbox" / "*.jsonl")]}
    for source, opts in sources.items():
        if not opts.get("enabled") or source == "activitywatch":
            continue
        paths = expand_paths(opts.get("paths", []))
        if source == "dsh":
            generations, custom = {}, []
            for path in paths:
                match = re.fullmatch(r"session(?:\.v(\d+))?\.jsonl(?:\.zstd|\.zst)?", path.name)
                if match:
                    version = int(match[1] or 0)
                    previous = generations.get(path.parent)
                    rank = (version, path.stat().st_mtime_ns)
                    if not previous or rank > previous[0]:
                        generations[path.parent] = (rank,path)
                else:
                    custom.append(path)
            paths = custom + [value[1] for value in generations.values()]
        exports = expand_paths(opts.get("export_paths", [])) if source == "opencode" else []
        status_key = "source:" + source
        if not paths and not exports:
            if source != "bridge":
                store.issue(status_key, source, "未找到日志；请在设置中选择本机日志路径。未采集不等于没有工作。")
        else:
            store.resolve_issue(status_key)
        for path in paths + exports:
            key = f"file:{source}:{path}"
            try:
                signature = file_signature(path)
                if not force and store.cache_get(key) == signature:
                    continue
                store.conn.execute("SAVEPOINT importing")
                limit = c["max_source_mb"] * 1024**2
                if source == "opencode" and path.suffix in {".db", ".sqlite", ".sqlite3"}:
                    import_opencode_db(path, store)
                    warnings = []
                elif source == "opencode":
                    if path.stat().st_size > limit:
                        raise ValueError("OpenCode export exceeds configured size limit")
                    parse_opencode_export(json.loads(path.read_text()), store, origin=str(path))
                    warnings = []
                else:
                    rows, warnings = read_rows(path, limit)
                    if source in PARSERS:
                        PARSERS[source](rows, path, store)
                    elif source == "bridge":
                        ingest_bridge(rows, store)
                store.conn.execute("RELEASE SAVEPOINT importing")
                if warnings:
                    store.issue(key, source, f"{path.name}: " + "; ".join(warnings[:3]))
                else:
                    store.resolve_issue(key)
                store.cache_set(key, signature)
                total += 1
            except Exception as e:
                try:
                    store.conn.execute("ROLLBACK TO SAVEPOINT importing")
                    store.conn.execute("RELEASE SAVEPOINT importing")
                except Exception:
                    pass
                store.issue(key, source, f"{path.name}: {type(e).__name__}: {e}")
    from .documents import collect_documents
    collect_documents(config, store)
    if c["sources"]["activitywatch"].get("enabled"):
        collect_activitywatch(config, store)
    store.cache_set("last_capture", now())
    store.conn.commit()
    return {"files": total, **store.summary()}


def collect_activitywatch(config: Config, store: Store):
    """Optional local window context. Not proof of human authorship or work duration."""
    import urllib.request
    from .llm import validate_url
    from .util import day_bounds, today
    url = config.data["sources"]["activitywatch"]["url"].rstrip("/")
    try:
        validate_url(url, False)
        def fetch(endpoint):
            with urllib.request.urlopen(url + endpoint, timeout=3) as r:
                return json.load(r)
        buckets = fetch("/api/0/buckets/")
        start, end = day_bounds(today(config.data["timezone"]), config.data["timezone"])
        from urllib.parse import urlencode
        for key in buckets:
            if key.startswith("aw-watcher-window"):
                for e in fetch(f"/api/0/buckets/{key}/events?" + urlencode({"start": start, "end": end, "limit": 5000})):
                    data = e.get("data", {})
                    store.event("activitywatch", f"{key}/{e['id']}", "context", occurred_at=e.get("timestamp"), actor="unknown", text=f"{data.get('app','')} — {data.get('title','')}", evidence="foreground_window", metadata={"duration": e.get("duration"), "not_work_time": True})
        store.resolve_issue("activitywatch")
    except Exception as e:
        store.issue("activitywatch", "activitywatch", str(e))
