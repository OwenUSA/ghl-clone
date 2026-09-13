"""The opportunity modal's own records: tabs of custom fields, tasks, notes.

Added 2026-09-13 so the opportunity modal can do what GoHighLevel's does — the
owner: "in ours we cant add tasks or stuff". Mounted by `main.py` as one router, so
the app-level auth gate covers every route here exactly as it covers the rest.

Four rules carry the weight, and each is asserted by a test rather than intended:

* **A custom-field GROUP holds no answer.** It is a named, ordered tab that applies to
  every pipeline. Deleting one moves its fields back to Opportunity details and
  touches neither a definition's attachments nor a single value in any deal's blob.
* **A task notifies nobody.** No task route calls `automations` or `enqueue`; a
  task has a due date and an assignee and neither one queues a job. The tests
  count the `jobs` table around every task write rather than trusting this.
* **Notes are STAFF-only on every path**, the same rule as a NOTE event on a thread
  (DECISIONS.md, 2026-09-10) and the same predicate (`auth.sees_internal`). A TECH
  asking for them by name gets 403 and a write that would create one it cannot read
  is refused; the board's note count is left out of a TECH's payload altogether.
* **Nothing here deletes an opportunity, a contact or an appointment.**
"""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import auth, automations
from .db import get_db
from .models import (
    Contact,
    ContactTag,
    Conversation,
    ConversationEvent,
    CustomFieldDef,
    CustomFieldGroup,
    EventType,
    Opportunity,
    OpportunityContact,
    OpportunityFollower,
    OpportunityNote,
    OpportunityTask,
    Tag,
    User,
    utcnow,
)

router = APIRouter()

GROUP_NAME_MAX = 80
TASK_TITLE_MAX = 255
TASK_DESCRIPTION_MAX = 5000
NOTE_MAX = 20000
# GoHighLevel's own label: "Additional contacts (Max: 10)".
ADDITIONAL_CONTACTS_MAX = 10
# How many first lines of notes the board card's tooltip carries. A card is a
# preview; the Notes tab is where the whole list lives.
NOTE_PREVIEWS_MAX = 10
NOTE_PREVIEW_CHARS = 60


def _get_opportunity(db: Session, opp_id: int) -> Opportunity:
    o = db.get(Opportunity, opp_id)
    if not o:
        raise HTTPException(404, "opportunity not found")
    return o


def _refuse_notes(principal: auth.Principal, verb: str) -> None:
    if not auth.sees_internal(principal):
        raise HTTPException(403, "role %s may not %s internal notes" % (
            principal.role.value, verb))


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite hands back naive datetimes even from a timezone=True column; the
    # values are UTC. Same reason `main._aware` exists.
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


# ---------- custom field groups (the modal's custom tabs) ----------

class GroupBody(BaseModel):
    name: str = Field(max_length=GROUP_NAME_MAX)


class GroupReorder(BaseModel):
    """Every group exactly once — the same permutation contract as stages and fields."""
    group_ids: list[int] = Field(min_length=1)


def _clean_group_name(name: str | None) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise HTTPException(400, "a group needs a name — it is the tab's label")
    return cleaned


def load_groups(db: Session) -> list[CustomFieldGroup]:
    return list(db.scalars(select(CustomFieldGroup).order_by(
        CustomFieldGroup.position, CustomFieldGroup.id)).all())


def _group_out(g: CustomFieldGroup, field_ids: list[int]) -> dict:
    return {"id": g.id, "name": g.name, "position": g.position,
            "field_ids": field_ids}


def _groups_payload(db: Session) -> list[dict]:
    members: dict[int, list[int]] = {}
    for d in db.scalars(select(CustomFieldDef).where(
            CustomFieldDef.group_id.is_not(None))
            .order_by(CustomFieldDef.position, CustomFieldDef.id)).all():
        members.setdefault(d.group_id, []).append(d.id)
    return [_group_out(g, members.get(g.id, [])) for g in load_groups(db)]


