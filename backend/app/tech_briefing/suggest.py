"""Suggested tools & materials for the jobs whose inspection recorded none - ONE call per
briefing, through the Dispatch AI's connection (`dispatch.ai.provider_for`: its switch, "Pause all
AI agents", its daily cap and the registry's limits apply). A suggestion is only ever shown as
"suggested - check the inspection"; what the technician recorded always wins. Anything long or
naming money is dropped."""
from __future__ import annotations

import json
import re
from datetime import datetime

from sqlalchemy.orm import Session

from ..ai import providers
from ..ai.providers import REASONING_ROOM
from ..dispatch import ai as dai

KIND = "briefing"
MAX_CHARS = 160
BAD = re.compile(r"\$|\bprice|\bcost|\bdollar", re.IGNORECASE)
SYSTEM = (
    "You help a roofing technician pack for tomorrow. For each job you get the roof type, where "
    "the leak is, what could not be inspected, and the job title. Suggest the typical tools and "
    "materials for that kind of repair, as a short comma-separated list (at most 10 items). "
    "Do not name prices, do not invent quantities or measurements, do not guess the cause. "
    "Answer with JSON only: {\"<job number>\": \"item, item, ...\"}. Omit a job you cannot help "
    "with.")


def suggest(db: Session, jobs: list[dict], now: datetime) -> tuple[dict[str, str], dict]:
    """({job_number: suggestion}, {model, input_tokens, output_tokens, note})."""
    meta = {"model": None, "input_tokens": 0, "output_tokens": 0, "note": None}
    if not jobs:
        return {}, meta
    try:
        prov, model = dai.provider_for(db, now, entry="tech_briefing")
    except (dai.Unavailable, providers.ProviderError) as e:
        meta["note"] = "no suggestions: %s" % e
        return {}, meta
    meta["model"] = model
    try:
        turn = prov.complete(system=SYSTEM, tools=[], model=model, max_tokens=REASONING_ROOM,
                             messages=[{"role": "user", "content": json.dumps(
                                 jobs, ensure_ascii=False)}])
    except providers.ProviderError as e:
        dai._record(db, KIND, model, None, error=e.sentence[:300])
        meta["note"] = "no suggestions: %s" % e.sentence
        return {}, meta
    dai._record(db, KIND, model, turn)
    meta["input_tokens"], meta["output_tokens"] = turn.usage.input_tokens, \
        turn.usage.output_tokens
    m = re.search(r"\{.*\}", turn.text or "", re.DOTALL)
    try:
        raw = json.loads(m.group(0)) if m else {}
    except ValueError:
        raw = {}
    wanted = {str(j["job_number"]) for j in jobs}
    out = {}
    for k, v in (raw if isinstance(raw, dict) else {}).items():
        v = " ".join(str(v or "").split())
        if str(k) in wanted and v and len(v) <= MAX_CHARS and not BAD.search(v):
            out[str(k)] = v
    return out, meta
