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
SDK_MAX_BUFFER_BYTES = 32 * 1024 * 1024   # the CLI -> SDK message limit (the SDK's default: 1 MB)

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


def photo_dir() -> Path:
    """Where `job_photos` keeps a job's downscaled pictures for Claude's Read tool (2026-11-06):
    OWEN_AI_PHOTO_DIR, else `photos/` beside the Claude config dir (the owen_ai_home volume),
    else the system temp dir. Always absolute; agent.py hands it to the CLI as an --add-dir."""
    raw = (os.getenv("OWEN_AI_PHOTO_DIR") or "").strip()
    if raw:
        return Path(raw).resolve()
    conf = (os.getenv("CLAUDE_CONFIG_DIR") or "").strip()
    if conf:
        return (Path(conf).resolve().parent / "photos")
    import tempfile
    return Path(tempfile.gettempdir()).resolve() / "owen-ai-photos"


def cli_path() -> str | None:
    """None = the Claude Code CLI bundled with the SDK. Set to use a separately installed one."""
    return (os.getenv("OWEN_AI_CLI_PATH") or "").strip() or None


def quo_key() -> str:
    return (os.getenv("OWEN_AI_QUO_API_KEY") or "").strip()


# The scheduled audit sweep (2026-11-03): at these Florida times, Mon-Sat, every open job on the
# board is audited (one fresh Claude session per job). Empty = no sweep.
SWEEP_WEEKDAYS = (0, 1, 2, 3, 4, 5)


def sweep_times() -> tuple[str, ...]:
    out = []
    for part in (os.getenv("OWEN_AI_SWEEP_TIMES") or "").replace(" ", "").split(","):
        h, _, m = part.partition(":")
        if h.isdigit() and m.isdigit() and int(h) < 24 and int(m) < 60:
            out.append("%02d:%02d" % (int(h), int(m)))
    return tuple(sorted(set(out)))


def sweep_board() -> str:
    return (os.getenv("OWEN_AI_SWEEP_BOARD") or "AHS Simple Flow").strip()


# ------------------------------------------------------------------ inspection reports (2026-11-07)

REPORT_KEEP_DAYS = 90
PLACEHOLDER = "[placeholder]"


def report_dir() -> Path:
    """Where make_inspection_report writes <id>/report.html|pdf: OWEN_AI_REPORT_DIR, else
    `reports/` beside the Claude config dir (the owen_ai_home volume, which the api container
    mounts read-only at the same path), else the system temp dir. Always absolute."""
    raw = (os.getenv("OWEN_AI_REPORT_DIR") or "").strip()
    if raw:
        return Path(raw).resolve()
    conf = (os.getenv("CLAUDE_CONFIG_DIR") or "").strip()
    if conf:
        return Path(conf).resolve().parent / "reports"
    import tempfile
    return Path(tempfile.gettempdir()).resolve() / "owen-ai-reports"


def report_setting(name: str) -> str | None:
    """OWEN_AI_REPORT_PHONE / _LICENSE / _CITY / _INSPECTOR_DEFAULT; None when unset."""
    return (os.getenv("OWEN_AI_REPORT_" + name) or "").strip() or None


def chromium() -> str | None:
    """The browser that prints the PDF: OWEN_AI_CHROMIUM (a path), else `chromium` /
    `chromium-browser` on PATH (Dockerfile.api installs Debian's `chromium`). None = HTML only."""
    import shutil
    raw = (os.getenv("OWEN_AI_CHROMIUM") or "").strip()
    if raw:
        return raw if Path(raw).is_file() else shutil.which(raw)
    return shutil.which("chromium") or shutil.which("chromium-browser")


# ------------------------------------------------------------ proposal step pictures (2026-11-09)

def picture_dir() -> Path:
    """Where the step-picture banners for a created proposal are saved (<draft id>/<name>.jpg):
    OWEN_AI_PICTURE_DIR, else `pictures/` beside the reports folder. Both the api (the owner's
    button and the preview) and the owen_ai runner (the chat tool) WRITE here, so production sets
    OWEN_AI_PICTURE_DIR to the owen_ai_pictures volume mounted read-write in both containers
    (docker-compose.prod.yml) — the reports folder's volume is read-only in the api."""
    raw = (os.getenv("OWEN_AI_PICTURE_DIR") or "").strip()
    if raw:
        return Path(raw).resolve()
    return report_dir().parent / "pictures"


# ------------------------------------------------- saved after-inspection reports (2026-11-12)

def report_store_dir() -> Path:
    """Where SAVED reports keep their files (<id>/lib, pictures, v<n>): OWEN_AI_REPORT_STORE_DIR,
    else `inspection-reports/` inside the pictures folder — the owen_ai_pictures volume, the one
    BOTH the api (Owen's edits on the page) and the owen_ai runner (Owen AI's edits) may write."""
    raw = (os.getenv("OWEN_AI_REPORT_STORE_DIR") or "").strip()
    if raw:
        return Path(raw).resolve()
    return picture_dir() / "inspection-reports"


REPORT_PICTURES_OFF = ("The option pictures are off on this server "
                       "(OWEN_AI_REPORT_PICTURES_ENABLED), so none was made.")


def report_pictures_enabled() -> bool:
    """The after-inspection report's AI option pictures (infographics.py). Off by default:
    each one is a paid OpenAI call (about $0.18)."""
    return _flag("OWEN_AI_REPORT_PICTURES_ENABLED")


def picture_openai_key() -> str:
    """OWEN_AI_OPENAI_API_KEY. Empty = fall back to an OpenAI AI connection
    (pictures.openai_key)."""
    return (os.getenv("OWEN_AI_OPENAI_API_KEY") or "").strip()
