"""When the call follow-up acts - PURE: no database, no network (tests/test_followup_rules.py).

A follow-up starts from a TEAM call to a customer that nobody picked up: outgoing, on Quo, the
CRM line or Zuper Connect, made by a person (never an AI's own call), final (not still ringing).
Then, unless the customer was reached in the meantime:

    trigger + text_delay   ->  text   "We just tried to call you..."
    text + wait            ->  call   Anisa, 8 AM-8 PM Mon-Sat, at most 2 a day per number

"Reached" = anything after the trigger showing a conversation happened: any answered call either
way, a text from the customer or from the team, a call FROM the customer (answered or not - a
missed call back belongs to the auto-reply and the office, not to this agent).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class Call:
    """The bits of a comm these rules read (built from dispatch.comms.Comm)."""
    at: datetime
    kind: str            # call | text
    out: bool
    answered: bool
    ai: bool
    final: bool = True
    source: str = ""
    staff: str | None = None


def unanswered_team_call(c: Call) -> bool:
    return c.kind == "call" and c.out and not c.answered and not c.ai and c.final


def reached(after: list[Call]) -> str | None:
    """Why the customer counts as reached by these later comms, or None."""
    for c in after:
        if c.kind == "call" and c.answered:
            return "a call was answered"
        if c.kind == "text":
            return "the customer texted back" if not c.out else "the team texted them"
        if c.kind == "call" and not c.out:
            return "the customer called back"
    return None


def trigger(comms: list[Call], *, now: datetime, since: datetime | None,
            lookback: timedelta) -> Call | None:
    """The latest unanswered team call in the window that nothing after it has resolved.
    `comms` oldest first. `since` = when the agent was switched on (no backlog)."""
    floor = now - lookback
    if since is not None and since > floor:
        floor = since
    for i in range(len(comms) - 1, -1, -1):
        c = comms[i]
        if c.at < floor:
            return None
        if unanswered_team_call(c):
            return None if reached(comms[i + 1:]) else c
    return None


@dataclass
class Step:
    action: str          # text | call | wait | done | expire
    reason: str = ""


def _mins(td: timedelta) -> int:
    return max(1, int(td.total_seconds() // 60) + (1 if td.total_seconds() % 60 else 0))


def next_step(*, trigger_at: datetime, text_at: datetime | None, called: bool,
              after: list[Call], now: datetime, text_delay: timedelta, wait: timedelta,
              lookback: timedelta, in_call_hours: bool, calls_today: int,
              calls_per_day: int) -> Step:
    why = reached(after)
    if why:
        return Step("done", why)
    if called:
        return Step("done", "Anisa called")
    if text_at is None:
        if now - trigger_at > lookback:
            return Step("expire", "too old to text")
        if now - trigger_at < text_delay:
            return Step("wait", "text in %d min" % _mins(text_delay - (now - trigger_at)))
        return Step("text")
    if now - text_at < wait:
        return Step("wait", "call in %d min" % _mins(wait - (now - text_at)))
    if calls_today >= calls_per_day:
        return Step("done", "already called %d times today" % calls_today)
    if not in_call_hours:
        if now - trigger_at > lookback:
            return Step("expire", "the calling window passed")
        return Step("wait", "outside 8 AM-8 PM Mon-Sat")
    return Step("call")


def render(template: str, first_name: str | None, fallback: str) -> str:
    """The text with the first name, or the no-name wording when there is no usable name."""
    name = (first_name or "").strip().split(" ")[0] if first_name else ""
    if not name.isalpha():
        name = ""
    if "{first_name}" not in template:
        return template
    return template.replace("{first_name}", name) if name else fallback
