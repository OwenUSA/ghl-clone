"""Zuper sync configuration: environment switches and the one settings row.

Three things must all be true before the sync does anything by itself (enqueue, poll,
listen, push, pull):

1. `ZUPER_SYNC_ENABLED=true` in the deployment's environment — off by default;
2. the Settings → Zuper switch (`zuper_settings.enabled`), which an ADMIN can only turn on
   once the setup check has passed and every "confirm by hand" item is confirmed;
3. an API key (`ZUPER_API_KEY`, or `ZUPER_API_KEY_FILE` naming a 0600 file).

With the environment flag false the app makes ZERO requests to Zuper from any path — the
client refuses before a connection exists — and the webhook answers 503 with a sentence.
The operator's commands (`python -m app.zuper.setup`, `python -m app.zuper.load`) need only
the key, because the runbook runs them before the switch is turned on; both are dry runs
unless given `--commit`.
"""
from __future__ import annotations

import os
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..models import ZuperSettings

ACCOUNT_TZ = ZoneInfo("America/New_York")
DEFAULT_BASE_URL = "https://us-east-1.zuperpro.com/api"
DEFAULT_CRM_BASE_URL = "https://crm.dreamteamroofingfl.com"

OFF_SENTENCE = ("Zuper sync is switched off on this deployment (ZUPER_SYNC_ENABLED is not "
                "true), so nothing is sent to or accepted from Zuper.")
NO_KEY_SENTENCE = "No Zuper API key is configured (ZUPER_API_KEY or ZUPER_API_KEY_FILE)."


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_enabled() -> bool:
    return _flag("ZUPER_SYNC_ENABLED", False)


PULL_ONLY_SENTENCE = ("This deployment mirrors Zuper one way (ZUPER_PULL_ONLY): Zuper's jobs, "
                      "statuses and customers are copied into the CRM, and the CRM never "
                      "writes to Zuper.")


def pull_only() -> bool:
    """One-way mirror (2026-09-23, the owner's choice): Zuper is the source of truth and the
    CRM imitates it. Every path that would write to Zuper — a push, a status move, a stamp on
    a pulled record, Send to Zuper, the sweep's CRM half — is skipped, counted and logged."""
    return _flag("ZUPER_PULL_ONLY", False)


AHS_CREATE_SENTENCE = ("An AHS work-order email is the one thing that creates a job in Zuper "
                       "from here (ZUPER_AHS_EMAIL_CREATES_JOBS). Nothing else the CRM holds is "
                       "written to Zuper, and this card is not an AHS email.")


def ahs_email_creates_jobs() -> bool:
    """The ONE exception to the one-way mirror (2026-09-28, the owner's scoped decision).

    Zuper cannot read a mailbox — it has no email ingest, and a workflow can only be started
    by a record event — so an AHS work order that arrives as an email can reach Zuper no other
    way than the CRM creating it. This flag permits exactly that and nothing more: the
    `create` half of one AHS-email card (its customer, the job, the work order as a note), on
    the board `mapping.CATEGORIES` names for the AHS pipeline, in whatever status Zuper makes
    a new job in. No update, no status move, no delete, no other card — `pull_only()` still
    answers for all of those, and `client.ahs_create()` is the only scope in which the
    exception applies.
    """
    return _flag("ZUPER_AHS_EMAIL_CREATES_JOBS", False)


DISPATCH_WRITES_OFF_SENTENCE = (
    "This server does not let the Dispatch page change Zuper (DISPATCH_ZUPER_WRITES is not "
    "true), so nothing is written whatever the switches on the page say.")


def dispatch_writes_enabled() -> bool:
    """The server's gate on the Dispatch page's "let the agent do it" (phase 3, 2026-10-01).

    Off unless the deployment says so. With it off, `client.dispatch_write()` allows NOTHING,
    whatever the page's own switches (dispatch_settings) say — so a switch flipped by mistake
    on a server nobody armed still writes nothing. It is the second key of two."""
    return _flag("DISPATCH_ZUPER_WRITES", False)


