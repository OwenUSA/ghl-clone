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
# The customer pausing the work (live 2026-10-12, #727: "put a hold on the roofing service",
# "wait for insurance company inspection results before proceeding").
# Only a hold on the WORK: "Luis was placed on hold" is the phone line (live 2026-10-14,
# #299 Kalach read as "on hold").
HOLD = re.compile(r"\b(put (a )?hold on (the |this |her |his |their |our )?(\w+ ){0,2}"
                  r"(roof\w*|repair|work|job|service|visit|appointment|project)|hold off on|"
                  r"wait(ing)? for (the |their |her |his )?(insurance|adjuster)\w*|"
                  r"pausar (el|la) (trabajo|reparaci\w+))", re.IGNORECASE)
# "Mary cannot be present tomorrow": a day named to say it does NOT work.
NEGATED = re.compile(r"\b(can'?t|cannot|won'?t|will not|not be|not available|unable|"
                     r"no (puede|puedo|podr\w*|va a estar|voy a estar|estar\w*))\b[^.!?]{0,50}$",
                     re.IGNORECASE)
GREETING = re.compile(r"^\W*(hi|hello|hey|hola|good (morning|afternoon|evening)|buen[oa]s? "
                      r"(d[ií]as|tardes|noches)|mr\.? \w+|ms\.? \w+)\W*", re.IGNORECASE)
# A text that asks for something is not a "yes" to our proposal, even if it starts with "Ok".
ASKS = re.compile(r"\?|\b(call me|ll[aá]m\w*|whats ?app|text me|can you|could you|"
                  r"please|por favor|c favor)\b", re.IGNORECASE)
