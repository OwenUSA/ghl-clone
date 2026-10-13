"""What scripts can work out BEFORE any AI is asked (2026-10-13, the owner's ask: "everything
that doesn't need an agent we won't spend tokens on").

  brief(job)   one customer, compact: board / stage / visit / technician, the job's important
               fields (roof, leak, AHS numbers...), what they said about when they can have the
               visit, and SIGNALS read from every call and text by fixed patterns, English and
               Spanish: confirmed, cancel / move, on the way, visit done, report sent to AHS, a
               day (and time) somebody agreed, a question still unanswered. Each signal keeps
               its time, line and the words it came from.
  check(...)   the Zuper check: fixed rules over those briefs — double-booked, a different day
               or time agreed, on the way but the stage not moved, a visit past its time with
               nothing from the field, a report sent to AHS but the column behind, a promised
               day that is not in Zuper, the Technician field disagreeing. Each finding has its
               evidence and, only where it is unambiguous, the fix (`fix`) the Dispatch page can
               hand to the write switches (all OFF by default; writes.py decides).

No AI, no network, nothing written. The Dispatch page's "Check Zuper" tab shows `check` for
free; the assistant reads the same briefs instead of searching every conversation itself, which
is where its tokens went (live 2026-10-09: 20 look-ups, 14 of them one customer at a time) and
where it guessed (it moved jobs "On the Way" from an agreed time, with no one on the way).

The patterns only FLAG. A signal is a quote with a label; a finding says what to check and why.
Test jobs (a customer, title or address with the word "test") are never checked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DispatchActivity, DispatchAvailability, DispatchJob
from . import config as c
from . import lookups
from .comms import summary_of
from .rules import is_ack, visit_kind

COMMS_DAYS = 14   # how far back a brief reads calls and texts
PROMISE_DAYS = 10   # an agreed day older than this is history, not a booking
UNANSWERED_MINUTES = 30      # the customer's last message with no reply after this long
QUOTE = 160       # characters of a quote kept with a signal
TEST = re.compile(r"\btest\b", re.IGNORECASE)

# The job's fields that matter for a visit, by label (Zuper's own labels vary by board).
KEY_FIELDS = re.compile(r"roof|material|leak|tile|shingle|flat|stor(y|ies)|pitch|age\b|years|"
                        r"ahs|work order|claim|authori|net total|deductible|service fee|"
                        r"customer (pays|chose)|gate|access|pet|dog|language|idioma|priority|"
                        r"add-?on|upsell|video|scope", re.IGNORECASE)
MAX_FIELDS = 14

# ------------------------------------------------------------------------------- patterns

WEEKDAY = {"monday": 0, "mon": 0, "lunes": 0, "tuesday": 1, "tue": 1, "tues": 1, "martes": 1,
           "wednesday": 2, "wed": 2, "miercoles": 2, "miércoles": 2, "thursday": 3, "thu": 3,
           "thurs": 3, "jueves": 3, "friday": 4, "fri": 4, "viernes": 4, "saturday": 5,
           "sat": 5, "sabado": 5, "sábado": 5, "sunday": 6, "sun": 6, "domingo": 6}
DAY_WORD = re.compile(r"\b(today|tonight|this afternoon|this morning|tomorrow|hoy|"
                      r"(?<!la )(?<!en la )mañana|pasado mañana|"
                      r"(?:next |this |el |este |el próximo |el proximo )?(?:%s))\b"
                      % "|".join(sorted(WEEKDAY, key=len, reverse=True)), re.IGNORECASE)
# A window: "between 2 PM and 4 PM", "2-4 PM", "from 9 to 11", "entre las 9 y 11 AM",
# "10:30 AM to 12:30 PM". A bare "2 to 4" needs a lead word (between / from / entre / de).
_T = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
WINDOW = re.compile(r"(?:(between|from|entre(?: las)?|de(?: las)?)\s+)?" + _T +
                    r"\s*(?:-|–|—|to|and|y|a|hasta)\s*(?:las\s+)?" + _T, re.IGNORECASE)  # noqa: RUF001

CONFIRM_WORDS = re.compile(r"\b(confirm\w*|yes|yep|yeah|sure|that works|works for (me|us)|"
                           r"will work|that'?s fine|sounds good|see you|perfect\w*|great|"
                           r"s[ií]|claro|de acuerdo|est[aá] bien|listo|ok(ay)?|okey|"
                           r"👍|👌)", re.IGNORECASE)
CONFIRM_IN_CALL = re.compile(
    r"(confirm(ed|s)?\s+(?:[\w-]+\s+){0,4}?(appointment|visit|schedul\w*|time|slot)\b|"
    r"\b(customer|caller|client|homeowner) (acknowledged|agreed|accepted|was fine with|"
    r"is fine with|said (yes|ok|okay|that works|it works))\b|"
    r"\b(accepted|agreed to (the |this |an |that )?(appointment|visit|time|schedul\w*|slot|"
    r"date|it))\b|which \w+ accepted|ultimately agreed|"
    # "Owen scheduled a visit for the roof inspection on Wednesday" (live 2026-10-09, #735)
    r"\b(scheduled|booked|set up)\s+(?:[\w-]+\s+){0,4}?(appointment|visit|inspection|repair)\b)",
    re.IGNORECASE)
# A customer asking, in a call, to cancel or move: never "we could reschedule".
CANCEL_IN_CALL = re.compile(
    r"\b(wants?|wanted|asked|requested|would like|needs?|needed|prefers?) to (cancel|"
    r"reschedule|postpone|move|push back)|\b(cancell?ed|postponed) (the|their|her|his) "
    r"(appointment|visit)|\bwon'?t be (home|available)\b", re.IGNORECASE)
# The customer, in a text, asking to cancel or move: they say it about THEMSELVES ("I can't
# make it"), never "if he can't make it in time" (live 2026-10-13, about the technician).
CANCEL = re.compile(r"\b(cancel\w*|reschedul\w*|postpone\w*|push (it|this) back|"
                    r"(i|we|i'?m|we'?re|i will|we will) (can'?t|cannot|won'?t|will not|am not|"
                    r"are not|not) (make it|be (home|there|available|able))|"
                    r"no puedo|no podr[eé]|cancelar|reprogramar|cambiar la cita|"
                    r"no voy a estar|no estar[eé]mos|no estar[eé])\b", re.IGNORECASE)
# "Mary cannot be present tomorrow": a day named to say it does NOT work.
NEGATED = re.compile(r"\b(can'?t|cannot|won'?t|will not|not be|not available|unable|"
                     r"no (puede|puedo|podr\w*|va a estar|voy a estar|estar\w*))\b[^.!?]{0,50}$",
                     re.IGNORECASE)
GREETING = re.compile(r"^\W*(hi|hello|hey|hola|good (morning|afternoon|evening)|buen[oa]s? "
                      r"(d[ií]as|tardes|noches)|mr\.? \w+|ms\.? \w+)\W*", re.IGNORECASE)
# A text that asks for something is not a "yes" to our proposal, even if it starts with "Ok".
ASKS = re.compile(r"\?|\b(call me|ll[aá]m\w*|whats ?app|text me|can you|could you|"
                  r"please|por favor|c favor)\b", re.IGNORECASE)
ON_THE_WAY = re.compile(r"\b(on (his|her|their|the|my|our) way|en camino|"
                        r"(llega|llegar[aá]|arrive|arriving|be there)\b.{0,30}\b\d+\s*(min|minutes|"
                        r"minutos)|\d+\s*(minutes|minutos) away)", re.IGNORECASE)
DONE = re.compile(r"\b(we (have )?(just )?finished|(has|have) finished|already finished|"
                  r"(inspection|repair) (is |was )?(done|complete|completed|finished)|"
                  r"hemos terminado|ya terminamos|terminamos|ya hemos terminado|terminado la)",
                  re.IGNORECASE)
AHS_SENT = re.compile(r"((submitted|sent|uploaded|subimos|enviamos|subieron|lo subi\w*|"
                      r"subido)\b.{0,80}\b(american home shield|ahs)\b|"
                      r"\b(american home shield|ahs)\b.{0,40}\b(submitted|received the report))",
                      re.IGNORECASE)
QUESTION = re.compile(r"\?|\b(when|what time|have you|did you|can you|could you|cu[aá]ndo|"
                      r"qu[eé] hora|pueden|podr[ií]an)\b", re.IGNORECASE)


@dataclass
class Signal:
    kind: str    # confirmed | cancel | on_the_way | done | ahs_sent | promise
    at: datetime
    who: str      # us | customer
    line: str    # Quo / CRM line / Zuper Connect
    how: str      # text | call
    quote: str
    day: date | None = None   # promise / confirmed: the day it is for
    window: tuple[int, int] | None = None   # minutes after midnight

    def out(self) -> dict:
        d = {"signal": self.kind, "at": _when(self.at), "from": self.who, "line": self.line,
             "how": self.how, "said": self.quote}
        if self.day:
            d["for"] = self.day.strftime("%a %m/%d") + (" " + _win(self.window)
                                                        if self.window else "")
        return d


def _when(dt: datetime | None) -> str | None:
    dt = c.aware(dt)
    return c.local(dt).strftime("%a %m/%d %I:%M %p").replace(" 0", " ") if dt else None


def _hm(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return "%d:%02d %s" % (h % 12 or 12, m, "AM" if h < 12 else "PM")


def _win(w: tuple[int, int] | None) -> str:
    return "%s–%s" % (_hm(w[0]), _hm(w[1])) if w else ""  # noqa: RUF001


def window_in(text: str) -> tuple[int, int] | None:
    """The first time window in a sentence, as minutes after midnight. With no AM / PM, 1-6
    is the afternoon and 7-12 the morning (a working day)."""
    t = (text or "").replace("a.m.", "am").replace("p.m.", "pm").replace("A.M.", "AM") \
        .replace("P.M.", "PM")
    for m in WINDOW.finditer(t):
        lead, h1, m1, ap1, h2, m2, ap2 = m.groups()
        if not (lead or ap1 or ap2):
            continue    # "3 to 4 inches", "7-12 days": no lead, no AM/PM
        h1, h2 = int(h1), int(h2)
        if not (1 <= h1 <= 12 and 1 <= h2 <= 12):
            continue

        def to24(h, ap):
            ap = (ap or "").lower() or ("pm" if 1 <= h <= 6 or h == 12 else "am")
            return (h % 12) + (12 if ap == "pm" else 0)
        e = to24(h2, ap2) * 60 + int(m2 or 0)
        s = to24(h1, ap1 or ap2) * 60 + int(m1 or 0)
        if s >= e:
            s = to24(h1, "am") * 60 + int(m1 or 0)
        if s < e and e - s <= 6 * 60:
            return s, e
    return None


def day_in(text: str, said_at: datetime, near: int | None = None) -> date | None:
    """The day a sentence names, from the day it was said: today / tomorrow / mañana / a
    weekday (the next one on or after that day; "next Monday" said on a Monday is a week on).
    With several ("we couldn't today, but tomorrow 7:30-10"), the one nearest `near` (where
    the time is) wins, else the last one."""
    found = list(DAY_WORD.finditer(text or ""))
    if not found:
        return None
    m = min(found, key=lambda x: abs(x.start() - near)) if near is not None else found[-1]
    w = m.group(1).lower()
    base = c.local(said_at).date()
    if w in ("today", "tonight", "this afternoon", "this morning", "hoy"):
        return base
    if w in ("tomorrow", "mañana"):
        return base + timedelta(days=1)
    if w == "pasado mañana":
        return base + timedelta(days=2)
    nxt = w.startswith(("next", "el próximo", "el proximo"))
    wd = WEEKDAY.get(w.split()[-1])
    if wd is None:
        return None
    ahead = (wd - base.weekday()) % 7
    if nxt and ahead == 0:
        ahead = 7
    return base + timedelta(days=ahead)


def window_at(text: str) -> int | None:
    m = WINDOW.search(text or "")
    return m.start() if m else None


# "We'll give you a heads-up WHEN he is on his way", "after the repair is done": the event has
# not happened. Looked for in the words just before the match.
NOT_YET = re.compile(r"\b(when|once|as soon as|before|after|until|will|would|cuando|antes|"
                     r"después|despues|heads[- ]?up|notify|let you know|avisar\w*)\b"
                     r"[^.!?]{0,40}$", re.IGNORECASE)


def _happened(src: str, m: re.Match) -> bool:
    return not NOT_YET.search(src[max(0, m.start() - 60):m.start()])


# "needs to call back or text to confirm", "asked her to confirm", "will confirm the time",
# "pending confirmation": a confirmation still to come.
CONFIRM_NOT_YET = re.compile(r"\b(to|will|would|needs?|pending|awaiting|waiting|asked|ask|"
                             r"until|wants?|should|must|can)\s+(\w+\s+){0,2}$", re.IGNORECASE)


def _confirm_happened(src: str, m: re.Match) -> bool:
    if re.match(r"confirmation", m.group(0), re.IGNORECASE):
        return False
    # "before her scheduled appointment" describes it; "Owen scheduled a visit" did it.
    if re.match(r"(scheduled|booked|set up)\b", m.group(0), re.IGNORECASE) and re.search(
            r"\b(her|his|their|the|your|our|my|a|an|already|previously)\s+$",
            src[max(0, m.start() - 20):m.start()], re.IGNORECASE):
        return False
    return not CONFIRM_NOT_YET.search(src[max(0, m.start() - 40):m.start()])


def _quote(text: str, m: re.Match | None) -> str:
    text = " ".join((text or "").split())
    if not m or len(text) <= QUOTE:
        return text[:QUOTE]
    start = max(0, m.start() - QUOTE // 3)
    return ("…" if start else "") + text[start:start + QUOTE] + "…"


def signals(evs: list[dict]) -> list[Signal]:
    """Every signal in a customer's calls and texts (oldest first in, oldest first out). A
    call is read from its summary (Quo's or Zuper's), else the end of its transcript, where
    the agreement is."""
    out: list[Signal] = []
    prev_us: dict | None = None
    for e in evs:
        who, how, line = e["who"], e["kind"], e["source"]
        text = e["text"] or ""
        if how == "call":
            text = summary_of(text) or ("" if e["source"] != "Zuper Connect" else text) or \
                (e.get("transcript") or "")[-900:]
        if not text.strip():
            continue

        def add(kind: str, m: re.Match | None, src: str, e=e, who=who, line=line, how=how,
                **kw) -> None:
            out.append(Signal(kind, e["at"], who, line, how, _quote(src, m), **kw))
        win = window_in(text)
        day = day_in(text, e["at"], window_at(text))
        dm = DAY_WORD.search(text)
        if day and dm and NEGATED.search(text[max(0, dm.start() - 60):dm.start()]):
            day = None                         # "cannot be present tomorrow"
        # A day (and maybe a time) somebody PROPOSED or mentioned: in our text, in a call, in
        # theirs. Not an agreement by itself — "confirmed" is.
        if day and (how == "call" or who == "us" or win):
            add("promise", dm, text, day=day, window=win)
        if who == "customer" and how == "text":
            lead = GREETING.sub("", text, count=1)
            if CANCEL.search(text):
                add("cancel", CANCEL.search(text), text)
            elif CONFIRM_WORDS.match(lead) and not ASKS.search(text) and prev_us is not None \
                    and e["at"] - prev_us["at"] <= timedelta(hours=24):
                # "Yes" / "Confirmed" / "Perfecto" answering OUR proposal or reminder: it is
                # for the day the customer names ("Yes, today works"), else the day and time we
                # proposed (a time with no day: the day it was said).
                pt = prev_us["text"] or ""
                pw = win or window_in(pt)
                pd = day or day_in(pt, prev_us["at"], window_at(pt)) or (
                    c.local(prev_us["at"]).date() if pw else None)
                if pd or pw or re.search(r"remind|confirm|appointment|visit|cita|visita", pt,
                                         re.IGNORECASE):
                    add("confirmed", CONFIRM_WORDS.search(text), text, day=pd, window=pw)
        if how == "call":
            # Only a confirmation that HAPPENED: never "needs to call back to confirm the
            # appointment", "pending confirmation" (live 2026-10-13, #703).
            m = next((x for x in CONFIRM_IN_CALL.finditer(text) if _confirm_happened(text, x)),
                     None)
            talked = (e.get("seconds") or 0) >= 30
            if not m and talked and day:
                # "Owen confirmed that they will fix the leak today between 2 and 4": a
                # conversation that ended on a day.
                m = next((x for x in re.finditer(r"\bconfirm(ed|s)?\b[^.]{0,120}", text,
                                                 re.IGNORECASE)
                          if _confirm_happened(text, x) and DAY_WORD.search(x.group(0))), None)
            cancel = CANCEL_IN_CALL.search(text)
            if cancel:
                add("cancel", cancel, text)    # never also "confirmed" in the same call
            elif m:
                add("confirmed", m, text, day=day, window=win)
        for kind, rx in (("on_the_way", ON_THE_WAY), ("done", DONE), ("ahs_sent", AHS_SENT)):
            if who == "customer" and how == "text":
                continue   # the customer saying it is not us doing it
            m = rx.search(text)
            if m and _happened(text, m):
                add(kind, m, text)
        if who == "us" and how == "text":
            prev_us = e
    return out


# ---------------------------------------------------------------------------------- brief

@dataclass
class Brief:
    job: DispatchJob
    evs: list[dict]
    sigs: list[Signal]
    field_today: list[DispatchActivity] = field(default_factory=list)
    last_schedule_change: datetime | None = None
    availability: DispatchAvailability | None = None

    @property
    def test(self) -> bool:
        j = self.job
        return bool(TEST.search(" ".join(x or "" for x in (j.customer_name, j.title,
                                                              j.address)))) \
            or (j.board or "") in c.IGNORED_BOARDS or "TEST" in (j.board or "")

    def newest(self, *kinds: str) -> Signal | None:
        return next((s for s in reversed(self.sigs) if s.kind in kinds), None)

    def unanswered(self, now: datetime) -> dict | None:
        """The customer's last message, when it is the last word, is not a plain thank-you,
        and nobody answered for UNANSWERED_MINUTES."""
        texts = [e for e in self.evs if e["kind"] == "text" or e["who"] == "customer"]
        if not texts:
            return None
        last = texts[-1]
        if last["who"] != "customer" or last["kind"] != "text":
            return None
        body = last["text"] or ""
        if is_ack(body) or now - last["at"] < timedelta(minutes=UNANSWERED_MINUTES):
            return None
        if not (QUESTION.search(body) or ASKS.search(body)):
            return None                        # "Perfect 👌", "Yes - this works. Many thanks."
        if now - last["at"] > timedelta(days=5):
            return None
        return {"at": _when(last["at"]), "said": _quote(body, None),
                "question": bool(QUESTION.search(body))}

    def out(self, now: datetime, *, full: bool = False) -> dict:
        j = self.job
        fields = {k: str(v)[:80] for k, v in (j.fields or {}).items()
                  if v not in (None, "", [], {}) and k != "Technician" and KEY_FIELDS.search(k)}
        d = {"job": "#%s" % j.job_number, "customer": j.customer_name, "board": j.board,
             "stage": j.status, "stage_since": _when(j.status_since), "kind": visit_kind(j),
             "city": j.city,
             "visit": visit_text(j),
             "assigned": ", ".join(j.assigned or []) or None, "technician_field": j.technician,
             "fields": dict(list(fields.items())[:MAX_FIELDS]) or None}
        if self.availability and (self.availability.summary or self.availability.limits):
            d["availability"] = self.availability.summary
        talks = [e for e in self.evs if e["kind"] == "text" or (e.get("seconds") or 0) >= 30
                 or e["source"] == "Zuper Connect"]
        if talks:
            e = talks[-1]
            d["last_contact"] = "%s %s %s (%s): %s" % (
                _when(e["at"]), e["kind"], "from customer" if e["who"] == "customer" else "by us",
                e["source"], _quote(summary_of(e["text"]) or e["text"] or "", None))
        mine = [e for e in self.evs if e["who"] == "customer" and e["kind"] == "text"]
        if mine:
            d["last_text_from_customer"] = "%s: %s" % (_when(mine[-1]["at"]),
                                                       _quote(mine[-1]["text"], None))
        ua = self.unanswered(now)
        if ua:
            d["waiting_for_our_answer"] = ua
        sigs = self.sigs if full else self.sigs[-6:]
        if sigs:
            d["signals_newest_first"] = [s.out() for s in reversed(sigs)]
        if self.field_today or (j.photos_today or 0):
            d["from_the_field_today"] = "%d picture(s); %s" % (
                j.photos_today or 0, "; ".join(
                    "%s %s" % (_when(a.at).split(" ", 2)[-1], (a.message or "")[:60])
                    for a in self.field_today[-4:]) or "no app actions")
        return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def visit_text(j: DispatchJob) -> str | None:
    start, end = c.aware(j.scheduled_start), c.aware(j.scheduled_end)
    if not start:
        return None
    return "%s %s–%s" % (c.local(start).strftime("%a %m/%d"), _hm(_mins(start)),  # noqa: RUF001
                         _hm(_mins(end) if end else _mins(start)))


def _at(day: date, minutes: int) -> str:
    """A New York day and minute as an ISO time with its offset (what /book takes)."""
    return datetime(day.year, day.month, day.day, minutes // 60, minutes % 60,
                    tzinfo=c.TZ).isoformat()


def _mins(dt: datetime) -> int:
    t = c.local(dt)
    return t.hour * 60 + t.minute


def _field_activity(db: Session, since: datetime) -> dict[str, list[DispatchActivity]]:
    out: dict[str, list[DispatchActivity]] = {}
    for a in db.scalars(select(DispatchActivity).where(
            DispatchActivity.at >= since, DispatchActivity.job_uid.is_not(None))
            .order_by(DispatchActivity.at)):
        if (a.via or "").startswith("zuper_v3"):
            out.setdefault(a.job_uid, []).append(a)
    return out


def _schedule_changes(db: Session, since: datetime) -> dict[str, datetime]:
    """When each job's visit was last set or moved in Zuper (any source, our scripts too)."""
    out: dict[str, datetime] = {}
    for a in db.scalars(select(DispatchActivity).where(
            DispatchActivity.at >= since, DispatchActivity.job_uid.is_not(None))):
        if re.search(r"\b(re)?scheduled job\b", a.message or "", re.IGNORECASE):
            at = c.aware(a.at)
            if at and (a.job_uid not in out or at > out[a.job_uid]):
                out[a.job_uid] = at
    return out


