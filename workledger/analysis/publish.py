"""Publish all report formats as one immutable generation, then flip one pointer."""
from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from ..util import atomic_write, digest, now
from .render import render_html, render_markdown


def publish(config, report):
    dest = config.reports / report["date"]
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    versions = dest / "versions"
    versions.mkdir(exist_ok=True, mode=0o700)
    # Migrate old plain-file reports before rendering, preserving even v1 reports.
    # A new render failure does not modify the live originals.
    regular = [name for name in ("report.html", "report.md", "report.json")
               if (dest/name).exists() and not (dest/name).is_symlink()]
    legacy = None
    if regular:
        legacy = versions / ("legacy-"+uuid.uuid4().hex[:8]); legacy.mkdir(mode=0o700)
        for name in regular:
            shutil.copy2(dest/name, legacy/name)
    previous = None
    candidates = sorted((p for p in versions.iterdir() if (p/"state.json").exists()), key=lambda p: p.name, reverse=True)
    for p in candidates:
        try:
            state = json.loads((p/"state.json").read_text())
            if state.get("analysis_status") == "complete":
                previous = p; break
        except (OSError, ValueError):
            continue
    if report["analysis"]["status"] not in {"complete", "empty"}:
        if previous:
            report["previous_success"] = {"label": "本次未取得完整分析，上次成功版本及其当时证据仍保留。",
                                          "url": (previous/"report.html").as_uri()}
        elif legacy and (legacy/"report.html").exists():
            report["previous_success"] = {"label": "升级前的报告及当时证据已原样备份；此链接不表示它经过语义分析。",
                                          "url": (legacy/"report.html").as_uri()}
    generation = now().replace(":", "-") + "-" + uuid.uuid4().hex[:8]
    new = versions / generation
    new.mkdir(mode=0o700)
    # Render BEFORE mutating any live report pointer. A renderer failure keeps the old set.
    texts = {"report.html": render_html(report), "report.md": render_markdown(report),
             "report.json": json.dumps(report, ensure_ascii=False, indent=2)}
    state = {"date": report["date"], "generation": generation, "generated_at": report["generated_at"],
             "analysis_status": report["analysis"]["status"], "backend": report["analysis"]["backend"],
             "files": {name: digest(value.encode()) for name, value in texts.items()}}
    for name, value in texts.items():
        atomic_write(new/name, value)
    atomic_write(new/"state.json", json.dumps(state, ensure_ascii=False))
    pointer = dest / (".current-"+uuid.uuid4().hex)
    pointer.symlink_to(Path("versions")/generation, target_is_directory=True)
    os.replace(pointer, dest/".current")
    for name in (*texts, "state.json"):
        link = dest / name
        if link.is_symlink() and os.readlink(link) == ".current/"+name:
            continue
        temp = dest / (".link-"+uuid.uuid4().hex)
        temp.symlink_to(".current/"+name)
        os.replace(temp, link)
    atomic_write(config.reports/"latest.json", json.dumps({"date": report["date"], "path": str(dest/"report.html"), **state}))
    return dest/"report.html"
