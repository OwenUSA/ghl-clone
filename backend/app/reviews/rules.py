"""When a finished job's customer is asked how we did, what we say, and how a reply is read.
PURE: no database, no network, `now` passed in - pinned by tests/test_reviews.py.

The user's decisions (2026-10-22):
* Asked 2 days (`wait_days`) after the job reached Repair Complete, never later than 14 days -
  so a leak has time to show first - and only if nothing says the customer is unhappy: no
  still-leaking / complaint / come-back / payment-problem words since the visit, no open
  customer issue, no callback job, no money still due, no STOP, not asked in 90 days.
* Compliant with Google's rule against review gating: EVERY customer who answers is offered the
  review link. A 5 gets "thank you + link"; 1-4 gets "what could we improve?" (and the office is
  alerted at once) and, once they have answered that, the link too.
* The wording never contains the phrases Zuper's "Thank you for confirming" workflow reacts to
  ("to confirm the appointment", "to rate our technician"), so it never answers our survey.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

UNHAPPY_TAGS = ("still_leaking", "complaint", "callback_request", "payment_issue")
LEAK_WORDS = ("leak", "leaking", "leaks", "still wet", "water coming", "gotera", "filtra",
              "goteando", "mojado")


@dataclass
class Facts:
    """Everything known about one finished job when it comes due."""
    completed_at: datetime
    phone: str | None
    customer_uid: str | None
    cancelled: bool = False
    unhappy_tag: str | None = None          # the first unhappy tag heard since the visit
    open_issue: bool = False
    callback_job: bool = False
    money_due: bool = False
    opted_out: bool = False
    asked_recently: bool = False
    test: bool = True


def due(completed_at: datetime, now: datetime, wait_days: int, window_days: int) -> str:
    """'early' | 'due' | 'late'."""
    if now < completed_at + timedelta(days=wait_days):
        return "early"
    if now > completed_at + timedelta(days=window_days):
        return "late"
    return "due"


def skip_reason(f: Facts, *, test_only: bool) -> str | None:
    """Why this customer is NOT asked, or None."""
    if test_only and not f.test:
        return "test mode: not a test customer"
    if not f.phone or not f.customer_uid:
        return "no mobile number or Zuper customer"
    if f.cancelled:
        return "the job was cancelled"
    if f.unhappy_tag:
        return "the customer said something unhappy since the visit (%s)" % \
            f.unhappy_tag.replace("_", " ")
    if f.open_issue:
        return "an open customer issue in Dispatch"
    if f.callback_job:
        return "a callback job exists for it"
    if f.money_due:
        return "money is still due"
    if f.opted_out:
        return "the customer texted STOP"
    if f.asked_recently:
        return "already asked recently"
    return None


# ---------------------------------------------------------------------------- the words

def first_name(name: str | None) -> str | None:
    first = (name or "").strip().split(" ")[0] if name else ""
    return first if first.isalpha() else None


def _hi(name: str | None) -> str:
    first = first_name(name)
    return "Hi %s" % first if first else "Hi"


def ask_text(name: str | None) -> str:
    return ("%s, this is Dream Team Roofing. Your roof repair is complete - how did we do? "
            "Reply with a number from 1 to 5 (5 = excellent). Reply STOP to opt out."
            % _hi(name))


def five_text(name: str | None, link: str) -> str:
    first = first_name(name)
    thanks = "Thank you, %s!" % first if first else "Thank you!"
    if not link:
        return "%s We're glad you're happy with your roof. - Dream Team Roofing" % thanks
    return ("%s Would you share that on Google? It takes a minute: %s - Dream Team Roofing"
            % (thanks, link))


def improve_text(name: str | None) -> str:
    first = first_name(name)
    return ("Thank you%s. What could we have done better? Your answer goes straight to our "
            "manager. - Dream Team Roofing" % (", %s" % first if first else ""))


def offer_text(name: str | None, link: str) -> str:
    first = first_name(name)
    head = "Thank you for telling us%s - we'll use it." % (", %s" % first if first else "")
    if not link:
        return head + " - Dream Team Roofing"
    return head + " If you'd like, you can also leave a review: %s" % link


# ---------------------------------------------------------------------------- reading a reply

STARS = re.compile("[⭐★]")
NUMBER = re.compile(r"^\s*([1-5])(?:\s*(?:/\s*5|out of 5|stars?|estrellas?))?\s*[.!]*\s*$",
                    re.IGNORECASE)


def parse_score(text: str | None) -> int | None:
    """A bare 1-5 ("5", "4/5", "5 stars", "⭐⭐⭐⭐⭐"); anything else is for the AI to read."""
    t = (text or "").strip()
    m = NUMBER.match(t)
    if m:
        return int(m.group(1))
    stars = len(STARS.findall(t))
    if stars and not STARS.sub("", t).strip(" !.️"):
        return min(stars, 5)
    return None


def mentions_leak(text: str | None) -> bool:
    t = (text or "").lower()
    return any(w in t for w in LEAK_WORDS)
