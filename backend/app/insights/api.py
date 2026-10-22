"""/api/insights - the Insights tab (Reporting). Dispatch's audience (`dispatch.api.VIEW`: ADMIN
and unrestricted DISPATCHER; a technician or restricted user 403). Boards hidden from the reader
(pipeline permissions) are left out of every number, row, alert and pinned KPI. Read-only except
pinning a KPI; nothing is sent and nothing reaches Zuper."""
from __future__ import annotations

import csv
import io
import json
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth
from ..db import get_db
from ..dispatch import config as dc
from ..dispatch.api import VIEW, _hidden, _item
from ..models import ConversationSignal, DispatchItem, InsightSavedKpi, Role
from ..registry import service as registry
from . import ask, excel, metrics, service
from . import config as c

router = APIRouter(prefix="/api/insights", tags=["insights"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _today() -> date:
    return dc.local(datetime.now(UTC)).date()


def _range(rng: str, day: date | None) -> tuple[str, date, date]:
    if rng not in metrics.RANGES:
        raise HTTPException(422, "range must be one of %s" % ", ".join(metrics.RANGES))
    first, last = metrics.period(rng, day or _today())
    return rng, first, last


def _report(db: Session, principal: auth.Principal, rng: str, day: date | None,
            board: str | None, technician: str | None) -> tuple[dict, dict]:
    rng, first, last = _range(rng, day)
    hidden = _hidden(db, principal)
    if board and board in hidden:
        board = "\0hidden"                        # a hidden board reads as an empty one
    kw = {"hidden": hidden, "board": board or None, "technician": technician or None}
    rep = metrics.compute(db, first, last, **kw)
    pf, pl = metrics.previous(first, last, rng)
    return rep, metrics.compute(db, pf, pl, **kw)


def _pinned(db: Session, hidden: set[str]) -> list[dict]:
    out = []
    for k in db.scalars(select(InsightSavedKpi).order_by(InsightSavedKpi.position,
                                                         InsightSavedKpi.id)):
        try:
            res = ask.run_spec(db, k.spec or {}, hidden=hidden, today=_today())
        except Exception as e:  # noqa: BLE001 - a broken pin never breaks the page
            res = {"title": k.name, "columns": [], "rows": [], "chart": "none",
                   "error": str(e)[:200]}
        out.append({"id": k.id, "name": k.name, "spec": k.spec, "created_by_id":
                    k.created_by_id, "result": res})
    return out


@router.get("/report")
def insights_report(range: str = "week", date: date | None = None,
                    board: str | None = None, technician: str | None = None,
                    principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    rep, prev = _report(db, principal, range, date, board, technician)
    hidden = _hidden(db, principal)
    issues = [_item(i) for i in db.scalars(
        select(DispatchItem).where(DispatchItem.kind == service.KIND,
                                   DispatchItem.state == "open")
        .order_by(DispatchItem.created_at.desc()).limit(100))
        if not (i.board and i.board in hidden)]
    tiles = [{"key": k, "label": metrics.TILE_LABELS[k], "value": v,
              "previous": prev["tiles"].get(k), "lower_is_better": k in metrics.LOWER_IS_BETTER}
             for k, v in rep["tiles"].items()]
    st = service.settings(db)
    return {"range": range, "first": rep["first"], "last": rep["last"],
            "previous": {"first": prev["first"], "last": prev["last"]},
            "tiles": tiles, "series": rep["series"], "tables": rep["tables"],
            "issues": issues, "pinned": _pinned(db, hidden),
            "boards": sorted({b for b in _boards(db) if b not in hidden}),
            "technicians": sorted({r["technician"] for r in rep["tables"]["technicians"]}),
            "reading": {"server": service.zconfig.insights_enabled(),
                        "sentence": None if service.zconfig.insights_enabled()
                        else service.zconfig.INSIGHTS_OFF_SENTENCE,
                        "exclude_phones": len(st.exclude_phones)}}


def _boards(db: Session) -> set[str]:
    from ..models import DispatchJob
    return {b for b in db.scalars(select(DispatchJob.board).distinct()) if b}


@router.get("/report.xlsx")
def insights_excel(range: str = "week", date: date | None = None,
                   board: str | None = None, technician: str | None = None,
                   principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    rep, prev = _report(db, principal, range, date, board, technician)
    data = excel.workbook(rep, prev, label=range)
    name = "Insights %s %s to %s.xlsx" % (range, rep["first"], rep["last"])
    return StreamingResponse(io.BytesIO(data), media_type=XLSX,
                             headers={"Content-Disposition": 'attachment; filename="%s"' % name})


def _csv(columns: list, rows: list, name: str) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return StreamingResponse(io.BytesIO(buf.getvalue().encode("utf-8-sig")),
                             media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="%s"' % name})


@router.get("/table/{name}.csv")
def insights_table_csv(name: str, range: str = "week", date: date | None = None,
                       board: str | None = None, technician: str | None = None,
                       principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    if name not in ask.TABLES:
        raise HTTPException(404, "no such table")
    rep, _ = _report(db, principal, range, date, board, technician)
    rows = rep["tables"][name]
    cols = list(rows[0].keys()) if rows else ["(nothing in this period)"]
    return _csv(cols, [[r.get(k) for k in cols] for r in rows],
                "%s %s to %s.csv" % (name, rep["first"], rep["last"]))


@router.get("/signals")
def insights_signals(tag: str | None = None, range: str = "week",
                     date: date | None = None, principal: auth.Principal = VIEW,
                     db: Session = Depends(get_db)):
    if tag and tag not in c.TAGS:
        raise HTTPException(422, "unknown tag")
    _, first, last = _range(range, date)
    start, end = metrics._bounds(first, last)
    hidden = _hidden(db, principal)
    q = select(ConversationSignal).where(ConversationSignal.at >= start,
                                         ConversationSignal.at < end)
    if tag:
        q = q.where(ConversationSignal.tag == tag)
    rows = [dict(metrics._said(s), tag=s.tag, label=c.TAGS[s.tag])
            for s in db.scalars(q.order_by(ConversationSignal.at.desc()).limit(500))
            if not (s.board and s.board in hidden)]
    return {"first": first.isoformat(), "last": last.isoformat(), "signals": rows,
            "tags": c.TAGS}


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


@router.post("/ask")
def insights_ask(body: AskIn, principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    why = registry.refusal(db, c.KEY, "ai_chat")
    if why:
        return {"reply": why, "error": True, "result": None, "spec": None}
    return ask.answer(db, body.question, hidden=_hidden(db, principal))


@router.get("/result.xlsx")
def insights_result_excel(spec: str, principal: auth.Principal = VIEW,
                          db: Session = Depends(get_db)):
    """An Ask result (or a pinned KPI) as Excel: the TOOL CALL is sent, never the numbers, and
    recomputed here for this reader - hidden boards stay hidden."""
    try:
        parsed = json.loads(spec)
    except ValueError:
        raise HTTPException(422, "spec is not JSON") from None
    if not isinstance(parsed, dict) or parsed.get("tool") not in ("kpis", "breakdown", "trend"):
        raise HTTPException(422, "only a result from Ask can be downloaded")
    res = ask.run_spec(db, parsed, hidden=_hidden(db, principal), today=_today())
    return StreamingResponse(io.BytesIO(excel.result_workbook(res)), media_type=XLSX,
                             headers={"Content-Disposition": 'attachment; filename="KPI.xlsx"'})


class PinIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    spec: dict


@router.get("/saved")
def insights_saved(principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    return {"saved": _pinned(db, _hidden(db, principal))}


@router.post("/saved")
def insights_pin(body: PinIn, principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    if body.spec.get("tool") not in ("kpis", "breakdown", "trend"):
        raise HTTPException(422, "only a result from Ask can be pinned")
    last = db.scalar(select(InsightSavedKpi.position).order_by(
        InsightSavedKpi.position.desc()).limit(1)) or 0
    k = InsightSavedKpi(name=body.name.strip(), spec={"tool": body.spec["tool"],
                                                      "args": body.spec.get("args") or {}},
                        position=last + 1, created_by_id=principal.user_id)
    db.add(k)
    db.commit()
    return {"id": k.id}


@router.delete("/saved/{kpi_id}")
def insights_unpin(kpi_id: int, principal: auth.Principal = VIEW,
                   db: Session = Depends(get_db)):
    k = db.get(InsightSavedKpi, kpi_id)
    if k is None:
        raise HTTPException(404, "not found")
    if k.created_by_id != principal.user_id and principal.role != Role.ADMIN:
        raise HTTPException(403, "Only the person who pinned it, or an admin, can remove it.")
    db.delete(k)
    db.commit()
    return {"deleted": True}
