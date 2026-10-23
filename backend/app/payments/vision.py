"""Does a picture a customer texted look like a payment? ONE vision call per picture, through the
agent's vision connection (OpenAI / Anthropic - the post-inspection report's connection code).
The answer only ever leads to a proof "to review" and a suggestion; nothing is marked paid."""
from __future__ import annotations

import json
import re
from datetime import date

from sqlalchemy.orm import Session

from ..ai import providers
from ..ai.providers import REASONING_ROOM
from ..inspection_report import photos as rphotos
from ..inspection_report import vision as rvision
from . import config as c
from . import rules

SYSTEM = (
    "You look at ONE picture a roofing company's customer texted. Decide whether it is proof of "
    "a payment to the company (a Zelle / Venmo / Cash App / bank transfer confirmation, a "
    "check, a card receipt). Answer with JSON only: {\"looks_like_payment\": true|false, "
    "\"amount\": \"1500.00\" or null, \"date\": \"YYYY-MM-DD\" or null, \"payer\": \"...\" or "
    "null, \"app\": \"Zelle\" or null}. Read the amount and date exactly as shown; never guess.")


def read(db: Session, s: c.Settings, data: bytes) -> dict:
    """{looks_like_payment, amount_cents, date, payer, app, model, input_tokens, output_tokens}
    - raises rvision.Unavailable when no usable connection or the model did not answer."""
    rs = rvision.c.Settings(vision_connection_id=s.vision_connection_id,
                            vision_model=s.vision_model)
    conn, prov, model = rvision._connection(db, rs)
    jpeg = rphotos.to_jpeg(data, 1024)
    try:
        turn = prov.complete(system=SYSTEM, tools=[], model=model,
                             max_tokens=REASONING_ROOM, messages=[
            {"role": "user", "content": [rvision._image_block(conn.provider, jpeg)]}])
    except providers.ProviderError as e:
        raise rvision.Unavailable("the vision model did not answer: %s" % e.sentence) from None
    m = re.search(r"\{.*\}", turn.text or "", re.DOTALL)
    try:
        out = json.loads(m.group(0)) if m else {}
    except ValueError:
        out = {}
    when = None
    try:
        when = date.fromisoformat(str(out.get("date"))) if out.get("date") else None
    except ValueError:
        when = None
    return {"looks_like_payment": out.get("looks_like_payment") is True,
            "amount_cents": rules.cents(out.get("amount")) if out.get("amount") else None,
            "date": when.isoformat() if when else None,
            "payer": str(out.get("payer") or "")[:120] or None,
            "app": str(out.get("app") or "")[:40] or None,
            "model": model, "input_tokens": turn.usage.input_tokens,
            "output_tokens": turn.usage.output_tokens}
