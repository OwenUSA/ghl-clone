"""Per-pipeline access — GoHighLevel's "Manage permissions", enforced.

The rule, as the owner gave it (2026-09-13):

* A pipeline with NO permission rows is open to everyone. That is every pipeline
  that existed before `pipeline_permissions` did.
* A pipeline WITH rows is open to exactly the users named, plus every ADMIN.
* Access decides whether a user can SEE the pipeline and its deals at all. Their
  role still decides what they may DO with what they can see.

A user without access must not learn the pipeline exists: not on the board, not in
a selector, not in a count, not in a search result, not in a dashboard figure, not
behind an id. Every read path that touches pipelines or opportunities asks THIS
module, and a refused lookup by id answers exactly what an id that does not exist
answers (404, or the same 400 an unknown id gets) so existence does not leak.

The shape is deliberately a set of HIDDEN pipeline ids rather than a set of visible
ones. For the common case — nobody has restricted anything — it is the empty set,
and every query is exactly the query it was before this feature. Excluding is also
the fail-safe direction for a pipeline created mid-request: it is visible until a
permission row says otherwise, which is what "nobody selected = everyone" means.
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import auth
from .models import Opportunity, Pipeline, PipelinePermission, Role, Stage


def hidden_pipeline_ids(db: Session, principal: auth.Principal) -> set[int]:
    """The pipelines this principal may NOT see. Empty for an ADMIN, always."""
    if principal.role is Role.ADMIN:
        return set()
    restricted: dict[int, set[int]] = {}
    for pipeline_id, user_id in db.execute(
            select(PipelinePermission.pipeline_id, PipelinePermission.user_id)).all():
        restricted.setdefault(pipeline_id, set()).add(user_id)
    return {pid for pid, users in restricted.items() if principal.user_id not in users}


def can_see(db: Session, principal: auth.Principal, pipeline_id: int | None) -> bool:
    if pipeline_id is None:
        return True
    return pipeline_id not in hidden_pipeline_ids(db, principal)


def visible_opportunities(stmt, hidden: set[int]):
    """Narrow any SELECT that involves `Opportunity` to what may be seen."""
    if hidden:
        stmt = stmt.where(Opportunity.pipeline_id.not_in(hidden))
    return stmt


def get_pipeline(db: Session, principal: auth.Principal, pipeline_id: int) -> Pipeline:
    """A pipeline by id, or 404 — the SAME 404 for "no such pipeline" and "not
    yours to see"."""
    p = db.get(Pipeline, pipeline_id)
    if p is None or not can_see(db, principal, p.id):
        raise HTTPException(404, "pipeline not found")
    return p


def get_stage(db: Session, principal: auth.Principal, stage_id: int) -> Stage:
    s = db.get(Stage, stage_id)
    if s is None or not can_see(db, principal, s.pipeline_id):
        raise HTTPException(404, "stage not found")
    return s


def get_opportunity(db: Session, principal: auth.Principal, opp_id: int) -> Opportunity:
    """404, never 403: a 403 would confirm the deal exists."""
    o = db.get(Opportunity, opp_id)
    if o is None or not can_see(db, principal, o.pipeline_id):
        raise HTTPException(404, "opportunity not found")
    return o
