"""ONE call to a vision model: it sees the visit's pictures and the job's written findings, picks
the pictures that best show the problem, captions them, and writes the report's text in the
customer's language. The answer goes through `rules.accept` - nothing unchecked reaches a draft.

The connection is the agent's setting `vision_connection_id`: an OpenAI or Anthropic connection
(DeepSeek cannot read images). Tokens and cost come back so the registry can count the spend.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..ai import pricing, providers, vault
from ..models import AiConnection
from . import config as c
from . import rules

SYSTEM = (
    "You prepare a roof inspection report for a homeowner, for Dream Team Roofing. You are given "
    "the inspection pictures (numbered from 0) and what the technician wrote. Pick the {n} "
    "pictures that best show the roof's condition and the problem (the leak area first), skip "
    "blurry, duplicate or unrelated ones, and write a one-line caption for each. Then write a "
    "short title, a summary of 2-4 sentences and 3-6 findings, in plain words a homeowner "
    "understands, in {language}.\n"
    "Rules: describe only what the pictures and the notes show. Never mention prices, money, "
    "insurance or AHS approval, never promise a date or a result, never invent measurements.\n"
    "This text goes to the HOMEOWNER: describe only the roof. Never mention the technician, the "
    "notes, the job, the work order, what was booked or requested (e.g. gutters), our systems, or "
    "what was or was not documented - and never compare the notes with the pictures.\n"
    "Finally pick 1-3 GUIDES, by key, that explain the findings to the homeowner (you never "
    "write guide text; only choose keys from this list):\n{guides}\n"
    "Answer with JSON only: {{\"photos\": [{{\"index\": 0, \"caption\": \"...\"}}], "
    "\"title\": \"...\", \"summary\": \"...\", \"findings\": [\"...\"], "
    "\"guides\": [\"key\"]}}")
LANG = {"en": "English", "es": "Spanish"}


class Unavailable(Exception):
    """No usable vision connection, or the model did not answer."""


@dataclass
class Result:
    draft: rules.Draft
    model: str
    input_tokens: int
    output_tokens: int
    cost_micros: int | None


def _connection(db: Session, s: c.Settings) -> tuple[AiConnection, providers.Provider, str]:
    conn = db.get(AiConnection, s.vision_connection_id) if s.vision_connection_id else None
    if conn is None:
        raise Unavailable("no vision connection is chosen (AI Agents -> Overview -> "
                          "Post-inspection report -> Settings)")
    if conn.provider not in ("openai", "anthropic"):
        raise Unavailable("the %s connection cannot read pictures; choose an OpenAI or "
                          "Anthropic connection" % conn.provider)
    try:
        prov = providers.build(conn.provider, vault.decrypt(conn.api_key_encrypted),
                               conn.base_url)
    except (vault.SecretsUnavailable, providers.ProviderError) as e:
        raise Unavailable(str(e)) from None
    return conn, prov, (s.vision_model or conn.default_model)


def _image_block(provider: str, jpeg: bytes) -> dict:
    b64 = base64.b64encode(jpeg).decode()
    if provider == "anthropic":
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": b64}}
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64,
                                               "detail": "low"}}


def _json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def write(db: Session, s: c.Settings, pictures: list[bytes], written: str,
          language: str) -> Result:
    conn, prov, model = _connection(db, s)
    content: list[dict] = [{"type": "text", "text": "What the technician wrote:\n%s" % (
        written.strip() or "(nothing written)")}]
    for i, jpeg in enumerate(pictures):
        content.append({"type": "text", "text": "Picture %d:" % i})
        content.append(_image_block(conn.provider, jpeg))
    from .guides import catalog
    system = SYSTEM.format(n=min(s.max_photos, len(pictures)),
                           language=LANG.get(language, "English"),
                           guides="\n".join("  %s: %s" % (g["key"], g["about"])
                                            for g in catalog()))
    try:
        turn = prov.complete(system=system, tools=[], model=model, max_tokens=1500,
                             messages=[{"role": "user", "content": content}])
    except providers.ProviderError as e:
        raise Unavailable("the vision model did not answer: %s" % e.sentence) from None
    draft = rules.accept(_json(turn.text), candidates=len(pictures), max_photos=s.max_photos)
    if draft is None:
        raise Unavailable("the vision model's answer had no usable pictures")
    cost = pricing.cost_micros(conn.provider, conn.price_input_micros, conn.price_output_micros,
                               input_tokens=turn.usage.input_tokens,
                               output_tokens=turn.usage.output_tokens)
    return Result(draft, model, turn.usage.input_tokens, turn.usage.output_tokens, cost)