ON_THE_WAY = re.compile(r"\b(on (his|her|their|the|our) way|en camino|"
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
# The customer offering several days: "available Friday, Monday, or possibly late Thursday".
OPTIONS = re.compile(r"\b(available|either|or|options?|can (do|make)|works?|prefer\w*|"
                     r"disponible|puede)\b", re.IGNORECASE)
# In a call summary, the CUSTOMER offering days ("Frederick is out of town but available
# Friday, Monday…", "the caller requested … either Wednesday or …"), never "Owen offered".
OFFERED = re.compile(r"\b(?!owen\b|luis\b|antonio\b|marian\w*\b)[A-Z]\w+ (is|was|would be|will "
                     r"be|are) (out of town but )?(available|free)|"
                     r"\b(customer|caller|client|she|he|they) (is |are |would be )?"
                     r"(available|requested|prefers?|can do|asked)",
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
    days: list | None = None  # options: the days the customer offered
    window: tuple[int, int] | None = None   # minutes after midnight

    def out(self) -> dict:
        d = {"signal": self.kind, "at": _when(self.at), "from": self.who, "line": self.line,
             "how": self.how, "said": self.quote}
        if self.day:
            d["for"] = self.day.strftime("%a %m/%d") + (" " + _win(self.window)
                                                        if self.window else "")
        if self.days:
            d["days_offered"] = ", ".join(x.strftime("%a %m/%d") for x in self.days)
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
    # "call back with confirmed appointment date and time" (a to-do, live 2026-10-13, #687)
    if re.match(r"confirmed\b", m.group(0), re.IGNORECASE) and re.search(
            r"\b(with|a|an|the|any|no|for|of|her|his|their|your|our)\s+$",
            src[max(0, m.start() - 12):m.start()], re.IGNORECASE):
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
            hold = HOLD.search(text)
            if hold and not cancel:
                add("hold", hold, text)        # "put a hold … wait for the insurance"
            if cancel:
                add("cancel", cancel, text)    # never also "confirmed" in the same call
            elif m and not hold:
                add("confirmed", m, text, day=day, window=win)
        for kind, rx in (("on_the_way", ON_THE_WAY), ("done", DONE), ("ahs_sent", AHS_SENT)):
            if who == "customer" and how == "text":
                continue   # the customer saying it is not us doing it
            m = rx.search(text)
            if m and _happened(text, m):
                add(kind, m, text)
        if (how == "text" and who == "customer") or (how == "call" and OFFERED.search(text)):
            days = sorted({d for d in (day_in(x.group(0), e["at"]) for x in
                                       DAY_WORD.finditer(text)) if d})
            if len(days) >= 2 and OPTIONS.search(text) and not any(
                    s.kind == "confirmed" and s.at == e["at"] for s in out):
                add("options", OPTIONS.search(text), text, days=days)
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
         jobs: list[DispatchJob] | None = None, days: int = COMMS_DAYS) -> list[Brief]:
    """A brief for every open job (or the ones given), reading the database once."""
    hidden = hidden or set()
    since = now - timedelta(days=days)
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

        # 2b. The customer offered several days and none is booked: call to pick one.
        op = b.newest("options")
        ahead = [d for d in (op.days if op else []) if d >= today]
        if op and len(ahead) >= 2 and now - op.at <= timedelta(days=PROMISE_DAYS) and \
                (p is None or op.at > p.at) and (moved is None or op.at > moved) and \
                not _after(j) and vday not in ahead and \
                not any(sibling_has(b, d) for d in ahead):
            findings.append(_finding(
                b, "customer_options", "medium",
                "The customer offered %s; nothing is booked on those days." % ", ".join(
                    d.strftime("%a %m/%d") for d in op.days if d >= today),
                "Call them to pick one of those days and a time, then set the visit.", [op]))

        # 3. The customer asked to cancel or move after the last agreement — also when the
        # visit's day has passed and the job still sits in a visit stage (live 2026-10-13:
        # #287 cancelled on Sunday's call, still "On the Way to Repair" on Tuesday).
        cx = b.newest("cancel")
        cancelled = False
        if cx and now - cx.at <= timedelta(days=PROMISE_DAYS) and (p is None or cx.at > p.at) \
                and (op is None or cx.at > op.at) \
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
                noted = c.aware(j.last_note_at)
                done_st = DONE_STAGE.get(board)
                if noted and noted >= since and done_st:
                    # Notes written after it went in progress (live 2026-10-13: Owen's notes on
                    # #736 at 4:31 / 4:49 PM, "send the report to AHS"): the visit looks done.
                    findings.append(_finding(
                        b, "stale_on_the_way", "low",
                        "Stage “%s” since %s; notes were added after it (last %s), so the "
                        "visit looks done." % (j.status, _when(j.status_since), _when(noted)),
                        "Check with %s, then move it to %s." % (
                            ", ".join(j.assigned or []) or "the technician", done_st)))
                else:
                    findings.append(_finding(
                        b, "stale_on_the_way", "low", "Stage “%s” since %s and no visit today."
                        % (j.status, _when(j.status_since)),
                        "Ask %s where the job really is and set that stage." % (
                            ", ".join(j.assigned or []) or "the technician")))
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
        # Under way without the app: moved into an in-progress stage today (by anyone), or a
        # call about the work after the visit began (live 2026-10-13: #711, truck trouble,
        # the office moved it to "Repair In Process", calls about the plywood and a deposit).
        since = c.aware(j.status_since)
        # Our own "he's on his way" text is not the work happening; an answered call or the
        # customer's text after the visit began is.
        talk_today = [e for e in b.evs if start and e["at"] >= start and (
            (e["kind"] == "call" and (e.get("seconds") or 0) >= 30) or
            (e["kind"] == "text" and e["who"] == "customer"))]
        active = (_in_visit(j) and since and c.local(since).date() == today) or bool(talk_today)
        if today_visit and end and now > end + timedelta(minutes=30) and not _after(j):
            if done and c.local(done.at).date() == today:
                st = DONE_STAGE.get(board)
                findings.append(_finding(
                    b, "done_not_moved", "high",
                    "The visit is done (%s) but Zuper still shows “%s”." % (
                        done.quote[:60], j.status),
                    "Move it to %s." % (st or "the done stage"), [done],
                    {"action": "stage", "to": st} if st else None))
            elif active and _in_visit(j):
                last = talk_today[-1] if talk_today else None
                findings.append(_finding(
                    b, "running_late", "low",
                    "Still “%s” after its %s window%s." % (
                        j.status, _hm(_mins(end)), "; the last call or text about it was %s"
                        % _when(last["at"]) if last else ""),
                    "When %s finishes, have the job moved on (Repair Complete / Inspection "
                    "Completed)." % (", ".join(j.assigned or []) or "the technician"),
                    [{"at": _when(last["at"]), "line": last["source"], "how": last["kind"],
                      "from": last["who"], "said": _quote(summary_of(last["text"] or "") or
                                                           last["text"] or "", None)}]
                    if last else None))
            elif not field_done and not active:
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


# --------------------------------------------------------------- who still needs a booking

# Every job that needs a visit (the planner's own list, scheduling.waiting_jobs), judged by
# script from 60 days of calls and texts (2026-10-13): the AI's answer to "what jobs are missing
# to book" put a return visit on the wrong one of a customer's three jobs, said "book" for
# visits that had happened, and called leads imported in September "nobody has called" because
# its summaries read 14 days. Each job now comes with ONE verdict; the model only reports them.
BOOK_DAYS = 60
STALE_DAYS = 30
TO_BOOK = (
    ("book", "Agreed with the customer, not in Zuper — book it"),
    ("on_other_job", "Already booked on the customer's other job — nothing to book here"),
    ("visit_happened", "The visit looks like it happened — update the stage, don't rebook"),
    ("confirm_time", "The customer offered days — call to pick one and a time"),
    ("new_date", "The customer asked to move or can't make it — call for a new date"),
    ("on_hold", "The customer put it on hold — do not book"),
    ("keep_calling", "We reached out, the customer has not answered — keep calling"),
    ("call_and_book", "Call the customer and book"),
    ("stale", "No call or text in %d days — check it is still a job" % STALE_DAYS),
)


def to_book(db: Session, now: datetime, hidden: set[str] | None = None) -> dict:
    """Who still needs a booking, each with its verdict and evidence. Reads only."""
    from . import scheduling
    waiting = {pj.uid: pj for pj in scheduling.waiting_jobs(
        db, now, hidden=hidden or set(), boards="all", use_saved=False)}
    every = load(db, now, hidden, days=BOOK_DAYS)
    by_uid = {b.job.job_uid: b for b in every}
    by_phone: dict[str, list[Brief]] = {}
    for b in every:
        for ph in b.job.phones or []:
            by_phone.setdefault(ph, []).append(b)
    today = c.local(now).date()
    groups: dict[str, list[dict]] = {k: [] for k, _ in TO_BOOK}
    skipped_tests = 0
    for uid, pj in waiting.items():
        b = by_uid.get(uid)
        if b is None:
            continue
        if b.test:
            skipped_tests += 1
            continue
        j = b.job
        start = c.aware(j.scheduled_start)
        vday = c.local(start).date() if start else None
        sibs = {o.job.job_uid: o for ph in j.phones or [] for o in by_phone.get(ph, [])
                if o is not b and o.job.is_open}
        upcoming = next((o for o in sibs.values() if c.aware(o.job.scheduled_start) and c.local(
            c.aware(o.job.scheduled_start)).date() >= today and lookups.booked_stage(
                o.job.status)), None)
        recent = b.sigs
        # A signal on a day a sibling job had its visit belongs to that job (#323 / #721).
        sib_days = {c.local(c.aware(o.job.scheduled_start)).date() for o in sibs.values()
                    if c.aware(o.job.scheduled_start)}
        agreed = [s for s in recent if s.kind == "confirmed" and s.day and s.day >= today]
        opts = next((s for s in reversed(recent) if s.kind == "options"
                     and any(d >= today for d in s.days or [])), None)
        # A cancel from before the job entered its stage is history (live: #677's 9/23).
        stop = next((s for s in reversed(recent) if s.kind in ("cancel", "hold") and (
            c.aware(j.status_since) is None or s.at >= c.aware(j.status_since) - timedelta(
                hours=12)) and c.local(s.at).date() not in sib_days), None)
        newest_yes = max([x.at for x in agreed] + ([opts.at] if opts else []), default=None)
        # The visit happened: on the way / arrived / done / pictures on or after the visit day
        # AND after the job entered its stage — for a job waiting on its NEXT visit (AHS
        # Approved, Call Back) the inspection that happened before is not it (live: #677).
        entered = c.aware(j.status_since)
        # "On my way" a little before the technician moves the stage still counts.
        after = (entered - timedelta(hours=12)) if entered else None
        happened = None
        if vday and vday < today:
            # A customer with several jobs (Janeth Palacio has six on one number): only a
            # signal ON this job's visit day is this job's.
            happened = next((s for s in reversed(recent) if s.kind in ("on_the_way", "done")
                             and (c.local(s.at).date() == vday if sibs else
                                  c.local(s.at).date() >= vday)
                             and (after is None or s.at >= after)
                             and c.local(s.at).date() not in sib_days), None)
            photo = c.aware(j.visit_photo_at) or c.aware(j.last_photo_at)
            if happened is None and photo and c.local(photo).date() >= vday and (
                    entered is None or photo >= entered):
                happened = {"at": _when(photo), "said": "pictures in Zuper (%s)" % (
                    j.visit_photo_by or j.last_photo_by or "the field")}
        last = b.evs[-1] if b.evs else None
        theirs = [e for e in b.evs if e["who"] == "customer"]
        since = c.aware(j.status_since) or c.aware(j.zuper_created_at)
        row = {"job_number": j.job_number, "customer": j.customer_name, "board": j.board,
               "stage": j.status, "visit": visit_text(j), "why_waiting": pj.why,
               "assigned": ", ".join(j.assigned or []) or None,
               "last_contact": "%s %s by %s (%s)" % (
                   _when(last["at"]), last["kind"], "the customer" if last["who"] ==
                   "customer" else "us", last["source"]) if last else None,
               "availability": b.availability.summary if b.availability else None}
        if upcoming is not None:
            kind, say, ev = "on_other_job", "The visit is booked on #%s (%s, %s)." % (
                upcoming.job.job_number, visit_text(upcoming.job), upcoming.job.status), None
        elif happened is not None:
            ev = happened.out() if isinstance(happened, Signal) else happened
            kind, say = "visit_happened", "Visit %s: %s %s — check with %s and set the stage." % (
                vday.strftime("%a %m/%d"), ev.get("at"), ev.get("said", "")[:90],
                ", ".join(j.assigned or []) or "the technician")
        elif stop and (newest_yes is None or stop.at > newest_yes):
            ev = stop.out()
            kind = "on_hold" if stop.kind == "hold" else "new_date"
            say = "%s %s." % ("On hold since" if stop.kind == "hold" else
                              "Asked to move / can't make it on", _when(stop.at))
        elif agreed:
            s = agreed[-1]
            ev = s.out()
            kind, say = "book", "Agreed %s for %s%s." % (
                _when(s.at), s.day.strftime("%a %m/%d"),
                " " + _win(s.window) if s.window else " (no time said — ask)")
        elif opts:
            ev = opts.out()
            kind, say = "confirm_time", "Offered %s on %s." % (", ".join(
                d.strftime("%a %m/%d") for d in opts.days if d >= today), _when(opts.at))
        elif last is None or (now - last["at"] > timedelta(days=STALE_DAYS) and (
                since is None or now - since > timedelta(days=STALE_DAYS))):
            kind, say, ev = "stale", "No call or text in %d days (last: %s); in “%s” since %s." % (
                STALE_DAYS, _when(last["at"]) if last else "none in %d days" % BOOK_DAYS,
                j.status, _when(since)), None
        elif last is not None and last["who"] == "us" and (
                not theirs or last["at"] - theirs[-1]["at"] > timedelta(hours=24)):
            # The customer has not answered for over a day since we last reached out (a reply
            # two minutes before our "thank you" is an answer — live: #279).
            heard = _when(theirs[-1]["at"]) if theirs else "never in %d days" % BOOK_DAYS
            kind, say, ev = "keep_calling", "We reached out %s; the customer last answered %s." % (
                _when(last["at"]), heard), None
        else:
            kind, say, ev = "call_and_book", "Last contact %s." % (
                row["last_contact"] or "none in %d days" % BOOK_DAYS), None
        row.update(verdict=kind, say=say, evidence=[ev] if ev else [])
        groups[kind].append(row)
    out = []
    for k, title in TO_BOOK:
        if groups[k]:
            out.append({"verdict": k, "title": title, "count": len(groups[k]),
                        "jobs": sorted(groups[k], key=lambda r: int(r["job_number"] or 0))})
    return {"checked_at": _when(now), "jobs_waiting": sum(len(v) for v in groups.values()),
            "skipped_test_jobs": skipped_tests, "groups": out,
            "how_to_judge": "Each job's verdict is worked out by script from 60 days of calls "
                            "and texts and Zuper. Report EVERY group, in order, each job with "
                            "its sentence; do not re-judge. Only `book` is ready for Zuper."}


# ------------------------------------------------------------- the office's day plan, judged

# What to do about each visit on the office's spreadsheet, worked out by script from the calls
# and texts and Zuper (2026-10-13): the AI's answer to "what is missing in Zuper from this
# Excel" left out the Wednesday-Saturday visits Zuper did not have, which it had been handed
# raw; now each comes with its verdict and the model only reports them.
VERDICTS = (
    ("book", "Agreed, not in Zuper — book it"),
    ("fix_details", "Agreed — fix the time / technician in Zuper"),
    ("fits_options", "The customer offered this day — confirm the time, then book"),
    ("call_first", "Not agreed — call the customer before booking"),
    ("file_wrong", "The calls say another day — fix the Excel, not Zuper"),
    ("calls_say_other_day", "The calls agreed another day that neither Zuper nor the Excel has"),
    ("on_hold", "The customer cancelled or put it on hold — do not book"),
    ("not_in_zuper", "No Zuper job matches the row"),
    ("matches", "Matches Zuper"),
)


def day_plan_verdicts(db: Session, now: datetime, out: dict,
                      hidden: set[str] | None = None) -> dict:
    """Add a verdict to every visit `lookups.compare_schedule` returned, and the visits by
    verdict. Reads only."""
    visits = out.get("visits") or []
    if not visits:
        return out
    every = load(db, now, hidden)
    by_num = {b.job.job_number: b for b in every}
    by_phone: dict[str, list[Brief]] = {}
    for b in every:
        for ph in b.job.phones or []:
            by_phone.setdefault(ph, []).append(b)
    today = c.local(now).date()
    groups: dict[str, list[str]] = {k: [] for k, _ in VERDICTS}
    for v in visits:
        f = v["file"]
        fday = lookups.parse_day(f["day"], now)
        label = "%s %s %s" % (f["day"], f.get("window") or "", f["customer"])
        z = v.get("zuper")
        b = by_num.get(z["job_number"]) if z else None
        verdict: dict = {}
        if b is None:
            verdict = {"kind": "not_in_zuper", "say": "No Zuper job matches this row: find or "
                                                       "create the job first."}
        else:
            j = b.job
            sibs = [o for ph in j.phones or [] for o in by_phone.get(ph, []) if o is not b]
            on_sib = next((o for o in sibs if c.aware(o.job.scheduled_start) and c.local(
                c.aware(o.job.scheduled_start)).date() == fday), None)
            recent = [s for s in b.sigs if now - s.at <= timedelta(days=PROMISE_DAYS)]
            agreed = [s for s in recent if s.kind == "confirmed" and s.day and s.day >= today]
            stop = next((s for s in reversed(recent) if s.kind in ("cancel", "hold")), None)
            last_agreed = agreed[-1] if agreed else None
            opts = next((s for s in reversed(recent) if s.kind == "options"), None)
            tech_diff = next((d for d in v.get("differences") or [] if "technician" in d), None)
            same_slot = not any(("Zuper has" in d or "time differs" in d or "technician" in d
                                 or "not in Zuper" in d or "closed" in d)
                                for d in v.get("differences") or [])
            if v.get("matches"):
                verdict = {"kind": "matches", "say": "Zuper has the same day, time and "
                                                     "technician."}
            elif same_slot:
                verdict = {"kind": "matches", "say": "Zuper has the same day and time; its "
                           "stage is “%s” — see Check Zuper." % j.status}
            elif on_sib is not None:
                verdict = {"kind": "matches", "say": "Zuper has this visit on #%s (%s)." % (
                    on_sib.job.job_number, visit_text(on_sib.job))}
            elif stop and (last_agreed is None or stop.at > last_agreed.at) and (
                    opts is None or stop.at > opts.at):
                verdict = {"kind": "on_hold", "say": "The customer %s on %s — do not book it." % (
                    "put it on hold" if stop.kind == "hold" else "asked to cancel or move it",
                    _when(stop.at)), "evidence": [stop.out()]}
            elif any(s.day == fday for s in agreed):
                s = [x for x in agreed if x.day == fday][-1]
                vday = c.local(c.aware(j.scheduled_start)).date() \
                    if c.aware(j.scheduled_start) else None
                verdict = {"kind": "fix_details" if vday == fday else "book",
                           "say": "Agreed on %s for %s%s; Zuper shows %s." % (
                               _when(s.at), s.day.strftime("%a %m/%d"),
                               " " + _win(s.window) if s.window else " (no time said)",
                               visit_text(j) or "no visit"), "evidence": [s.out()]}
            elif last_agreed is not None:
                other = last_agreed.day
                in_zuper = any(c.aware(x.job.scheduled_start) and c.local(c.aware(
                    x.job.scheduled_start)).date() == other for x in [b, *sibs])
                verdict = {"kind": "file_wrong" if in_zuper else "calls_say_other_day",
                           "say": "The calls agreed %s (%s), not %s%s." % (
                               other.strftime("%a %m/%d"), _when(last_agreed.at), f["day"],
                               "; Zuper already has it" if in_zuper else ""),
                           "evidence": [last_agreed.out()]}
            elif opts and fday in (opts.days or []):
                verdict = {"kind": "fits_options", "say": "The customer offered %s on %s; %s "
                           "fits. Confirm the time with them, then book." % (
                               ", ".join(d.strftime("%a %m/%d") for d in opts.days),
                               _when(opts.at), f["day"]), "evidence": [opts.out()]}
            else:
                last = b.out(now).get("last_contact")
                verdict = {"kind": "call_first", "say": "No call or text shows the customer "
                           "agreeing to %s. Last contact: %s." % (f["day"], last or "none in "
                                                                  "the last %d days" % COMMS_DAYS)}
            if tech_diff and verdict["kind"] != "matches":
                verdict["technician"] = tech_diff + " — ask the office which."
        v["verdict"] = verdict
        groups[verdict["kind"]].append("%s → %s" % (label.strip(), (
            "#%s " % z["job_number"] if z else "") + verdict["say"] + (
            " Technician: " + verdict["technician"] if verdict.get("technician") else "")))
    out["by_verdict"] = [{"verdict": title, "visits": groups[k]} for k, title in VERDICTS
                         if groups[k]]
    out["how_to_judge"] = ("Each visit's `verdict` is worked out by script from every call and "
                           "text and Zuper. Report EVERY group in `by_verdict`, in that order, "
                           "with each visit's sentence; do not re-judge a verdict. Only `book` "
                           "and `fix_details` are ready for Zuper; `call_first` needs a call "
                           "first; `file_wrong` means the Excel is wrong.")
    return out


# ------------------------------------------------------------------- the whole pipeline, mapped

# Every open job on a board, one row each: where it is, what it needs, where it should go and
# why (2026-10-14, the user: "make sure it truly maps all the jobs in the AHS pipelines and tells
# me for each job what we need to update — where it is, where it should be, for what reason —
# organized"). The AI's answer counted 59 of the 129 jobs and missed every job stuck AFTER its
# visit; this reads every job and uses every rule above, plus how long a job has sat in a stage.

# A job left this many days in a stage after its visit is stuck; where it goes next.
STUCK_AFTER = {
    "Inspection Completed": (2, "Submit To AHS For Approval",
                             "the report has not gone to AHS"),
    "Submit To AHS For Approval": (2, "Awaiting AHS Decision",
                                   "submitted? then it is waiting on AHS"),
    "Awaiting AHS Decision": (10, None, "AHS has not answered — follow up with AHS"),
    "Repair Complete": (3, "Call Satisfaction Check",
                        "the repair is done — the satisfaction call is next"),
    "Call Satisfaction Check": (7, "Invoice Submitted to AHS",
                                "call the customer, then invoice AHS"),
    "Invoice Submitted to AHS": (30, None, "AHS has not paid in a month — follow up with AHS"),
    "Awaiting AHS Payment": (30, None, "still unpaid — follow up with AHS"),
    "Review Requested": (7, None, "no review yet — remind the customer once, or close it"),
    "Waiting for Customer": (7, None, "follow up with the customer"),
    "Proposal Made": (7, None, "follow up on the proposal"),
}
# The board's stages after "Awaiting AHS Decision": a report the customer was told went to AHS
# can no longer be "behind" there.
AFTER_AHS = {"Awaiting AHS Decision", "AHS Approved", "Proposal Made", "Waiting for Customer",
             "Estimate Declined", "Cancelled"}
STATES = (
    ("update", "Update Zuper"),
    ("call", "Call first, then update"),
    ("waiting", "Waiting — nothing to change now"),
    ("ok", "Up to date"),
)
# What each finding / verdict asks for: (state, where it should be or the action).
KIND = {
    "ahs_sent_not_moved": ("update", "Awaiting AHS Decision"),
    "done_not_moved": ("update", None), "visit_happened": ("update", "the stage after the visit"),
    "customer_cancelled": ("update", "Reschedule Required"),
    "visit_on_closed_job": ("update", "take the visit off this job"),
    "technician_field": ("update", None), "agreed_other_time": ("update", "book the agreed day"),
    "book": ("update", "book the agreed day"), "on_the_way_not_moved": ("update", None),
    "booked_no_time": ("update", "give the visit a time, or Reschedule Required"),
    "stuck": ("update", None), "ahs_sent_check": ("update", "Awaiting AHS Decision"),
    "follow_up": ("call", None),
    "stale_on_the_way": ("update", None),
    "not_confirmed": ("call", "confirm the visit"),
    "customer_options": ("call", "pick one of the days the customer offered, then book"),
    "confirm_time": ("call", "pick one of the days the customer offered, then book"),
    "new_date": ("call", "a new date"),
    "keep_calling": ("call", "keep calling"), "call_and_book": ("call", "book a visit"),
    "unanswered": ("call", "answer the customer"), "no_field_update": ("call", None),
    "stale": ("call", "check it is still a job"), "fits_options": ("call", None),
    "running_late": ("waiting", None), "on_hold": ("waiting", "leave it"),
    "on_other_job": ("waiting", "nothing — the visit is on the other job"),
}
STATE_RANK = {s: i for i, (s, _) in enumerate(STATES)}


def _stage_key(board: str, stage: str | None) -> int:
    """Zuper's column order (playbook.BOARDS); Zuper cuts long names ("Inspection: Same-Day
    Confi"), so a prefix matches."""
    from . import playbook
    names = [n for n, _ in playbook.BOARDS.get(board, [])]
    s = (stage or "").lower()
    for i, n in enumerate(names):
        if n.lower() == s or n.lower().startswith(s) or s.startswith(n.lower()):
            return i
    return len(names)


def pipeline_map(db: Session, now: datetime, hidden: set[str] | None = None, *,
                 boards: tuple[str, ...] = (c.INSPECTION_BOARD, c.REPAIR_BOARD)) -> dict:
    """Every open job on `boards`, each with ONE state (update / call / waiting / ok), where it
    is, where it should be and why, with the evidence. Reads only."""
    hidden = hidden or set()
    boards = tuple(b for b in boards if b not in hidden)
    today = c.local(now).date()
    every = load(db, now, hidden, days=BOOK_DAYS)
    on_boards = [b for b in every if b.job.board in boards]
    issues: dict[str, list[dict]] = {}

    def add(uid: str, kind: str, why: str, *, to: str | None = None, ev=None) -> None:
        state, default_to = KIND.get(kind, ("update", None))
        issues.setdefault(uid, []).append({
            "kind": kind, "state": state, "should": to or default_to, "why": why,
            "evidence": [e for e in (ev or []) if e]})

    chk = check(db, now, hidden, date_from=today, date_to=today + timedelta(days=6),
                briefs=every)
    for f in chk["findings"]:
        if f["board"] not in boards:
            continue
        fix = f.get("fix") or {}
        to = fix.get("to") if fix.get("action") == "stage" else (
            "Technician field → %s" % fix["to"] if fix.get("action") == "field" else (
                "book %s, %s" % (fix["label"], fix["technician"])
                if fix.get("action") == "booking" else None))
        if f["kind"] == "stale_on_the_way":
            fj = next((b.job for b in every if b.job.job_uid == f["job_uid"]), None)
            vstart = c.aware(fj.scheduled_start) if fj else None
            done_to = DONE_STAGE.get(f["board"] or "")
            if "move it to " in f["do"]:
                to = f["do"].split("move it to ")[-1].rstrip(".")
            elif done_to and vstart and c.local(vstart).date() < today:
                # Its visit day has passed and it still says in progress: most likely done.
                to = "%s — check with %s first" % (done_to, f["assigned"] or "the technician")
            else:
                to = "the stage it is really in"
        add(f["job_uid"], f["kind"], "%s %s" % (f["what"], f["do"]), to=to, ev=f["evidence"])
    for g in to_book(db, now, hidden)["groups"]:
        for r in g["jobs"]:
            b = next((x for x in on_boards if x.job.job_number == r["job_number"]), None)
            if b is not None:
                add(b.job.job_uid, g["verdict"], r["say"], ev=r["evidence"])
    for b in on_boards:
        j = b.job
        if b.test:
            continue
        since = c.aware(j.status_since)
        days = (now - since).days if since else None
        rule = STUCK_AFTER.get(j.status or "")
        if rule and days is not None and days >= rule[0] and not any(
                x["state"] == "update" for x in issues.get(j.job_uid, [])):
            # A next stage = a Zuper change; no next stage = a person follows up, the job stays.
            add(j.job_uid, "stuck" if rule[1] else "follow_up",
                "%d days in “%s” — %s." % (days, j.status, rule[2]),
                to=rule[1] or "follow up; it stays in “%s”" % j.status)
        sent = b.newest("ahs_sent")
        if sent and j.board == c.INSPECTION_BOARD and (j.status or "") not in AFTER_AHS and \
                (j.status or "") not in c.WAITING_ON_AHS and since and sent.at >= since - \
                timedelta(days=7) and not any(x["kind"] == "ahs_sent_not_moved"
                                              for x in issues.get(j.job_uid, [])):
            add(j.job_uid, "ahs_sent_check", "We told the customer the report went to AHS (%s), "
                "but the job is in “%s”. Check the stage." % (_when(sent.at), j.status),
                ev=[sent.out()])

    out_boards = []
    for board in boards:
        rows = []
        for b in on_boards:
            j = b.job
            if j.board != board:
                continue
            since = c.aware(j.status_since)
            mine = sorted(issues.get(j.job_uid, []), key=lambda x: STATE_RANK[x["state"]])
            state = "test" if b.test else (mine[0]["state"] if mine else (
                "waiting" if (j.status or "") in c.WAITING_ON_AHS
                or j.status == "Waiting for Customer" else "ok"))
            top = mine[0] if mine else None
            rows.append({
                "job_number": j.job_number, "job_uid": j.job_uid, "customer": j.customer_name,
                "stage": j.status, "days_in_stage": (now - since).days if since else None,
                "visit": visit_text(j), "assigned": ", ".join(j.assigned or []) or None,
                "state": state, "should": top["should"] if top else None,
                "why": top["why"] if top else (
                    "A test job — never touched." if b.test else
                    "Waiting on AHS." if (j.status or "") in c.WAITING_ON_AHS else
                    "Nothing found in Zuper or the calls that needs a change."),
                "evidence": top["evidence"] if top else [],
                "also": [x["why"] for x in mine[1:4]] or None})
        cols = []
        for stage in sorted({r["stage"] for r in rows}, key=lambda s: (_stage_key(board, s), s)):
            these = sorted((r for r in rows if r["stage"] == stage),
                           key=lambda r: (STATE_RANK.get(r["state"], 9), int(r["job_number"] or 0)))
            cols.append({"stage": stage, "count": len(these), "jobs": these})
        counts = {s: sum(1 for r in rows if r["state"] == s) for s, _ in (*STATES, ("test", ""))}
        out_boards.append({"board": board, "total": len(rows), "by_state": counts,
                           "columns": cols})
    return {"checked_at": _when(now), "boards": out_boards,
            "states": [{"state": s, "title": t} for s, t in STATES],
            "how_to_read": "Every open job on the board, by column in Zuper's order. `state`: "
                           "update = change Zuper now (`should` says to what); call = call the "
                           "customer first; waiting = nothing to change now; ok = up to date. "
                           "Report the totals per board and EVERY update and call row, "
                           "grouped by column; do not re-judge."}


def pipeline_for_model(out: dict) -> dict:
    """The map, compact: every update / call row in full, waiting / ok as job numbers."""
    boards = []
    for b in out["boards"]:
        cols = []
        for col in b["columns"]:
            act = [{k: r[k] for k in ("job_number", "customer", "days_in_stage", "visit",
                                      "assigned", "state", "should", "why") if r.get(k)}
                   | ({"evidence": r["evidence"][:1]} if r["evidence"] else {})
                   for r in col["jobs"] if r["state"] in ("update", "call")]
            rest = {s: ["#%s" % r["job_number"] for r in col["jobs"] if r["state"] == s]
                    for s in ("waiting", "ok", "test")}
            cols.append({"stage": col["stage"], "count": col["count"], "act": act,
                         **{k: v for k, v in rest.items() if v}})
        boards.append({"board": b["board"], "total": b["total"], "by_state": b["by_state"],
                       "columns": cols})
    return {"checked_at": out["checked_at"], "boards": boards, "how_to_read": out["how_to_read"]}


# --------------------------------------------------------- what the calls say about WHEN

def booking_limits(b: Brief, now: datetime) -> tuple[dict | None, Signal | None]:
    """The scheduling limits a customer's own calls and texts set (2026-10-14, the user: "check
    the calls, flag what times they cannot be there"), in `scheduling.apply_limits`' shape:

      * a day they AGREED to (a "yes" to our proposal, a call that set it) -> only that day,
        in its window when one was said;
      * the days they OFFERED ("Friday, Monday or late Thursday") -> only those days;
      * a cancel / hold newer than any yes -> no limits, and that signal (second): a HOLD
        means do not book; a cancel / "can't make it" means find a new date.

    The saved availability (availability.py) is applied separately; these narrow it."""
    today = c.local(now).date()
    entered = c.aware(b.job.status_since)

    def current(s: Signal) -> bool:
        return now - s.at <= timedelta(days=PROMISE_DAYS) and (
            entered is None or s.at >= entered - timedelta(hours=12))
    yes = next((s for s in reversed(b.sigs) if s.kind == "confirmed" and s.day
                and s.day >= today and current(s)), None)
    offer = next((s for s in reversed(b.sigs) if s.kind == "options" and current(s)
                  and any(d >= today for d in s.days or [])), None)
    stop = next((s for s in reversed(b.sigs) if s.kind in ("cancel", "hold") and current(s)),
                None)
    newest_ok = max([x.at for x in (yes, offer) if x], default=None)
    if stop and (newest_ok is None or stop.at > newest_ok):
        return None, stop
    far = today + timedelta(days=120)
    if yes and (offer is None or yes.at >= offer.at):
        lim = {"not_before": yes.day.isoformat(),
               "blocked": [{"from": (yes.day + timedelta(days=1)).isoformat(),
                            "to": far.isoformat()}],
               "summary": "only %s%s (agreed %s: “%s”)" % (
                   yes.day.strftime("%a %m/%d"), " " + _win(yes.window) if yes.window else "",
                   _when(yes.at), yes.quote[:120]),
               "agreed_day": yes.day.isoformat()}
        if yes.window:
            lim["after"] = "%02d:%02d" % divmod(max(0, yes.window[0] - 30), 60)
            lim["before"] = "%02d:%02d" % divmod(min(24 * 60 - 1, yes.window[1] + 30), 60)
        return lim, None
    if offer:
        days = sorted(d for d in offer.days if d >= today)
        blocked, prev = [], days[0]
        for d in days[1:]:
            if (d - prev).days > 1:
                blocked.append({"from": (prev + timedelta(days=1)).isoformat(),
                                "to": (d - timedelta(days=1)).isoformat()})
            prev = d
        blocked.append({"from": (days[-1] + timedelta(days=1)).isoformat(), "to": far.isoformat()})
        return {"not_before": days[0].isoformat(), "blocked": blocked,
                "summary": "only %s (offered %s: “%s”)" % (
                    ", ".join(d.strftime("%a %m/%d") for d in days), _when(offer.at),
                    offer.quote[:120])}, None
    return None, None


# ---------------------------------------------------------------- one customer, everything

# "Tell me all the info about Anita McGruder — a resume of the conversations on Quo and Zuper"
# (2026-10-15). The AI answered with 14 look-ups (four of them the same conversation again,
# ~28k characters each) and missed that the AHS work order that arrived on 10/01 was the "new
# leak — call AHS" from the 9/28 call. The script gathers everything once; the model writes.
PROFILE_DAYS = 120
PROFILE_ITEMS = 90
# Things worth a person's attention, found in the customer's calls and texts by fixed patterns.
def _any(*alternatives: str) -> re.Pattern:
    return re.compile("|".join(alternatives), re.IGNORECASE)


FLAG_PATTERNS = (
    ("money", _any(r"\$\s?\d[\d,]*", r"\b\d{3,5} (dollars|dólares)\b", r"\bzelle\b",
                   r"\bdeductible\b", r"\bdeposit\b", r"out of pocket", r"non-?covered",
                   r"\bpay(ing|ment)?\b")),
    ("complaint", _any(r"still leak\w*", r"not happy|unhappy|upset|angry", r"frustrat\w*",
                       r"disappoint\w*|complain\w*", r"no ?one (came|showed)",
                       r"nobody (came|showed)", r"waiting (for )?(weeks|so long)",
                       r"sigue (goteando|la gotera)")),
    ("quote asked", _any(r"\b(quote|estimate|cotizaci\w+|presupuesto|proposal)\b",
                         r"\b(replacement|new roof|re-?roof)\b")),
    ("contact preference", _any(r"(by|via|prefer\w*|only) (text|texting|email|whats ?app)",
                                r"text me|don'?t call",
                                r"call (my|her|his) (son|daughter|husband|wife|tenant|niece)",
                                r"(son|daughter|husband|wife|tenant)\b.{0,30}"
                                r"\b(will be|meets?|handles?|number)")),
    ("access", _any(r"\bgate\b|\bdog\b|\bgate code\b|lock ?box",
                    r"can'?t (get|go) (up )?on the roof",
                    r"(not be|won'?t be) (home|there)")),
    ("new AHS claim", _any(r"call (american home shield|ahs)", r"new claim|another claim",
                           r"not covered|only covers one")),
    ("warranty", _any(r"\bwarrant\w*", r"garant\w*")),
)


# A line with something flagged in it is passed whole: John Dahdouh's 10/15 Zuper Connect summary
# was cut at 300 characters, right before "proposing to pay $450 instead of $675".
LINE_MAX, FLAGGED_LINE_MAX, FLAG_QUOTE_MAX = 300, 900, 600


def _flagged(text: str) -> bool:
    return any(rx.search(text or "") for _, rx in FLAG_PATTERNS)


def _sentences(text: str, rx: re.Pattern) -> str:
    """Every sentence of `text` that `rx` matches, joined — so a flag carries the whole point
    (the counter-offer after the price), not a window around the first match."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    hit = " … ".join(" ".join(s.split()) for s in parts if rx.search(s))
    return hit[:FLAG_QUOTE_MAX] or _quote(text, rx.search(text or ""))


def _line(e: dict) -> str:
    text = e["text"] or ""
    cap = FLAGGED_LINE_MAX if _flagged(text) else LINE_MAX
    if e["kind"] == "call":
        said = summary_of(text) or ("" if e["source"] != "Zuper Connect" else text)
        if not said and e.get("transcript"):
            said = "…" + " ".join(e["transcript"].split())[-260:]
        status = (e.get("status") or "").lower()
        secs = e.get("seconds") or 0
        if not said:
            said = ("no answer" if status in ("no-answer", "missed", "canceled", "cancelled")
                    or secs == 0 else "%ss, nothing captured" % secs)
        return " ".join(said.split())[:cap]
    return " ".join(text.split())[:cap if cap > LINE_MAX else 240]


def customer_profile(db: Session, now: datetime, hidden: set[str] | None = None, *,
                     query: str) -> dict:
    """Everything about ONE customer, gathered once: every job (open and closed) on their
    numbers or name, each with stage, history, notes, fields and what it needs now; one
    timeline of every call and text on every number they use (family included); flags; and the
    last standing. Reads only."""
    from ..models import DispatchNote, ZuperStatusHistory
    hidden = hidden or set()
    q = (query or "").strip().lstrip("#").lower()
    if not q:
        return {"error": "give a customer name, a job number or a phone"}
    jobs = [j for j in db.scalars(select(DispatchJob)) if not (j.board and j.board in hidden)]
    digits_q = re.sub(r"\D", "", q)[-10:]
    seed = [j for j in jobs if (q.isdigit() and j.job_number == q)
            or (len(digits_q) == 10 and digits_q in (j.phones or []))
            or (not q.isdigit() and q in (j.customer_name or "").lower())]
    if not seed:
        words = [w for w in re.split(r"\W+", q) if len(w) > 2]
        seed = [j for j in jobs if words and all(w in (j.customer_name or "").lower()
                                                   for w in words)]
    if not seed:
        return {"error": "no customer matches “%s” in Zuper" % query}
    names = {" ".join((j.customer_name or "").lower().split()) for j in seed}
    if len(names) > 3:
        return {"error": "“%s” matches several customers — be more specific" % query,
                "matches": sorted({"#%s %s" % (j.job_number, j.customer_name) for j in seed})[:12]}
    evs_all = [e for e in lookups._events(db, now - timedelta(days=PROFILE_DAYS))
               if e["at"] and e["at"] <= now]
    reach = lookups.customer_phones(db, now, evs_all)
    phones = {p for j in seed for p in reach.get(j.job_uid, set(j.phones or []))}
    # The office saves a relative in Quo under the customer's name ("Basil Binns (Anita
    # Mcgruder) SON", live 2026-10-15): that number is the customer's too.
    full = [n for n in names if len(n.split()) >= 2]
    labels: dict[str, str] = {}
    for e in evs_all:
        label = " ".join((e["name"] or "").lower().split())
        if e["phone"] and label and any(n in label for n in full):
            phones.add(e["phone"])
            labels.setdefault(e["phone"], e["name"])
    family = phones - {p for j in seed for p in (j.phones or [])}
    mine = [j for j in jobs if set(j.phones or []) & phones
            or " ".join((j.customer_name or "").lower().split()) in names]
    evs = sorted({(e["at"], e["phone"], e["kind"], (e["text"] or "")[:60]): e for e in evs_all
                  if e["phone"] in phones}.values(), key=lambda e: e["at"])
    open_jobs = [j for j in mine if j.is_open]
    pmap = pipeline_map(db, now, hidden, boards=tuple(sorted({j.board for j in open_jobs
                                                                if j.board}))) \
        if open_jobs else {"boards": []}
    verdict = {r["job_number"]: r for b in pmap["boards"] for col in b["columns"]
               for r in col["jobs"]}
    out_jobs = []
    for j in sorted(mine, key=lambda j: (not j.is_open, -(int(j.job_number or 0)))):
        hist = db.scalars(select(ZuperStatusHistory).where(
            ZuperStatusHistory.job_uid == j.job_uid).order_by(
            ZuperStatusHistory.changed_at)).all()
        notes = db.scalars(select(DispatchNote).where(
            DispatchNote.job_uid == j.job_uid, DispatchNote.removed.is_(False)).order_by(
            DispatchNote.created_at)).all()
        v = verdict.get(j.job_number)
        out_jobs.append({
            "job": "#%s" % j.job_number, "title": j.title, "open": j.is_open,
            "board": j.board, "stage": j.status, "stage_since": _when(j.status_since),
            "created": _when(j.zuper_created_at), "visit": visit_text(j),
            "assigned": ", ".join(j.assigned or []) or None, "address": ", ".join(
                x for x in (j.address, j.city) if x) or None,
            "fields": {k: str(val)[:80] for k, val in (j.fields or {}).items()
                       if val not in (None, "", [], {}) and KEY_FIELDS.search(k)} or None,
            "stage_history": ["%s %s%s" % (_when(h.changed_at), h.status_name,
                                           " (%s)" % h.done_by_name if h.done_by_name else "")
                              for h in hist][-10:] or None,
            "notes": ["%s %s: %s" % (_when(n.created_at), n.by_name or "",
                                     " ".join((n.text or "").split())[:300] or "(no text)")
                      for n in notes][-6:] or None,
            "pictures": ("last %s by %s" % (_when(j.last_photo_at), j.last_photo_by)
                         if j.last_photo_at else None),
            "needs_now": ({"state": v["state"], "should": v.get("should"), "why": v["why"]}
                          if v else None)})
    timeline = [{"at": _when(e["at"]), "line": e["source"], "who": e["who"],
                 "how": e["kind"],
                 "number": ("%s %s" % (e["phone"], labels.get(e["phone"], ""))).strip()
                 if e["phone"] in family else None,
                 "said": _line(e)} for e in evs]
    if len(timeline) > PROFILE_ITEMS:
        timeline = timeline[-PROFILE_ITEMS:]
    flags: list[dict] = []
    for e in evs:
        text = " ".join(((e["text"] or "") + " " + (e.get("transcript") or "")).split())
        for name, rx in FLAG_PATTERNS:
            m = rx.search(text)
            if m:
                flags.append({"flag": name, "at": _when(e["at"]), "who": e["who"],
                              "said": _sentences(text, rx)})
    seen_flag: set[tuple] = set()
    flags = [f for f in reversed(flags) if not ((f["flag"], f["said"][:40]) in seen_flag
                                                 or seen_flag.add((f["flag"], f["said"][:40])))]
    brief = next(iter(load(db, now, hidden, jobs=open_jobs[:1], days=BOOK_DAYS)), None) \
        if open_jobs else None
    ua = brief.unanswered(now) if brief else None
    last = evs[-1] if evs else None
    theirs = [e for e in evs if e["who"] == "customer"]
    return {
        "customer": seed[0].customer_name, "phones": sorted(phones - family),
        "family_or_other_numbers": ["%s %s" % (p, labels.get(p, "")) for p in sorted(family)]
        or None,
        "jobs": out_jobs,
        "last_standing": {
            "last_contact": "%s %s %s (%s): %s" % (_when(last["at"]), last["kind"],
                                                  "from the customer" if last["who"] ==
                                                  "customer" else "by us", last["source"],
                                                  _line(last)) if last else None,
            "last_from_customer": "%s: %s" % (_when(theirs[-1]["at"]), _line(theirs[-1]))
            if theirs else None,
            "days_since_contact": (now - last["at"]).days if last else None,
            "waiting_for_our_answer": ua,
            "open_jobs_need": [{"job": "#%s" % j.job_number, **(verdict[j.job_number] and {
                "state": verdict[j.job_number]["state"],
                "should": verdict[j.job_number].get("should"),
                "why": verdict[j.job_number]["why"]})} for j in open_jobs
                if j.job_number in verdict]},
        "flags": flags[:25] or None,
        "timeline_oldest_first": timeline,
        "covers": "calls and texts of the last %d days on every number above (Quo, the CRM "
                  "line, Zuper Connect summaries); Zuper's jobs, stage history and notes"
                  % PROFILE_DAYS,
        "how_to_write": "Write, in this order: 1) LAST STANDING — where things are now and the "
                        "next step, 2-4 lines; 2) FLAGS — what a person must know (money, "
                        "complaints, quotes asked, how to contact, family, access, AHS), each "
                        "with its date; 3) JOBS — one table row per job: stage, since, visit, "
                        "what it needs; 4) THE CONVERSATION — summarised by phase (a few lines "
                        "each), not every message; 5) what could not be checked. Link the jobs: "
                        "a new AHS work order after we told the customer to call AHS about a "
                        "new leak IS that claim; a visit booked on one job may also have "
                        "covered another job's warranty fix — say so and ask the technician, "
                        "never call it unbooked without saying that. MONEY: give every figure "
                        "said — the price, any counter-offer the customer made, who was to "
                        "answer it — and say 'agreed' only where a call or text records the "
                        "agreement; otherwise say it is open. Do not search the "
                        "conversation again unless a call must be read in full."}
