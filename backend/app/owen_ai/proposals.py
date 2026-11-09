"""Owen AI's Good/Better/Best proposal drafts, approved by the owner (2026-11-09).

OWNER DECISION (2026-11-09, a scoped lift of "Zuper is read-only"): Owen AI prepares a proposal
for a job; the owner sees it on the Owen AI page (Proposals tab) with Approve / Reject. Only on
Approve does the server create it in Zuper as a DRAFT — or fill the empty draft Zuper made for
that job. It is NEVER sent, and it carries no banner pictures (no option_image).

  * `propose_proposal_draft` (the tool Claude calls) validates the spec strictly, READS Zuper
    (the job, its customer, the template, the job's proposals) and stores a PENDING row. It
    writes nothing to Zuper.
  * `approve` (POST /api/owen-ai/proposals/{id}/approve, the owner only) claims the row
    pending -> creating in one UPDATE (a double click cannot create two), checks the server gate
    and the registry BEFORE any request, then inside `client.owen_ai_proposal_write()` sends ONE
    POST /estimate (new) or ONE PUT /estimate/{uid} (fill, only the uid the row recorded), reads
    it back and checks it is a DRAFT whose option totals match. Anything wrong -> failed, with
    Zuper's message. Never retried automatically.
  * It runs inside the API request, not a queued job: it is one write and two or three reads
    (well under the request timeout), the owner sees the outcome at once, and the row's atomic
    claim is what makes it safe — a queue would add a worker dependency and a second place a
    retry could creep in.
  * Approve and reject each post a short `system` message into the thread the draft came from;
    the runner hands those to Claude with the owner's next message.

The body is the one PROVEN live on the TEST customer (dispatch:/opt/ops/zuper/zbrowser/nextmap/
proposal/gbb_create.py + gbb_scoped.py -> DTR-154 / DTR-155, 2026-11-07): one ITEM line per
option on the template's DTR package product, quantity 1, price = the option's price, deposit
50% unless given, the scope as the line's HTML description (Luis's convention), option title in
`option_description`. Product uid/code come from the TEMPLATE, never from Claude.
"""
from __future__ import annotations

import html
import math
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import OwenAiMessage, OwenAiProposalDraft, OwenAiThread
from ..registry import service as registry
from ..zuper import client as zc
from ..zuper import config as zuper_config
from . import config, dossier

TZ = ZoneInfo("America/New_York")
ACTION = "zuper_proposals"            # registry action on the owen_ai entry

# The DTR templates (verified live, read-only, 2026-11-09). key -> template_uid.
# Owner (2026-11-09): EVERY draft uses the 5th template, "DTR - Tile Leak Repair - Short Proposal
# (Good / Better / Best)" - the Tile options on the short layout ("DTR Proposal - Short": no logo,
# financing, CertainTeed, comparison or letter pages). The others stay listed for reading old
# proposals and for the fill check, but the tool always drafts on SHORT.
SHORT = "short"
TEMPLATES = {
    SHORT: "0e5d788e-656e-40f5-a4af-cb03ffc92fc0",
    "flat": "784d8985-ef0d-4d49-ae08-113a8c9b7ff9",
    "tile": "eb322d43-0d4b-4d36-93de-2ac528a8d25e",
    "replacement": "06ce5f36-651f-4009-b34c-1194a06cb8c8",
    "custom": "7c697c78-8760-4049-93b8-9a4050c9eeab",
}
QUOTE_TEMPLATE_UID = "d979180c-8a39-4bfc-802f-2cbcc20d64eb"     # "Quote Template" (122 of 128)
DEFAULT_LAYOUT_UID = "e9b8add9-8fb8-44a4-9406-d8b9b7bec422"     # "DTR Proposal"
PREFIX = "DTR-"
EXPIRY_DAYS = 30
OPTION_NAMES = ("GOOD", "BETTER", "BEST")
STATUSES = ("pending", "approved", "creating", "created", "rejected", "failed")
PROPOSALS_READ_MAX = 10       # the job's newest proposals read in full
PRICE_MAX = 500_000
# Text a template's own placeholder description carries (live: DTR-CUSTOM-* products).
PLACEHOLDER_MARKERS = ("(write the scope", "(state warranty")
# Owner, 2026-11-09: every option's workmanship warranty is 1 month by default (Good, Better and
# Best alike). Claude uses another period only when Owen gives one for that proposal.
DEFAULT_WARRANTY = ("Workmanship Warranty: 1 Month This warranty covers workmanship on the repair "
                    "work in this option for 1 month from completion.")
DASH = chr(0x2013)  # the en dash Luis's payment terms use
WAITING = ("Waiting for Owen to approve on the Proposals tab — nothing was created in Zuper "
           "yet.")