def load(db: Session, now: datetime, hidden: set[str] | None = None, *,
         jobs: list[DispatchJob] | None = None) -> list[Brief]:
    """A brief for every open job (or the ones given), reading the database once."""
    hidden = hidden or set()
    since = now - timedelta(days=COMMS_DAYS)
    # Never past `now`: a check of an earlier moment reads what was known then.
    evs = [e for e in lookups._events(db, since) if e["at"] and e["at"] <= now]
    reach = lookups.customer_phones(db, now, evs)
    by_phone: dict[str, list[dict]] = {}
    for e in evs:
        if e["phone"]:
            by_phone.setdefault(e["phone"], []).append(e)
    today0 = c.local(now).replace(hour=0, minute=0, second=0, microsecond=0)
    field_today = _field_activity(db, today0)
    moved = _schedule_changes(db, now - timedelta(days=30))
    avail = {a.job_uid: a for a in db.scalars(select(DispatchAvailability))}
    rows = jobs if jobs is not None else list(db.scalars(select(DispatchJob).where(
        DispatchJob.is_open.is_(True))))
    out = []
    for j in rows:
        if j.board and j.board in hidden:
            continue
        mine = sorted((e for p in reach.get(j.job_uid, set(j.phones or []))
                       for e in by_phone.get(p, [])), key=lambda e: e["at"])
        out.append(Brief(j, mine, signals(mine), field_today.get(j.job_uid, []),
                         moved.get(j.job_uid), avail.get(j.job_uid)))
    return out


