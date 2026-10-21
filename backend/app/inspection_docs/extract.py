"""Find the inspection details the job fields do not hold, in what people WROTE: the notes, the
written checklist answers, the voice note's transcript, the technician's reply. The model must
return each value WITH the exact words it came from; a value whose quote is not in the sources
is thrown away, so nothing can be made up.

Uses the Dispatch AI's connection and model (`dispatch.ai.provider_for`, which applies its
switch, "Pause all AI agents", its daily cap and the registry's spend limits). A run is a
`dispatch_ai_runs` row of kind "inspection".
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from sqlalchemy.orm import Session

from ..ai import providers
from ..dispatch import ai as dai
from . import rules

KIND = "inspection"
MAX_SOURCE_CHARS = 12000
SYSTEM = (
    "You read what a roofing technician and the office wrote about ONE job after a visit. "
    "Find only these details, and only when they are written explicitly:\n"
    "  leak_location    where on the roof the leak is\n"
    "  tools_materials  tools, equipment or materials the repair needs\n"
    "  time_estimate    how long the repair will take\n"
    "  couldnt_inspect  anything that could not be inspected yet (e.g. plywood under tiles)\n"
    "Answer with JSON only: {\"leak_location\": {\"value\": \"...\", \"quote\": \"...\"}, ...}. "
    "\"quote\" must be copied word for word from the sources. Use null for anything not "
    "written. Never guess, never infer from the roof type, never invent numbers.")


def _json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def find(db: Session, sources: list[tuple[str, str]], wanted: list[str],
         now: datetime) -> tuple[dict[str, rules.Found], str | None]:
    """({detail: Found}, why it could not run). `sources` = [(label, text)]."""
    wanted = [w for w in wanted if w in rules.DETAILS]
    sources = [(label, text.strip()) for label, text in sources if text and text.strip()]
    if not wanted or not sources:
        return {}, None
    try:
        prov, model = dai.provider_for(db, now, entry="inspection_docs")
    except (dai.Unavailable, providers.ProviderError) as e:
        return {}, str(e)
    joined = "\n\n".join("[%s]\n%s" % (label, text) for label, text in sources)
    joined = joined[:MAX_SOURCE_CHARS]
    try:
        turn = prov.complete(system=SYSTEM, tools=[], model=model, max_tokens=600,
                             messages=[{"role": "user", "content": json.dumps(
                                 {"find": wanted, "sources": joined}, ensure_ascii=False)}])
    except providers.ProviderError as e:
        dai._record(db, KIND, model, None, error=e.sentence[:300])
        return {}, e.sentence
    dai._record(db, KIND, model, turn)
    out = _json(turn.text) or {}
    found: dict[str, rules.Found] = {}
    for key in wanted:
        v = out.get(key)
        if not isinstance(v, dict):
            continue
        value, quote = str(v.get("value") or "").strip(), str(v.get("quote") or "").strip()
        if not rules.filled(value) or not rules.quote_ok(quote, joined):
            continue
        label = next((lbl for lbl, text in sources if rules.quote_ok(quote, text)), "notes")
        found[key] = rules.Found(value[:500], label, quote[:500])
    return found, None
