"""When a follow-up starts, stops, waits and runs its next step - PURE: no database, no network
(tests/test_cadences_rules.py). The owner's answers, 2026-11-11:

  Starts   a person's call to a customer that nobody picked up (any line), or a person adding
           the number by hand.
  Stops    the customer texts in; the customer calls in (answered, or not - then it is a call
           to return); any call the customer answers, a person's or the AI's.
  Records  a team member's text or unanswered call is an ATTEMPT: it does not stop anything.
  Steps    text / call me / AI call, each a delay after the previous step finished. Texts and
           AI calls only 8 AM-8 PM Monday-Saturday New York, never on a holiday.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from . import config as c


@dataclass
class Ev:
    """One call or text with the customer's number, from any line."""
    uid: str                 # stable per source: the dedupe key of its timeline event
    at: datetime
    kind: str                # call | text
    out: bool
    answered: bool = False
    ai: bool = False
    final: bool = True       # False = a call still ringing when it was read
    source: str = ""         # Quo | CRM line | Zuper
    staff: str | None = None
    body: str = ""


# What a piece of evidence means for an open follow-up.
STOP = "stop"
ATTEMPT = "attempt"
IGNORE = "ignore"


@dataclass
class Meaning:
    what: str                # STOP | ATTEMPT | IGNORE
    kind: str                # the timeline event's kind
    reason: str = ""
    person_call: bool = False   # a person's own outgoing call (completes a "call me" step)


def unanswered_team_call(e: Ev) -> bool:
    return e.kind == "call" and e.out and not e.answered and not e.ai and e.final


def meaning(e: Ev, *, ours: bool) -> Meaning:
    """`ours` = this module sent it (our text, our AI call): already on the timeline."""
    if e.kind == "call" and not e.final:
        return Meaning(IGNORE, "")
    if e.kind == "text":
        if not e.out:
            return Meaning(STOP, "reply", "The customer texted back")
        if ours:
            return Meaning(IGNORE, "")
        if e.ai:
            return Meaning(ATTEMPT, "ai_text", "An AI agent texted them")
        return Meaning(ATTEMPT, "team_text", "%s texted them" % (e.staff or "The team"))
    if not e.out:
        if e.answered:
            return Meaning(STOP, "call_back", "The customer called back and was answered")
        return Meaning(STOP, "call_back",
                       "The customer called back and nobody answered - call them back")
    if e.answered:
        if ours or e.ai:
            return Meaning(STOP, "reached", "Anisa (AI) reached them")
        return Meaning(STOP, "reached", "%s reached them on the phone" % (e.staff or "The team"),
                       person_call=True)
    if ours or e.ai:
        return Meaning(IGNORE, "")
    return Meaning(ATTEMPT, "team_call", "%s called - no answer" % (e.staff or "The team"),
                   person_call=True)


def ours_call(e: Ev, ai_call_times: list[datetime]) -> bool:
    """Our AI call, reported back by whatever line placed it (the report may not carry the AI
    mark): an outgoing call within a few minutes of a call we requested."""
    if not (e.kind == "call" and e.out):
        return False
    w = timedelta(minutes=c.OUR_CALL_WINDOW_MINUTES)
    return any(timedelta(minutes=-1) <= e.at - t <= w for t in ai_call_times)


def trigger(evs: list[Ev], *, now: datetime, floor: datetime | None,
            ai_call_times: list[datetime]) -> Ev | None:
    """The newest unanswered person's call worth starting a follow-up for: in the lookback, after
    `floor` (when the module was switched on: no backlog), and nothing after it reached them or
    showed the customer reaching out. `evs` oldest first."""
    start = now - timedelta(hours=c.LOOKBACK_HOURS)
    if floor is not None and floor > start:
        start = floor
    for i in range(len(evs) - 1, -1, -1):
        e = evs[i]
        if e.at < start:
            return None
        if unanswered_team_call(e) and not ours_call(e, ai_call_times):
            later = [meaning(x, ours=False).what for x in evs[i + 1:]]
            return None if STOP in later else e
    return None


# ---- the send window and step times ----------------------------------------------------------

def sending_hours(when: datetime) -> bool:
    t = c.local(c.aware(when))
    return (t.weekday() in c.SEND_DAYS and t.date() not in c.holidays(t.year)
            and c.SEND_OPEN <= t.time() < c.SEND_CLOSE)


