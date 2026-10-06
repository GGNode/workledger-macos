from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from .config import Config
from .util import under

LABEL = "local.workledger.agent"


def open_output(config: Config, path: Path, *, folder=True):
    if not under(path, config.reports) or not path.is_file():
        raise ValueError("Only generated files inside the report folder may be opened")
    if sys.platform != "darwin":
        return {"opened": False, "reason": "Automatic Finder opening is macOS-only", "path": str(path)}
    if folder:
        subprocess.run(["/usr/bin/open", str(path.parent)], check=True, timeout=10)
    subprocess.run(["/usr/bin/open", str(path)], check=True, timeout=10)
    return {"opened": True, "path": str(path)}


def launchd_plist(config: Config, executable: str) -> bytes:
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(Path(executable).expanduser().resolve()), "--home", str(config.home), "daemon"],
        "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 15,
        "ProcessType": "Background", "Umask": 0o077,
        "StandardOutPath": str(config.home / "logs" / "daemon.stdout.log"),
        "StandardErrorPath": str(config.home / "logs" / "daemon.stderr.log"),
        "EnvironmentVariables": {"PATH": str(Path.home() / ".local/bin") + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "PYTHONUNBUFFERED": "1"},
    }, sort_keys=False)


def install_launchd(config: Config, executable: str):
    if sys.platform != "darwin":
        raise RuntimeError("launchd installation requires macOS")
    dest = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = launchd_plist(config, executable)
    from .util import atomic_write
    atomic_write(dest, data.decode(), 0o600)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["/bin/launchctl", "bootout", domain, str(dest)], capture_output=True, timeout=15)
    subprocess.run(["/bin/launchctl", "bootstrap", domain, str(dest)], check=True, capture_output=True, timeout=15)
    return str(dest)


def uninstall_launchd():
    if sys.platform != "darwin":
        raise RuntimeError("launchd removal requires macOS")
    dest = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}", str(dest)], capture_output=True, timeout=15)
    dest.unlink(missing_ok=True)
