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
    the board `ahs_email_board()` names (AHS Simple Flow since 2026-11-06), in whatever status
    Zuper makes a new job in. No update, no status move, no delete, no other card —
    `pull_only()` still answers for all of those, and `client.ahs_create()` is the only scope
    in which the exception applies.
    """
    return _flag("ZUPER_AHS_EMAIL_CREATES_JOBS", False)


# The Zuper board an emailed AHS work order's job is created on (2026-11-06). It was AHS -
# Inspection until the team built "AHS Simple Flow" and began moving every new AHS job there by
# hand; now the job starts there. Still no status is named, so it lands in the board's NEW
# column ("Got a job from AHS"). Found BY NAME each time; a board of that name not in Zuper
# creates nothing (never a fallback to another board).
AHS_EMAIL_BOARD_DEFAULT = "AHS Simple Flow"


def ahs_email_board() -> str:
    return (os.environ.get("ZUPER_AHS_EMAIL_BOARD") or "").strip() or AHS_EMAIL_BOARD_DEFAULT


DISPATCH_WRITES_OFF_SENTENCE = (
    "This server does not let the Dispatch page change Zuper (DISPATCH_ZUPER_WRITES is not "
    "true), so nothing is written whatever the switches on the page say.")


def proposal_lines_enabled() -> bool:
    """A signed proposal's accepted option is copied onto its job's line items (2026-10-02,
    `app/zuper/proposals.py`). Off unless the deployment says so: the owner switches it on after
    the test on test job #701. With it off, `client.proposal_lines()` allows nothing."""
    return _flag("ZUPER_PROPOSAL_LINES", False)


OWEN_AI_PROPOSALS_OFF_SENTENCE = (
    "Owen AI may not create proposal drafts in Zuper on this server "
    "(OWEN_AI_PROPOSALS_ENABLED is not true).")


def owen_ai_proposals_enabled() -> bool:
    """The server's gate on Owen AI's approved proposal DRAFTS (2026-11-09, app/owen_ai/
    proposals.py). Off unless the deployment says so; with it off `client.owen_ai_proposal_write`
    allows nothing, whatever the registry says. Owen AI can still PREPARE a draft for the
    Proposals tab — that writes only to the CRM's database."""
    return _flag("OWEN_AI_PROPOSALS_ENABLED", False)


OWEN_AI_PROPOSAL_PICTURES_OFF_SENTENCE = (
    "Step pictures are switched off on this server (OWEN_AI_PROPOSAL_PICTURES_ENABLED is not "
    "true), so nothing is generated or attached.")


def owen_ai_proposal_pictures_enabled() -> bool:
    """The server's gate on Owen AI's optional step pictures for a CREATED proposal draft
    (2026-11-09, app/owen_ai/pictures.py): the OpenAI spend that makes them and the attach that
    puts them on the proposal. Off unless the deployment says so; with it off
    `client.owen_ai_proposal_pictures` allows nothing. OWEN_AI_PROPOSALS_ENABLED must be on too."""
    return _flag("OWEN_AI_PROPOSAL_PICTURES_ENABLED", False)


OWEN_AI_REPORT_ATTACH_OFF_SENTENCE = (
    "Attaching reports to proposals is switched off on this server (OWEN_AI_REPORT_ATTACH_ENABLED "
    "is not true), so nothing was put on the proposal.")


def owen_ai_report_attach_enabled() -> bool:
    """The server's gate on putting an after-inspection report's PDF on a DRAFT proposal
    (2026-11-12, app/owen_ai/report_attach.py). Off unless the deployment says so; with it off
    `client.owen_ai_report_attach` allows nothing. OWEN_AI_PROPOSALS_ENABLED must be on too."""
    return _flag("OWEN_AI_REPORT_ATTACH_ENABLED", False)


def dispatch_writes_enabled() -> bool:
    """The server's gate on the Dispatch page's "let the agent do it" (phase 3, 2026-10-01).

    Off unless the deployment says so. With it off, `client.dispatch_write()` allows NOTHING,
    whatever the page's own switches (dispatch_settings) say — so a switch flipped by mistake
    on a server nobody armed still writes nothing. It is the second key of two."""
    return _flag("DISPATCH_ZUPER_WRITES", False)


AUTOREPLY_OFF_SENTENCE = ("The auto-reply is switched off on this server "
                          "(ZUPER_AUTOREPLY_ENABLED is not set).")


def autoreply_enabled() -> bool:
    """The server's gate on the auto-reply (2026-10-19, app/autoreply): the polite text to a
    customer whose Zuper text or missed call sat unanswered. Off unless the deployment says so.
    With it off, `client.autoreply_write()` allows NOTHING and the pass reads nothing — whatever
    the switch in Dispatch → Settings says. The second key of two."""
    return _flag("ZUPER_AUTOREPLY_ENABLED", False)


FOLLOWUP_OFF_SENTENCE = ("The call follow-up is switched off on this server "
                         "(CALL_FOLLOWUP_ENABLED is not set, or FOLLOWUPS_ENABLED is also set).")


