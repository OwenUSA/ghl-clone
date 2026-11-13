"""Frontdoor reading's switches. All from the environment; nothing here is editable in the UI."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

BASE = "https://contractor.frontdoorhome.com"
TZ = "America/New_York"
CACHE_SECONDS = 15 * 60
PAGE_GAP_SECONDS = 2.0          # at least this long between two page loads
MAX_LIST_PAGES = 10             # pages of the dispatch list one read may turn
TIMEOUT_MS = 45_000

OFF_SENTENCE = "Frontdoor reading is off on this server (FRONTDOOR_READ_ENABLED)."
NO_LOGIN_SENTENCE = ("Frontdoor reading has no login on this server: set FRONTDOOR_USER and "
                     "FRONTDOOR_PASSWORD, or FRONTDOOR_CREDENTIALS_FILE.")


def enabled() -> bool:
    return (os.getenv("FRONTDOOR_READ_ENABLED") or "").strip().lower() in ("1", "true", "yes",
                                                                          "on")


def credentials() -> tuple[str, str] | None:
    """FRONTDOOR_USER + FRONTDOOR_PASSWORD, else the root-only file FRONTDOOR_CREDENTIALS_FILE
    holding the same two KEY=value lines. None = no login. Never logged, never returned."""
    user = (os.getenv("FRONTDOOR_USER") or "").strip()
    pw = os.getenv("FRONTDOOR_PASSWORD") or ""
    path = (os.getenv("FRONTDOOR_CREDENTIALS_FILE") or "").strip()
    if (not user or not pw) and path and Path(path).is_file():
        found = {}
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(FRONTDOOR_USER|FRONTDOOR_PASSWORD)\s*=\s*(.*?)\s*$", line)
            if m:
                found[m.group(1)] = m.group(2).strip("'\"")
        user = user or found.get("FRONTDOOR_USER", "")
        pw = pw or found.get("FRONTDOOR_PASSWORD", "")
    return (user, pw) if user and pw else None


def state_dir() -> Path:
    """Where the logged-in browser state is kept: FRONTDOOR_STATE_DIR, else `frontdoor/` beside
    the Claude config dir (the owen_ai_home volume), else the system temp dir."""
    raw = (os.getenv("FRONTDOOR_STATE_DIR") or "").strip()
    if raw:
        return Path(raw).resolve()
    conf = (os.getenv("CLAUDE_CONFIG_DIR") or "").strip()
    if conf:
        return Path(conf).resolve().parent / "frontdoor"
    return Path(tempfile.gettempdir()).resolve() / "frontdoor"


def chromium() -> str | None:
    """FRONTDOOR_CHROMIUM (a path or a name on PATH), else Debian's `chromium` (Dockerfile.api
    installs it), else None = Playwright's own downloaded browser (a laptop)."""
    raw = (os.getenv("FRONTDOOR_CHROMIUM") or "").strip()
    if raw:
        return raw if Path(raw).is_file() else shutil.which(raw)
    return shutil.which("chromium") or shutil.which("chromium-browser")


def ready() -> bool:
    """For the Owen AI page's green chip: reading is on and a login is at hand — the credentials,
    or a login Owen AI's container saved. The api container has no credentials (only owen_ai
    mounts them) but sees the owen_ai_home volume read-only, where the saved login lives."""
    if not enabled():
        return False
    return credentials() is not None or (state_dir() / "state.json").is_file()


def refusal() -> str | None:
    """Why no read may start now; None = it may."""
    if not enabled():
        return OFF_SENTENCE
    if credentials() is None:
        return NO_LOGIN_SENTENCE
    return None