# ---------------------------------------------------------------------------------- check

# What a stage means for a visit, by the stage's own name (config) or Zuper's status type.
IN_VISIT_TYPES = {"ON_MY_WAY", "STARTED"}
AFTER_VISIT = set(c.AFTER_INSPECTION_STAGES) | set(c.WAITING_ON_AHS) | set(
    c.AFTER_REPAIR_STAGES) | {"AHS Approved", "Proposal Made", "Invoice Submitted to AHS",
                              "Awaiting AHS Payment", "Paid", "Review Requested",
                              "Review Received", "Estimate Sent"}
INVOICED = {"Invoice Submitted to AHS", "Awaiting AHS Payment", "Paid", "Review Requested",
            "Review Received", "Invoice"}
# Stages named for ONE technician: never offered for anyone else's job (2026-10-13).
NAMED_STAGES = {c.INSPECTION_BOARD: ("Antonio On the Way",)}
# The one stage a fix may name, per board and event; anything else is "do it in Zuper".
ON_THE_WAY_STAGE = {c.REPAIR_BOARD: "On the Way to Repair"}
DONE_STAGE = {c.INSPECTION_BOARD: "Inspection Completed", c.REPAIR_BOARD: "Repair Complete"}
SUBMITTED_STAGE = {c.INSPECTION_BOARD: "Awaiting AHS Decision"}
RESCHEDULE_STAGE = {c.INSPECTION_BOARD: "Reschedule Required",
                    c.REPAIR_BOARD: "Reschedule Required"}
