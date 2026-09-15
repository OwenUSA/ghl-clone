"""Knowledge bases: what an agent may know about the business, and what it could not answer.

* Items are FAQs (question + answer), articles (title + text) and files (PDF via `pypdf`,
  Word .docx via `python-docx`). A file keeps its extracted text, name, type and size —
  NOT its bytes: this CRM has no file store to keep them in.
* Every item is cut into chunks. Retrieval is LEXICAL and runs the same on SQLite and
  PostgreSQL: each chunk stores its normalised words, a query's words pre-filter candidate
  chunks in SQL, and a BM25-style score ranks them in Python. `Retriever` is the seam an
  embeddings retriever plugs into later without touching the engine.
* A search is always scoped to the knowledge bases ATTACHED to the agent running it.
* A KNOWLEDGE GAP is recorded when a search finds nothing useful, or the model reports one:
  deduplicated on the normalised question, with a count, when it was last seen and the run
  that saw it. Resolving a gap writes the FAQ; dismissing it keeps it out of the list.

Customer facts are NOT in knowledge bases — they come from the CRM through `get_context`.
"""
from __future__ import annotations

import io
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import AiKbChunk, AiKbItem, AiKnowledgeGap

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 500_000
# "Nothing useful" = no chunk contains at least this share of the question's words (after
# stop words): the agent then gets no result and a gap is recorded. A share rather than a
# BM25 score, because a score's scale moves with the size of the knowledge base, and one
# shared word out of five is not an answer.
MIN_COVERAGE = 0.5

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_STOP = frozenset((  # noqa: SIM905 - a word list reads better as prose than 150 literals
    "a about above after again against all am an and any are as at be because been before "
    "being below between both but by can could did do does doing down during each few for "
    "from further had has have having he her here hers herself him himself his how i if in "
    "into is it its itself just me more most my myself no nor not now of off on once only or "
    "other our ours ourselves out over own same she should so some such than that the their "
    "theirs them themselves then there these they this those through to too under until up "
    "very was we were what when where which while who whom why will with would you your "
    "yours yourself yourselves please hi hello thanks thank ok okay yes yeah").split())
_WORD = re.compile(r"[a-z0-9]+")


def _stem(word: str) -> str:
    """Just enough folding that "roofs" finds "roof" and "leaking" finds "leak"."""
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def terms(text: str) -> list[str]:
    return [_stem(w) for w in _WORD.findall((text or "").lower()) if w not in _STOP]


def terms_field(text: str) -> str:
    """How a chunk's words are stored: space-padded so `LIKE '% word %'` is a whole-word
    match on every database."""
    return " " + " ".join(terms(text)) + " "


