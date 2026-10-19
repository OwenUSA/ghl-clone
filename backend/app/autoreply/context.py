"""What the reply is written from (Q7, Q20, Q26): the whole Zuper conversation, every Quo and
CRM-line text and call with their transcripts and summaries, Zuper's call summaries — plus our
own transcript of the customer's recorded Zuper calls, made just in time — and the Zuper job.

Q28: phone numbers, email addresses and street addresses are REMOVED before any of it leaves for
the model provider. A polite reply never needs them.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import providers, vault
from ..dispatch.comms import Comm
from ..models import AiConnection, DispatchCall, DispatchJob
from ..zuper import client
from . import config as c
from .rules import ZMsg, answered_call

log = logging.getLogger("autoreply")

TRANSCRIBE_CALLS = 3              # the customer's most recent recorded Zuper calls, at most
TEXT_MAX = 600
TRANSCRIPT_MAX = 3000

PHONE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b")
EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
STREET = re.compile(
    r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Za-z0-9']+\s+){0,4}"
    r"(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|boulevard|ct|court|cir|circle|"
    r"way|pl|place|ter|terrace|pkwy|parkway|hwy|highway|trl|trail|loop|run)\b\.?"
    r"(?:\s*(?:#|apt|unit|suite|ste)\.?\s*\w+)?", re.IGNORECASE)


def redact(text: str | None, extra: tuple[str, ...] = ()) -> str:
    t = text or ""
    for s in extra:
        if s and len(s) >= 5:
            t = re.sub(re.escape(s), "[address]", t, flags=re.IGNORECASE)
    t = EMAIL.sub("[email]", t)
    t = PHONE.sub("[phone]", t)
    return STREET.sub("[address]", t)


def first_name(full: str | None) -> str:
    name = re.sub(r"\(.*?\)", "", full or "").strip()
    first = name.split()[0] if name else ""
    return first.capitalize() if first.isupper() else first


def jobs_for(db: Session, customer_uid: str | None, phone: str) -> list[DispatchJob]:
    """The customer's Zuper jobs, open and most recently changed first."""
    rows = []
    if customer_uid:
        rows = list(db.scalars(select(DispatchJob).where(
            DispatchJob.customer_uid == customer_uid)))
    if not rows and phone:
        rows = [j for j in db.scalars(select(DispatchJob)) if phone in (j.phones or [])]
    return sorted(rows, key=lambda j: (not j.is_open, -(c.aware(j.status_since) or
                                                         datetime.min.replace(
                                                             tzinfo=c.TZ)).timestamp()))


def _local(dt) -> str | None:
    return c.local(c.aware(dt)).strftime("%a %b %d %Y, %I:%M %p") if dt else None


def _window(j: DispatchJob) -> str | None:
    if not j.scheduled_start:
        return None
    s = c.local(c.aware(j.scheduled_start))
    out = s.strftime("%A, %B %d, %I:%M %p").replace(" 0", " ")
    if j.scheduled_end:
        e = c.local(c.aware(j.scheduled_end))
        if e.date() == s.date():
            out += " to " + e.strftime("%I:%M %p").lstrip("0")
    return out


def job_facts(j: DispatchJob, now: datetime) -> dict:
    visit_future = bool(j.scheduled_start and c.aware(j.scheduled_start) > now)
    tech = (j.technician or (j.assigned or [None])[0] or "").split()
    return {"job_number": j.job_number, "board": j.board, "column": j.status,
            "in_column_since": _local(j.status_since), "open": j.is_open,
            "next_visit": _window(j) if visit_future else None,
            "last_visit": _window(j) if j.scheduled_start and not visit_future else None,
            "technician_first_name": tech[0] if tech else None}


# ------------------------------------------------------------------ transcripts

def _transcriber(db: Session, s) -> providers.Provider | None:
    conn = db.get(AiConnection, s.autoreply_transcribe_connection_id) \
        if s.autoreply_transcribe_connection_id else None
    if conn is None or conn.provider != "openai":
        return None
    try:
        return providers.build(conn.provider, vault.decrypt(conn.api_key_encrypted),
                               conn.base_url)
    except (vault.SecretsUnavailable, providers.ProviderError):
        return None