def check_group(db: Session, group_id: int | None) -> int | None:
    """A field's group must exist. None means Opportunity details."""
    if group_id is not None and not db.get(CustomFieldGroup, group_id):
        raise HTTPException(400, "unknown group_id %d" % group_id)
    return group_id


@router.get("/api/custom-field-groups")
def list_groups(db: Session = Depends(get_db), _: auth.Principal = auth.ANY_USER):
    """In tab order. Readable by everyone, like the fields themselves: the modal
    of every role draws these tabs."""
    return _groups_payload(db)


@router.post("/api/custom-field-groups", status_code=201)
def create_group(body: GroupBody, db: Session = Depends(get_db),
                 _: auth.Principal = auth.ADMIN):
    """ADMIN, matching every other write to the custom-field structure. Appended
    as the last tab. Duplicate names are allowed, as they are for stages."""
    n = db.scalar(select(func.count(CustomFieldGroup.id))) or 0
    g = CustomFieldGroup(name=_clean_group_name(body.name), position=n)
    db.add(g)
    db.commit()
    db.refresh(g)
    return _group_out(g, [])


@router.patch("/api/custom-field-groups/{group_id}")
def rename_group(group_id: int, body: GroupBody, db: Session = Depends(get_db),
                 _: auth.Principal = auth.ADMIN):
    g = db.get(CustomFieldGroup, group_id)
    if not g:
        raise HTTPException(404, "group not found")
    g.name = _clean_group_name(body.name)
    db.commit()
    return next(p for p in _groups_payload(db) if p["id"] == group_id)


@router.post("/api/custom-field-groups/reorder")
def reorder_groups(body: GroupReorder, db: Session = Depends(get_db),
                   _: auth.Principal = auth.ADMIN):
    groups = load_groups(db)
    if sorted(body.group_ids) != sorted(g.id for g in groups):
        raise HTTPException(400, (
            "reorder takes every group exactly once — expected %d ids, got %d"
            % (len(groups), len(set(body.group_ids)))))
    by_id = {g.id: g for g in groups}
    for position, group_id in enumerate(body.group_ids):
        by_id[group_id].position = position
    db.commit()
    return _groups_payload(db)


@router.delete("/api/custom-field-groups/{group_id}")
def delete_group(group_id: int, db: Session = Depends(get_db),
                 _: auth.Principal = auth.ADMIN):
    """Delete the TAB. Its fields move to Opportunity details, unchanged.

    The row really goes — a group is a label, not a record anybody typed — but
    nothing it named is touched: each field keeps its key, its pipelines, its
    archive state and every answer recorded against it. The response names the
    fields that moved so the caller can say so.
    """
    g = db.get(CustomFieldGroup, group_id)
    if not g:
        raise HTTPException(404, "group not found")
    moved = db.scalars(select(CustomFieldDef).where(
        CustomFieldDef.group_id == group_id)).all()
    for d in moved:
        d.group_id = None
    db.flush()
    db.delete(g)
    db.flush()
    for position, rest in enumerate(load_groups(db)):
        rest.position = position
    db.commit()
    return {"deleted": group_id, "moved_field_ids": [d.id for d in moved]}


# ---------- tasks ----------

class TaskCreate(BaseModel):
    title: str = Field(max_length=TASK_TITLE_MAX)
    description: str | None = Field(None, max_length=TASK_DESCRIPTION_MAX)
    due_at: datetime | None = None
    assigned_user_id: int | None = None


class TaskPatch(BaseModel):
    title: str | None = Field(None, max_length=TASK_TITLE_MAX)
    description: str | None = Field(None, max_length=TASK_DESCRIPTION_MAX)
    due_at: datetime | None = None
    assigned_user_id: int | None = None
    # True completes the task, False reopens it.
    done: bool | None = None


