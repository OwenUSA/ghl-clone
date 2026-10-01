"""The rules that turn Zuper's jobs + every call and text into the Dispatch page's items, and
the moments worth a bell. PURE: no database, no network, `now` passed in — so every rule is
pinned by tests/test_dispatch_rules.py with plain objects.

The owner's design (2026-09-30): fixed rules FIND the work; a person (and, in phase 2, the AI)
explains it. Same input, same items.

An item's `key` names its evidence — the job and the event that caused it — so the same
evidence is one item however often a pass sees it, and NEW evidence (a later call, a later
stage) is a new item even after the old one was marked Done.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from . import config as c
from .comms import Comm

# A text that closes a conversation needs no answer ("Okay", "Thanks", "👍").
ACKS = {"ok", "okay", "k", "kk", "thanks", "thank you", "thank you!", "thanks!", "ty", "great",
        "perfect", "sounds good", "got it", "will do", "yes", "yes thank you", "ok thanks",
        "ok thank you", "👍", "🙏", "👌"}
MISSED_URGENT_DAYS = 7      # an older missed call / text is still listed, never "urgent now"


def is_ack(text: str) -> bool:
    t = " ".join((text or "").lower().replace(".", " ").replace(",", " ").split())
    return t in ACKS


QUEUES = ("new", "after_visit", "missed", "book", "zuper", "visits", "stale")
QUEUE_LABEL = {
    "new": "New jobs not called",
    "after_visit": "After inspection / repair",
    "missed": "Missed calls & texts",
    "book": "Book a visit",
    "zuper": "Update Zuper",
    "visits": "Today & tomorrow",
    "stale": "Gone quiet",
}


@dataclass
class Item:
    key: str
    kind: str
    queue: str
    title: str
    why: str
    todo: str
    due_at: datetime | None
    job_uid: str | None = None
    job_number: str | None = None
    board: str | None = None
    phone: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    # Never shown as urgent, whatever its due time (quiet jobs, unknown callers).
    never_urgent: bool = False

    def urgent(self, now: datetime) -> bool:
        return (not self.never_urgent) and self.due_at is not None and now >= self.due_at


@dataclass
class Event:
    """A bell. `key` makes it ring once."""
    key: str
    kind: str
    title: str
    body: str
    job_uid: str | None = None
    urgent: bool = False


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _ago(now: datetime, dt: datetime | None) -> str:
    if dt is None:
        return "never"
    mins = int((now - dt).total_seconds() // 60)
    if mins < 60:
        return "%d min ago" % max(mins, 0)
    if mins < 48 * 60:
        return "%d h ago" % (mins // 60)
    return "%d days ago" % (mins // (60 * 24))


def _when(dt: datetime | None) -> str:
    if dt is None:
        return ""
    t = c.local(dt)
    return "%s %d, %s" % (t.strftime("%a %b"), t.day, t.strftime("%I:%M %p").lstrip("0"))


def visit_kind(j) -> str:
    """Is a visit on this job an inspection or a repair?"""
    if j.board == c.INSPECTION_BOARD:
        return "inspection"
    if j.board == c.REPAIR_BOARD:
        return "repair"
    if j.status in c.INSPECTION_STAGES_RETAIL or j.status in ("Proposal Made", "Estimate Sent"):
        return "inspection"
    return "repair"


def _label(j) -> str:
    who = (j.customer_name or "").strip() or "customer"
    return "#%s %s" % (j.job_number or "?", who)


def _future_visit(j, now: datetime) -> datetime | None:
    start = c.aware(j.scheduled_start)
    if start and c.local(start).date() >= c.local(now).date():
        return start
    return None


def _comms_for(j, comms: dict[str, list[Comm]]) -> list[Comm]:
    out = [x for p in (j.phones or []) for x in comms.get(p, [])]
    return sorted(out, key=lambda x: x.at)


def _last_update(j) -> datetime | None:
    stamps = [c.aware(x) for x in (j.status_since, j.last_note_at) if x]
    return max(stamps) if stamps else None


def visit_done_at(j, now: datetime) -> datetime | None:
    """When the technician left: pictures posted on the visit day, then none for
    PHOTO_QUIET_MINUTES (Q22). None while pictures are still coming in, or there are none."""
    last = c.aware(j.last_photo_at)
    if not last or not j.photos_today:
        return None
    if now - last < timedelta(minutes=c.PHOTO_QUIET_MINUTES):
        return None
    return last


def evaluate(jobs, comms: dict[str, list[Comm]], now: datetime,
             phone_names: dict[str, str] | None = None) -> tuple[list[Item], list[Event]]:
    items: list[Item] = []
    events: list[Event] = []
    by_phone: dict[str, Any] = {}
    for j in jobs:
        for p in j.phones or []:
            # A number on several jobs belongs to the open one, else the newest.
            cur = by_phone.get(p)
            if cur is None or (j.is_open and not cur.is_open) or (
                    j.is_open == cur.is_open and (c.aware(j.zuper_created_at) or now)
                    > (c.aware(cur.zuper_created_at) or now)):
                by_phone[p] = j

    for j in jobs:
        if not j.is_open or j.board in c.IGNORED_BOARDS:
            continue
        mine = _comms_for(j, comms)
        talks = [x for x in mine if x.talked]
        last_talk = talks[-1].at if talks else None
        base = {"job_uid": j.job_uid, "job_number": j.job_number, "board": j.board,
                "phone": (j.phones or [None])[0]}
        created = c.aware(j.zuper_created_at)
        since = c.aware(j.status_since)
        job_items: list[Item] = []

        # --- off a pipeline -------------------------------------------------------------
        if j.board not in c.BOARDS and j.board not in c.OTHER_PIPELINES:
            job_items.append(Item(
                key="offboard:%s:%s" % (j.job_uid, j.board), kind="off_board", queue="zuper",
                title="%s is on \"%s\", not a pipeline" % (_label(j), j.board),
                why="Jobs outside AHS - Inspection, AHS - Repair & Review and Retail are not "
                    "tracked by the boards, the routes or this page.",
                todo="In Zuper: open #%s, change its Category to the right pipeline and set "
                     "its stage." % j.job_number,
                due_at=created or now, **base))

        # --- a new job nobody has contacted ----------------------------------------------
        first = c.FIRST_CONTACT.get(j.board, set())
        if j.status in first:
            reached = [x for x in mine if (x.out or x.talked) and (not created or x.at >= created)]
            if not reached:
                sla = c.SLA_MINUTES["new_retail" if j.board == c.RETAIL_BOARD else "new_ahs"]
                job_items.append(Item(
                    key="new:%s" % j.job_uid, kind="new_not_called", queue="new",
                    title="%s — new job, nobody has called" % _label(j),
                    why="Arrived %s (%s), stage \"%s\". No call or text from us since." % (
                        _ago(now, created), _when(created), j.status),
                    todo=c.BOOK.get(j.status, "Call the customer") + (
                        ". Then in Zuper move it to the next stage (the Welcome Call! "
                        "checklist pops up — answer it)." if j.board == c.INSPECTION_BOARD
                        else ". Then in Zuper move it to Contact Attempted."),
                    due_at=c.add_business_minutes(created or since or now, sla),
                    evidence={"created_at": _iso(created), "attempts_in": sum(
                        1 for x in mine if not x.out)}, **base))

        # --- the visit is done: call the customer (Q4/Q22) ---------------------------------
        kind = visit_kind(j)
        done_at = visit_done_at(j, now)
        if done_at is not None and j.photo_day == c.local(now).date().isoformat():
            events.append(Event(
                key="visit_done:%s:%s" % (j.job_uid, j.photo_day), kind="%s_done" % kind,
                title="%s finished: %s" % ("Inspection" if kind == "inspection" else "Repair",
                                          _label(j)),
                body="%s posted %d picture%s; the last at %s. %s" % (
                    j.last_photo_by or "The technician", j.photos_today,
                    "" if j.photos_today == 1 else "s", _when(done_at),
                    "Call the customer now." if kind == "inspection"
                    else "Satisfaction call and review request next."),
                job_uid=j.job_uid))
        stage_done = since if (
            (kind == "inspection" and j.status in c.AFTER_INSPECTION_STAGES) or
            (kind == "repair" and j.status in c.AFTER_REPAIR_STAGES)) else None
        trigger = max([t for t in (done_at, stage_done) if t], default=None)
        if trigger and now - trigger <= timedelta(days=c.DONE_EVENT_DAYS) and not (
                last_talk and last_talk >= trigger):
            if kind == "inspection":
                ahs = j.board == c.INSPECTION_BOARD
                todo = ("Call %s: the inspection is done and %s. " % (
                    j.customer_name or "the customer",
                    "we are submitting it to AHS right away" if ahs
                    else "the estimate / proposal is coming soon"))
                if j.status not in c.AFTER_INSPECTION_STAGES and ahs:
                    todo += ("In Zuper: move #%s to Inspection Completed and answer its "
                             "checklist, then Submit To AHS For Approval." % j.job_number)
                elif ahs:
                    todo += "In Zuper: move #%s to Submit To AHS For Approval once it is sent." \
                        % j.job_number
                else:
                    todo += "In Zuper: move #%s to Proposal Made when the proposal goes out." \
                        % j.job_number
                due = c.add_business_minutes(trigger, c.SLA_MINUTES["after_inspection"])
            else:
                todo = ("Satisfaction call to %s, and ask for a Google review. " % (
                    j.customer_name or "the customer"))
                if j.board != c.REPAIR_BOARD:
                    todo += "In Zuper: move #%s to Repair Complete, then Invoice." % j.job_number
                elif j.status == "Call Satisfaction Check":
                    todo += ("In Zuper: move #%s to Review Requested once you have asked."
                             % j.job_number)
                else:
                    todo += ("In Zuper: move #%s to Call Satisfaction Check, then Review "
                             "Requested." % j.job_number)
                due = c.end_of_next_business_day(trigger)
            job_items.append(Item(
                key="after:%s:%s" % (j.job_uid, c.local(trigger).date().isoformat()),
                kind="after_%s" % kind, queue="after_visit",
                title="%s — %s done, customer not called" % (_label(j), kind),
                why=("The technician's last picture was %s." % _ago(now, trigger)
                     if trigger == done_at else
                     "Moved to \"%s\" %s." % (j.status, _ago(now, trigger))) +
                    (" Last conversation: %s." % _ago(now, last_talk) if last_talk else
                     " No conversation with the customer since."),
                todo=todo, due_at=due,
                evidence={"photos": j.photos_today, "last_photo_at": _iso(c.aware(
                    j.last_photo_at)), "stage": j.status}, **base))

        # --- a visit today with no pictures ----------------------------------------------
        end = c.aware(j.scheduled_end)
        start = c.aware(j.scheduled_start)
        today = c.local(now).date()
        if start and end and c.local(start).date() == today and not j.photos_today and \
                now - end >= timedelta(minutes=c.NO_PHOTOS_AFTER_MINUTES) and \
                j.status != "Reschedule Required":
            who = ", ".join(j.assigned or []) or j.technician or "the technician"
            job_items.append(Item(
                key="nophotos:%s:%s" % (j.job_uid, today.isoformat()), kind="no_photos",
                queue="visits", title="%s — no pictures yet" % _label(j),
                why="The visit was due to end at %s and nothing has been posted." % _when(end),
                todo="Check with %s: did the visit happen? Pictures go on the job in Zuper."
                     % who, due_at=end + timedelta(minutes=c.NO_PHOTOS_AFTER_MINUTES), **base))
            events.append(Event(
                key="nophotos:%s:%s" % (j.job_uid, today.isoformat()), kind="no_photos",
                title="No pictures yet: %s" % _label(j),
                body="The visit was due to end at %s. Check with %s." % (_when(end), who),
                job_uid=j.job_uid))

        # --- a visit coming up that nobody confirmed ---------------------------------------
        if start and now < start <= now + timedelta(hours=24):
            recent = [x for x in mine if (x.talked or x.out) and now - x.at <= timedelta(days=3)]
            if not recent:
                job_items.append(Item(
                    key="confirm:%s:%s" % (j.job_uid, c.local(start).date().isoformat()),
                    kind="visit_unconfirmed", queue="visits",
                    title="%s — visit %s not confirmed" % (_label(j), _when(start)),
                    why="No call or text with the customer in the last 3 days.",
                    todo="Call or text to confirm the visit (%s, with %s)." % (
                        _when(start), ", ".join(j.assigned or []) or "the technician"),
                    due_at=start - timedelta(hours=3), **base))

        # --- book a visit / a stage that needs a date ------------------------------------
        future = _future_visit(j, now)
        if j.status in c.BOOK and not future and not any(i.queue == "new" for i in job_items):
            job_items.append(Item(
                key="book:%s:%s:%s" % (j.job_uid, j.status, _iso(since)), kind="book_visit",
                queue="book", title="%s — %s" % (_label(j), c.BOOK[j.status].split(":")[0]),
                why="In \"%s\" since %s; nothing on the calendar." % (j.status, _ago(now, since))
                    + (" Last conversation %s." % _ago(now, last_talk) if last_talk else ""),
                todo=c.BOOK[j.status] + ". Then in Zuper set the visit date and technician "
                     "and move the stage on.",
                due_at=c.add_business_minutes(since or now, c.SLA_MINUTES["book"]),
                evidence={"stage": j.status, "since": _iso(since)}, **base))
        if j.status and (j.status in c.OLD_VISIT_NAMES or any(
                j.status.startswith(p) for p in c.NEEDS_DATE)) and not future:
            job_items.append(Item(
                key="nodate:%s:%s:%s" % (j.job_uid, j.status, _iso(start)), kind="needs_date",
                queue="zuper", title="%s — \"%s\" but no visit date" % (_label(j), j.status),
                why="The stage says a visit is booked, but the job's date is %s." % (
                    _when(start) if start else "empty"),
                todo="In Zuper: set the visit date on #%s, or move it to the stage it is "
                     "really in." % j.job_number, due_at=since or now, **base))

        # --- we talked to the customer after Zuper's last update ---------------------------
        updated = _last_update(j)
        if last_talk and (updated is None or last_talk > updated) and \
                now - last_talk >= timedelta(minutes=30):
            t = talks[-1]
            job_items.append(Item(
                key="talked:%s:%s" % (j.job_uid, _iso(last_talk)), kind="talked_not_updated",
                queue="zuper",
                title="%s — talked %s, Zuper not updated" % (_label(j), _ago(now, last_talk)),
                why="%s call, %ds, %s. Zuper's last change: %s." % (
                    t.source, t.seconds, _when(last_talk),
                    ("stage \"%s\" %s" % (j.status, _ago(now, updated))) if updated
                    else "none"),
                todo="Put what was agreed into Zuper: move the stage, set the date, or add a "
                     "note on #%s." % j.job_number,
                due_at=c.add_business_minutes(last_talk, c.SLA_MINUTES["talked_not_updated"]),
                evidence={"summary": t.summary, "call_at": _iso(last_talk),
                          "source": t.source, "seconds": t.seconds}, **base))

        # --- gone quiet --------------------------------------------------------------------
        if not job_items and since and now - since >= timedelta(days=c.STALE_DAYS) and (
                last_talk is None or now - last_talk >= timedelta(days=c.STALE_DAYS)):
            job_items.append(Item(
                key="stale:%s:%s" % (j.job_uid, _iso(since)), kind="stale", queue="stale",
                title="%s — quiet for %d days" % (_label(j), (now - since).days),
                why="In \"%s\" since %s; last conversation %s." % (
                    j.status, _when(since), _ago(now, last_talk)),
                todo=("Ask AHS where it stands." if j.status in c.WAITING_ON_AHS else
                      "Call the customer, then move the job on in Zuper (or close it)."),
                due_at=None, never_urgent=True, **base))
        items.extend(job_items)

    # --- missed calls and texts nobody answered (any number) -----------------------------
    for phone, mine in comms.items():
        def attended_after(t: datetime, mine: list[Comm] = mine) -> bool:
            return any(x.at > t and (x.out or x.talked) for x in mine)
        missed = [x for x in mine if x.missed and not attended_after(x.at)]
        texts = [x for x in mine if x.kind == "text" and not x.out and not is_ack(x.summary)
                 and not attended_after(x.at)]
        for what, rows in (("call", missed), ("text", texts)):
            if not rows:
                continue
            last = rows[-1]
            j = by_phone.get(phone)
            name = (j.customer_name if j else None) or last.name or (
                phone_names or {}).get(phone)
            known = j is not None
            label = name or "(%s) %s-%s" % (phone[:3], phone[3:6], phone[6:])
            title = "%s from %s%s" % ("Missed call" if what == "call" else "Text not answered",
                                     label, (" — #%s" % j.job_number) if j else "")
            items.append(Item(
                key="missed:%s:%s:%s" % (what, phone, _iso(last.at)), kind="missed_" + what,
                queue="missed", title=title,
                why="%d %s, the last %s; nobody has called or texted back." % (
                    len(rows), "unanswered call(s)" if what == "call" else "unanswered text(s)",
                    _ago(now, last.at)) + ("" if known else
                                           " Not a customer in Zuper — maybe a new lead."),
                todo=("Call them back." if what == "call" else "Reply or call them.") + (
                    " (Their job is #%s, \"%s\".)" % (j.job_number, j.status) if j else ""),
                due_at=c.add_business_minutes(last.at, c.SLA_MINUTES["missed"]),
                job_uid=j.job_uid if j else None, job_number=j.job_number if j else None,
                board=j.board if j else None, phone=phone,
                never_urgent=not known or now - last.at > timedelta(days=MISSED_URGENT_DAYS),
                evidence={"text": last.summary if what == "text" else "",
                          "at": _iso(last.at), "count": len(rows)}))
            if known and now - last.at <= timedelta(days=2):
                events.append(Event(
                    key="missed:%s:%s:%s" % (what, phone, _iso(last.at)), kind="missed_" + what,
                    title=title, body=("“%s”" % last.summary[:200]) if what == "text"
                    else "Called %s. Nobody has called back." % _when(last.at),
                    job_uid=j.job_uid, urgent=c.is_business_time(now)))

    # --- a limit just passed (Q17 c) -------------------------------------------------------
    for it in items:
        if it.kind in ("new_not_called", "after_inspection", "after_repair", "missed_call",
                       "missed_text", "book_visit") and it.urgent(now) and \
                c.is_business_time(now) and it.due_at and now - it.due_at <= timedelta(days=1):
            events.append(Event(
                key="overdue:%s" % it.key, kind="overdue", title="Overdue: %s" % it.title,
                body=it.todo, job_uid=it.job_uid, urgent=True))
    return items, events