def api_key() -> str:
    key = os.getenv("ZUPER_API_KEY", "").strip()
    if key:
        return key
    path = os.getenv("ZUPER_API_KEY_FILE", "").strip()
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""
    return ""


def key_set() -> bool:
    return bool(api_key())


def base_url() -> str:
    return (os.getenv("ZUPER_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")


def crm_base_url() -> str:
    return (os.getenv("ZUPER_CRM_BASE_URL") or DEFAULT_CRM_BASE_URL).strip().rstrip("/")


def company_name() -> str:
    """Optional: the Zuper company name, for the setup check's region lookup."""
    return os.getenv("ZUPER_COMPANY_NAME", "").strip()


def job_url_template() -> str:
    """How to link a Zuper job from the CRM. UNVERIFIED default; empty = no link drawn."""
    raw = os.getenv("ZUPER_JOB_URL_TEMPLATE")
    if raw is None:
        return "https://app.zuperpro.com/jobs/{uid}/details"
    return raw.strip()


def webhook_token() -> str:
    return os.getenv("ZUPER_WEBHOOK_TOKEN", "").strip()


def webhook_secret() -> str:
    """Optional: an HMAC secret, used only if Zuper turns out to sign its deliveries."""
    return os.getenv("ZUPER_WEBHOOK_SECRET", "").strip()


def requests_per_minute() -> int:
    try:
        return max(1, int(os.getenv("ZUPER_REQUESTS_PER_MINUTE", "180")))
    except ValueError:
        return 180


def sweep_minutes() -> int:
    try:
        return max(1, int(os.getenv("ZUPER_SWEEP_MINUTES", "15")))
    except ValueError:
        return 15


def settings(db: Session) -> ZuperSettings:
    row = db.get(ZuperSettings, 1)
    if row is None:
        row = ZuperSettings(id=1)
        db.add(row)
        db.flush()
    return row


def peek_settings(db: Session) -> ZuperSettings | None:
    """The row if it exists, without creating one (read paths and the flush listener)."""
    with db.no_autoflush:
        return db.get(ZuperSettings, 1)


def armed(db: Session) -> bool:
    """May the sync act by itself right now?"""
    if not env_enabled() or not key_set():
        return False
    row = peek_settings(db)
    return bool(row and row.enabled and row.setup_passed)


def today_et(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(ACCOUNT_TZ).date()


def cutover_date(db: Session) -> date | None:
    row = peek_settings(db)
    raw = (row.workiz_cutover_date or "").strip() if row else ""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def before_cutover(db: Session, now: datetime | None = None) -> bool:
    """Is Workiz still in use? True while no cutover date is set, and before that day."""
    cut = cutover_date(db)
    return cut is None or today_et(now) < cut


def workiz_retired_sentence(db: Session, now: datetime | None = None) -> str | None:
    """Why the Workiz importer may not run, or None. It may not on or after the cutover date
    set in Settings → Zuper. A database without the Zuper tables has no cutover date."""
    from sqlalchemy import inspect

    bind = db.get_bind()
    with getattr(bind, "engine", bind).connect() as conn:
        if not inspect(conn).has_table("zuper_settings"):
            return None
    cut = cutover_date(db)
    if cut is None or today_et(now) < cut:
        return None
    return ("Workiz was retired on %s (Settings → Zuper → Workiz cutover date), so the Workiz "
            "importer no longer runs: jobs come from Zuper now. Nothing was read or written."
            % cut.isoformat())


def config_summary() -> dict:
    """What an ADMIN may know about the deployment. Never the key or the tokens."""
    return {"env_enabled": env_enabled(), "key_set": key_set(), "base_url": base_url(),
            "webhook_token_set": bool(webhook_token()),
            "webhook_secret_set": bool(webhook_secret()),
            "crm_base_url": crm_base_url(), "requests_per_minute": requests_per_minute(),
            "sweep_minutes": sweep_minutes()}