# The Zuper user the proposal is "sold by" and signed internally by (Owen). Env overrides.
SIGNER_UID = "74cd936c-9386-469b-addc-d05d3d1a06a0"
SIGNER_NAME = "Owen Buzaglo"
SIGNER_EMAIL = "owen@dreamteamroofingfl.com"

# The thread the runner is answering right now (one message at a time, by design): the tool
# records it so approve / reject can tell that conversation what happened.
CURRENT_THREAD: int | None = None


class SpecError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt):
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


def signer() -> dict:
    return {"uid": (os.getenv("OWEN_AI_PROPOSAL_SIGNER_UID") or SIGNER_UID).strip(),
            "name": (os.getenv("OWEN_AI_PROPOSAL_SIGNER_NAME") or SIGNER_NAME).strip(),
            "email": (os.getenv("OWEN_AI_PROPOSAL_SIGNER_EMAIL") or SIGNER_EMAIL).strip()}


def estimate_url(uid: str | None) -> str | None:
    """The proposal in Zuper's web app. UNVERIFIED path (next.zuperpro.com lists Quotes at
    /estimates); OWEN_AI_PROPOSAL_URL_TEMPLATE overrides, empty = no link."""
    tpl = os.getenv("OWEN_AI_PROPOSAL_URL_TEMPLATE")
    tpl = "https://next.zuperpro.com/estimates/{uid}/details" if tpl is None else tpl.strip()
    return tpl.format(uid=uid) if uid and tpl else None


# ------------------------------------------------------------------------------ text

_TAG_OK = re.compile(r"&lt;(/?)(b|i|ul|li|p)&gt;", re.IGNORECASE)
_BR_OK = re.compile(r"&lt;br\s*/?&gt;", re.IGNORECASE)


def sanitize(value: str) -> str:
    """Everything escaped except bare <b>, <i>, <ul>, <li>, <p>, <br> (no attributes).
    <strong>/<em> are taken as <b>/<i> (Zuper's own descriptions use them)."""
    s = re.sub(r"<(/?)strong>", r"<\1b>", str(value), flags=re.IGNORECASE)
    s = re.sub(r"<(/?)em>", r"<\1i>", s, flags=re.IGNORECASE)
    # Decode first: Claude often sends "&amp;" already, and escaping it again showed
    # "Prep &amp; Access" in Zuper (2026-11-09).
    s = html.escape(html.unescape(s), quote=False)
    s = _TAG_OK.sub(lambda m: "<%s%s>" % (m.group(1), m.group(2).lower()), s)
    return _BR_OK.sub("<br>", s)


