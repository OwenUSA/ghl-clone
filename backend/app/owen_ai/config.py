"""Owen AI switches. All from the environment (.env.prod); nothing here is editable in the UI."""
from __future__ import annotations

import os
from pathlib import Path

KEY = "owen_ai"                     # registry entry
POLL_SECONDS = 2
MAX_TURNS = 40                      # tool calls + answers in one reply, then it stops
TURN_TIMEOUT_SECONDS = 15 * 60
TOOL_RESULT_MAX_CHARS = 60_000      # a bigger Zuper/Quo answer is cut, with a sentence saying so
TITLE_MAX = 80

OFF_SENTENCE = "Owen AI is off on this server (OWEN_AI_ENABLED)."
NO_TOKEN_SENTENCE = ("Owen AI has no Claude login on this server: log in once inside the owen_ai "
                     "container (`claude /login`) or set CLAUDE_CODE_OAUTH_TOKEN in .env.prod.")

# Repo root /owen-ai locally; /app/owen-ai in the image (Dockerfile.api copies it).
_DEFAULT_WORKSPACE = Path(__file__).resolve().parents[3] / "owen-ai"


def _flag(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _flag("OWEN_AI_ENABLED")


def owner_email() -> str:
    return (os.getenv("OWEN_AI_OWNER_EMAIL") or "").strip().lower()


def credentials_file() -> Path | None:
    d = (os.getenv("CLAUDE_CONFIG_DIR") or "").strip()
    return Path(d) / ".credentials.json" if d else None


def token_set() -> bool:
    """A Claude login exists: the long-lived token in the environment, or a `/login` saved in
    the config volume (`$CLAUDE_CONFIG_DIR/.credentials.json`; the api container mounts the
    volume read-only only to answer this)."""
    if (os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or "").strip():
        return True
    f = credentials_file()
    return bool(f and f.is_file() and f.stat().st_size > 0)


def model() -> str | None:
    """None = the plan's default model."""
    return (os.getenv("OWEN_AI_MODEL") or "").strip() or None


def workspace() -> Path:
    return Path(os.getenv("OWEN_AI_WORKSPACE") or _DEFAULT_WORKSPACE)


def cli_path() -> str | None:
    """None = the Claude Code CLI bundled with the SDK. Set to use a separately installed one."""
    return (os.getenv("OWEN_AI_CLI_PATH") or "").strip() or None


def quo_key() -> str:
    return (os.getenv("OWEN_AI_QUO_API_KEY") or "").strip()
