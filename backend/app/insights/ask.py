"""Ask for a KPI: "callbacks per technician last month", "how many said still leaking this
week vs last week", "upsell money by month this quarter". The AI only COMPOSES the fixed metrics
(`metrics.compute`) through three tools - it never writes a query - and the last table a tool
produced is the answer's result (table + chart), which can be downloaded or pinned. Every
number in the reply must appear in what the tools returned; otherwise the reply says so.
Through the Dispatch AI's connection (`provider_for(entry="insights")`)."""
from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from ..ai import providers
from ..dispatch import ai as dai
from ..dispatch import config as dc
from . import metrics

STEPS = 5
TABLES = ("technicians", "callbacks", "still_leaking", "complaints", "callback_requests",
          "upsells", "ratings")
SERIES = ("leads", "inspections", "repairs", "callbacks", "still_leaking", "upsell_paid")
SYSTEM = (
    "You answer questions about Dream Team Roofing's numbers using ONLY the tools. Ranges are "
    "day, week (Mon-Sun), month or quarter, anchored on a date (YYYY-MM-DD); 'last month' is the "
    "month before today's. Tiles: %s. Tables: %s. Series over time: %s. Use breakdown for "
    "'per technician' questions, kpis for totals and 'vs last week' (it returns the previous "
    "range too), trend for 'over time'. "
    "Say the numbers briefly; the table and chart are shown to the person. Today is {today}."
    % (", ".join(metrics.TILE_LABELS), ", ".join(TABLES), ", ".join(SERIES)))

_STR, _ENUM_R = {"type": "string"}, {"type": "string", "enum": list(metrics.RANGES)}
TOOLS = [
    providers.ToolSpec("kpis", "All tile numbers for a range, with the previous range.",
                       {"type": "object", "properties": {"range": _ENUM_R, "date": _STR,
                                                         "board": _STR, "technician": _STR},
                        "required": ["range"]}),
    providers.ToolSpec("breakdown", "A table: technicians, callbacks, still_leaking, "
                       "complaints, callback_requests, upsells or ratings (what customers "
                       "rated the finished repair, 1-5).",
                       {"type": "object", "properties": {"table": {"type": "string",
                                                                   "enum": list(TABLES)},
                                                         "range": _ENUM_R, "date": _STR,
                                                         "board": _STR, "technician": _STR},
                        "required": ["table", "range"]}),
    providers.ToolSpec("trend", "A series over time inside a range (by day, or by week for "
                       "long ranges).",
                       {"type": "object", "properties": {"series": {"type": "string",
                                                                    "enum": list(SERIES)},
                                                         "range": _ENUM_R, "date": _STR,
                                                         "board": _STR},
                        "required": ["series", "range"]}),
]


def _day(value, today: date) -> date:
    try:
        return date.fromisoformat(str(value)) if value else today
    except ValueError:
        return today


def run_spec(db: Session, spec: dict, *, hidden: set[str], today: date) -> dict:
    """A tool call -> {title, columns, rows, chart}. Also how a pinned KPI is recomputed."""
    tool, a = spec.get("tool"), spec.get("args") or {}
    rng = a.get("range") if a.get("range") in metrics.RANGES else "week"
    first, last = metrics.period(rng, _day(a.get("date"), today))
    kw = {"hidden": hidden, "board": a.get("board") or None,
          "technician": a.get("technician") or None}
    rep = metrics.compute(db, first, last, **kw)
    span = "%s to %s" % (first.isoformat(), last.isoformat())
    if tool == "kpis":
        pf, pl = metrics.previous(first, last, rng)
        prev = metrics.compute(db, pf, pl, **kw)["tiles"]
        rows = [[metrics.TILE_LABELS[k], v, prev.get(k)] for k, v in rep["tiles"].items()]
        return {"title": "KPIs, %s" % span, "columns": ["KPI", "This period", "Previous"],
                "rows": rows, "chart": "none"}
    if tool == "breakdown":
        table = a.get("table") if a.get("table") in TABLES else "technicians"
        rows = rep["tables"][table]
        cols = list(rows[0].keys()) if rows else ["(nothing in this range)"]
        chart = "bar" if table == "technicians" and rows else "none"
        return {"title": "%s, %s" % (table.replace("_", " ").capitalize(), span),
                "columns": cols, "rows": [[r.get(k) for k in cols] for r in rows],
                "chart": chart}
    if tool == "trend":
        name = a.get("series") if a.get("series") in SERIES else "leads"
        s = rep["series"].get(name, {})
        return {"title": "%s over time, %s" % (name.replace("_", " ").capitalize(), span),
                "columns": ["Period", name], "rows": [[k, v] for k, v in s.items()],
                "chart": "line"}
    return {"title": "Unknown", "columns": [], "rows": [], "chart": "none"}


NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def unchecked_numbers(reply: str, seen: str) -> list[str]:
    have = {n.replace(",", "") for n in NUM.findall(seen)}
    out = []
    for n in NUM.findall(reply or ""):
        v = n.replace(",", "")
        if v not in have and v.rstrip("0").rstrip(".") not in have and len(v) > 1:
            out.append(n)
    return out


def answer(db: Session, question: str, *, hidden: set[str], now: datetime | None = None) -> dict:
    """-> {reply, error, result, spec}."""
    now = now or datetime.now(UTC)
    today = dc.local(now).date()
    try:
        prov, model = dai.provider_for(db, now, entry="insights")
    except dai.Unavailable as e:
        return {"reply": str(e), "error": True, "result": None, "spec": None}
    convo: list[dict] = [{"role": "user", "content": question}]
    system = SYSTEM.replace("{today}", today.isoformat() + " (" + today.strftime("%A") + ")")
    result, spec, seen = None, None, ""
    for _ in range(STEPS):
        try:
            turn = prov.complete(system=system, messages=convo, tools=TOOLS, model=model,
                                 max_tokens=700)
        except providers.ProviderError as e:
            dai._record(db, "insights", model, None, error=str(e))
            db.commit()
            return {"reply": str(e), "error": True, "result": result, "spec": spec}
        dai._record(db, "insights", model, turn)
        if not turn.tool_calls:
            reply = turn.text.strip() or "Here it is."
            bad = unchecked_numbers(reply, seen + " " + system)
            if bad:
                reply += ("\n\n(These numbers are not in the data I looked up - check them: %s)"
                          % ", ".join(bad[:5]))
            db.commit()
            return {"reply": reply, "error": False, "result": result, "spec": spec}
        convo.append({"role": "assistant", "text": turn.text, "tool_calls": turn.tool_calls,
                      "raw": turn.raw})
        results = []
        for call in turn.tool_calls:
            this = {"tool": call.name, "args": call.arguments or {}}
            out = run_spec(db, this, hidden=hidden, today=today)
            if out["rows"]:
                result, spec = out, this
            text = json.dumps(out, default=str)[:12000]
            seen += text
            results.append({"id": call.id, "name": call.name, "content": text,
                            "is_error": False})
        convo.append({"role": "tool_results", "results": results})
    db.commit()
    return {"reply": "Here is what I found.", "error": False, "result": result, "spec": spec}
