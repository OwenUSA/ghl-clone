"""What a finished visit must have, and what is missing - PURE: no database, no network
(tests/test_inspection_docs_rules.py).

    inspection  leak location, tools & materials, time to complete, photos, a video
    repair      photos, a video of the finished repair

"Couldn't inspect yet" (e.g. plywood under tiles) is collected and shown, never flagged: it is
an optional answer. A detail counts as found from a Zuper job field, a written checklist answer,
a note, the voice note, or the technician's own reply - wherever it was written.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

DETAILS = ("leak_location", "tools_materials", "time_estimate", "couldnt_inspect")
LABEL = {
    "leak_location": "where the leak is",
    "tools_materials": "tools & materials needed",
    "time_estimate": "time to complete",
    "couldnt_inspect": "what could not be inspected yet",
    "photos": "photos",
    "video": "a video",
}
# The Zuper job fields each detail is copied to (the inspection checklist links them).
FIELD_LABELS = {
    "leak_location": ("Leak location",),
    "tools_materials": ("Tools & materials needed",),
    "time_estimate": ("Estimated time to complete",),
    "couldnt_inspect": ("Couldn't inspect yet (e.g. plywood under tiles)",),
}
# Words in a checklist QUESTION that say which detail its written answer is.
QUESTION_WORDS = {
    "leak_location": ("leak location", "where is the leak", "leak"),
    "tools_materials": ("tools", "materials"),
    "time_estimate": ("time to complete", "estimated time", "how long"),
    "couldnt_inspect": ("couldn't inspect", "could not inspect", "plywood"),
}
REQUIRED = {
    "inspection": ("leak_location", "tools_materials", "time_estimate", "photos", "video"),
    "repair": ("photos", "video"),
}
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".3gp", ".webm", ".avi", ".mkv")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic")
EMPTY = {"", "-", "n/a", "na", "none", "no", "tbd", "?", "null"}


@dataclass
class Found:
    value: str
    source: str            # field | checklist | note | voice note | technician
    quote: str = ""


@dataclass
class Evidence:
    details: dict[str, Found] = field(default_factory=dict)
    photos: int = 0
    videos: int = 0

    def add(self, key: str, f: Found) -> None:
        if key in DETAILS and key not in self.details and filled(f.value):
            self.details[key] = f


def filled(value) -> bool:
    return str(value or "").strip().lower() not in EMPTY


def is_video(url_or_name: str, ctype: str = "") -> bool:
    s = (url_or_name or "").lower().split("?")[0]
    return (ctype or "").lower().startswith("video/") or s.endswith(VIDEO_EXT)


def is_image(url_or_name: str, ctype: str = "") -> bool:
    s = (url_or_name or "").lower().split("?")[0]
    return (ctype or "").lower().startswith("image/") or s.endswith(IMAGE_EXT)


def from_fields(ev: Evidence, fields: dict | None) -> None:
    for key, labels in FIELD_LABELS.items():
        for label in labels:
            v = (fields or {}).get(label)
            if filled(v):
                ev.add(key, Found(str(v).strip(), "field", str(v).strip()))


def _question_detail(question: str) -> str | None:
    q = (question or "").lower()
    for key in ("couldnt_inspect", "tools_materials", "time_estimate", "leak_location"):
        if any(w in q for w in QUESTION_WORDS[key]):
            return key
    return None


def from_checklist(ev: Evidence, entries: list[dict]) -> list[str]:
    """Count the media in checklist answers and take written answers for known questions.
    Returns the written answers (for the AI to read too)."""
    texts = []
    for q in entries:
        if not isinstance(q, dict):
            continue
        question = str(q.get("question") or q.get("label") or "")
        qtype = str(q.get("type") or q.get("field_type") or q.get("component") or "").upper()
        answer = q.get("answer")
        urls = [a for a in answer if isinstance(a, str) and a.startswith("http")] \
            if isinstance(answer, list) else (
                [answer] if isinstance(answer, str) and answer.startswith("http") else [])
        if urls:
            for u in urls:
                if "VIDEO" in qtype or "video" in question.lower() or is_video(u):
                    ev.videos += 1
                elif is_image(u) or "PHOTO" in qtype or "IMAGE" in qtype or "photo" in \
                        question.lower():
                    ev.photos += 1
            continue
        if isinstance(answer, list):
            answer = ", ".join(str(a) for a in answer if a)
        if not filled(answer):
            continue
        answer = str(answer).strip()
        texts.append("%s: %s" % (question, answer))
        key = _question_detail(question)
        if key:
            ev.add(key, Found(answer, "checklist", answer))
    return texts


def from_attachments(ev: Evidence, rows: list[dict]) -> None:
    for r in rows:
        name = str(r.get("file_name") or r.get("attachment_name") or r.get("name")
                   or r.get("attachment") or r.get("url") or "")
        ctype = str(r.get("mime_type") or r.get("file_type") or r.get("content_type") or "")
        if is_video(name, ctype):
            ev.videos += 1
        elif is_image(name, ctype) or str(r.get("attachment_type") or "").upper() == "IMAGE":
            ev.photos += 1


def missing(kind: str, ev: Evidence) -> list[str]:
    out = []
    for need in REQUIRED.get(kind, ()):
        if need == "photos":
            if ev.photos <= 0:
                out.append(need)
        elif need == "video":
            if ev.videos <= 0:
                out.append(need)
        elif need not in ev.details:
            out.append(need)
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


def quote_ok(quote: str, sources: str) -> bool:
    """The AI's quote must really be in what it read (ignoring case and punctuation)."""
    q = _norm(quote)
    return len(q) >= 3 and q in _norm(sources)


def words(items: list[str]) -> str:
    names = [LABEL.get(x, x) for x in items]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]