SEVERITY = {"high": 0, "medium": 1, "low": 2}


def _after(j: DispatchJob) -> bool:
    return (j.status or "") in AFTER_VISIT or (j.status_type or "") in {
        "COMPLETED", "INVOICING", "PAID"}


def _in_visit(j: DispatchJob) -> bool:
    return (j.status_type or "") in IN_VISIT_TYPES


def _finding(b: Brief, kind: str, severity: str, what: str, do: str,
             evidence: list[Signal | dict] | None = None, fix: dict | None = None) -> dict:
    j = b.job
    start = c.aware(j.scheduled_start)
    return {"kind": kind, "severity": severity, "job_number": j.job_number,
            "job_uid": j.job_uid, "customer": j.customer_name, "board": j.board,
            "stage": j.status, "visit_day": c.local(start).date().isoformat() if start else None,
            "visit": visit_text(j),
            "assigned": ", ".join(j.assigned or []) or None,
            # What Zuper shows now, ready to copy: the AI writes it from here, never from
            # another row (live 2026-10-13: #711's stage was written on #695's row).
            "zuper_now": " · ".join(x for x in (
                "%s / %s" % (j.board, j.status) if j.board else j.status, visit_text(j) or
                "no visit", ", ".join(j.assigned or []) or "nobody assigned") if x),
            "what": what, "do": do,
            "evidence": [x.out() if isinstance(x, Signal) else x for x in evidence or []],
            "fix": fix}


