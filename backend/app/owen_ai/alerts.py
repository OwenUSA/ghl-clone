"""Owen AI's alerts: what is wrong with a job, kept until it is fixed (2026-11-03).

Claude raises them with the `report_issue` tool while auditing a job (owen-ai/.claude/skills/
job-audit), against the rules in owen-ai/knowledge/ahs-simple-flow-playbook.md. They live ONLY in
the CRM's database — nothing goes to Zuper or to a customer.

  * One OPEN alert per (job, rule): raising it again refreshes the evidence and `last_seen_at`.
  * A job's audit that no longer finds an open alert resolves it (`settle_job`), so a fixed
    problem disappears by itself at the next sweep.
  * A NEW urgent/today alert rings the owner's bell (AiAlert); `watch` ones only show on the page.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AiAlert, OwenAiAlert, User
from . import config

SEVERITIES = ("urgent", "today", "watch")
RANK = {s: i for i, s in enumerate(SEVERITIES)}
DISMISS_DAYS = 7                  # a dismissed job+rule stays quiet this long unless it worsens


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime) -> datetime:
    """SQLite hands datetimes back naive; they are UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _iso(dt):
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


def out(a: OwenAiAlert) -> dict:
    return {"id": a.id, "job_uid": a.job_uid, "job_number": a.job_number, "rule_id": a.rule_id,
            "severity": a.severity, "title": a.title, "evidence": a.evidence, "action": a.action,
            "status": a.status, "resolution": a.resolution, "times_seen": a.times_seen,
            "first_seen_at": _iso(a.first_seen_at), "last_seen_at": _iso(a.last_seen_at),
            "closed_at": _iso(a.closed_at)}


def _owner(db: Session) -> User | None:
    email = config.owner_email()
    if not email:
        return None
    return db.scalars(select(User).where(User.email.ilike(email))).first()


def _ring(db: Session, a: OwenAiAlert) -> None:
    owner = _owner(db)
    if owner is None:
        return
    db.add(AiAlert(user_id=owner.id, kind="owen_ai_issue", urgent=a.severity == "urgent",
                   title=("Owen AI · #%s: %s" % (a.job_number or "?", a.title))[:300],
                   body=(a.evidence or "")[:2000] or None))


def raise_alert(db: Session, *, job_uid: str, job_number: str | None, rule_id: str,
                severity: str, title: str, evidence: str | None = None,
                action: str | None = None) -> dict:
    job_uid, rule_id = (job_uid or "").strip(), (rule_id or "").strip().upper()
    if not job_uid or not rule_id or not (title or "").strip():
        raise ValueError("job_uid, rule_id and title are required")
    if severity not in SEVERITIES:
        raise ValueError("severity must be one of %s" % ", ".join(SEVERITIES))
    now = _now()
    a = db.scalars(select(OwenAiAlert).where(
        OwenAiAlert.job_uid == job_uid, OwenAiAlert.rule_id == rule_id,
        OwenAiAlert.status == "open")).first()
    if a is None:
        # The owner dismissed this job+rule recently: respect it unless it got worse.
        gone = db.scalars(select(OwenAiAlert).where(
            OwenAiAlert.job_uid == job_uid, OwenAiAlert.rule_id == rule_id,
            OwenAiAlert.status == "dismissed",
            OwenAiAlert.closed_at >= now - timedelta(days=DISMISS_DAYS))
            .order_by(OwenAiAlert.closed_at.desc())).first()
        if gone is not None and RANK[severity] >= RANK.get(gone.severity, 9):
            return {**out(gone), "new": False, "suppressed": True}
    new = a is None
    if new:
        a = OwenAiAlert(job_uid=job_uid, rule_id=rule_id, first_seen_at=now, times_seen=1)
        db.add(a)
    else:
        a.times_seen = (a.times_seen or 1) + 1
    escalated = not new and RANK[severity] < RANK.get(a.severity, 9)
    a.job_number = (job_number or "").lstrip("#") or a.job_number
    a.severity, a.title = severity, title.strip()[:300]
    a.evidence, a.action = (evidence or "").strip() or None, (action or "").strip() or None
    a.last_seen_at = now
    db.flush()
    if (new or escalated) and severity in ("urgent", "today"):
        _ring(db, a)
    db.commit()
    return {**out(a), "new": new}


def close(db: Session, alert: OwenAiAlert, status: str, why: str) -> None:
    alert.status, alert.resolution, alert.closed_at = status, (why or "").strip()[:1000], _now()


