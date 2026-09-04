"""Sign the harness into OUR app.

Every script that drives localhost:5173 now hits a login wall. This is the one
place that gets past it, so credentials live in one file rather than seven.

Deliberately no auth-bypass switch exists in the backend (DECISIONS.md), so the
harness authenticates for real — which also means the harness exercises the same
code path a user does.

Credentials come from the environment:

    GHL_APP_EMAIL      required (the app account to sign in as)
    GHL_APP_PASSWORD   required

The session is cached as Playwright storage state in
`.capture-profile/app_auth.json` (gitignored) so repeated runs do not re-login.
"""
import json
import os
from pathlib import Path

APP = "http://localhost:5173/"
STATE = Path(__file__).resolve().parent.parent / ".capture-profile" / "app_auth.json"

EMAIL = os.getenv("GHL_APP_EMAIL")
PASSWORD = os.getenv("GHL_APP_PASSWORD")


def _signed_in(page) -> bool:
    return page.locator("text=Sign in to continue").count() == 0


def ensure_logged_in(page, *, timeout: int = 20000) -> None:
    """Call right after the first `page.goto(APP)`. A no-op if already signed in.

    Leaves the page on whatever view the app booted into, so callers can carry on
    exactly as they did before auth existed.
    """
    page.wait_for_load_state("networkidle")
    if _signed_in(page):
        return

    if not EMAIL or not PASSWORD:
        missing = " and ".join(
            n for n, v in (("GHL_APP_EMAIL", EMAIL), ("GHL_APP_PASSWORD", PASSWORD))
            if not v)
        raise SystemExit(
            "the app requires a login and %s is not set.\n"
            "  Neither has a default -- a real address hardcoded in the source is\n"
            "  exactly the kind of thing that should not be committed. Export both,\n"
            "  then create the password with:\n"
            "    cd backend && uv run python -m app.bootstrap set-password "
            "--email $GHL_APP_EMAIL" % missing)

    page.fill('input[type="email"]', EMAIL)
    page.fill('input[type="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_selector("text=Sign in to continue", state="detached",
                           timeout=timeout)
    page.wait_for_load_state("networkidle")

    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        page.context.storage_state(path=str(STATE))
    except Exception:
        # Caching is an optimisation; a failure here must not fail the capture.
        pass


def storage_state() -> str | None:
    """Pass to `browser.new_context(storage_state=...)` to skip the login form."""
    if STATE.exists():
        try:
            json.loads(STATE.read_text())
            return str(STATE)
        except ValueError:
            return None
    return None
