from __future__ import annotations

import difflib
import fnmatch
import json
import os
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from .config import Config
from .store import Store
from .util import digest, json_text, now, under, redact

TEXT_EXTS = {".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml", ".ini", ".csv", ".tex", ".bib", ".html", ".css", ".cpp", ".c", ".h", ".hpp", ".rs", ".sh", ".lua", ".ipynb", ".sql", ".m", ".r", ".swift"}
DOCUMENT_EXTS = {".docx", ".pptx", ".xlsx", ".pdf", ".pages", ".numbers", ".keynote"}
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main", "a": "http://schemas.openxmlformats.org/drawingml/2006/main", "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main", "p": "http://schemas.openxmlformats.org/presentationml/2006/main", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def xml(data: bytes):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("DTD/entity declarations are not allowed")
    return ET.fromstring(data)


def office_assets(z, names) -> dict[str,str]:
    return {"图片或图表 / " + n: digest(z.read(n)) for n in sorted(names) if re.match(r"^(word|ppt|xl)/(media|charts|diagrams)/", n) and not n.endswith("/")}


def structure(path: Path, data: bytes, limit=32 * 1024**2) -> dict[str, str]:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTS:
        text = data.decode("utf-8", errors="replace")
        if suffix == ".ipynb":
            book = json.loads(text)
            # Outputs may be large and are not authored source changes.
            return {f"单元格 {i+1}": redact("".join(c.get("source", []))) for i, c in enumerate(book.get("cells", []))}
        return {"正文": redact(text)}
    if suffix not in {".docx", ".pptx", ".xlsx"}:
        return {"文件内容": "二进制内容 SHA256 " + digest(data)}
    import io
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = z.infolist()
        if len(infos) > 10000 or sum(i.file_size for i in infos) > limit:
            raise ValueError("Office document exceeds safe decompression limit")
        names = set(z.namelist())
        if suffix == ".docx":
            root = xml(z.read("word/document.xml"))
            paras = ["".join(n.text or "" for n in p.findall(".//w:t", NS)) for p in root.findall(".//w:p", NS)]
            result = {"正文": redact("\n".join(paras)), **office_assets(z, names)}
            style_nodes = root.findall(".//w:pPr", NS) + root.findall(".//w:rPr", NS) + root.findall(".//w:sectPr", NS)
            if style_nodes:
                result["段落与页面样式"] = digest([ET.tostring(n).decode() for n in style_nodes])
            return result
        if suffix == ".pptx":
            order = []
            if {"ppt/presentation.xml", "ppt/_rels/presentation.xml.rels"} <= names:
                rel = {n.attrib["Id"]: n.attrib.get("Target", "") for n in xml(z.read("ppt/_rels/presentation.xml.rels"))}
                for s in xml(z.read("ppt/presentation.xml")).findall(".//p:sldId", NS):
                    target = rel.get(s.attrib.get("{" + NS["r"] + "}id"), "")
                    if target:
                        from posixpath import normpath
                        order.append(normpath("ppt/" + target) if not target.startswith("/") else target.lstrip("/"))
            if not order:
                order = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=lambda n: int(re.search(r"slide(\d+)", n)[1]))
            result = office_assets(z, names)
            for i,n in enumerate(order):
                slide = xml(z.read(n))
                result[f"第 {i+1} 页"] = redact("\n".join(t.text or "" for t in slide.findall(".//a:t", NS)))
                positions = slide.findall(".//a:xfrm", NS)
                if positions:
                    result[f"第 {i+1} 页的布局"] = digest([ET.tostring(n).decode() for n in positions])
            return result
        strings = []
        if "xl/sharedStrings.xml" in names:
            strings = ["".join(t.text or "" for t in si.findall(".//s:t", NS)) for si in xml(z.read("xl/sharedStrings.xml"))]
        result = office_assets(z, names)
        for n in sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)):
            cells = []
            for c in xml(z.read(n)).findall(".//s:c", NS):
                v = c.find("s:v", NS)
                value = v.text or "" if v is not None else ""
                if c.attrib.get("t") == "s" and value.isdigit() and int(value) < len(strings):
                    value = strings[int(value)]
                elif c.attrib.get("t") == "inlineStr":
                    value = "".join(t.text or "" for t in c.findall(".//s:t", NS))
                formula = c.find("s:f", NS)
                if formula is not None:
                    value = "=" + (formula.text or "")  # cached recalculation isn't a user's formula edit
                cells.append(f"{c.attrib.get('r', '?')}\t{value}")
            result[Path(n).stem] = redact("\n".join(cells))
        return result


def changes(before: dict, after: dict) -> list[dict]:
    result = []
    for k in sorted(set(before) | set(after)):
        a, b = before.get(k, ""), after.get(k, "")
        if a == b:
            continue
        diff = list(difflib.unified_diff(a.splitlines(), b.splitlines(), fromfile="before", tofile="after", lineterm=""))
        result.append({"section": k, "before": a[:2000], "after": b[:2000], "diff": "\n".join(diff[:120]),
                       "added_lines": sum(x.startswith("+") and not x.startswith("+++") for x in diff),
                       "removed_lines": sum(x.startswith("-") and not x.startswith("---") for x in diff)})
    return result


