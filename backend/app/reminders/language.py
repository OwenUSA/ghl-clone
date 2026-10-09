"""English or Spanish, from what the customer actually wrote and said to us (2026-10-08).

Neither Zuper nor the CRM stores a language (checked: no customer field, no custom field), so it
is worked out from the evidence the CRM already holds for the number — the customer's own texts
(inbound only: our staff's replies say nothing about them) and the transcripts of calls on Quo
or the CRM line — and SAVED per number in `customer_languages`. A person can set it by hand;
a manual choice is never overwritten. With no evidence, English.

`detect` is pure and counts common function words. Spanish wins only on clear evidence: two
texts, or one call, reading as Spanish, and more Spanish than English items overall.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import phone_match
from ..models import (
    Contact,
    Conversation,
    ConversationEvent,
    CustomerLanguage,
    Direction,
    EventType,
    NumberThread,
    NumberThreadEvent,
)


# Words that are common in one language and (almost) never in the other. "no", "me", "a",
# "son" and the like are left out because they are both.
def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


SPANISH = _words("""
hola gracias buenos buenas días dias tardes noches por favor que qué el la los las del para
con una uno mi mis su sus está esta estoy están estan puede pueden cuando cuándo mañana manana
hoy techo casa señor señora senor senora bien muy pero también tambien tengo tiene necesito
quiero llamar ahora aquí aqui usted ustedes nosotros cita hora cuánto cuanto dónde donde porque
sí entonces vamos hacer tiempo agua lluvia goteras gotera reparación reparacion
""")
ENGLISH = _words("""
the and you is are my your to of it that this for with have has can will roof thanks thank
hello hi yes please tomorrow today what when there would could just okay ok we they our about
know call time leak repair inspection appointment
""")

WORD = re.compile(r"[a-záéíóúñü]+", re.IGNORECASE)
TEXT_MIN_HITS = 2          # a text with fewer marker words says nothing
CALL_MIN_HITS = 8          # a transcript needs more, it mixes both sides of the call
CALL_SPANISH_SHARE = 0.6   # ...and Spanish must be most of what was said


def score(text: str) -> tuple[int, int]:
    words = WORD.findall((text or "").lower())
    return sum(w in SPANISH for w in words), sum(w in ENGLISH for w in words)


def classify(text: str, *, call: bool = False) -> str | None:
    es, en = score(text)
    if call:
        if es + en < CALL_MIN_HITS:
            return None
        share = es / (es + en)
        return "es" if share >= CALL_SPANISH_SHARE else ("en" if share <= 0.4 else None)
    if es >= TEXT_MIN_HITS and es > en:
        return "es"
    if en >= TEXT_MIN_HITS and en > es:
        return "en"
    return None


def detect(texts: list[str], transcripts: list[str]) -> tuple[str, dict]:
    """(language, evidence) from the customer's inbound texts and call transcripts."""
    ev = {"es_texts": 0, "en_texts": 0, "es_calls": 0, "en_calls": 0}
    for t in texts:
        lang = classify(t)
        if lang:
            ev["%s_texts" % lang] += 1
    for t in transcripts:
        lang = classify(t, call=True)
        if lang:
            ev["%s_calls" % lang] += 1
    es = ev["es_texts"] + ev["es_calls"]
    en = ev["en_texts"] + ev["en_calls"]
    clear = ev["es_texts"] >= 2 or ev["es_calls"] >= 1
    return ("es" if clear and es > en else "en"), ev


def _evidence(db: Session, phone_key: str) -> tuple[list[str], list[str]]:
    texts: list[str] = []
    calls: list[str] = []
    for e in db.scalars(
            select(ConversationEvent)
            .join(Conversation, Conversation.id == ConversationEvent.conversation_id)
            .join(Contact, Contact.id == Conversation.contact_id)
            .where(phone_match.phone_clause(Contact.phone, phone_key),
                   ConversationEvent.type.in_((EventType.SMS, EventType.CALL)))
            .order_by(ConversationEvent.occurred_at.desc()).limit(200)):
        if e.type == EventType.SMS and e.direction == Direction.INBOUND and e.body:
            texts.append(e.body)
        elif e.type == EventType.CALL and e.transcript:
            calls.append(e.transcript)
    for e in db.scalars(
            select(NumberThreadEvent)
            .join(NumberThread, NumberThread.id == NumberThreadEvent.number_thread_id)
            .where(NumberThread.phone_key == phone_key,
                   NumberThreadEvent.type.in_((EventType.SMS, EventType.CALL)))
            .order_by(NumberThreadEvent.occurred_at.desc()).limit(200)):
        if e.type == EventType.SMS and e.direction == Direction.INBOUND and e.body:
            texts.append(e.body)
        elif e.type == EventType.CALL and e.transcript:
            calls.append(e.transcript)
    return texts, calls


def for_phone(db: Session, phone_key: str, *, save: bool = True) -> str:
    """The language to text this number in: a person's choice, else worked out now from the
    evidence and saved (`save=False` reads without writing — the dry run)."""
    row = db.scalar(select(CustomerLanguage).where(CustomerLanguage.phone_key == phone_key))
    if row is not None and row.source == "manual":
        return row.language
    lang, ev = detect(*_evidence(db, phone_key))
    if save:
        if row is None:
            row = CustomerLanguage(phone_key=phone_key)
            db.add(row)
        row.language, row.source, row.evidence = lang, "auto", ev
        row.updated_at = datetime.now(UTC)
        db.flush()
    return lang


def set_manual(db: Session, phone_key: str, language: str | None, user_id: int | None) -> dict:
    """A person's choice ("en" / "es"), or None to go back to detecting it."""
    row = db.scalar(select(CustomerLanguage).where(CustomerLanguage.phone_key == phone_key))
    if language is None:
        if row is not None and row.source == "manual":
            row.source, row.set_by = "auto", user_id
        for_phone(db, phone_key)
    else:
        if row is None:
            row = CustomerLanguage(phone_key=phone_key)
            db.add(row)
        row.language, row.source, row.set_by = language, "manual", user_id
        row.updated_at = datetime.now(UTC)
        db.flush()
    return describe(db, phone_key)


def describe(db: Session, phone_key: str) -> dict:
    row = db.scalar(select(CustomerLanguage).where(CustomerLanguage.phone_key == phone_key))
    if row is None:
        lang, ev = detect(*_evidence(db, phone_key))
        return {"phone": phone_key, "language": lang, "source": "auto", "evidence": ev,
                "saved": False}
    return {"phone": phone_key, "language": row.language, "source": row.source,
            "evidence": row.evidence, "saved": True}
