"""Where the CLI keeps its API token.

    %APPDATA%\\ghl\\config.json    on Windows
    ~/.config/ghl/config.json     elsewhere ($XDG_CONFIG_HOME honoured)

Environment variables win over the file, so a different backend or identity can be
used for one command without touching stored state:

    GHL_API_URL      default http://127.0.0.1:8000
    GHL_API_TOKEN    a ghl_pat_... token
"""
import contextlib
import json
import os
import stat
import sys
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8000"


def config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / "ghl"
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "ghl"


def config_path() -> Path:
    return config_dir() / "config.json"


def load() -> dict:
    """Never raises. A corrupt or unreadable config behaves as "not logged in",
    which is recoverable with `ghl auth login`; an exception here would break
    every command including the one that would fix it."""
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(cfg: dict) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    # No-op on Windows, where the per-user ACL on %APPDATA% is the real control.
    # Applied anyway so the file is not world-readable if the config directory is
    # ever synced to a POSIX machine.
    with contextlib.suppress(OSError):
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path


def clear() -> None:
    with contextlib.suppress(OSError):
        config_path().unlink()


def api_url() -> str:
    return os.environ.get("GHL_API_URL") or load().get("api_url") or DEFAULT_URL


def token() -> str | None:
    return os.environ.get("GHL_API_TOKEN") or load().get("token")