def collect_documents(config: Config, store: Store):
    c = config.data
    visited = set()
    count = 0
    for project in c["projects"]:
        for raw in project.get("paths", []):
            root = Path(raw).expanduser().resolve()
            issue = "project:" + str(root)
            if not root.is_dir():
                store.issue(issue, "documents", f"项目目录不可访问：{root}")
                continue
            store.resolve_issue(issue)
            initialized = store.cache_get("root-baseline:" + str(root), False)
            walk_errors = []
            for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_errors.append):
                dirs[:] = [d for d in dirs if not any(fnmatch.fnmatch(d, pattern) for pattern in c["exclude"]) and not Path(directory, d).is_symlink() and not under(Path(directory, d), config.home)]
                for name in files:
                    path = Path(directory, name)
                    if path in visited or path.is_symlink() or under(path, config.home) or any(fnmatch.fnmatch(name, p) for p in c["exclude"]):
                        continue
                    if path.suffix.lower() not in TEXT_EXTS | DOCUMENT_EXTS:
                        continue
                    count += 1
                    if count > c["max_project_files"]:
                        store.issue("project-file-limit", "documents", "已达到文件扫描上限；本轮覆盖不完整，请缩小项目目录或增加上限。")
                        return
                    visited.add(path)
                    fkey = "snapshot:" + str(path)
                    try:
                        s = path.stat()
                        if s.st_size > c["max_file_mb"] * 1024**2:
                            store.issue(fkey, "documents", f"{path.name} 超过单文件采集上限，未比较内容")
                            continue
                        sig = json_text([s.st_ino, s.st_size, s.st_mtime_ns])
                        prev = store.conn.execute("SELECT * FROM snapshots WHERE path=?", (str(path),)).fetchone()
                        if prev and prev["stat_signature"] == sig:
                            continue
                        data = path.read_bytes()
                        s2 = path.stat()
                        if (s2.st_size, s2.st_mtime_ns) != (s.st_size, s.st_mtime_ns):
                            continue  # editor is still saving; compare the next stable observation
                        h = digest(data)
                        parsed = structure(path, data)
                        captured = now()
                        if (prev and h != prev["hash"]) or (not prev and initialized):
                            delta = changes(json.loads(prev["structure"]) if prev else {}, parsed)
                            if delta:
                                eid = store.event("documents", digest([str(path), prev["hash"] if prev else None, h, prev["captured_at"] if prev else captured]), "document_change",
                                      actor="unknown", occurred_at=captured, chronology="live_observed", artifact=str(path),
                                      text=f"{path.name}：" + "、".join(d["section"] for d in delta[:6]), evidence="snapshot_diff",
                                      metadata={"project": project["name"], "before_hash": prev["hash"] if prev else None, "after_hash": h,
                                                "interval_start": prev["captured_at"] if prev else store.cache_get("root-baseline:" + str(root)), "observed_not_edit_time": True, "newly_observed": not bool(prev), "changes": delta[:30],
                                                "sections_changed": len(delta), "requires_author_confirmation": True})
                        # First observation is a baseline, never today's newly created output.
                        store.conn.execute("INSERT INTO snapshots VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET hash=excluded.hash,structure=excluded.structure,captured_at=excluded.captured_at,stat_signature=excluded.stat_signature",
                                           (str(path), h, json_text(parsed), captured, sig))
                        store.resolve_issue(fkey)
                    except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile, KeyError) as e:
                        store.issue(fkey, "documents", f"{path.name}: {e}")
            if walk_errors:
                store.issue(issue, "documents", "部分子目录不可读：" + str(walk_errors[0]))
            else:
                # Deletions are only observed after a complete, accessible root scan.
                for previous in list(store.conn.execute("SELECT * FROM snapshots")):
                    oldpath = Path(previous["path"])
                    if under(oldpath, root) and not oldpath.exists():
                        when = now()
                        delta = changes(json.loads(previous["structure"]), {})
                        store.event("documents", digest([str(oldpath), previous["hash"], "deleted", previous["captured_at"]]), "document_change", actor="unknown", occurred_at=when,
                                    chronology="live_observed", artifact=str(oldpath), text=oldpath.name + "：观察到删除或移走", evidence="snapshot_missing",
                                    metadata={"project":project["name"], "deleted_or_moved":True, "observed_not_edit_time":True, "interval_start":previous["captured_at"], "changes":delta[:30]})
                        store.conn.execute("DELETE FROM snapshots WHERE path=?",(str(oldpath),))
                store.cache_set("root-baseline:" + str(root), now())
    store.resolve_issue("project-file-limit")
