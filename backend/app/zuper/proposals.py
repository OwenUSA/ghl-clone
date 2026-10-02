"""A signed Good / Better / Best proposal's option goes onto its job's line items (2026-10-02).

Why: Zuper's "Convert to Invoice" puts the accepted option on the INVOICE only (measured on test
job #701), so the job's line items — the one place every report reads — missed every proposal
upsell or sale. The owner chose ONE source: the job's line items. Zuper has no setting that does
this, so the CRM does it, through the narrowest write it has (`client.proposal_lines()`: one
`PUT /jobs` carrying only `job_uid`, `products` and `job_total`).

What it does, for each proposal with an accepted option it has not handled before:

- the option's lines (product, price, quantity) are ADDED to the proposal's own job — the
  job's existing lines are sent back unchanged (Zuper replaces the whole list on a save);
- not if the option is $0 (an "AHS covered" Good adds nothing), not if the job already has that
  line, and not if the job already has ANY other upgrade line (a person added it by hand) — those
  are recorded for review, never doubled;
- Job Value becomes the sum of the lines, but only when it equalled the lines before; otherwise
  it is left as it is and the row says so;
- the job is read back: the new lines must be there and every other line unchanged.

Every proposal it looks at gets ONE `zuper_proposal_lines` row, so nothing is added twice.
Off unless `ZUPER_PROPOSAL_LINES=true`. Runs after each sweep on the worker's Zuper thread.

  uv run python -m app.zuper.proposals            # DRY RUN: what it would add
  uv run python -m app.zuper.proposals --commit   # ...and add it (the switch must be on)
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..kpi_report import line_total, upgrade_tier
from ..models import ZuperProposalLine
from . import client, config, zapi

log = logging.getLogger("zuper.proposals")

TAX_EXEMPT = {"tax_rate": 0, "tax_name": "Tax Exempt", "tax_exempt": True}


def accepted_option(estimate: dict) -> dict | None:
    return next((o for o in estimate.get("proposal_options") or [] if o.get("is_accepted")), None)


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def job_line(item: dict, product: dict | None) -> dict:
    """An option line as a job line item, in the shape the job page saves (2026-10-01 load)."""
    p = product or {}
    qty = _num(item.get("quantity") or item.get("display_quantity")) or 1.0
    total = line_total(item)
    price = round(total / qty, 2) if qty else total
    category = p.get("product_category")
    return {"line_item_type": "ITEM", "product_id": item.get("product_id") or p.get("product_id"),
            "product_uid": item.get("product_uid") or p.get("product_uid"),
            "product_category": (category.get("category_uid") if isinstance(category, dict)
                                 else category),
            "product_image": "", "product_name": p.get("product_name") or item.get("name"),
            "product_description": p.get("product_description") or item.get("description") or "",
            "product_type": item.get("product_type") or p.get("product_type") or "SERVICE",
            "uom": p.get("uom") or item.get("uom") or "Job", "quantity": qty, "price": price,
            "total": total, "purchase_price": 0, "discount": 0, "discount_type": "FIXED",
            "tax": TAX_EXEMPT, "is_billable": True, "is_commissionable": True,
            "consider_profitability": True, "location_uid": None, "meta_data": [], "serial_nos": [],
            "section_type": "EXPANDED", "section_name": None, "section_uid": None}


def plan(estimate: dict, job: dict | None, products: dict[str, dict]) -> dict:
    """What to do about one signed proposal: {outcome, add, new_total, note}. Pure."""
    option = accepted_option(estimate) or {}
    lines = [i for i in option.get("line_items") or [] if line_total(i) > 0
             and (i.get("product_uid") or i.get("product_id"))]
    if not job:
        return {"outcome": "no_job", "add": [], "note": "the proposal is not linked to a job"}
    if _num(option.get("total")) <= 0 or not lines:
        return {"outcome": "zero_option", "add": [],
                "note": "the accepted option costs the customer $0"}
    existing = [p for p in job.get("products") or [] if isinstance(p, dict)]
    have = {(p.get("product_id"), round(line_total(p), 2)) for p in existing}
    if all((i.get("product_id"), round(line_total(i), 2)) in have for i in lines):
        return {"outcome": "already_on_job", "add": [], "note": "the job already has these lines"}
    if any(upgrade_tier(p) for p in existing):
        return {"outcome": "job_has_other_upgrade", "add": [],
                "note": "the job already has an upgrade line a person added - left for review"}
    add = [job_line(i, products.get(i.get("product_uid") or "")) for i in lines]
    old_sum = round(sum(line_total(p) for p in existing), 2)
    old_total = round(_num(job.get("job_total")), 2)
    added = round(sum(p["total"] for p in add), 2)
    if abs(old_total - old_sum) < 0.005:
        return {"outcome": "added", "add": add, "new_total": round(old_sum + added, 2), "note": ""}
    return {"outcome": "added", "add": add, "new_total": None,
            "note": "Job Value $%s did not equal its lines $%s - left as it was" % (
                old_total, old_sum)}


def _fingerprint(job: dict) -> list:
    keys = ("product_id", "quantity", "total")
    return sorted(json.dumps({k: p.get(k) for k in keys}, sort_keys=True)
                  for p in job.get("products") or [] if isinstance(p, dict))


def apply(estimate: dict, job: dict, decided: dict) -> str:
    """Write the new lines and read the job back. Returns "" or why it failed."""
    existing = [p for p in job.get("products") or [] if isinstance(p, dict)]
    keep = [{k: v for k, v in p.items() if k not in ("product", "product_ref_id")}
            for p in existing]
    body = {"job": {"job_uid": job["job_uid"], "products": keep + decided["add"]}}
    if decided.get("new_total") is not None:
        body["job"]["job_total"] = decided["new_total"]
    with client.proposal_lines():
        client.request("PUT", client.path("jobs"), body=body)
    time.sleep(0.5)
    after = zapi.job(job["job_uid"])
    want = sorted(_fingerprint(job) + _fingerprint({"products": decided["add"]}))
    if _fingerprint(after) != want:
        return "read-back did not match: %s" % _fingerprint(after)
    return ""


def run(db: Session, *, commit: bool = True) -> dict:
    counts: dict[str, int] = {}
    if not config.proposal_lines_enabled() and commit:
        return {"off": 1}
    done = set(db.scalars(select(ZuperProposalLine.estimate_uid)).all())
    for row in client.iter_all(client.path("estimates")):
        uid = row.get("estimate_uid")
        options = row.get("proposal_options") or []
        if not uid or uid in done or not any(o.get("is_accepted") for o in options):
            continue
        est = zapi.estimate(uid)
        if est.get("is_deleted"):
            continue
        job_ref = est.get("job") if isinstance(est.get("job"), dict) else {}
        job = zapi.job(job_ref["job_uid"]) if job_ref.get("job_uid") else None
        products = {}
        for item in (accepted_option(est) or {}).get("line_items") or []:
            puid = item.get("product_uid")
            if puid and puid not in products:
                try:
                    products[puid] = zapi._record(
                        client.request("GET", client.path("product", uid=puid)))
                except client.ZuperError:
                    products[puid] = {}
        decided = plan(est, job, products)
        outcome = decided["outcome"]
        note = decided.get("note", "")
        if outcome == "added" and commit:
            problem = apply(est, job, decided)
            if problem:
                outcome, note = "refused", problem
        counts[outcome] = counts.get(outcome, 0) + 1
        option = accepted_option(est) or {}
        log.info("proposal #%s -> job #%s: %s %s", est.get("estimate_no"),
                 job_ref.get("work_order_number"), outcome, note)
        if commit:
            db.add(ZuperProposalLine(
                estimate_uid=uid, estimate_no=str(est.get("estimate_no") or ""),
                option_name=str(option.get("option_name") or "")[:200],
                job_uid=job_ref.get("job_uid"),
                job_number=str(job_ref.get("work_order_number") or ""),
                outcome=outcome, amount=_num(option.get("total")),
                detail={"note": note, "lines": [p["product_id"] for p in decided["add"]],
                        "new_total": decided.get("new_total")}))
            db.commit()
        else:
            counts.setdefault("would", 0)
            print("proposal #%s -> job #%s: %s %s %s" % (
                est.get("estimate_no"), job_ref.get("work_order_number"), outcome,
                [(p["product_id"], p["total"]) for p in decided["add"]], note))
    return counts


def main(argv: list[str] | None = None) -> int:
    from ..db import SessionLocal
    from . import engine
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true", help="write (default: dry run)")
    args = ap.parse_args(argv)
    db = engine.mark_quiet(SessionLocal())
    try:
        with client.operator_mode():
            counts = run(db, commit=args.commit)
    finally:
        db.close()
    print(("COMMITTED " if args.commit else "DRY RUN - nothing written ") + json.dumps(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