def check(db: Session, now: datetime, hidden: set[str] | None = None, *,
          date_from: date | None = None, date_to: date | None = None,
          briefs: list[Brief] | None = None) -> dict:
    """The Zuper check for a range of days (default today and tomorrow)."""
    today = c.local(now).date()
    d0 = date_from or today
    d1 = date_to or (today + timedelta(days=1))
    every = briefs if briefs is not None else load(db, now, hidden)
    bs = [b for b in every if not b.test]
    findings: list[dict] = []
    in_range = []
    for b in bs:
        s = c.aware(b.job.scheduled_start)
        if s and d0 <= c.local(s).date() <= d1:
            in_range.append(b)

    # A customer with several jobs (Janeth Palacio has four on one number) shares one
    # conversation: a day agreed for one of them belongs to the job with a visit that day.
    by_phone: dict[str, list[Brief]] = {}
    for b in bs:
        for ph in b.job.phones or []:
            by_phone.setdefault(ph, []).append(b)

    def sibling_has(b: Brief, day: date) -> bool:
        return any(o is not b and c.aware(o.job.scheduled_start) and
                   c.local(c.aware(o.job.scheduled_start)).date() == day
                   for ph in b.job.phones or [] for o in by_phone.get(ph, []))

    # 1. Double-booked: the same person on two visits that START together (arrival windows
    # overlap by design: 7:30-10 then 9-11 is one route, not a clash).
    by_tech: dict[str, list[Brief]] = {}
    for b in in_range:
        for t in b.job.assigned or []:
            by_tech.setdefault(t, []).append(b)
    for tech, lst in by_tech.items():
        lst.sort(key=lambda b: c.aware(b.job.scheduled_start))
        for i, a in enumerate(lst):
            for b in lst[i + 1:]:
                if set(a.job.phones or []) & set(b.job.phones or []):
                    continue            # one customer's two jobs at once: one visit
                gap = c.aware(b.job.scheduled_start) - c.aware(a.job.scheduled_start)
                if gap < timedelta(minutes=60):
                    for x in (a, b):
                        findings.append(_finding(
                            x, "double_booked", "high",
                            "%s is booked on #%s and #%s at the same time (%s)." % (
                                tech, a.job.job_number, b.job.job_number,
                                visit_text(x.job)),
                            "Call one of the two customers and move that visit.",
                            [s for s in (x.newest("confirmed"),) if s]))

    for b in bs:
        j = b.job
        start, end = c.aware(j.scheduled_start), c.aware(j.scheduled_end)
        vday = c.local(start).date() if start else None
        moved = b.last_schedule_change
        board = j.board or ""

        # 2. A day (and time) the customer AGREED to (a "yes" to our proposal, a call where
        # they accepted) that Zuper does not show. Only when the agreement is NEWER than
        # Zuper's last change of the visit, still ahead, and not a sibling job's visit. A
        # proposal nobody answered is not an agreement (live 2026-10-13: "Owen offered a slot").
        p = b.newest("confirmed")
        if p and p.day and p.day >= today and p.day <= d1 + timedelta(days=7) and \
                now - p.at <= timedelta(days=PROMISE_DAYS) and (moved is None or p.at > moved) \
                and not _after(j) and not sibling_has(b, p.day):
            same_day = vday == p.day
            same_time = p.window is None or (start and abs(_mins(start) - p.window[0]) < 30)
            if not (same_day and same_time):
                fix = None
                tech = (j.assigned or [None])[0] if len(j.assigned or []) == 1 else j.technician
                if p.window and tech:
                    fix = {"action": "booking", "day": p.day.isoformat(),
                           "start": _at(p.day, p.window[0]), "end": _at(p.day, p.window[1]),
                           "label": "%s %s" % (p.day.strftime("%a %m/%d"), _win(p.window)),
                           "technician": tech}
                findings.append(_finding(
                    b, "agreed_other_time", "high" if p.day <= today + timedelta(days=1)
                    else "medium",
                    "Agreed %s%s, but Zuper shows %s." % (
                        p.day.strftime("%a %m/%d"), " " + _win(p.window) if p.window else "",
                        visit_text(j) or "no visit"),
                    "Set the visit in Zuper to what was agreed%s." % (
                        "" if p.window else " (no time was said: ask the customer)"),
                    [p], fix))

        # 3. The customer asked to cancel or move after the last agreement — also when the
        # visit's day has passed and the job still sits in a visit stage (live 2026-10-13:
        # #287 cancelled on Sunday's call, still "On the Way to Repair" on Tuesday).
        cx = b.newest("cancel")
        cancelled = False
        if cx and now - cx.at <= timedelta(days=PROMISE_DAYS) and (p is None or cx.at > p.at) \
                and (moved is None or cx.at > moved) and not _after(j) and \
                lookups.booked_stage(j.status):
            cancelled = True
            st = RESCHEDULE_STAGE.get(board)
            findings.append(_finding(
                b, "customer_cancelled", "high",
                "The customer asked to cancel or move the visit after it was agreed.",
                "Call them and rebook, or move the job to Reschedule Required.", [cx],
                {"action": "stage", "to": st} if st else None))

        if vday is None or not (d0 <= vday <= d1):
            # 4. Report sent to AHS, column behind (any day, not only visits in range).
            sent = b.newest("ahs_sent")
            if sent and now - sent.at <= timedelta(days=7) and board == c.INSPECTION_BOARD \
                    and j.status not in c.WAITING_ON_AHS and j.status in (
                        set(c.AFTER_INSPECTION_STAGES) | {"Scheduled"} | set(c.NEEDS_DATE)):
                findings.append(_finding(
                    b, "ahs_sent_not_moved", "medium",
                    "We told the customer the report went to AHS; Zuper still shows “%s”."
                    % j.status, "Move it to Awaiting AHS Decision.", [sent],
                    {"action": "stage", "to": SUBMITTED_STAGE[board]}))
            ua = b.unanswered(now)
            if ua:
                findings.append(_finding(
                    b, "unanswered", "medium", "The customer asked something and nobody "
                    "answered: “%s”" % ua["said"], "Answer the customer.", [ua]))
            if start is None and lookups.booked_stage(j.status) and j.status != "Call Back":
                findings.append(_finding(
                    b, "booked_no_time", "low", "Stage “%s” but no visit time in Zuper."
                    % j.status, "Give the visit a time, or move it to Reschedule Required."))
            since = c.aware(j.status_since)
            if _in_visit(j) and since and c.local(since).date() < today and \
                    now - since <= timedelta(days=14) and not cancelled:
                findings.append(_finding(
                    b, "stale_on_the_way", "low", "Stage “%s” since %s and no visit today."
                    % (j.status, _when(j.status_since)),
                    "Put it back to the stage it is really in."))
            continue

        # ---- visits in the range
        # A visit still on a job that is already invoiced or paid (live 2026-10-13: #410's
        # Wednesday visit was the return visit booked on #720; #691 paid, still on the route).
        if (j.status or "") in INVOICED or (j.status_type or "") in {"INVOICING", "PAID"}:
            findings.append(_finding(
                b, "visit_on_closed_job", "medium",
                "A visit is still booked on this job, which is at “%s”." % j.status,
                "Take the visit off this job (if it is a return visit, it belongs on the job "
                "the visit is for)."))
            continue
        today_visit = vday == today
        # Confirmed for THIS visit: a "yes" naming its day, or one with no day said after the
        # visit was last set and within two days of it.
        conf = [s for s in b.sigs if s.kind == "confirmed" and now - s.at <= timedelta(
            days=PROMISE_DAYS) and (s.day == vday or (s.day is None and (
                moved is None or s.at > moved) and start - s.at <= timedelta(days=2)))]
        if not conf and not _after(j) and not _in_visit(j) and vday <= today + timedelta(days=1):
            findings.append(_finding(
                b, "not_confirmed", "medium",
                "No call or text shows the customer agreeing to %s." % (
                    "today" if today_visit else vday.strftime("%a %m/%d")),
                "Call the customer to confirm before the technician goes.",
                [s for s in (b.newest("promise", "confirmed"),) if s]))
            conf = None
        otw = b.newest("on_the_way")
        if today_visit and otw and c.local(otw.at).date() == today and not _in_visit(j) \
                and not _after(j):
            st = ON_THE_WAY_STAGE.get(board)
            named = next((s for s in NAMED_STAGES.get(board, ()) if not any(
                s.split()[0] in (a or "") for a in j.assigned or [])), None)
            who = ", ".join(j.assigned or []) or "nobody"
            findings.append(_finding(
                b, "on_the_way_not_moved", "medium",
                "We told the customer the technician is on the way; Zuper still shows “%s”."
                % j.status, ("Move it to the on-the-way stage (the technician does it in the "
                             "app)." if not named else
                             "Move it to the on-the-way stage. This board's is named “%s” and %s "
                             "is assigned: ask the office which stage to use; never “%s” for "
                             "%s's job." % (named, who, named, who)),
                [otw], {"action": "stage", "to": st} if st else None))
        done = b.newest("done")
        field_done = (j.photos_today or 0) > 0 or bool(b.field_today)
        if today_visit and end and now > end + timedelta(minutes=30) and not _after(j):
            if done and c.local(done.at).date() == today:
                st = DONE_STAGE.get(board)
                findings.append(_finding(
                    b, "done_not_moved", "high",
                    "The visit is done (%s) but Zuper still shows “%s”." % (
                        done.quote[:60], j.status),
                    "Move it to %s." % (st or "the done stage"), [done],
                    {"action": "stage", "to": st} if st else None))
            elif not field_done:
                findings.append(_finding(
                    b, "no_field_update", "high",
                    "The visit was until %s; nothing from the field in Zuper today (no "
                    "picture, note or stage)." % _hm(_mins(end)),
                    "Ask %s whether the visit happened, and have them update the job."
                    % (", ".join(j.assigned or []) or "the technician")))
        sent = b.newest("ahs_sent")
        if sent and board == c.INSPECTION_BOARD and j.status not in c.WAITING_ON_AHS and \
                now - sent.at <= timedelta(days=7):
            findings.append(_finding(
                b, "ahs_sent_not_moved", "medium",
                "We told the customer the report went to AHS; Zuper still shows “%s”."
                % j.status, "Move it to Awaiting AHS Decision.", [sent],
                {"action": "stage", "to": SUBMITTED_STAGE[board]}))
        if j.technician and j.assigned and j.technician not in j.assigned:
            findings.append(_finding(
                b, "technician_field", "low",
                "Technician field says %s; %s is assigned (the route follows who is assigned)."
                % (j.technician, ", ".join(j.assigned)),
                "Set the Technician field to %s." % j.assigned[0], None,
                {"action": "field", "field": "Technician", "to": j.assigned[0]}
                if len(j.assigned) == 1 else None))
        ua = b.unanswered(now)
        if ua:
            findings.append(_finding(
                b, "unanswered", "medium", "The customer's last text has no answer: “%s”"
                % ua["said"], "Answer the customer.", [ua]))

    findings.sort(key=lambda f: (f["visit_day"] or "9", SEVERITY[f["severity"]],
                                 f["job_number"] or ""))
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    return {"from": d0.isoformat(), "to": d1.isoformat(), "checked_at": _when(now),
            "visits_checked": len(in_range), "jobs_checked": len(bs),
            "findings": findings, "counts": counts, "skipped_test_jobs": len(every) - len(bs)}