def resolve(db: Session, *, job_uid: str, rule_id: str, why: str) -> dict | None:
    a = db.scalars(select(OwenAiAlert).where(
        OwenAiAlert.job_uid == job_uid, OwenAiAlert.rule_id == rule_id.strip().upper(),
        OwenAiAlert.status == "open")).first()
    if a is None:
        return None
    close(db, a, "resolved", why)
    db.commit()
    return out(a)


def open_for(db: Session, job_uid: str | None = None) -> list[OwenAiAlert]:
    q = select(OwenAiAlert).where(OwenAiAlert.status == "open")
    if job_uid:
        q = q.where(OwenAiAlert.job_uid == job_uid)
    return list(db.scalars(q.order_by(OwenAiAlert.last_seen_at.desc())))


def settle_job(db: Session, job_uid: str, audit_started: datetime) -> int:
    """After a job's audit SUCCEEDED: every open alert it did not raise again is fixed."""
    n = 0
    started = _aware(audit_started)
    for a in open_for(db, job_uid):
        if _aware(a.last_seen_at) < started:
            close(db, a, "resolved", "Not found by the audit of %s." % audit_started.date())
            n += 1
    db.commit()
    return n


# ------------------------------------------------------------------- the tools Claude calls

def _session():
    from ..db import SessionLocal
    return SessionLocal()


def tool_report_issue(args: dict) -> str:
    db = _session()
    try:
        a = raise_alert(db, job_uid=args.get("job_uid", ""), job_number=args.get("job_number"),
                        rule_id=args.get("rule_id", ""), severity=args.get("severity", ""),
                        title=args.get("title", ""), evidence=args.get("evidence"),
                        action=args.get("action"))
    except ValueError as exc:
        return "ERROR: %s" % exc
    finally:
        db.close()
    if a.get("suppressed"):
        return ("Not raised: Owen dismissed %s on #%s recently (%s)." % (
            a["rule_id"], a["job_number"] or a["job_uid"], a["resolution"]))
    return "%s alert #%d (%s, %s) for #%s." % (
        "New" if a["new"] else "Refreshed", a["id"], a["rule_id"], a["severity"],
        a["job_number"] or a["job_uid"])


def tool_clear_issue(args: dict) -> str:
    db = _session()
    try:
        a = resolve(db, job_uid=args.get("job_uid", ""), rule_id=args.get("rule_id", ""),
                    why=args.get("why") or "Fixed.")
    finally:
        db.close()
    return "Resolved alert #%d." % a["id"] if a else "No open alert for that job and rule."


def tool_open_issues(args: dict) -> str:
    import json
    db = _session()
    try:
        rows = [out(a) for a in open_for(db, (args.get("job_uid") or "").strip() or None)]
    finally:
        db.close()
    keep = ("id", "job_number", "job_uid", "rule_id", "severity", "title", "evidence",
            "first_seen_at", "times_seen")
    return json.dumps([{k: r[k] for k in keep} for r in rows], ensure_ascii=False)


SPECS = (
    ("report_issue",
     ("Record a problem found on a job (an alert for Owen). Use the playbook's rule_id "
      "(knowledge/ahs-simple-flow-playbook.md). severity: urgent | today | watch. evidence: the "
      "facts with dates and sources, quoting the customer's exact words when relevant. action: "
      "the one thing to do now. Raising the same job+rule again just refreshes it."),
     {"type": "object", "properties": {
         "job_uid": {"type": "string"}, "job_number": {"type": "string"},
         "rule_id": {"type": "string"}, "severity": {"type": "string",
                                                     "enum": list(SEVERITIES)},
         "title": {"type": "string"}, "evidence": {"type": "string"},
         "action": {"type": "string"}},
      "required": ["job_uid", "rule_id", "severity", "title", "evidence"]},
     tool_report_issue),
    ("clear_issue",
     "Resolve an open alert on a job that is no longer a problem. `why` = what fixed it.",
     {"type": "object", "properties": {"job_uid": {"type": "string"},
                                       "rule_id": {"type": "string"},
                                       "why": {"type": "string"}},
      "required": ["job_uid", "rule_id", "why"]},
     tool_clear_issue),
    ("open_issues",
     "The open alerts, for one job (job_uid) or all. Check these before reporting.",
     {"type": "object", "properties": {"job_uid": {"type": "string"}}},
     tool_open_issues),
)