def next_open(when: datetime) -> datetime:
    """`when` itself inside the send window, else the window's next opening."""
    when = c.aware(when)
    if sending_hours(when):
        return when
    t = c.local(when)
    day = t if t.time() < c.SEND_OPEN else t + timedelta(days=1)
    for _ in range(14):
        cand = day.replace(hour=c.SEND_OPEN.hour, minute=c.SEND_OPEN.minute, second=0,
                           microsecond=0)
        if sending_hours(cand):
            return cand.astimezone(UTC)
        day = day + timedelta(days=1)
    return when


def _at(step: dict) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(step.get("at") or "10:00"))
    return (int(m.group(1)), int(m.group(2))) if m else (10, 0)


def due_at(step: dict, after: datetime) -> datetime:
    """When `step` runs, counted from `after` (the previous step's finish, or the start)."""
    after = c.aware(after)
    if step.get("delay_days"):
        h, m = _at(step)
        day = c.local(after) + timedelta(days=int(step["delay_days"]))
        when = day.replace(hour=h, minute=m, second=0, microsecond=0).astimezone(UTC)
    else:
        when = after + timedelta(minutes=int(step.get("delay_minutes") or 0))
    # Every step lands in the window: a person is reminded to call when they are working too.
    return next_open(when)


def render(template: str, first_name: str | None) -> str:
    name = (first_name or "").strip().split(" ")[0] if first_name else ""
    if not name.isalpha():
        name = ""
    if name:
        return template.replace("{first_name}", name)
    return re.sub(r"\s*\{first_name\}", "", template)


def first_name(full: str | None) -> str | None:
    if not full:
        return None
    head = full.strip().split(" ")[0]
    return head if head.isalpha() else None


def validate_steps(steps) -> list[str]:
    """Problems with a sequence a person built; empty = fine."""
    if not isinstance(steps, list) or not steps:
        return ["a sequence needs at least one step"]
    if len(steps) > c.MAX_STEPS:
        return ["a sequence has at most %d steps" % c.MAX_STEPS]
    out = []
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict) or s.get("type") not in c.STEP_TYPES:
            out.append("step %d: the type must be text, call_me or ai_call" % i)
            continue
        extra = set(s) - {"type", "delay_minutes", "delay_days", "at", "template"}
        if extra:
            out.append("step %d: unknown keys %s" % (i, ", ".join(sorted(extra))))
        days, mins = s.get("delay_days"), s.get("delay_minutes")
        if days is not None:
            if not (isinstance(days, int) and not isinstance(days, bool) and 1 <= days <= 60):
                out.append("step %d: days must be a whole number from 1 to 60" % i)
            if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", str(s.get("at") or "")):
                out.append("step %d: a day delay needs a time like 10:00" % i)
        elif not (isinstance(mins, int) and not isinstance(mins, bool) and 0 <= mins <= 1440):
            out.append("step %d: minutes must be a whole number from 0 to 1440" % i)
        if s["type"] == "text":
            t = s.get("template")
            if not (isinstance(t, str) and t.strip()):
                out.append("step %d: a text step needs its wording" % i)
            elif len(render(t, "Christopher")) > c.TEMPLATE_MAX:
                out.append("step %d: the text is longer than %d characters" % (
                    i, c.TEMPLATE_MAX))
            elif re.search(r"\{(?!first_name\})[^}]*\}", t):
                out.append("step %d: the only placeholder is {first_name}" % i)
        elif "template" in s:
            out.append("step %d: only a text step has wording" % i)
    return out


def describe(step: dict) -> str:
    """"Text, 5 min after" / "AI call, next day at 2:00 PM" / "Call (me), 3 days later at …"."""
    label = c.STEP_LABEL.get(step.get("type"), step.get("type"))
    if step.get("delay_days"):
        h, m = _at(step)
        clock = datetime(2000, 1, 1, h, m).strftime("%I:%M %p").lstrip("0")
        d = int(step["delay_days"])
        return "%s, %s at %s" % (label, "next day" if d == 1 else "%d days later" % d, clock)
    mins = int(step.get("delay_minutes") or 0)
    if mins == 0:
        return "%s, right away" % label
    if mins % 60 == 0:
        return "%s, %d hour%s after" % (label, mins // 60, "" if mins == 60 else "s")
    return "%s, %d min after" % (label, mins)
