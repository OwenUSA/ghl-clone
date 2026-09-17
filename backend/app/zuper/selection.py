"""Which cards go to Zuper without anyone pressing Send (2026-09-16, v2).

* **The day-one load:** EVERY card in the AHS pipeline, and the Retail cards in the stages where
  a job is booked or money is owed — Scheduled, Inspection / Estimate and Estimate Sent while
  open, Invoice while open or won. Retail New Lead and Follow Up stay CRM-only. Stages are
  matched by NAME inside the Retail pipeline; a named stage that does not exist is a refusal
  with a sentence, never a guess.
* **New Workiz jobs, before the cutover date:** an AHS one is sent; a Retail one is sent when
  the import files it in one of those stages (the sweep does it, because the importer itself
  may queue nothing). An AHS email card is sent at creation instead (`api.auto_send`).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Opportunity, Pipeline, Stage
from . import mapping

RETAIL_STAGES: dict[str, tuple[str, ...]] = {
    "Scheduled": ("open",),
    "Inspection / Estimate": ("open",),
    "Estimate Sent": ("open",),
    "Invoice": ("open", "won"),
}
WORKIZ_CREATED_BY = "Workiz import"


class Refused(Exception):
    """A reason to select nothing at all."""


def _pipeline(db: Session, name: str) -> Pipeline:
    rows = db.scalars(select(Pipeline).where(Pipeline.name == name)).all()
    if len(rows) != 1:
        raise Refused("%s pipeline named “%s” — the day-one selection needs exactly one." % (
            "No" if not rows else "More than one", name))
    return rows[0]


def rule(db: Session) -> tuple[int, dict[int, tuple[str, tuple[str, ...]]]]:
    """(AHS pipeline id, {Retail stage id: (stage name, statuses)}). Refuses on a missing
    or ambiguous named stage."""
    ahs = _pipeline(db, mapping.AHS_PIPELINE)
    retail = _pipeline(db, mapping.RETAIL_PIPELINE)
    stages: dict[int, tuple[str, tuple[str, ...]]] = {}
    for name, statuses in RETAIL_STAGES.items():
        ids = db.scalars(select(Stage.id).where(Stage.pipeline_id == retail.id,
                                                Stage.name == name)).all()
        if not ids:
            raise Refused("The Retail pipeline has no stage named “%s”, which the day-one "
                          "selection sends. Rename or add it, then run again. Nothing was "
                          "sent." % name)
        if len(ids) > 1:
            raise Refused("The Retail pipeline has %d stages named “%s”; refusing to guess."
                          % (len(ids), name))
        stages[ids[0]] = (name, statuses)
    return ahs.id, stages


def qualifies(o: Opportunity, ahs_id: int, stages: dict) -> bool:
    if o.pipeline_id == ahs_id:
        return True
    hit = stages.get(o.stage_id)
    return hit is not None and o.status in hit[1]


def day_one(db: Session) -> tuple[list[Opportunity], dict[str, int]]:
    """The cards the initial load sends, and the count per group (for the dry run)."""
    ahs_id, stages = rule(db)
    counts: dict[str, int] = {"AHS (all)": 0}
    for name, statuses in RETAIL_STAGES.items():
        for status in statuses:
            counts["Retail / %s (%s)" % (name, status)] = 0
    chosen = []
    for o in db.scalars(select(Opportunity).order_by(Opportunity.id)).all():
        if not qualifies(o, ahs_id, stages):
            continue
        chosen.append(o)
        if o.pipeline_id == ahs_id:
            counts["AHS (all)"] += 1
        else:
            name = stages[o.stage_id][0]
            counts["Retail / %s (%s)" % (name, o.status)] += 1
    return chosen, counts


def is_workiz_origin(o: Opportunity) -> bool:
    return o.created_by == WORKIZ_CREATED_BY or bool((o.custom_fields or {}).get(
        mapping.WORKIZ_ID))