def _task_out(t: OpportunityTask) -> dict:
    return {"id": t.id, "opportunity_id": t.opportunity_id,
            "contact_id": t.contact_id, "title": t.title,
            "description": t.description, "due_at": _aware(t.due_at),
            "assigned_user_id": t.assigned_user_id,
            "assigned_user_name": t.assignee.name if t.assignee else None,
            "done": t.completed_at is not None,
            "completed_at": _aware(t.completed_at),
            "created_by_id": t.created_by_id,
            "created_at": _aware(t.created_at)}


def _clean_task_title(title: str | None) -> str:
    cleaned = (title or "").strip()
    if not cleaned:
        raise HTTPException(400, "a task needs a title")
    return cleaned


def _check_assignee(db: Session, user_id: int | None) -> None:
    """Any user account, any role: a TECH is exactly who a site task goes to."""
    if user_id is not None and not db.get(User, user_id):
        raise HTTPException(400, "unknown assigned_user_id")


@router.get("/api/opportunities/{opp_id}/tasks")
def list_tasks(opp_id: int, db: Session = Depends(get_db),
               _: auth.Principal = auth.ANY_USER):
    """Open tasks first, soonest due first (undated last); done ones after.
    Everyone reads: a TECH is who a site task is usually for."""
    _get_opportunity(db, opp_id)
    rows = db.scalars(select(OpportunityTask)
                      .options(selectinload(OpportunityTask.assignee))
                      .where(OpportunityTask.opportunity_id == opp_id)).all()
    rows = sorted(rows, key=lambda t: (
        t.completed_at is not None, t.due_at is None,
        _aware(t.due_at) or utcnow(), t.id))
    return [_task_out(t) for t in rows]


@router.post("/api/opportunities/{opp_id}/tasks", status_code=201)
def create_task(opp_id: int, body: TaskCreate, db: Session = Depends(get_db),
                principal: auth.Principal = auth.STAFF):
    """STAFF. A TECH cannot edit records (CLAUDE.md), and a task is one.

    Enqueues NOTHING. There is no reminder and no assignee notification — the
    owner's rule — and `test_opportunity_workspace.py` counts the jobs table.
    """
    o = _get_opportunity(db, opp_id)
    _check_assignee(db, body.assigned_user_id)
    t = OpportunityTask(opportunity_id=o.id, contact_id=o.contact_id,
                        title=_clean_task_title(body.title),
                        description=(body.description or "").strip() or None,
                        due_at=body.due_at,
                        assigned_user_id=body.assigned_user_id,
                        created_by_id=principal.user_id)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _task_out(t)