def transcribe_audio(prov: providers.Provider, url: str) -> str:
    """Download one recording (client.fetch_file: https only, the key only to Zuper's own
    hosts) and transcribe it. Monkeypatched in tests."""
    audio, ctype = client.fetch_file(url)
    ext = "wav" if "wav" in ctype else "mp3"
    return prov.transcribe(audio, "call." + ext)


def transcripts(db: Session, s, msgs: list[ZMsg]) -> dict[str, str]:
    """call_uid -> transcript for the customer's last recorded Zuper calls. A transcript is made
    once and kept on dispatch_calls; a failure leaves the call to its summary."""
    recorded = [m for m in msgs if m.kind == "call" and m.recording_url and m.call_uid]
    recorded = recorded[-TRANSCRIBE_CALLS:]
    if not recorded:
        return {}
    rows = {r.call_uid: r for r in db.scalars(select(DispatchCall).where(
        DispatchCall.call_uid.in_([m.call_uid for m in recorded])))}
    out: dict[str, str] = {}
    prov = None
    for m in recorded:
        row = rows.get(m.call_uid)
        if row is not None and row.transcript is not None:
            out[m.call_uid] = row.transcript
            continue
        prov = prov or _transcriber(db, s)
        if prov is None:
            break
        try:
            text = transcribe_audio(prov, m.recording_url)
        except (client.ZuperError, providers.ProviderError) as e:   # the summary is still there
            log.info("transcript of call %s failed: %s", m.call_uid, type(e).__name__)
            continue
        out[m.call_uid] = text
        if row is not None:
            row.transcript = text
    return out


# ------------------------------------------------------------------ the brief

def build(db: Session, s, *, conv_customer: dict, msgs: list[ZMsg], other: list[Comm],
          jobs: list[DispatchJob], stretch: list[ZMsg], now: datetime) -> dict:
    addresses = tuple(j.address for j in jobs if j.address)
    texts = transcripts(db, s, msgs)
    stretch_ids = {m.uid for m in stretch}

    def r(t):
        return redact(t, addresses)

    zuper = []
    for m in msgs[-30:]:
        row = {"at": _local(m.at), "from": "customer" if not m.out else (
            "us (automatic text)" if m.user and m.user.lower() == "zuper" else
            "us (%s)" % (m.user or "office")),
            "waiting_for_answer": m.uid in stretch_ids}
        if m.kind == "text":
            row["text"] = r(m.body)[:TEXT_MAX]
        else:
            row["call"] = ("answered, %ds" % m.seconds if answered_call(m)
                           else "voicemail" if (m.call_status or "") == "VOICEMAIL"
                           else "missed" if not m.out else "not answered")
            if m.summary:
                row["call_summary"] = r(m.summary)[:TEXT_MAX]
            if m.sentiment:
                row["call_sentiment"] = m.sentiment
            if texts.get(m.call_uid):
                row["call_transcript"] = r(texts[m.call_uid])[:TRANSCRIPT_MAX]
        zuper.append(row)
    since = now - timedelta(days=c.COMMS_DAYS)
    elsewhere = []
    for x in other:
        if x.source == "Zuper" or c.aware(x.at) < since:
            continue                        # Zuper's own calls are in the conversation above
        row = {"at": _local(x.at), "line": x.source, "from": "us" if x.out else "customer",
               "kind": x.kind}
        if x.kind == "text":
            row["text"] = r(x.summary)[:TEXT_MAX]
        else:
            row["call"] = "answered, %ds" % x.seconds if x.answered else "missed"
            if x.summary:
                row["call_summary"] = r(x.summary)[:TEXT_MAX]
            if x.transcript:
                row["call_transcript"] = r(x.transcript)[:TRANSCRIPT_MAX]
        elsewhere.append(row)
    return {
        "now": _local(now),
        "office_open_now": c.office_open(now),
        "customer_first_name": first_name(conv_customer.get("name")) or None,
        "zuper_conversation_oldest_first": zuper,
        "quo_and_crm_line_oldest_first": elsewhere[-30:],
        "zuper_jobs": [job_facts(j, now) for j in jobs[:3]],
    }