def chunk_text(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", (text or "").replace("\r", "")).strip()
    if not text:
        return []
    if len(text) <= CHUNK_CHARS:
        return [text]
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        if end < len(text):
            # Prefer to cut at a paragraph, then a sentence, then a space.
            window = text[start:end]
            for sep in ("\n\n", ". ", "\n", " "):
                cut = window.rfind(sep)
                if cut > CHUNK_CHARS // 2:
                    end = start + cut + len(sep)
                    break
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return [c for c in out if c]


class ExtractionError(ValueError):
    pass


def extract_text(filename: str, content_type: str | None, data: bytes) -> tuple[str, str]:
    """(text, content type) from a PDF or a .docx. Anything else is refused with a sentence."""
    if len(data) > MAX_FILE_BYTES:
        raise ExtractionError("that file is larger than %d MB" % (MAX_FILE_BYTES // 1024 // 1024))
    name = (filename or "").lower()
    if name.endswith(".pdf") or content_type == PDF or data[:5] == b"%PDF-":
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        except (PdfReadError, ValueError, KeyError, TypeError) as e:
            raise ExtractionError("that PDF could not be read (%s)" % type(e).__name__) from None
        kind = PDF
    elif name.endswith(".docx") or content_type == DOCX:
        import zipfile

        import docx
        try:
            document = docx.Document(io.BytesIO(data))
        except (zipfile.BadZipFile, ValueError, KeyError) as e:
            raise ExtractionError(
                "that Word file could not be read (%s)" % type(e).__name__) from None
        parts = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        text = "\n".join(parts)
        kind = DOCX
    else:
        raise ExtractionError("only PDF and Word (.docx) files can be added — "
                              "paste other text as an article")
    text = text.strip()
    if not text:
        raise ExtractionError("no text could be read from that file (a scanned image has "
                              "no text to extract)")
    return text[:MAX_TEXT_CHARS], kind


def item_text(item: AiKbItem) -> str:
    if item.kind == AiKbItem.FAQ:
        return "Q: %s\nA: %s" % (item.title, item.body)
    if item.kind == AiKbItem.ARTICLE:
        return "%s\n\n%s" % (item.title, item.body)
    return item.body


def reindex(db: Session, item: AiKbItem) -> int:
    """Replace an item's chunks. Returns how many it now has."""
    for old in db.scalars(select(AiKbChunk).where(AiKbChunk.item_id == item.id)).all():
        db.delete(old)
    db.flush()
    pieces = chunk_text(item_text(item))
    for i, piece in enumerate(pieces):
        # A file's name is searchable in every one of its chunks.
        indexed = piece if item.kind != AiKbItem.FILE else item.title + "\n" + piece
        db.add(AiKbChunk(item_id=item.id, kb_id=item.kb_id, position=i, text=piece,
                         terms=terms_field(indexed)))
    db.flush()
    return len(pieces)


@dataclass
class Hit:
    item_id: int
    kb_id: int
    title: str
    kind: str
    text: str
    score: float
    # The share of the question's words this chunk contains.
    coverage: float = 0.0


class Retriever(Protocol):
    def search(self, db: Session, kb_ids: list[int], query: str, limit: int = 5
               ) -> list[Hit]: ...


class LexicalRetriever:
    """BM25 over the candidate chunks that share at least one word with the query."""

    k1 = 1.2
    b = 0.75

    def search(self, db: Session, kb_ids: list[int], query: str, limit: int = 5) -> list[Hit]:
        q = list(dict.fromkeys(terms(query)))[:24]
        if not kb_ids or not q:
            return []
        scope = AiKbChunk.kb_id.in_(kb_ids)
        total = db.query(AiKbChunk.id).filter(scope).count()
        if total == 0:
            return []
        like = [AiKbChunk.terms.like("% " + t + " %") for t in q]
        rows = db.execute(select(AiKbChunk, AiKbItem).join(
            AiKbItem, AiKbItem.id == AiKbChunk.item_id).where(scope, or_(*like))
            .limit(500)).all()
        if not rows:
            return []
        docs = [(c, i, c.terms.split()) for c, i in rows]
        avg = sum(len(d) for _, _, d in docs) / len(docs) or 1
        df = {t: sum(1 for _, _, d in docs if t in d) for t in q}
        scored = []
        for chunk, item, words in docs:
            tf = Counter(words)
            s = 0.0
            for t in q:
                if not tf[t]:
                    continue
                idf = math.log(1 + (total - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (self.k1 + 1) / (
                    tf[t] + self.k1 * (1 - self.b + self.b * len(words) / avg))
            coverage = sum(1 for t in q if tf[t]) / len(q)
            # Matching more of the question matters more than repeating one word.
            s = (s + 0.001) * (coverage + 0.5)
            scored.append(Hit(item.id, item.kb_id, item.title, item.kind, chunk.text, s,
                              coverage))
        scored.sort(key=lambda h: (-h.score, h.item_id))
        best, seen = [], set()
        for h in scored:
            if h.item_id in seen:
                continue
            seen.add(h.item_id)
            best.append(h)
            if len(best) >= limit:
                break
        return best


RETRIEVER: Retriever = LexicalRetriever()


def useful(hits: list[Hit]) -> bool:
    return any(h.coverage >= MIN_COVERAGE for h in hits)


def question_key(question: str) -> str:
    words = _WORD.findall((question or "").lower())
    return " ".join(words)[:500]


def record_gap(db: Session, question: str, *, agent_id: int | None, run_id: int | None
               ) -> AiKnowledgeGap | None:
    """Count one sighting of a question. Deduplicated on the normalised wording; a
    resolved or dismissed gap keeps its status and only its count and dates move."""
    question = (question or "").strip()[:2000]
    key = question_key(question)
    if not key:
        return None
    now = datetime.now(UTC)
    gap = db.scalar(select(AiKnowledgeGap).where(AiKnowledgeGap.question_key == key))
    if gap is None:
        gap = AiKnowledgeGap(question_key=key, question=question, count=1, agent_id=agent_id,
                             last_run_id=run_id, first_seen_at=now, last_seen_at=now)
        db.add(gap)
    else:
        gap.count += 1
        gap.last_seen_at = now
        gap.last_run_id = run_id
        gap.agent_id = agent_id
    db.flush()
    return gap