@router.patch("/api/tasks/{task_id}")
def update_task(task_id: int, body: TaskPatch, db: Session = Depends(get_db),
                _: auth.Principal = auth.STAFF):
    t = db.get(OpportunityTask, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    data = body.model_dump(exclude_unset=True)
    if "title" in data:
        t.title = _clean_task_title(data["title"])
    if "description" in data:
        t.description = (data["description"] or "").strip() or None
    if "due_at" in data:
        t.due_at = data["due_at"]
    if "assigned_user_id" in data:
        _check_assignee(db, data["assigned_user_id"])
        t.assigned_user_id = data["assigned_user_id"]
    if "done" in data and data["done"] is not None:
        if data["done"] and t.completed_at is None:
            t.completed_at = utcnow()
        elif not data["done"]:
            t.completed_at = None
    db.commit()
    db.refresh(t)
    return _task_out(t)


@router.delete("/api/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db),
                _: auth.Principal = auth.STAFF):
    """STAFF, not ADMIN: a task is the team's own to-do, not a customer record,
    and making a dispatcher ask an admin to remove a typo would be friction for
    nothing. Overrulable — see DECISIONS.md."""
    t = db.get(OpportunityTask, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    db.delete(t)
    db.commit()
    return {"deleted": task_id}


# ---------- notes ----------

class NoteBody(BaseModel):
    body: str = Field(max_length=NOTE_MAX)


def _note_out(n: OpportunityNote) -> dict:
    return {"id": n.id, "opportunity_id": n.opportunity_id, "body": n.body,
            "created_by_id": n.created_by_id,
            "created_by": n.author.name if n.author else None,
            "created_at": _aware(n.created_at), "updated_at": _aware(n.updated_at)}


def _clean_note(body: str | None) -> str:
    cleaned = (body or "").strip()
    if not cleaned:
        raise HTTPException(400, "a note needs some text")
    return cleaned


def contact_note_events(db: Session, contact_id: int | None) -> list[ConversationEvent]:
    """The contact's NOTE events — the thread notes, including the Workiz merge
    notes. Shown read-only under a deal's own notes; never INTERNAL_COMMENT, which
    is chat on a thread rather than a note about the customer."""
    if contact_id is None:
        return []
    return list(db.scalars(
        select(ConversationEvent)
        .join(Conversation, Conversation.id == ConversationEvent.conversation_id)
        .where(Conversation.contact_id == contact_id,
               ConversationEvent.type == EventType.NOTE)
        .order_by(ConversationEvent.occurred_at.desc(),
                  ConversationEvent.id.desc())).all())


@router.get("/api/opportunities/{opp_id}/notes")
def list_notes(opp_id: int, db: Session = Depends(get_db),
               principal: auth.Principal = auth.ANY_USER):
    """This deal's notes, newest first, and under them the contact's thread notes.

    ANY_USER at the gate and refused inside, rather than `auth.STAFF`, so the
    refusal carries the same sentence every other internal-note door gives.
    """
    _refuse_notes(principal, "read")
    o = _get_opportunity(db, opp_id)
    notes = db.scalars(select(OpportunityNote)
                       .options(selectinload(OpportunityNote.author))
                       .where(OpportunityNote.opportunity_id == opp_id)
                       .order_by(OpportunityNote.created_at.desc(),
                                 OpportunityNote.id.desc())).all()
    return {
        "notes": [_note_out(n) for n in notes],
        "contact_notes": [
            {"id": e.id, "conversation_id": e.conversation_id, "body": e.body or "",
             "occurred_at": _aware(e.occurred_at)}
            for e in contact_note_events(db, o.contact_id)],
    }


@router.post("/api/opportunities/{opp_id}/notes", status_code=201)
def create_note(opp_id: int, body: NoteBody, db: Session = Depends(get_db),
                principal: auth.Principal = auth.ANY_USER):
    _refuse_notes(principal, "write")
    _get_opportunity(db, opp_id)
    n = OpportunityNote(opportunity_id=opp_id, body=_clean_note(body.body),
                        created_by_id=principal.user_id)
    db.add(n)
    db.commit()
    db.refresh(n)
    return _note_out(n)


@router.patch("/api/opportunity-notes/{note_id}")
def update_note(note_id: int, body: NoteBody, db: Session = Depends(get_db),
                principal: auth.Principal = auth.ANY_USER):
    _refuse_notes(principal, "write")
    n = db.get(OpportunityNote, note_id)
    if not n:
        raise HTTPException(404, "note not found")
    n.body = _clean_note(body.body)
    db.commit()
    db.refresh(n)
    return _note_out(n)


@router.delete("/api/opportunity-notes/{note_id}")
def delete_note(note_id: int, db: Session = Depends(get_db),
                principal: auth.Principal = auth.ANY_USER):
    _refuse_notes(principal, "delete")
    n = db.get(OpportunityNote, note_id)
    if not n:
        raise HTTPException(404, "note not found")
    db.delete(n)
    db.commit()
    return {"deleted": note_id}


# ---------- the contact's thread, for the card's "View conversations" ----------

@router.post("/api/contacts/{contact_id}/conversation")
def open_contact_conversation(contact_id: int, db: Session = Depends(get_db),
                              _: auth.Principal = auth.ANY_USER):
    """The contact's thread id, creating the empty thread if there is none yet.

    Creating a thread writes one `conversations` row and no event: nothing is sent
    and nothing is queued. It goes through `automations.thread_for`, the one
    find-or-create the composer and the call button already share, so a contact
    never ends up with two threads.
    """
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(404, "contact not found")
    existed = db.scalar(select(Conversation.id).where(
        Conversation.contact_id == contact_id)) is not None
    conv = automations.thread_for(db, contact_id)
    db.commit()
    return {"conversation_id": conv.id, "created": not existed}


# ---------- what the board card shows ----------

def first_line(body: str | None) -> str:
    line = next((ln.strip() for ln in (body or "").splitlines() if ln.strip()), "")
    if len(line) > NOTE_PREVIEW_CHARS:
        return line[:NOTE_PREVIEW_CHARS - 1].rstrip() + "…"
    return line


def card_extras(db: Session, opps: list[Opportunity],
                principal: auth.Principal) -> dict[int, dict]:
    """Per-card counts for a page of the board, in a fixed number of queries.

    * `tags` — the primary contact's tag names (see DECISIONS.md: opportunities
      have no tags of their own; the card and the modal show the contact's).
    * `open_tasks_count` — tasks not done.
    * `notes_count` / `note_previews` — this deal's notes plus the contact's thread
      NOTE events, exactly what the Notes tab lists. **Absent for a TECH**: the
      keys are not sent at all, so no count of a note they cannot read leaves the
      server.
    """
    if not opps:
        return {}
    opp_ids = [o.id for o in opps]
    contact_ids = sorted({o.contact_id for o in opps if o.contact_id is not None})

    tags: dict[int, list[str]] = {}
    if contact_ids:
        for contact_id, name in db.execute(
                select(ContactTag.contact_id, Tag.name)
                .join(Tag, Tag.id == ContactTag.tag_id)
                .where(ContactTag.contact_id.in_(contact_ids))
                .order_by(Tag.name)).all():
            tags.setdefault(contact_id, []).append(name)

    open_tasks = dict(db.execute(
        select(OpportunityTask.opportunity_id, func.count(OpportunityTask.id))
        .where(OpportunityTask.opportunity_id.in_(opp_ids),
               OpportunityTask.completed_at.is_(None))
        .group_by(OpportunityTask.opportunity_id)).all())

    staff = auth.sees_internal(principal)
    deal_notes: dict[int, list[str]] = {}
    contact_notes: dict[int, list[str]] = {}
    if staff:
        for opp_id, body in db.execute(
                select(OpportunityNote.opportunity_id, OpportunityNote.body)
                .where(OpportunityNote.opportunity_id.in_(opp_ids))
                .order_by(OpportunityNote.created_at.desc(),
                          OpportunityNote.id.desc())).all():
            deal_notes.setdefault(opp_id, []).append(first_line(body))
        if contact_ids:
            for contact_id, body in db.execute(
                    select(Conversation.contact_id, ConversationEvent.body)
                    .join(Conversation,
                          Conversation.id == ConversationEvent.conversation_id)
                    .where(Conversation.contact_id.in_(contact_ids),
                           ConversationEvent.type == EventType.NOTE)
                    .order_by(ConversationEvent.occurred_at.desc(),
                              ConversationEvent.id.desc())).all():
                contact_notes.setdefault(contact_id, []).append(first_line(body))

    out: dict[int, dict] = {}
    for o in opps:
        row = {"tags": tags.get(o.contact_id, []) if o.contact_id else [],
               "open_tasks_count": open_tasks.get(o.id, 0)}
        if staff:
            lines = deal_notes.get(o.id, []) + (
                contact_notes.get(o.contact_id, []) if o.contact_id else [])
            row["notes_count"] = len(lines)
            row["note_previews"] = lines[:NOTE_PREVIEWS_MAX]
        out[o.id] = row
    return out


# ---------- followers and additional contacts, for the detail PATCH ----------

def followers_of(db: Session, opp_id: int) -> list[dict]:
    rows = db.execute(
        select(User.id, User.name).join(
            OpportunityFollower, OpportunityFollower.user_id == User.id)
        .where(OpportunityFollower.opportunity_id == opp_id)
        .order_by(User.name, User.id)).all()
    return [{"id": uid, "name": name} for uid, name in rows]


def additional_contacts_of(db: Session, opp_id: int) -> list[dict]:
    rows = db.scalars(
        select(Contact).join(OpportunityContact,
                             OpportunityContact.contact_id == Contact.id)
        .where(OpportunityContact.opportunity_id == opp_id)
        .order_by(OpportunityContact.id)).all()
    return [{"id": c.id, "name": c.name, "email": c.email, "phone": c.phone}
            for c in rows]


def set_followers(db: Session, o: Opportunity, user_ids: list[int]) -> None:
    wanted = set(user_ids)
    for uid in sorted(wanted):
        if not db.get(User, uid):
            raise HTTPException(400, "unknown follower user id %d" % uid)
    for link in db.scalars(select(OpportunityFollower).where(
            OpportunityFollower.opportunity_id == o.id)).all():
        if link.user_id in wanted:
            wanted.discard(link.user_id)
        else:
            db.delete(link)
    for uid in sorted(wanted):
        db.add(OpportunityFollower(opportunity_id=o.id, user_id=uid))


def set_additional_contacts(db: Session, o: Opportunity, contact_ids: list[int],
                            primary_id: int | None) -> None:
    """Replace the set. Refused whole, having written nothing, when it holds more
    than ten, names a contact that does not exist, or names the primary contact."""
    wanted = list(dict.fromkeys(contact_ids))
    if len(wanted) > ADDITIONAL_CONTACTS_MAX:
        raise HTTPException(400, "an opportunity takes at most %d additional "
                                 "contacts — %d were sent"
                            % (ADDITIONAL_CONTACTS_MAX, len(wanted)))
    for cid in wanted:
        if not db.get(Contact, cid):
            raise HTTPException(400, "unknown additional contact id %d" % cid)
        if cid == primary_id:
            raise HTTPException(400, "the primary contact is already on this "
                                     "opportunity — it cannot also be additional")
    keep = set(wanted)
    for link in db.scalars(select(OpportunityContact).where(
            OpportunityContact.opportunity_id == o.id)).all():
        if link.contact_id in keep:
            keep.discard(link.contact_id)
        else:
            db.delete(link)
    for cid in wanted:
        if cid in keep:
            db.add(OpportunityContact(opportunity_id=o.id, contact_id=cid))


def remove_opportunity_records(db: Session, opp_id: int) -> dict:
    """What deleting a deal takes with it: its OWN notes and tasks, and its link
    rows. Nothing else — not the contact, not an additional contact, not a note on
    the contact's thread. Counted, so the delete can say what went."""
    counts = {}
    for model, key in ((OpportunityNote, "notes_deleted"),
                       (OpportunityTask, "tasks_deleted"),
                       (OpportunityFollower, "followers_removed"),
                       (OpportunityContact, "additional_contacts_removed")):
        rows = db.scalars(select(model).where(model.opportunity_id == opp_id)).all()
        for r in rows:
            db.delete(r)
        counts[key] = len(rows)
    db.flush()
    return counts


def release_contact(db: Session, contact_id: int) -> list[int]:
    """Before a contact is deleted: take it off every deal it is an ADDITIONAL
    contact on (a link row, nothing else), and detach its tasks. Returns the ids of
    the deals it was removed from."""
    links = db.scalars(select(OpportunityContact).where(
        OpportunityContact.contact_id == contact_id)).all()
    removed = sorted({link.opportunity_id for link in links})
    for link in links:
        db.delete(link)
    for t in db.scalars(select(OpportunityTask).where(
            OpportunityTask.contact_id == contact_id)).all():
        t.contact_id = None
    db.flush()
    return removed
