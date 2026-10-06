from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def stamp(value: Any) -> str | None:
    """Normalize a source timestamp; never substitute observation time for missing time."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            seconds = value / 1000 if abs(value) > 100_000_000_000 else value
            dt = datetime.fromtimestamp(seconds, UTC)
        elif isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return None
        else:
            return None
        return dt.astimezone(UTC).isoformat(timespec="milliseconds")
    except (ValueError, OverflowError, OSError):
        return None


def local_zone() -> str:
    env = os.environ.get("TZ", "").lstrip(":")
    candidates = [env]
    try:
        p = str(Path("/etc/localtime").resolve())
        if "zoneinfo/" in p:
            candidates.append(p.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    try:
        candidates.append(Path("/etc/timezone").read_text().strip())
    except OSError:
        pass
    for key in candidates:
        try:
            ZoneInfo(key)
            return key
        except (ValueError, KeyError):
            pass
    return "UTC"


def day_bounds(day: str, zone: str) -> tuple[str, str]:
    d = date.fromisoformat(day)
    tz = ZoneInfo(zone)
    # Build both midnights in local time, including 23/25-hour DST days.
    a = datetime.combine(d, time.min, tz)
    b = datetime.combine(d + timedelta(days=1), time.min, tz)
    return stamp(a.isoformat()), stamp(b.isoformat())


def today(zone: str) -> str:
    return datetime.now(ZoneInfo(zone)).date().isoformat()


def json_text(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(obj: Any) -> str:
    data = obj if isinstance(obj, bytes) else json_text(obj).encode()
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix=".workledger-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def clip(text: Any, n: int = 150) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def content_text(value: Any) -> str:
    """Extract visible text only. Do not collect reasoning/thinking, image bytes or secrets."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (content_text(v) for v in value)))
    if isinstance(value, dict):
        typ = value.get("type", "")
        if typ in {"thinking", "reasoning", "redacted_thinking", "image", "image_url", "input_image"}:
            return ""
        if typ in {"text", "input_text", "output_text"}:
            return str(value.get("text", ""))
        if "parts" in value:
            return content_text(value["parts"])
        if "content" in value:
            return content_text(value["content"])
    return ""


SECRET_PATTERNS = [
    re.compile(r"(?i)\b(?:sk-[a-z0-9_-]{16,}|gh[pousr]_[a-z0-9]{16,}|github_pat_[a-z0-9_]{16,})"),
    re.compile(r"(?i)((?:api[_-]?key|authorization|access[_-]?token|password)\s*[=:]\s*)[^\s,;\"']+"),
    re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S),
]


def redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def nested(data: Any, *keys: str, default=None):
    for key in keys:
        if not isinstance(data, dict):
            return default
        data = data.get(key)
    return default if data is None else data


def intervals_seconds(intervals: list[tuple[str, str]], start: str, end: str) -> float:
    """Union of observed execution spans, clipped to the report day."""
    windows = sorted((max(a, start), min(b, end)) for a, b in intervals if a and b and b > a and b > start and a < end)
    merged: list[list[str]] = []
    for a, b in windows:
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return sum((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() for a, b in merged)


def sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if re.search(r"(?i)^(password|authorization|api.?key|access.?token|secret)$", k) else sanitize(v)) for k, v in value.items()}
    return value
