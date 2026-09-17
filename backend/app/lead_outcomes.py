"""Lead outcome (2026-09-16), CRM-only: why a card closed without ever being booked.

The owner's rule: when a card is closed — status lost or abandoned — WITHOUT booking, a lead
outcome is required: Spam, Wrong number, Not interested, Price shopping, Out of service area,
Duplicate, No response, Other (+ a note, required for Other). "Booked" means the card has a
visit that is not cancelled, or was sent to Zuper. Settable in the modal, by bulk action and by
an AI agent's action; reported by source / campaign and period. Cards closed before this
existed stay blank — nothing is backfilled. Zuper never sees it.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Appointment, Opportunity

OUTCOMES = ["Spam", "Wrong number", "Not interested", "Price shopping", "Out of service area",
            "Duplicate", "No response", "Other"]
OTHER = "Other"
CLOSED = ("lost", "abandoned")
NOTE_MAX = 2000
CAMPAIGN_KEY = "owen_campaign"

NEEDED = ("Choose a lead outcome before closing this card: it was never booked, so the "
          "report needs to know why it closed (%s)." % ", ".join(OUTCOMES))
NOTE_NEEDED = "The lead outcome “Other” needs a note saying what happened."


def booked(db: Session, o: Opportunity) -> bool:
    from .zuper import locks
    if o.id is None:
        return False
    with db.no_autoflush:
        visit = db.scalar(select(Appointment.id).where(
            Appointment.opportunity_id == o.id, Appointment.status != "cancelled").limit(1))
    return visit is not None or locks.is_sent(db, o.id)


def clean(outcome: str | None, note: str | None) -> tuple[str | None, str | None]:
    """The stored pair, or a 400 with the sentence."""
    outcome = (outcome or "").strip() or None
    note = (note or "").strip() or None
    if outcome is None:
        return None, None
    if outcome not in OUTCOMES:
        raise HTTPException(400, "“%s” is not a lead outcome. Choose one of: %s." % (
            outcome, ", ".join(OUTCOMES)))
    if outcome == OTHER and not note:
        raise HTTPException(400, NOTE_NEEDED)
    if note and len(note) > NOTE_MAX:
        raise HTTPException(400, "the lead outcome note is longer than %d characters"
                            % NOTE_MAX)
    return outcome, note


def apply(o: Opportunity, outcome: str | None, note: str | None) -> None:
    if (o.lead_outcome, o.lead_outcome_note) == (outcome, note):
        return
    o.lead_outcome, o.lead_outcome_note = outcome, note
    o.lead_outcome_set_at = datetime.now(UTC) if outcome else None


def require_on_close(db: Session, o: Opportunity, new_status: str | None) -> None:
    """Closing (a change INTO lost / abandoned) a card that was never booked needs an outcome
    — the one already on the card or the one sent in the same request (applied first)."""
    if new_status not in CLOSED or (o.status == new_status and o.id is not None):
        return
    if o.lead_outcome:
        return
    if not booked(db, o):
        raise HTTPException(400, NEEDED)


def report(db: Session, *, since: datetime | None, until: datetime | None,
           opportunities) -> dict:
    """Closed-without-booking outcomes, counted by source and by campaign. `opportunities`
    narrows a SELECT to what the reader may see (assigned_access / pipeline_access)."""
    stmt = select(Opportunity.lead_outcome, Opportunity.source,
                  Opportunity.custom_fields, func.count(Opportunity.id)).where(
        Opportunity.lead_outcome.is_not(None))
    if since is not None:
        stmt = stmt.where(Opportunity.lead_outcome_set_at >= since)
    if until is not None:
        stmt = stmt.where(Opportunity.lead_outcome_set_at < until)
    stmt = opportunities(stmt)
    by_source: dict[str | None, dict[str, int]] = {}
    by_campaign: dict[str | None, dict[str, int]] = {}
    total = 0
    rows = db.execute(stmt.group_by(Opportunity.lead_outcome, Opportunity.source,
                                    Opportunity.custom_fields)).all()
    for outcome, source, blob, n in rows:
        campaign = (blob or {}).get(CAMPAIGN_KEY) if isinstance(blob, dict) else None
        campaign = str(campaign).strip() or None if campaign is not None else None
        for table, key in ((by_source, (source or "").strip() or None),
                           (by_campaign, campaign)):
            counts = table.setdefault(key, {})
            counts[outcome] = counts.get(outcome, 0) + n
        total += n

    def rows_of(table: dict, name: str) -> list[dict]:
        out = [{name: key, "counts": counts, "total": sum(counts.values())}
               for key, counts in table.items()]
        return sorted(out, key=lambda r: (-r["total"], r[name] is None, r[name] or ""))

    return {"outcomes": OUTCOMES, "total": total, "by_source": rows_of(by_source, "source"),
            "by_campaign": rows_of(by_campaign, "campaign"),
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None}
