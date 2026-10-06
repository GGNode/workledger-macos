"""Version-aware, read-only native agent log adapters."""
from .native import parse_codex, parse_claude, parse_pi, parse_dsh
from .opencode import parse_opencode_export, import_opencode_db

PARSERS = {"codex": parse_codex, "claude": parse_claude, "pi": parse_pi, "dsh": parse_dsh}
