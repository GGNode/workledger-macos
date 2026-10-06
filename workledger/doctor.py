from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
from .config import Config
from .ingest import expand_paths


def doctor(config: Config) -> dict:
    statuses = []
    commands = {"codex": "codex", "claude": "claude", "pi": "pi", "opencode": "opencode", "dsh": "dsh"}
    for source, opts in config.data["sources"].items():
        paths = expand_paths(opts.get("paths", []) + opts.get("export_paths", []))
        status = "disabled" if not opts.get("enabled") else "found" if paths else "needs_path"
        if source == "bridge":
            status = "ready" if opts.get("enabled") else "disabled"
        elif source == "activitywatch":
            status = "configured_not_probed" if opts.get("enabled") else "disabled"
        statuses.append({"source": source, "status": status, "files_found": len(paths), "examples": [str(p) for p in paths[:2]], "command": shutil.which(commands[source]) if source in commands else None})
    return {"platform": platform.platform(), "python": sys.version.split()[0], "timezone": config.data["timezone"], "zstd_decoder": importlib.util.find_spec("zstandard") is not None,
            "sources": statuses, "projects": len(config.data["projects"]), "running_on_macos": sys.platform == "darwin", "note": "found 仅代表发现文件。实际格式兼容性、缺失记录和错误请查看采集提示。物理键盘输入无法通过普通文件监听确定。"}
