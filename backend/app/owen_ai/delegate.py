"""Sub-chats: one main chat hands customers to their own chats (2026-11-19, phase 1).

Owen asks one chat for many proposals ("make proposals for #843, #836, #841; #698 is 1 leak").
That chat calls `delegate_jobs`: one SUB-CHAT per customer (an OwenAiThread with `parent_id` =
the main chat), each with its own queued message - the job, Owen's instructions and the
standing rules - so each customer's work keeps its own Claude session and its own history.
The runner answers them like any chat (`kind` "delegate" ranks after Owen's own messages).

- `status(parent)` - what each sub-chat is doing: its last answer, and the proposal draft it
  made (pending / created, the prices).
- `send(parent, child, text)` - a correction for one customer, queued in that sub-chat (only a
  sub-chat of THIS main chat).
- `after_answer(db, thread)` - called by the runner after every answer: when the last sub-chat
  of a main chat has nothing left to do, ONE follow-up is queued in the main chat asking it to
  read the results and write the summary - like the customer review's follow-up.

Nothing here talks to Zuper: a sub-chat drafts through `propose_proposal_draft` like any chat,
and a draft reaches Zuper only through Approve.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import OwenAiMessage, OwenAiProposalDraft, OwenAiThread

KIND = "delegate"
MAX_JOBS = 15
ANSWER_CHARS = 900
FOLLOW_UP_MARK = "[sub-chats finished]"
FOLLOW_UP = (FOLLOW_UP_MARK + " Every sub-chat you started has finished. Call delegated_status, "
             "then write Owen ONE summary table: customer, job, proposal draft, the prices "
             "(customer pays), and what needs him (a price question, a leak count that does not "
             "match Frontdoor, no photos, a refusal). Do not repeat each sub-chat's whole answer.")
PREAMBLE = (
    "You are a SUB-CHAT that Owen's main chat \"{parent}\" (#{pid}) started for ONE customer: "
    "job #{job}. Work only on this job. Follow the proposal-draft skill, the proposal and report "
    "guides and every current rule. Never send anything to a customer. Owen's instructions for "
    "this job:\n\n{text}\n\nEnd with a short result: the draft number (or why there is none), the "
    "price lines, and anything that needs Owen.")


def _now() -> datetime:
    return datetime.now(UTC)


def _job(value: Any) -> str | None:
    m = re.search(r"\d{2,7}", str(value or ""))
    return m.group(0) if m else None


def start(db: Session, parent_id: int, items: list[dict]) -> list[OwenAiThread]:
    """One sub-chat per item {job, instructions}. Raises ValueError with a sentence."""
    parent = db.get(OwenAiThread, parent_id)
    if parent is None:
        raise ValueError("Sub-chats can only be started from a chat.")
    if parent.parent_id:
        raise ValueError("A sub-chat cannot start sub-chats of its own; ask the main chat.")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_JOBS:
        raise ValueError("Give 1 to %d jobs, each {job, instructions}." % MAX_JOBS)
    clean = []
    for i, it in enumerate(items):
        job = _job(it.get("job") if isinstance(it, dict) else None)
        text = str((it or {}).get("instructions") or "").strip() if isinstance(it, dict) else ""
        if not job or not text:
            raise ValueError("Item %d needs a job number and instructions." % (i + 1))
        clean.append((job, text[:4000]))
    if len({j for j, _ in clean}) != len(clean):
        raise ValueError("Each job once: put everything for one customer in one item.")
    now = _now()
    out = []
    for job, text in clean:
        th = OwenAiThread(user_id=parent.user_id, kind="task", parent_id=parent.id,
                          title=("Job #%s · %s" % (job, text.splitlines()[0]))[:200],
                          job_number=job, created_at=now, updated_at=now)
        db.add(th)
        db.flush()
        db.add(OwenAiMessage(thread_id=th.id, role="user", kind=KIND, status="queued",
                             created_at=now, content=PREAMBLE.format(
                                 parent=parent.title, pid=parent.id, job=job, text=text)))
        out.append(th)
    parent.updated_at = now
    db.commit()
    return out


def children(db: Session, parent_id: int) -> list[OwenAiThread]:
    return list(db.scalars(select(OwenAiThread).where(OwenAiThread.parent_id == parent_id)
                           .order_by(OwenAiThread.id)))


def _state(db: Session, th: OwenAiThread) -> str:
    users = list(db.scalars(select(OwenAiMessage.status).where(
        OwenAiMessage.thread_id == th.id, OwenAiMessage.role == "user")))
    if "running" in users:
        return "working"
    if "queued" in users:
        return "waiting its turn"
    last = db.scalars(select(OwenAiMessage).where(
        OwenAiMessage.thread_id == th.id, OwenAiMessage.role == "assistant")
        .order_by(OwenAiMessage.id.desc()).limit(1)).first()
    return "error" if last is not None and last.status == "error" else "finished"


def status(db: Session, parent_id: int) -> dict:
    rows = []
    for th in children(db, parent_id):
        last = db.scalars(select(OwenAiMessage).where(
            OwenAiMessage.thread_id == th.id, OwenAiMessage.role == "assistant")
            .order_by(OwenAiMessage.id.desc()).limit(1)).first()
        d = db.scalars(select(OwenAiProposalDraft).where(OwenAiProposalDraft.thread_id == th.id)
                       .order_by(OwenAiProposalDraft.id.desc()).limit(1)).first()
        rows.append({
            "sub_chat": th.id, "job": th.job_number, "title": th.title, "state": _state(db, th),
            "last_answer": ((last.content or last.error or "") if last else "")[:ANSWER_CHARS],
            "draft": None if d is None else {
                "draft_id": d.id, "status": d.status, "target": d.target,
                "proposal": d.estimate_number or ((d.spec or {}).get("fill") or {}).get(
                    "estimate_number"),
                "customer": d.customer_name,
                "customer_pays": {o["name"]: o["price"] for o in d.options or []},
                "error": d.error}})
    return {"main_chat": parent_id, "sub_chats": rows,
            "all_finished": bool(rows) and all(r["state"] in ("finished", "error") for r in rows)}


def send(db: Session, parent_id: int, child_id: int, text: str) -> OwenAiThread:
    th = db.get(OwenAiThread, child_id)
    if th is None or th.parent_id != parent_id:
        raise ValueError("Sub-chat %s is not one this chat started." % child_id)
    text = str(text or "").strip()
    if not text:
        raise ValueError("Say what to change.")
    now = _now()
    db.add(OwenAiMessage(thread_id=th.id, role="user", kind=KIND, status="queued",
                         created_at=now, content=text[:4000]))
    th.updated_at = now
    db.commit()
    return th


def after_answer(db: Session, th: OwenAiThread | None) -> bool:
    """After a sub-chat answers: when none of its siblings has anything left, queue ONE
    follow-up in the main chat. Returns True when one was queued."""
    if th is None or not th.parent_id:
        return False
    kids = [k.id for k in children(db, th.parent_id)]
    left = db.scalar(select(OwenAiMessage.id).where(
        OwenAiMessage.thread_id.in_(kids), OwenAiMessage.role == "user",
        OwenAiMessage.status.in_(("queued", "running"))).limit(1))
    if left is not None:
        return False
    waiting = db.scalar(select(OwenAiMessage.id).where(
        OwenAiMessage.thread_id == th.parent_id, OwenAiMessage.role == "user",
        OwenAiMessage.status.in_(("queued", "running")),
        OwenAiMessage.content.like(FOLLOW_UP_MARK + "%")).limit(1))
    if waiting is not None or db.get(OwenAiThread, th.parent_id) is None:
        return False
    db.add(OwenAiMessage(thread_id=th.parent_id, role="user", kind=KIND, status="queued",
                         created_at=_now(), content=FOLLOW_UP))
    db.commit()
    return True