def followup_enabled() -> bool:
    """The server's gate on the call follow-up (2026-10-21, app/followup): a text, then an AI
    call, after the team's call to a customer went unanswered. Off unless the deployment says
    so; then `client.followup_write()` allows NOTHING and the pass reads nothing. Never
    together with Follow-ups (app/cadences): with both gates set, NEITHER runs."""
    return _flag("CALL_FOLLOWUP_ENABLED", False) and not _flag("FOLLOWUPS_ENABLED", False)


FOLLOWUPS_OFF_SENTENCE = ("Follow-ups are switched off on this server "
                          "(FOLLOWUPS_ENABLED is not set).")
FOLLOWUPS_CLASH_SENTENCE = ("Follow-ups refuse to run while the old automatic call follow-up "
                            "is on (CALL_FOLLOWUP_ENABLED): one customer would get two texts.")


def followups_enabled() -> bool:
    """The server's gate on Follow-ups (2026-11-11, app/cadences): the sequences people
    schedule after a customer does not answer. Off unless the deployment says so. It never runs
    together with the old call follow-up - either gate on alone; both on = neither sends."""
    return _flag("FOLLOWUPS_ENABLED", False) and not _flag("CALL_FOLLOWUP_ENABLED", False)


def followups_clash() -> bool:
    return _flag("FOLLOWUPS_ENABLED", False) and _flag("CALL_FOLLOWUP_ENABLED", False)


INSPECTION_DOCS_OFF_SENTENCE = ("The inspection documentation agent is switched off on this "
                                "server (INSPECTION_DOCS_ENABLED is not set).")


def inspection_docs_enabled() -> bool:
    """The server's gate on the inspection-documentation agent (2026-10-21,
    app/inspection_docs). It only READS Zuper; off unless the deployment says so."""
    return _flag("INSPECTION_DOCS_ENABLED", False)


INSPECTION_REPORT_OFF_SENTENCE = ("The post-inspection report is switched off on this server "
                                  "(INSPECTION_REPORT_ENABLED is not set).")


def inspection_report_enabled() -> bool:
    """The server's gate on the post-inspection report (2026-10-21, app/inspection_report). It
    only READS Zuper (job pictures); off unless the deployment says so."""
    return _flag("INSPECTION_REPORT_ENABLED", False)


TECH_BRIEFING_OFF_SENTENCE = ("The field technician briefing is switched off on this server "
                              "(TECH_BRIEFING_ENABLED is not set).")


def tech_briefing_enabled() -> bool:
    """The server's gate on the technicians' day-before briefing (2026-10-21,
    app/tech_briefing). Off unless the deployment says so."""
    return _flag("TECH_BRIEFING_ENABLED", False)


PAYMENTS_OFF_SENTENCE = ("The payments agent's background pass is switched off on this server "
                         "(PAYMENTS_ENABLED is not set).")
PAYMENTS_TEXTS_OFF_SENTENCE = ("Payment reminders to customers are switched off on this server "
                               "(PAYMENTS_TEXTS_ENABLED is not set).")


def payments_enabled() -> bool:
    """The server's gate on the payments agent's background pass (2026-10-21, app/payments):
    due items, Zuper suggestions, screenshot checks. The page and the chat work regardless."""
    return _flag("PAYMENTS_ENABLED", False)


def payments_texts_enabled() -> bool:
    """The second key on payment reminders TO CUSTOMERS (the registry level is the first): with
    it off, `client.payments_write()` allows nothing."""
    return _flag("PAYMENTS_TEXTS_ENABLED", False)


INSIGHTS_OFF_SENTENCE = ("The insights agent's reading of calls and texts is switched off on "
                         "this server (INSIGHTS_ENABLED is not set).")


def insights_enabled() -> bool:
    """The server's gate on the insights pass (2026-10-22, app/insights): the AI reads each call
    and text once and raises customer issues. The report itself works regardless."""
    return _flag("INSIGHTS_ENABLED", False)


REVIEWS_OFF_SENTENCE = ("The reviews & QC agent is switched off on this server "
                        "(REVIEWS_ENABLED is not set).")
REVIEWS_TEXTS_OFF_SENTENCE = ("Review texts to customers are switched off on this server "
                              "(REVIEWS_TEXTS_ENABLED is not set).")


def reviews_enabled() -> bool:
    """The server's gate on the reviews & QC pass (2026-10-22, app/reviews)."""
    return _flag("REVIEWS_ENABLED", False)


def reviews_texts_enabled() -> bool:
    """The second key on its texts to customers (the registry level is the first): with it off,
    `client.reviews_write()` allows nothing."""
    return _flag("REVIEWS_TEXTS_ENABLED", False)


DROPPED_CALL_OFF_SENTENCE = ("The text to callers who left an AI call is switched off on this "
                             "server (DROPPED_CALL_TEXTS_ENABLED is not set).")


def dropped_call_texts_enabled() -> bool:
    """The server's gate on app/dropped_call.py (2026-10-22)."""
    return _flag("DROPPED_CALL_TEXTS_ENABLED", False)


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
    """How to link a Zuper job from the CRM; empty = no link drawn. The office works in Zuper's
    new app (next.zuperpro.com, mapped 2026-10-05: a job is /jobs/<job_uid>/details); the old
    app.zuperpro.com link opened the wrong place (the user, 2026-10-13)."""
    raw = os.getenv("ZUPER_JOB_URL_TEMPLATE")
    if raw is None:
        return "https://next.zuperpro.com/jobs/{uid}/details"
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
