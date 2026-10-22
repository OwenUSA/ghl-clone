"""Talk to the payments agent: "Smith paid the 50% deposit, $1,500", "we quoted Jones $8,000",
"they agreed to $7,500, 50/50". A small tool loop (the Dispatch chat's shape, its own tools),
through the Dispatch AI's connection (`provider_for(entry="payments")`: switch, pause, daily
cap and the registry's limits).

Writes APPLY AT ONCE (the user's choice, 2026-10-21) through `ledger`, each a change that can be
undone. Two guards are code, not prompt:
  * an amount must be one the person typed in THIS conversation, or one already on file (the
    agreement's total, quote, or the part's own amount) - otherwise the tool refuses and the
    model has to ask;
  * a customer must resolve to exactly one agreement / job - otherwise the candidates go back.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..ai import providers
from ..dispatch import ai as dai
from ..dispatch import config as dc
from ..models import Contact, DispatchJob, PaymentAgreement, PaymentMilestone
from . import config as c
from . import ledger, rules, service

STEPS = 6
SYSTEM = (
    "You are the payments assistant of Dream Team Roofing's office. You keep track of what each "
    "customer agreed to pay, the parts (e.g. 50% before the job, 50% after), and what was paid. "
    "Use the tools to look things up and to record what the person tells you. Always find the "
    "customer first. If a name matches more than one, ask which one. Never invent an amount: "
    "use only amounts the person said or that are already on file. After a change, say "
    "briefly what you changed. Today is {today}. Answer in the person's language, briefly.")


def _spec(name, desc, props, required):
    return providers.ToolSpec(name, desc, {"type": "object", "properties": props,
                                           "required": required})


STR, INT, NUM = {"type": "string"}, {"type": "integer"}, {"type": "number"}
TOOLS = [
    _spec("find_customer", "Find agreements, Zuper jobs and contacts by name, phone or job #.",
          {"query": STR}, ["query"]),
    _spec("show_agreement", "One agreement with its parts and their status.",
          {"agreement_id": INT}, ["agreement_id"]),
    _spec("list_due", "Parts due or overdue now.", {}, []),
    _spec("create_agreement", "Record a quote or an agreement for a customer / job.",
          {"job_number": STR, "customer_name": STR, "quoted": NUM, "agreed": NUM,
           "description": STR,
           "split": {"type": "string", "description": "50/50, 30/70 or full"}}, []),
    _spec("update_agreement", "Change an agreement's quote, agreed total, description, terms or "
          "notes, or cancel it.", {"agreement_id": INT, "quoted": NUM, "agreed": NUM,
                                   "description": STR, "terms": STR, "notes": STR,
                                   "cancel": {"type": "boolean"}, "split": STR},
          ["agreement_id"]),
    _spec("record_payment", "Mark a part paid.",
          {"agreement_id": INT, "part": {"type": "string", "description": "the part's label or "
                                       "id, e.g. Deposit"}, "amount": NUM,
           "date": {"type": "string", "description": "YYYY-MM-DD, default today"},
           "method": STR}, ["agreement_id", "part"]),
    _spec("waive", "Waive a part (it will not be collected).",
          {"agreement_id": INT, "part": STR}, ["agreement_id", "part"]),
]


def _said(texts: list[str]) -> set[int]:
    """Every amount (in cents) the person typed in this conversation."""
    out = set()
    for t in texts:
        for m in re.finditer(r"\$?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(k)?\b", t or "", re.IGNORECASE):
            v = rules.cents(m.group(1))
            if v is not None:
                out.add(v * 1000 if m.group(2) else v)
    return out


def _ok_amount(value, said: set[int], on_file: set[int]) -> int | None:
    v = rules.cents(value)
    return v if v is not None and (v in said or v in on_file) else None


def _agreement_out(db: Session, a: PaymentAgreement, now: datetime) -> dict:
    s = service.settings(db)
    return {"id": a.id, "customer": a.customer_name, "job": a.job_number, "state": a.state,
            "quoted": rules.money(a.quoted_cents) if a.quoted_cents else None,
            "agreed": rules.money(a.agreed_cents) if a.agreed_cents else None,
            "terms": a.terms, "description": a.description,
            "parts": [{**p, "amount": rules.money(p["amount_cents"])}
                      for p in service.view(db, a, s, dc.local(now).date())]}


def _find(db: Session, q: str) -> dict:
    q = (q or "").strip()
    like = "%%%s%%" % q.lower()
    digits = re.sub(r"\D", "", q)
    ags = list(db.scalars(select(PaymentAgreement).where(or_(
        PaymentAgreement.customer_name.ilike(like), PaymentAgreement.job_number == q.lstrip("#"),
        PaymentAgreement.phone == digits[-10:] if len(digits) >= 10 else False)).limit(10)))
    jobs = list(db.scalars(select(DispatchJob).where(or_(
        DispatchJob.customer_name.ilike(like), DispatchJob.job_number == q.lstrip("#"))).limit(10)))
    contacts = list(db.scalars(select(Contact).where(or_(
        Contact.first_name.ilike(like), Contact.last_name.ilike(like))).limit(10)))
    return {"agreements": [{"id": a.id, "customer": a.customer_name, "job": a.job_number,
                            "state": a.state, "agreed": rules.money(a.agreed_cents)}
                           for a in ags],
            "zuper_jobs": [{"job": j.job_number, "customer": j.customer_name,
                            "board": j.board, "status": j.status} for j in jobs],
            "contacts": [{"id": x.id, "name": " ".join(p for p in (x.first_name, x.last_name)
                                                         if p)} for x in contacts]}


def _part(db: Session, a: PaymentAgreement, ref) -> PaymentMilestone | None:
    parts = ledger.milestones(db, a.id)
    if str(ref).isdigit():
        hit = [m for m in parts if m.id == int(ref)]
        if hit:
            return hit[0]
    r = str(ref or "").lower()
    hits = [m for m in parts if r and (r in m.label.lower() or m.label.lower().split()[0] in r)]
    pending = [m for m in hits if m.state == "pending"]
    return (pending or hits or [None])[0] if len(pending or hits) == 1 else None


def run_tool(db: Session, name: str, args: dict, *, said: set[int], user_id: int | None,
             now: datetime, changes: list[int]) -> str:
    try:
        if name == "find_customer":
            return json.dumps(_find(db, args.get("query", "")))
        if name == "list_due":
            s = service.settings(db)
            out = []
            for a in db.scalars(select(PaymentAgreement).where(
                    PaymentAgreement.state.in_(service.OPEN))):
                for p in service.view(db, a, s, dc.local(now).date()):
                    if p["status"] in ("due", "overdue"):
                        out.append({"agreement_id": a.id, "customer": a.customer_name,
                                    "part": p["label"], "amount": rules.money(p["amount_cents"]),
                                    "status": p["status"], "due_on": p["due_on"]})
            return json.dumps(out)
        if name == "create_agreement":
            jobs = []
            if args.get("job_number"):
                jobs = list(db.scalars(select(DispatchJob).where(
                    DispatchJob.job_number == str(args["job_number"]).lstrip("#"))))
            elif args.get("customer_name"):
                jobs = list(db.scalars(select(DispatchJob).where(
                    DispatchJob.customer_name.ilike("%%%s%%" % args["customer_name"]),
                    DispatchJob.is_open.is_(True))))
                if len(jobs) > 1:
                    return json.dumps({"refused": "more than one open job for that name - ask "
                                       "which", "candidates": [
                                           {"job": j.job_number, "customer": j.customer_name,
                                            "board": j.board} for j in jobs]})
            j = jobs[0] if len(jobs) == 1 else None
            quoted = _ok_amount(args["quoted"], said, set()) if args.get("quoted") else None
            agreed = _ok_amount(args["agreed"], said, set()) if args.get("agreed") else None
            if (args.get("quoted") and quoted is None) or (args.get("agreed") and agreed is None):
                return json.dumps({"refused": "that amount was not said by the person - ask "
                                   "them for it"})
            a, ch = ledger.create(
                db, user_id=user_id, via="chat",
                customer_name=(j.customer_name if j else None) or args.get("customer_name"),
                job_uid=j.job_uid if j else None, job_number=j.job_number if j else None,
                board=j.board if j else None,
                phone=(j.phones or [None])[0] if j else None,
                quoted_cents=quoted, agreed_cents=agreed, description=args.get("description"),
                split=args.get("split") if agreed else None)
            changes.append(ch.id)
            return json.dumps({"done": ch.summary, "agreement": _agreement_out(db, a, now)})
        a = db.get(PaymentAgreement, int(args.get("agreement_id") or 0))
        if a is None:
            return json.dumps({"refused": "no such agreement - find the customer first"})
        on_file = {x for x in (a.agreed_cents, a.quoted_cents) if x}
        if name == "show_agreement":
            return json.dumps(_agreement_out(db, a, now))
        if name == "update_agreement":
            fields = {}
            for k, col in (("quoted", "quoted_cents"), ("agreed", "agreed_cents")):
                if args.get(k) is not None:
                    v = _ok_amount(args[k], said, set())
                    if v is None:
                        return json.dumps({"refused": "that amount was not said by the person"})
                    fields[col] = v
            for k in ("description", "terms", "notes"):
                if args.get(k):
                    fields[k] = str(args[k])[:1000]
            if args.get("cancel"):
                fields["state"] = "cancelled"
            ch = ledger.update(db, a, fields, user_id=user_id, via="chat")
            if ch:
                changes.append(ch.id)
            if args.get("split"):
                ch2 = ledger.set_split(db, a, str(args["split"]), user_id=user_id, via="chat")
                changes.append(ch2.id)
            return json.dumps({"done": "updated", "agreement": _agreement_out(db, a, now)})
        m = _part(db, a, args.get("part"))
        if m is None:
            return json.dumps({"refused": "which part? (%s)" % ", ".join(
                x.label for x in ledger.milestones(db, a.id)) or "this agreement has no parts"})
        if name == "waive":
            ch = ledger.waive(db, a, m, user_id=user_id, via="chat")
            changes.append(ch.id)
            return json.dumps({"done": ch.summary})
        if name == "record_payment":
            amt = (_ok_amount(args["amount"], said, on_file | {m.amount_cents})
                   if args.get("amount") is not None else m.amount_cents)
            if amt is None:
                return json.dumps({"refused": "that amount was not said by the person and is "
                                   "not on file - ask them"})
            when = dc.local(now).date()
            if args.get("date"):
                try:
                    when = date.fromisoformat(str(args["date"]))
                except ValueError:
                    return json.dumps({"refused": "the date must be YYYY-MM-DD"})
            ch = ledger.record_payment(db, a, m, amount_cents=amt, paid_on=when,
                                       method=args.get("method"), user_id=user_id, via="chat",
                                       source="chat")
            changes.append(ch.id)
            return json.dumps({"done": ch.summary, "agreement": _agreement_out(db, a, now)})
        return json.dumps({"error": "unknown tool"})
    except ledger.Invalid as e:
        db.rollback()
        return json.dumps({"refused": str(e)})


def answer(db: Session, history: list[dict], *, user_id: int | None,
           now: datetime | None = None) -> dict:
    """history: [{role, text}] oldest first, the last one the new question.
    -> {reply, error, steps, change_ids}."""
    now = now or datetime.now(UTC)
    said = _said([h["text"] for h in history if h["role"] == "user"])
    steps: list[dict] = []
    changes: list[int] = []
    try:
        prov, model = dai.provider_for(db, now, entry=c.KEY)
    except dai.Unavailable as e:
        return {"reply": str(e), "error": True, "steps": [], "change_ids": []}
    system = SYSTEM.format(today=dc.local(now).strftime("%A %B %d, %Y"))
    convo = [{"role": "user", "content": h["text"]} if h["role"] == "user" else
             {"role": "assistant", "text": h["text"]} for h in history]
    for _ in range(STEPS):
        try:
            turn = prov.complete(system=system, messages=convo, tools=TOOLS, model=model,
                                 max_tokens=800)
        except providers.ProviderError as e:
            dai._record(db, "payments", model, None, user_id=user_id, error=str(e))
            db.commit()
            return {"reply": str(e), "error": True, "steps": steps, "change_ids": changes}
        dai._record(db, "payments", model, turn, user_id=user_id)
        if not turn.tool_calls:
            db.commit()
            return {"reply": turn.text.strip() or "Done.", "error": False, "steps": steps,
                    "change_ids": changes}
        convo.append({"role": "assistant", "text": turn.text, "tool_calls": turn.tool_calls,
                      "raw": turn.raw})
        results = []
        for call in turn.tool_calls:
            out = run_tool(db, call.name, call.arguments or {}, said=said, user_id=user_id,
                           now=now, changes=changes) if call.arguments is not None \
                else json.dumps({"error": "bad arguments"})
            db.commit()                     # each change kept as it succeeds (a later refusal
            #                                 rolls back only itself)
            steps.append({"tool": call.name, "args": call.arguments or {}, "result": out[:2000]})
            results.append({"id": call.id, "name": call.name, "content": out, "is_error": False})
        convo.append({"role": "tool_results", "results": results})
    db.commit()
    return {"reply": "I stopped after several steps; here is what I did so far.",
            "error": False, "steps": steps, "change_ids": changes}
