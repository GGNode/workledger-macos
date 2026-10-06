from __future__ import annotations

import copy
import json
import os
import secrets
from pathlib import Path
from zoneinfo import ZoneInfo
from .util import atomic_write, local_zone


def default_home() -> Path:
    override = os.environ.get("WORKLEDGER_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / "Library" / "Application Support" / "WorkLedger"


def defaults() -> dict:
    codex = os.environ.get("CODEX_HOME", "~/.codex")
    claude = os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")
    pi = os.environ.get("PI_CODING_AGENT_DIR", "~/.pi/agent")
    pi_sessions = os.environ.get("PI_CODING_AGENT_SESSION_DIR", f"{pi}/sessions")
    data = os.environ.get("XDG_DATA_HOME", "~/.local/share")
    return {
        "version": 1,
        "timezone": local_zone(),
        "port": 8765,
        "report_open": True,
        "poll_seconds": 30,
        "max_source_mb": 512,
        "max_file_mb": 20,
        "max_project_files": 10000,
        "projects": [],
        "exclude": [".git", ".venv", "node_modules", "__pycache__", ".DS_Store", ".env*", "*.pem", "*.key", "auth.json", "credentials*", "package-lock.json", "pnpm-lock.yaml", ".workledger*"],
        "sources": {
            "codex": {"enabled": True, "paths": [f"{codex}/sessions/**/*.jsonl", f"{codex}/archived_sessions/**/*.jsonl"]},
            "claude": {"enabled": True, "paths": [f"{claude}/projects/**/*.jsonl"]},
            "pi": {"enabled": True, "paths": [f"{pi_sessions}/**/*.jsonl"]},
            "opencode": {"enabled": True, "paths": [os.environ.get("OPENCODE_DB", f"{data}/opencode/opencode.db")], "export_paths": []},
            "dsh": {"enabled": True, "paths": [], "note": "Run doctor, then set the actual dsh persistence directory. No guessed home-wide scan."},
            "bridge": {"enabled": True, "paths": []},
            "activitywatch": {"enabled": False, "url": "http://127.0.0.1:5600"},
        },
        "llm": {"mode": "off", "url": "http://127.0.0.1:11434/api/chat", "model": "", "api_key_env": "WORKLEDGER_LLM_KEY", "allow_remote": False, "timeout": 90},
        "schedule": {"enabled": False, "time": "18:30", "weekdays_only": True, "open": True},
        "capture_paused": False,
    }


def validate(c: dict) -> dict:
    ZoneInfo(c["timezone"])
    if not isinstance(c.get("port"), int) or not 1024 <= c["port"] <= 65535:
        raise ValueError("port must be an integer from 1024 to 65535")
    if not isinstance(c.get("poll_seconds"), int) or c["poll_seconds"] < 5:
        raise ValueError("poll_seconds must be >= 5")
    for field in ("max_source_mb", "max_file_mb", "max_project_files"):
        if not isinstance(c.get(field), int) or c[field] < 1:
            raise ValueError(f"{field} must be a positive integer")
    if not isinstance(c.get("projects"), list):
        raise ValueError("projects must be a list")
    if not isinstance(c.get("exclude"), list) or not all(isinstance(x,str) for x in c["exclude"]):
        raise ValueError("exclude must be a string list")
    if not isinstance(c.get("sources"), dict):
        raise ValueError("sources must be an object")
    for source, opts in c["sources"].items():
        if not isinstance(opts,dict):
            raise ValueError(f"sources.{source} must be an object")
        for field in ("paths", "export_paths"):
            if field in opts and (not isinstance(opts[field],list) or not all(isinstance(x,str) for x in opts[field])):
                raise ValueError(f"sources.{source}.{field} must be a string list")
    for p in c.get("projects", []):
        if not p.get("name") or not isinstance(p.get("paths", []), list):
            raise ValueError("each project needs a name and a paths list")
        for raw in p.get("paths", []):
            path = Path(raw).expanduser().resolve()
            if path in {Path("/"), Path.home()}:
                raise ValueError("Choose a project folder, not your whole home or disk")
    if c["llm"]["mode"] not in {"off", "ollama", "openai-compatible"}:
        raise ValueError("unsupported llm.mode")
    hour, minute = map(int, c["schedule"]["time"].split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("schedule time must be HH:MM")
    return c


class Config:
    def __init__(self, home: Path | str | None = None):
        self.home = Path(home or default_home()).expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.home / "config.json"
        for d in ("reports", "inbox", "logs"):
            (self.home / d).mkdir(exist_ok=True, mode=0o700)
        tokenfile = self.home / "token"
        if not tokenfile.exists():
            # Exclusive creation avoids token races between daemon and CLI.
            try:
                with tokenfile.open("x") as f:
                    os.chmod(tokenfile, 0o600)
                    f.write(secrets.token_urlsafe(32))
            except FileExistsError:
                pass
        self.token = tokenfile.read_text().strip()
        if self.path.exists():
            self.data = self._merge(defaults(), json.loads(self.path.read_text()))
            validate(self.data)
        else:
            self.data = defaults()
            self.save()

    @staticmethod
    def _merge(base: dict, overrides: dict) -> dict:
        result = copy.deepcopy(base)
        for k, v in overrides.items():
            if isinstance(v, dict) and isinstance(result.get(k), dict):
                result[k] = Config._merge(result[k], v)
            else:
                result[k] = v
        return result

    def reload(self):
        self.data = validate(self._merge(defaults(), json.loads(self.path.read_text())))

    def save(self, values: dict | None = None):
        data = validate(self._merge(self.data, values or {}))
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        self.data = data

    @property
    def db_path(self) -> Path:
        return self.home / "workledger.sqlite3"

    @property
    def reports(self) -> Path:
        return self.home / "reports"
