"""When a report is built, and what of the model's answer is kept - PURE (tests below).

An inspection is over when its pictures stop: the job has pictures from a visit and none for
`quiet_minutes`. Then ONE report per job and visit day, never for a visit that finished before
the agent's first run (no backlog).

The model's answer keeps only what passes: photo indexes that exist, once each, at most the
maximum; short captions; findings that name no money, no approval, no date, no promise.
Nothing internal reaches the homeowner (2026-10-23, the owner, after the #681 test said "the
technician's notes identify the job as new gutters"): a sentence about our technician, our
notes, the job, the work order, what was booked, or our systems is dropped - from the summary
sentence by sentence, a finding or caption whole.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

MAX_CAPTION = 120
MAX_FINDING = 300
FORBIDDEN = re.compile(
    r"\$|\bdollars?\b|\bprice|\bcost|\bapproved?\b|\bauthori[sz]ed\b|\bguarantee|\bpromise"
    r"|\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b|\btomorrow\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d"
    r"|\b\d{1,2}/\d{1,2}\b", re.IGNORECASE)


INTERNAL = re.compile(
    r"\btechnician'?s?\b|\btech'?s?\b|\binspector'?s?\b|\bnotes?\b|\bwork order|\bjob\b"
    r"|\bbooked\b|\bscheduled\b|\bdispatch|\bticket\b|\bzuper\b|\bcrm\b|\bahs\b"
    r"|\bhome ?warranty\b|\bour (?:records|system|team noted)\b|\bnot documented\b"
    r"|\bno (?:active )?leak (?:is|was) (?:documented|recorded|noted)\b", re.IGNORECASE)
SENTENCE = re.compile(r"(?<=[.!?])\s+")


def customer_safe(text: str) -> tuple[str, list[str]]:
    """(the text without its internal sentences, the sentences dropped)."""
    keep, dropped = [], []
    for s in SENTENCE.split(text or ""):
        if not s.strip():
            continue
        (dropped if INTERNAL.search(s) else keep).append(s.strip())
    return " ".join(keep), dropped


def ready(*, kind: str, photo_count: int, last_photo_at: datetime | None, now: datetime,
          quiet: timedelta, started: datetime, already: bool) -> bool:
    if kind != "inspection" or photo_count <= 0 or last_photo_at is None or already:
        return False
    if last_photo_at < started:
        return False
    return now - last_photo_at >= quiet


@dataclass
class Draft:
    photos: list[tuple[int, str]] = field(default_factory=list)   # (candidate index, caption)
    title: str = ""
    summary: str = ""
    findings: list[str] = field(default_factory=list)
    guides: list[str] = field(default_factory=list)      # keys of guides.GUIDES
    dropped: list[str] = field(default_factory=list)


def _clean(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit].strip()


def accept(answer: dict | None, *, candidates: int, max_photos: int) -> Draft | None:
    """The model's JSON as a draft, or None if nothing usable came back."""
    if not isinstance(answer, dict):
        return None
    d = Draft()
    seen: set[int] = set()
    for p in answer.get("photos") or []:
        if not isinstance(p, dict):
            continue
        i = p.get("index")
        if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < candidates or i in seen:
            continue
        cap = _clean(p.get("caption"), MAX_CAPTION)
        if FORBIDDEN.search(cap) or INTERNAL.search(cap):
            d.dropped.append("caption: " + cap)
            cap = ""
        seen.add(i)
        d.photos.append((i, cap))
        if len(d.photos) >= max_photos:
            break
    d.title = _clean(answer.get("title"), 120)
    if FORBIDDEN.search(d.title) or INTERNAL.search(d.title):
        d.dropped.append("title: " + d.title)
        d.title = ""
    d.summary = _clean(answer.get("summary"), 1200)
    if FORBIDDEN.search(d.summary):
        d.dropped.append("summary: " + d.summary)
        d.summary = ""
    d.summary, gone = customer_safe(d.summary)
    d.dropped += ["summary sentence: " + s for s in gone]
    for f in answer.get("findings") or []:
        f = _clean(f, MAX_FINDING)
        if not f:
            continue
        if FORBIDDEN.search(f) or INTERNAL.search(f):
            d.dropped.append("finding: " + f)
            continue
        d.findings.append(f)
    from .guides import valid
    d.guides = valid(answer.get("guides"))
    if not d.photos:
        return None
    return d