def plain(value: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def money(v: float) -> str:
    return ("$%s" % format(round(v), ",")) if abs(v - round(v)) < 0.005 \
        else "$%s" % format(v, ",.2f")


def description(scope_html: str, warranty: str, price: float, deposit: float,
                payment_terms: str | None) -> str:
    """Luis's convention: the price block and scope bullets (Claude's HTML, sanitized), then the
    warranty and payment terms — each added only when the scope does not already say it."""
    out = sanitize(scope_html).strip()
    text = plain(out).lower()
    w = warranty.strip()
    if w.lower() not in text:
        if not w.lower().startswith("workmanship warranty"):
            w = "Workmanship Warranty: " + w
        out += "<p>%s</p>" % sanitize(w)
    if "payment terms" not in text:
        rest = money(round(price - deposit, 2))
        terms = (payment_terms or "").strip() or (
            "50%% Upfront (%s) %s due upon acceptance of proposal. 50%% Upon Completion (%s) %s "
            "due after final inspection and customer approval." % (
                money(deposit), DASH, rest, DASH))
        if price and abs(deposit - price * 0.5) > 0.005 and not (payment_terms or "").strip():
            terms = ("Deposit %s %s due upon acceptance of proposal. Balance %s %s due after "
                     "final inspection and customer approval." % (money(deposit), DASH, rest,
                                                                  DASH))
        out += "<p>Payment Terms: %s</p>" % sanitize(terms)
    return out


# ------------------------------------------------------------------------------ the spec

TOP_KEYS = {"job", "template", "title", "options", "allow_second", "note"}
OPTION_KEYS = {"name", "title", "price", "deposit", "recommended", "warranty", "scope_html",
               "payment_terms"}


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _text(d: dict, key: str, where: str, problems: list[str], max_len: int,
          required: bool = True) -> str | None:
    v = d.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v.strip():
        problems.append("%s.%s must be a non-empty string" % (where, key))
        return None
    if len(v) > max_len:
        problems.append("%s.%s is longer than %d characters" % (where, key, max_len))
        return None
    return v.strip()


def validate(spec: Any) -> dict:
    """The spec Claude sent -> a clean dict, or SpecError listing every problem."""
    problems: list[str] = []
    if not isinstance(spec, dict):
        raise SpecError(["the spec must be an object"])
    extra = set(spec) - TOP_KEYS
    if extra:
        problems.append("unknown keys: %s" % ", ".join(sorted(extra)))
    job = spec.get("job")
    job = str(job).strip() if isinstance(job, (str, int)) and str(job).strip() else None
    if not job:
        problems.append("job is required (a job number like 701, or the job uid)")
    template = spec.get("template")
    if template not in (None, *TEMPLATES):
        problems.append("template must be one of %s" % ", ".join(TEMPLATES))
    template = SHORT                               # owner's rule: always the Short template
    title = _text(spec, "title", "spec", problems, 200)
    allow_second = spec.get("allow_second", False)
    if not isinstance(allow_second, bool):
        problems.append("allow_second must be true or false")
    note = _text(spec, "note", "spec", problems, 1000, required=False)
    raw = spec.get("options")
    options: list[dict] = []
    if not isinstance(raw, list) or not 1 <= len(raw) <= 3:
        problems.append("options must be a list of 1 to 3 options")
        raw = raw if isinstance(raw, list) else []
    seen = set()
    for i, o in enumerate(raw[:3]):
        where = "options[%d]" % i
        if not isinstance(o, dict):
            problems.append("%s must be an object" % where)
            continue
        bad = set(o) - OPTION_KEYS
        if bad:
            problems.append("%s: unknown keys: %s" % (where, ", ".join(sorted(bad))))
        name = o.get("name")
        if name not in OPTION_NAMES:
            problems.append("%s.name must be GOOD, BETTER or BEST" % where)
        elif name in seen:
            problems.append("%s.name %s is used twice" % (where, name))
        seen.add(name)
        price = _num(o.get("price"))
        if price is None or price < 0 or price > PRICE_MAX:
            problems.append("%s.price must be a number from 0 to %d" % (where, PRICE_MAX))
        deposit = o.get("deposit")
        dep = None
        if deposit is not None:
            dep = _num(deposit)
            if dep is None or dep < 0 or (price is not None and dep > price):
                problems.append("%s.deposit must be a number from 0 to the price" % where)
        rec = o.get("recommended", None)
        if rec is not None and not isinstance(rec, bool):
            problems.append("%s.recommended must be true or false" % where)
        otitle = _text(o, "title", where, problems, 200)
        warranty = _text(o, "warranty", where, problems, 400, required=False) or DEFAULT_WARRANTY
        scope = _text(o, "scope_html", where, problems, 8000)
        terms = _text(o, "payment_terms", where, problems, 600, required=False)
        if price is None or not otitle or not warranty or not scope or name not in OPTION_NAMES:
            continue
        price = round(price, 2)
        dep = round(price * 0.5, 2) if dep is None else round(dep, 2)
        options.append({"name": name, "option_description": plain(sanitize(otitle))[:200],
                        "is_recommended": rec, "price": price, "deposit": dep,
                        "warranty": plain(sanitize(warranty)),
                        "scope_html": description(scope, warranty, price, dep, terms)})
    if sum(1 for o in options if o["is_recommended"]) > 1:
        problems.append("only one option can be recommended")
    if problems:
        raise SpecError(problems)
    options.sort(key=lambda o: OPTION_NAMES.index(o["name"]))
    if not any(o["is_recommended"] for o in options):
        best = next((o for o in options if o["name"] == "BEST"), None)
        if best is not None and len(options) > 1:
            best["is_recommended"] = True
    for o in options:
        o["is_recommended"] = bool(o["is_recommended"])
    return {"job": job, "template": template, "title": plain(sanitize(title))[:200],
            "options": options, "allow_second": allow_second, "note": note}


# ------------------------------------------------------------------------------ reading Zuper

def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def template_products(tpl: dict) -> dict[str, dict]:
    """GOOD/BETTER/BEST -> the DTR package product on that option's ITEM line (from the
    template record, GET /invoice_estimate/proposal_template/{uid}, where `product` is the
    whole product)."""
    out = {}
    for o in tpl.get("proposal_options") or []:
        name = str(_dict(o).get("option_name") or "").upper()
        if name not in OPTION_NAMES or name in out:
            continue
        for li in o.get("line_items") or []:
            p = _dict(_dict(li).get("product"))
            if li.get("line_item_type") == "ITEM" and p.get("product_uid"):
                out[name] = {"code": p.get("product_id"), "uid": p["product_uid"],
                             "name": p.get("product_name") or li.get("product_name") or name,
                             "type": p.get("product_type") or li.get("product_type")
                             or "SERVICE",
                             "uom": p.get("uom") or "Job",
                             "default_price": p.get("price"),
                             "default_description": p.get("product_description") or ""}
                break
    return out


def _address(a: Any) -> dict | None:
    a = _dict(a)
    keep = {k: a[k] for k in ("street", "city", "state", "zip_code", "country", "landmark",
                              "geo_cordinates") if a.get(k) not in (None, "")}
    return keep or None


def _template_uid(rec: dict) -> str | None:
    t = rec.get("proposal_template")
    return (t.get("template_uid") if isinstance(t, dict) else t) or None


def option_untouched(o: dict, products: dict[str, dict]) -> bool:
    """An option still as Zuper made it: every line's description is the template's
    placeholder or the product's default text, and its price is 0 or the product's default."""
    by_uid = {p["uid"]: p for p in products.values()}
    for li in o.get("line_items") or []:
        li = _dict(li)
        desc = li.get("description") or ""
        p = by_uid.get(li.get("product_uid")) or {}
        placeholder = (not plain(desc) or any(m in desc for m in PLACEHOLDER_MARKERS)
                       or (p and plain(desc) == plain(p.get("default_description"))))
        price = li.get("total") if li.get("total") is not None else li.get("unit_price")
        try:
            price = float(price or 0)
        except (TypeError, ValueError):
            return False
        defaults = {0.0} | ({float(p["default_price"])} if p.get("default_price") is not None
                            else set())
        if not placeholder or not any(abs(price - d) < 0.005 for d in defaults):
            return False
    return True


def fillable(rec: dict, template_uid: str, products: dict[str, dict]) -> bool:
    """An empty DRAFT Zuper made for the job (the CPQ trigger): same template (or none), every
    option untouched."""
    if rec.get("is_deleted") or rec.get("is_proposal") is False:
        return False
    if str(rec.get("estimate_status") or "").upper() != "DRAFT":
        return False
    t = _template_uid(rec)
    if t and t != template_uid:
        return False
    opts = [o for o in rec.get("proposal_options") or [] if isinstance(o, dict)]
    return bool(opts) and all(option_untouched(o, products) for o in opts)


def read_job(r: dossier.Reader, spec: dict) -> dict:
    """Every Zuper fact the draft needs, read-only. Raises LookupError / ZuperError."""
    rec = dossier.find_job(r, spec["job"])
    if not isinstance(rec, dict) or not rec.get("job_uid"):
        raise LookupError("no Zuper job %s" % spec["job"])
    job_uid = str(rec["job_uid"])
    cust = _dict(rec.get("customer"))
    cust_uid = cust.get("customer_uid")
    if not cust_uid:
        raise LookupError("job #%s has no customer in Zuper" % rec.get("work_order_number"))
    full = _dict(zc.data_of(r.get("/customers/" + str(cust_uid))))
    name = " ".join(x for x in (cust.get("customer_first_name") or full.get("customer_first_name"),
                                cust.get("customer_last_name") or full.get("customer_last_name"))
                    if x) or None
    prop = rec.get("property")
    prop_uid = prop.get("property_uid") if isinstance(prop, dict) else (prop or None)
    service = (_address(_dict(prop).get("property_address")) if isinstance(prop, dict) else None) \
        or _address(rec.get("customer_address")) or _address(full.get("customer_address"))
    billing = _address(full.get("customer_billing_address")) \
        or _address(full.get("customer_address")) or service
    tpl_uid = TEMPLATES[spec["template"]]
    tpl = _dict(zc.data_of(r.get("/invoice_estimate/proposal_template/" + tpl_uid)))
    if tpl.get("template_uid") != tpl_uid or not str(tpl.get("template_name") or "").startswith(
            "DTR"):
        raise LookupError("Zuper did not return the DTR %s template" % spec["template"])
    products = template_products(tpl)
    rows = [x for x in zc.rows_of(r.get("/estimate", {"filter.job": job_uid, "count": 50,
                                                      "page": 1}))
            if isinstance(x, dict) and not x.get("is_deleted")]
    rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    existing = []
    for row in rows[:PROPOSALS_READ_MAX]:
        one = _dict(zc.data_of(r.get("/estimate/" + str(row["estimate_uid"]))))
        if one:
            existing.append(one)
    return {"job": rec, "job_uid": job_uid, "job_number": str(rec.get("work_order_number") or "")
            or None, "customer_uid": cust_uid, "customer_name": name,
            "customer_email": (full.get("customer_email") or cust.get("customer_email") or "")
            .strip() or None,
            "property_uid": prop_uid, "service_address": service, "billing_address": billing,
            "template": tpl, "products": products, "existing": existing,
            "unread": max(0, len(rows) - PROPOSALS_READ_MAX)}


# ------------------------------------------------------------------------------ the tool

def prepare(db: Session, spec_in: Any, thread_id: int | None = None,
            reader: dossier.Reader | None = None) -> OwenAiProposalDraft:
    """Validate, read Zuper, decide the target, store a PENDING row. Raises SpecError,
    LookupError or ZuperError. Writes nothing to Zuper."""
    spec = validate(spec_in)
    r = reader or dossier.Reader()
    facts = read_job(r, spec)
    tpl_uid = TEMPLATES[spec["template"]]
    products = facts["products"]
    missing = [o["name"] for o in spec["options"] if o["name"] not in products]
    if missing:
        raise SpecError(["the %s template has no product for %s" % (spec["template"],
                                                                   ", ".join(missing))])
    same = [e for e in facts["existing"] if _template_uid(e) == tpl_uid
            and str(e.get("estimate_status") or "").upper() != "DRAFT"]
    if same and not spec["allow_second"]:
        raise SpecError(["job #%s already has %s on this template (%s). Pass allow_second: true "
                         "only if Owen wants a second one." % (
                             facts["job_number"], dossier.proposal_number(same[0]) or "a proposal",
                             same[0].get("estimate_status"))])
    target, fill = "new", None
    for e in facts["existing"]:                      # newest first
        if fillable(e, tpl_uid, products):
            fill = {"estimate_uid": e["estimate_uid"],
                    "estimate_number": dossier.proposal_number(e),
                    "option_uids": {str(o.get("option_name") or "").upper(): o.get("option_uid")
                                    for o in e.get("proposal_options") or []
                                    if isinstance(o, dict) and o.get("option_uid")}}
            target = "fill:%s" % e["estimate_uid"]
            break
    options = []
    for o in spec["options"]:
        p = products[o["name"]]
        options.append({**o, "product": {k: p[k] for k in ("code", "uid", "name", "type",
                                                            "uom")}})
    layout = facts["template"].get("layout_template_uid") or DEFAULT_LAYOUT_UID
    stored = {"input": spec_in if isinstance(spec_in, dict) else {}, "note": spec["note"],
              "allow_second": spec["allow_second"],
              "template_name": facts["template"].get("template_name"),
              "zuper": {"customer_uid": facts["customer_uid"],
                        "customer_name": facts["customer_name"],
                        "customer_email": facts["customer_email"],
                        "property_uid": facts["property_uid"],
                        "service_address": facts["service_address"],
                        "billing_address": facts["billing_address"],
                        "layout_template_uid": layout, "quote_template_uid": QUOTE_TEMPLATE_UID},
              "fill": fill,
              "existing": [{"number": dossier.proposal_number(e), "uid": e.get("estimate_uid"),
                            "status": e.get("estimate_status"),
                            "same_template": _template_uid(e) == tpl_uid} for e in
                           facts["existing"]]}
    # A newer draft for the same job and template replaces an older one still waiting.
    for old in db.scalars(select(OwenAiProposalDraft).where(
            OwenAiProposalDraft.job_uid == facts["job_uid"],
            OwenAiProposalDraft.template_key == spec["template"],
            OwenAiProposalDraft.status == "pending")):
        old.status, old.decided_at, old.decided_by = "rejected", _now(), "Owen AI"
        old.error = "Replaced by a newer draft."
    row = OwenAiProposalDraft(
        job_uid=facts["job_uid"], job_number=facts["job_number"],
        customer_name=facts["customer_name"], template_key=spec["template"],
        template_uid=tpl_uid, title=spec["title"], options=options, spec=stored,
        status="pending", target=target, thread_id=thread_id, created_at=_now())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _session():
    from ..db import SessionLocal
    return SessionLocal()


def tool_propose(args: dict) -> str:
    spec = args.get("spec") if isinstance(args, dict) and "spec" in args else args
    db = _session()
    try:
        row = prepare(db, spec, CURRENT_THREAD)
    except SpecError as exc:
        return ("ERROR: the spec is not valid — fix these and call again:\n"
                + "\n".join("- " + p for p in exc.problems))
    except LookupError as exc:
        return "ERROR: %s" % exc
    except zc.ZuperError as exc:
        return "ERROR from Zuper: %s" % zc.sentence(exc)
    finally:
        db.close()
    fill = (row.spec or {}).get("fill") or {}
    lines = ["Draft #%d for job #%s%s — %s, \"%s\"." % (
        row.id, row.job_number or row.job_uid,
        ", " + row.customer_name if row.customer_name else "",
        (row.spec or {}).get("template_name") or row.template_key, row.title)]
    for o in row.options:
        lines.append("- %s%s: %s — %s (deposit %s), %s" % (
            o["name"], " (recommended)" if o["is_recommended"] else "", o["option_description"],
            money(o["price"]), money(o["deposit"]), o["product"]["code"]))
    lines.append("Target: %s." % ("fill the empty draft %s Zuper already made for this job"
                                  % (fill.get("estimate_number") or row.target[5:])
                                  if row.target.startswith("fill:") else "a new DRAFT"))
    lines.append(WAITING)
    return "\n".join(lines)


def input_schema() -> dict:
    option = {"type": "object", "properties": {
        "name": {"type": "string", "enum": list(OPTION_NAMES)},
        "title": {"type": "string", "description": "short option title (option_description)"},
        "price": {"type": "number", "minimum": 0},
        "deposit": {"type": "number", "minimum": 0, "description": "default 50% of price"},
        "recommended": {"type": "boolean", "description": "default: BEST"},
        "warranty": {"type": "string", "description": "e.g. 'Workmanship Warranty: 6 months on "
                                                       "...'"},
        "scope_html": {"type": "string", "description": "<p><b>Scope of Work</b></p><ul><li>"
                                                         "price block</li><li>steps…</li></ul>"},
        "payment_terms": {"type": "string", "description": "default: 50% upfront / 50% on "
                                                            "completion, with the amounts"}},
        "required": ["name", "title", "price", "scope_html"]}
    return {"type": "object", "properties": {
        "job": {"type": "string", "description": "job number (701) or job uid"},
        "template": {"type": "string", "enum": [SHORT],
                     "description": "always 'short' (the owner's Short Proposal template)"},
        "title": {"type": "string", "description": "proposal_title, e.g. 'Active Leak Repair - "
                                                   "<street>'"},
        "options": {"type": "array", "items": option, "minItems": 1, "maxItems": 3},
        "allow_second": {"type": "boolean"},
        "note": {"type": "string", "description": "one line for Owen: why these options"}},
        "required": ["job", "title", "options"]}


DESCRIPTION = (
    "PREPARE a Good/Better/Best proposal for one Zuper job, for Owen to APPROVE on the Proposals "
    "tab. It writes NOTHING to Zuper: it stores a pending draft. Only when Owen presses Approve "
    "does the server create it in Zuper as a DRAFT (or fill the empty draft Zuper made for the "
    "job); it is never sent to the customer. It ALWAYS uses the owner's template 'DTR - Tile Leak "
    "Repair - Short Proposal (Good / Better / Best)'. Spec: job; title (proposal_title); "
    "options[1-3] {name GOOD|BETTER|BEST, title (short option title), price (customer pays), "
    "deposit? (default 50%), recommended? (default BEST), warranty? (default 1 month for every "
    "option - change it only when Owen gives another), scope_html (Luis's "
    "convention: a price-block bullet, then the scope bullets; allowed tags <p> <b> <i> <ul> "
    "<li> <br>, everything else is escaped; warranty and payment terms are added if missing), "
    "payment_terms?}; allow_second? (only if Owen wants a second proposal on the same "
    "template); note? (one line for Owen). Do NOT type product codes, customer or address: the "
    "tool reads them from Zuper. No pictures. A newer draft for the same job and template "
    "replaces an older one still waiting.")

SPECS = (("propose_proposal_draft", DESCRIPTION, input_schema(), tool_propose),)


# ------------------------------------------------------------------------------ the body

def _line(o: dict) -> dict:
    p = o["product"]
    return {"line_item_type": "ITEM", "product_uid": p["uid"], "product_id": p["code"],
            "name": p["name"], "description": o["scope_html"],
            "product_type": p.get("type") or "SERVICE", "uom": p.get("uom") or "Job",
            "quantity": 1, "unit_price": o["price"], "total": o["price"],
            "tax": {"tax_exempt": False}, "section_type": "EXPANDED", "show_child_prices": True}


def _options(row: OwenAiProposalDraft, option_uids: dict | None = None) -> list[dict]:
    out = []
    for o in row.options:
        opt = {"option_name": o["name"], "option_description": o["option_description"],
               "is_recommended": bool(o["is_recommended"]), "line_items": [_line(o)],
               "tax": [], "discount": {"type": "FIXED", "value": 0}, "addons": [],
               "deposit": o["deposit"]}
        uid = (option_uids or {}).get(o["name"])
        if uid:
            opt["option_uid"] = uid
        out.append(opt)
    return out


def _signatures(z: dict) -> list[dict]:
    s = signer()
    out = []
    if z.get("customer_email"):
        out.append({"email": z["customer_email"], "name": z.get("customer_name") or "",
                    "type": "CUSTOMER", "user_uid": "", "internal_user_type": None})
    out.append({"email": s["email"], "name": s["name"], "type": "INTERNAL", "user_uid": s["uid"],
                "internal_user_type": "CREATED_USER"})
    return out


def create_body(row: OwenAiProposalDraft, today=None) -> dict:
    """POST /estimate — the shape proven by gbb_create.py (DTR-154/155)."""
    z = (row.spec or {}).get("zuper") or {}
    today = today or datetime.now(TZ).date()
    est = {"prefix": PREFIX, "estimate_date": today.isoformat(),
           "expiry_date": (today + timedelta(days=EXPIRY_DAYS)).isoformat(),
           "customer": z.get("customer_uid"), "property": z.get("property_uid"),
           "job": row.job_uid,
           "customer_billing_address": z.get("billing_address"),
           "customer_service_address": z.get("service_address"),
           "template": z.get("quote_template_uid") or QUOTE_TEMPLATE_UID, "remarks": "",
           "custom_fields": [], "is_proposal": True, "proposal_title": row.title,
           "proposal_template": row.template_uid,
           "layout_template_uid": z.get("layout_template_uid") or DEFAULT_LAYOUT_UID,
           "signatures": _signatures(z), "sold_by_user": signer()["uid"], "tax": [],
           "tax_exempt": False, "proposal_options": _options(row), "line_items": []}
    return {"estimate": {k: v for k, v in est.items() if v is not None}}


def fill_body(row: OwenAiProposalDraft, estimate_uid: str) -> dict:
    """PUT /estimate/{uid} — the verified edit shape: uid, is_proposal, title, FULL options."""
    fill = (row.spec or {}).get("fill") or {}
    return {"estimate": {"estimate_uid": estimate_uid, "is_proposal": True,
                         "proposal_title": row.title,
                         "proposal_options": _options(row, fill.get("option_uids"))}}


# ------------------------------------------------------------------------------ approve / reject

class Refused(Exception):
    """Approve refused before anything was claimed or sent (409, the row stays pending)."""


def refusal(db: Session) -> str | None:
    if not zuper_config.owen_ai_proposals_enabled():
        return zuper_config.OWEN_AI_PROPOSALS_OFF_SENTENCE
    return registry.level_refusal(db, config.KEY, ACTION)


def _note(db: Session, row: OwenAiProposalDraft, text: str) -> None:
    if not row.thread_id or db.get(OwenAiThread, row.thread_id) is None:
        return
    db.add(OwenAiMessage(thread_id=row.thread_id, role="system", content=text, status="done",
                         created_at=_now(), finished_at=_now()))
    th = db.get(OwenAiThread, row.thread_id)
    th.updated_at = _now()


def _label(row: OwenAiProposalDraft) -> str:
    return "Proposal draft #%d (job #%s, %s)" % (row.id, row.job_number or row.job_uid,
                                                 row.template_key)


def check_readback(rec: dict, row: OwenAiProposalDraft) -> list[str]:
    problems = []
    if str(rec.get("estimate_status") or "").upper() != "DRAFT":
        problems.append("status is %s, not DRAFT" % rec.get("estimate_status"))
    if rec.get("is_proposal") is False:
        problems.append("it is not a proposal")
    got = {str(o.get("option_name") or "").upper(): o for o in rec.get("proposal_options") or []
           if isinstance(o, dict)}
    for o in row.options:
        g = got.get(o["name"])
        if g is None:
            problems.append("%s is missing" % o["name"])
            continue
        try:
            total = float(g.get("total"))
        except (TypeError, ValueError):
            problems.append("%s has no total" % o["name"])
            continue
        if abs(total - float(o["price"])) > 0.005:
            problems.append("%s total is %s, expected %s" % (o["name"], money(total),
                                                            money(o["price"])))
    extra = set(got) - {o["name"] for o in row.options}
    if extra:
        problems.append("it also has %s" % ", ".join(sorted(extra)))
    return problems


def approve(db: Session, row_id: int, who: str) -> OwenAiProposalDraft:
    row = db.get(OwenAiProposalDraft, row_id)
    if row is None:
        raise LookupError("no such draft")
    if row.status != "pending":
        raise Refused("This draft is already %s." % row.status)
    why = refusal(db)
    if why:
        raise Refused(why)
    took = db.execute(update(OwenAiProposalDraft).where(
        OwenAiProposalDraft.id == row.id, OwenAiProposalDraft.status == "pending").values(
        status="creating", decided_at=_now(), decided_by=(who or "")[:200])).rowcount
    db.commit()
    if not took:
        db.refresh(row)
        raise Refused("This draft is already %s." % row.status)
    db.refresh(row)
    fill_uid = row.target[5:] if row.target.startswith("fill:") else None
    uid = None
    try:
        with zc.owen_ai_proposal_write(fill_uids=[fill_uid] if fill_uid else []):
            if fill_uid:
                cur = _dict(zc.data_of(zc.request("GET", "/estimate/" + fill_uid)))
                if str(cur.get("estimate_status") or "").upper() != "DRAFT" \
                        or cur.get("is_deleted") or _dict(cur.get("job")).get(
                            "job_uid", row.job_uid) != row.job_uid:
                    raise LookupError(
                        "%s is no longer an empty DRAFT on this job (status %s); nothing was "
                        "changed. Ask Owen AI for a new draft." % (
                            dossier.proposal_number(cur) or fill_uid,
                            cur.get("estimate_status")))
                zc.request("PUT", "/estimate/" + fill_uid, body=fill_body(row, fill_uid))
                uid = fill_uid
            else:
                payload = zc.request("POST", "/estimate", body=create_body(row))
                uid = zc.uid_of(payload, "estimate_uid")
            row.estimate_uid = uid
            db.commit()
            back = _dict(zc.data_of(zc.request("GET", "/estimate/" + uid)))
        row.estimate_number = dossier.proposal_number(back)
        problems = check_readback(back, row)
        if problems:
            row.status = "failed"
            row.error = ("%s in Zuper, but the read-back does not match: %s. Check it in Zuper "
                         "before doing anything else." % (
                             row.estimate_number or uid, "; ".join(problems)))
        else:
            row.status, row.error = "created", None
    except zc.ZuperError as exc:
        row.status = "failed"
        row.error = zc.sentence(exc) + (
            " (Zuper may already hold %s — check before trying again.)" % uid if uid else "")
    except LookupError as exc:
        row.status, row.error = "failed", str(exc)
    except Exception as exc:  # noqa: BLE001 - a bug must still leave the row failed, not creating
        row.status = "failed"
        row.error = "Internal error (%s)%s." % (type(exc).__name__, (
            "; Zuper may already hold %s" % uid) if uid else "")
    if row.status == "created":
        _note(db, row, "%s was APPROVED by %s and created in Zuper as %s — a DRAFT, not sent "
                       "to the customer." % (_label(row), who,
                                             row.estimate_number or row.estimate_uid))
    else:
        _note(db, row, "%s was approved by %s but FAILED in Zuper: %s" % (_label(row), who,
                                                                         row.error))
    db.commit()
    db.refresh(row)
    return row


def reject(db: Session, row_id: int, who: str, why: str) -> OwenAiProposalDraft:
    row = db.get(OwenAiProposalDraft, row_id)
    if row is None:
        raise LookupError("no such draft")
    if row.status not in ("pending", "failed"):
        raise Refused("This draft is already %s." % row.status)
    took = db.execute(update(OwenAiProposalDraft).where(
        OwenAiProposalDraft.id == row.id,
        OwenAiProposalDraft.status.in_(("pending", "failed"))).values(
        status="rejected", decided_at=_now(), decided_by=(who or "")[:200],
        error=(why or "").strip()[:1000] or None)).rowcount
    db.commit()
    db.refresh(row)
    if not took:
        raise Refused("This draft is already %s." % row.status)
    _note(db, row, "%s was REJECTED by %s: %s. Nothing was created in Zuper." % (
        _label(row), who, (why or "").strip() or "no reason given"))
    db.commit()
    return row


def notes_for(db: Session, m: OwenAiMessage) -> list[str]:
    """The system notes posted in m's thread since the owner's previous message — handed to
    Claude with m, because its Claude session never saw them."""
    prev = db.scalar(select(OwenAiMessage.id).where(
        OwenAiMessage.thread_id == m.thread_id, OwenAiMessage.role == "user",
        OwenAiMessage.id < m.id).order_by(OwenAiMessage.id.desc()).limit(1)) or 0
    return list(db.scalars(select(OwenAiMessage.content).where(
        OwenAiMessage.thread_id == m.thread_id, OwenAiMessage.role == "system",
        OwenAiMessage.id > prev).order_by(OwenAiMessage.id)))


# ------------------------------------------------------------------------------ the API's view

def out(row: OwenAiProposalDraft, full: bool = True) -> dict:
    spec = row.spec or {}
    fill = spec.get("fill") or {}
    d = {"id": row.id, "job_uid": row.job_uid, "job_number": row.job_number,
         "customer_name": row.customer_name, "template_key": row.template_key,
         "template_name": spec.get("template_name"), "title": row.title, "status": row.status,
         "target": row.target, "target_number": fill.get("estimate_number"),
         "estimate_uid": row.estimate_uid, "estimate_number": row.estimate_number,
         "estimate_url": estimate_url(row.estimate_uid) if row.status in ("created", "failed")
         else None,
         "job_url": (zuper_config.job_url_template().format(uid=row.job_uid)
                     if zuper_config.job_url_template() else None),
         "error": row.error, "note": spec.get("note"), "thread_id": row.thread_id,
         "created_at": _iso(row.created_at), "decided_at": _iso(row.decided_at),
         "decided_by": row.decided_by,
         "total": round(sum(float(o["price"]) for o in row.options or []), 2)}
    d["options"] = [{k: o.get(k) for k in ("name", "option_description", "is_recommended",
                                           "price", "deposit", "warranty",
                                           *(("scope_html",) if full else ()))}
                    | {"product_code": (o.get("product") or {}).get("code")}
                    for o in row.options or []]
    if full:
        d["existing"] = spec.get("existing") or []
    from . import pictures  # the optional step pictures (2026-11-09)
    d.update(pictures.out(row))
    return d
