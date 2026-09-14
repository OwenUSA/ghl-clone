"""CompanyCam job photos, seen from the CRM. The photos never leave CompanyCam.

The crew photographs every job in CompanyCam. The owner wants those photos on the
card and on the customer, without a second app open and without copying 53,403
photos anywhere (DECISIONS.md, 2026-09-14). This module is the only thing in the
repository that talks to CompanyCam, and it does four things:

1. **Reads** (on demand, server-side, short cache): a project, a page of its photos,
   and the image bytes, which are relayed to the browser by `companycam_api.py`.
2. **Links** existing projects to cards — `plan_links` / `apply_plan`, used by the
   one-off command `python -m app.companycam_link` and by the worker's hourly check:

   * first, a project named ``Workiz <job #> - <customer>`` links to THE card whose
     `custom_fields.workiz_id` is that job number (method ``workiz_job``). Workiz's
     own integration names every project it creates that way (measured by the
     operator: 441 of 441 Workiz-typed projects);
   * everything else by ADDRESS — street number plus normalised street name, the
     card's own address else its contact's. One card, or ALL the cards that share
     the address (a repeat customer);
   * a project that only matches a customer NAME is never linked: it goes on the
     review list for an admin;
   * no match: left alone. Nothing here ever creates a contact or a card.

3. **Creates** a project for a card that should have one (`request_project`,
   `process_project_requests`) — on by default when a token is set, from exactly two
   doors: a card created in the CRM (the Add opportunity modal / `POST
   /api/opportunities`) or by an AHS work-order email, at creation when it has an
   address, else the moment an address is first saved on it. It SEARCHES first and
   links an existing project at that address instead of making a duplicate. A card
   made by the Workiz importer never creates.
4. **Records a heartbeat** of the hourly check (`CompanyCamSyncState`).

## The one write, enforced at the bottom

`_request` is the only function that sends a request with the API token, and it
refuses every method but GET — except ``POST /projects`` while creation is switched
on. There is no upload, delete, edit or webhook call anywhere, and
`test_companycam.py` drives the whole package through a recording transport to prove
the create POST is the only non-GET that ever leaves.

## Degrading quietly

Every failure CompanyCam can produce — no token, 401, 5xx, timeout, garbage — becomes
a `CompanyCamError`. The Photos tab turns that into "Photos are unavailable right
now"; the hourly check records it in the heartbeat and tries again next hour; a
creation request stays pending. Nothing else in the CRM waits on CompanyCam.

This module imports neither `app.automations` nor `app.queue`: nothing it does can
text a customer.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import (
    CompanyCamLink,
    CompanyCamProjectRequest,
    CompanyCamReviewItem,
    CompanyCamSyncState,
    Contact,
    Opportunity,
)

log = logging.getLogger("companycam")

API_BASE = "https://api.companycam.com/v2"
PROJECTS_PATH = "/projects"
# Measured 2026-09-14: asking for 100 returns 50. Page until an empty list.
PER_PAGE = 50
MAX_PAGES = 400                 # 20,000 projects; the account holds 1,759
TIMEOUT_SECONDS = 10.0
# Between pages of a bulk read (the linking command, the hourly check, a search).
# Interactive reads — the Photos tab — are not paced.
PACE_SECONDS = float(os.getenv("COMPANYCAM_PACE_SECONDS", "0.25"))
RETRIES_ON_429 = 3

SYNC_INTERVAL = timedelta(hours=1)
# The hourly check reads projects updated since its last success, minus this overlap,
# so a project written during the previous run is not missed between two clocks.
SYNC_OVERLAP_SECONDS = 15 * 60
# Once a day the check reads every project. That is what links an OLD project to a
# card that arrived later — a Workiz job imported a week after its project was made.
FULL_SWEEP_INTERVAL = timedelta(hours=24)
WORKER_TICK_SECONDS = 60
CREATE_MAX_ATTEMPTS = 5

LIST_CACHE_SECONDS = 120.0
PROJECT_CACHE_SECONDS = 600.0
PHOTO_INDEX_SECONDS = 3600.0

UNAVAILABLE = "Photos are unavailable right now"
OFF = "CompanyCam is not connected on this deployment"

WORKIZ_CREATED_BY = "Workiz import"      # workiz_import.py writes this on its contacts
AHS_EMAIL_CREATED_BY = "AHS email"       # ahs_jobs.CREATED_BY
AHS_JOB_ID = "ahs_job_id"                # ahs_jobs.AHS_JOB_ID
WORKIZ_ID = "workiz_id"

# Tests install an `httpx.MockTransport` here. Production leaves it None.
TRANSPORT: httpx.BaseTransport | None = None


# ---------------------------------------------------------------------- config

def token() -> str:
    return os.getenv("COMPANYCAM_API_TOKEN", "").strip()


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def enabled() -> bool:
    """No token, no CompanyCam: nothing is read, linked, created or sent."""
    return bool(token())


def create_enabled() -> bool:
    """ON by default once a token is set (operator, 2026-09-14). The switch stays so
    it can be turned off: COMPANYCAM_CREATE_PROJECTS=false."""
    return enabled() and _flag("COMPANYCAM_CREATE_PROJECTS", True)


def sync_enabled() -> bool:
    """OFF by default; the operator turns it on after the first linking run."""
    return enabled() and _flag("COMPANYCAM_SYNC_ENABLED", False)


def config_summary() -> dict:
    """What an admin may know about the configuration. Never the token."""
    return {"token_set": enabled(), "create_projects": create_enabled(),
            "sync_enabled": sync_enabled()}


# ---------------------------------------------------------------------- errors

class CompanyCamError(Exception):
    """Anything CompanyCam could not give us. `kind` is for logs and the heartbeat:
    off | unauthorized | not_found | unavailable | refused_write | bad_response."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__("%s%s" % (kind, (": " + detail) if detail else ""))
        self.kind = kind
        self.detail = detail


# ---------------------------------------------------------------------- HTTP

def _sleep(seconds: float) -> None:          # monkeypatched to a no-op in tests
    if seconds > 0:
        time.sleep(seconds)


def _allowed_write(method: str, path: str) -> bool:
    """The whole write surface of this package: create a project. Nothing else."""
    return method == "POST" and path == PROJECTS_PATH and create_enabled()


def _request(method: str, path: str, *, params: dict | None = None,
             body: dict | None = None) -> Any:
    method = method.upper()
    if method != "GET" and not _allowed_write(method, path):
        # Raised BEFORE a client exists, so a refused write cannot reach the wire.
        raise CompanyCamError("refused_write", "%s %s" % (method, path))
    if not enabled():
        raise CompanyCamError("off")
    headers = {"Authorization": "Bearer " + token(), "Accept": "application/json"}
    attempt = 0
    while True:
        try:
            with httpx.Client(base_url=API_BASE, transport=TRANSPORT,
                              timeout=TIMEOUT_SECONDS) as client:
                resp = client.request(method, path, params=params, json=body,
                                      headers=headers)
        except httpx.HTTPError as exc:
            raise CompanyCamError("unavailable", type(exc).__name__) from None
        if resp.status_code == 429 and attempt < RETRIES_ON_429:
            attempt += 1
            try:
                wait = float(resp.headers.get("Retry-After", "2"))
            except ValueError:
                wait = 2.0
            _sleep(min(wait, 30.0))
            continue
        break
    if resp.status_code in (401, 403):
        raise CompanyCamError("unauthorized", "HTTP %d" % resp.status_code)
    if resp.status_code == 404:
        raise CompanyCamError("not_found", path)
    if resp.status_code >= 400:
        raise CompanyCamError("unavailable", "HTTP %d" % resp.status_code)
    try:
        return resp.json()
    except ValueError:
        raise CompanyCamError("bad_response", "not JSON") from None


def _pages(path: str, params: dict | None = None, *, paced: bool) -> Iterator[list[dict]]:
    """Every page, 50 at a time, until CompanyCam answers with an empty list."""
    for page in range(1, MAX_PAGES + 1):
        if paced and page > 1:
            _sleep(PACE_SECONDS)
        rows = _request("GET", path, params={**(params or {}), "per_page": PER_PAGE,
                                             "page": page})
        if not isinstance(rows, list):
            raise CompanyCamError("bad_response", "expected a list from %s" % path)
        if not rows:
            return
        yield rows


def iter_projects(modified_since: int | None = None) -> Iterator[dict]:
    """Every project, or those updated at/after `modified_since` (unix seconds).

    `modified_since` is also sent to CompanyCam, but the filter that counts is the one
    applied here to `updated_at`: if CompanyCam ignores the parameter we read more
    pages, never fewer projects.
    """
    params = {"modified_since": modified_since} if modified_since is not None else None
    for rows in _pages(PROJECTS_PATH, params, paced=True):
        for p in rows:
            if not isinstance(p, dict) or p.get("id") is None:
                continue
            if modified_since is not None and _unix(p.get("updated_at")) is not None \
                    and _unix(p.get("updated_at")) < modified_since:
                continue
            yield p


def search_projects(street: str) -> list[dict]:
    """Projects CompanyCam finds for this street. Paged, and filtered by address here
    regardless of what CompanyCam's own `query` matched."""
    out: list[dict] = []
    for rows in _pages(PROJECTS_PATH, {"query": street}, paced=True):
        out.extend(p for p in rows if isinstance(p, dict) and p.get("id") is not None)
    return out


def create_project(payload: dict) -> dict:
    project = _request("POST", PROJECTS_PATH, body=payload)
    if not isinstance(project, dict) or project.get("id") is None:
        raise CompanyCamError("bad_response", "create returned no project id")
    return project


def _unix(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def iso_from_unix(value: Any) -> str | None:
    n = _unix(value)
    return datetime.fromtimestamp(n, UTC).isoformat() if n is not None else None


# ---------------------------------------------------------------------- addresses

# USPS street suffixes and directionals, the spellings that actually vary between a
# Workiz job, an AHS email and CompanyCam. Both sides are mapped to the short form.
SUFFIXES = {
    "STREET": "ST", "STR": "ST", "AVENUE": "AVE", "AV": "AVE", "AVEN": "AVE",
    "ROAD": "RD", "DRIVE": "DR", "DRV": "DR", "LANE": "LN", "COURT": "CT",
    "BOULEVARD": "BLVD", "BOUL": "BLVD", "PLACE": "PL", "TERRACE": "TER", "TERR": "TER",
    "CIRCLE": "CIR", "CIRC": "CIR", "PARKWAY": "PKWY", "PKY": "PKWY", "HIGHWAY": "HWY",
    "TRAIL": "TRL", "WAY": "WAY", "COVE": "CV", "LOOP": "LOOP", "PATH": "PATH",
    "SQUARE": "SQ", "CROSSING": "XING", "POINT": "PT", "RUN": "RUN", "ROW": "ROW",
    "PASS": "PASS", "PIKE": "PIKE", "GLEN": "GLN", "HOLLOW": "HOLW", "ALLEY": "ALY",
    "CAUSEWAY": "CSWY", "EXTENSION": "EXT", "ISLE": "ISLE", "KEY": "KY", "LANDING": "LNDG",
    "MANOR": "MNR", "PLAZA": "PLZ", "RIDGE": "RDG", "VIEW": "VW", "VISTA": "VIS",
    "WALK": "WALK", "BEND": "BND", "BLUFF": "BLF", "BRANCH": "BR", "CREEK": "CRK",
    "ESTATES": "ESTS", "HARBOR": "HBR", "HILLS": "HLS", "LAKE": "LK", "LAKES": "LKS",
    "MEADOWS": "MDWS", "OAKS": "OAKS", "PARK": "PARK", "SHORES": "SHRS", "SPRINGS": "SPGS",
}
SUFFIX_FORMS = set(SUFFIXES.values()) | {"ST", "AVE", "RD", "DR", "LN", "CT", "BLVD",
                                         "PL", "TER", "CIR", "PKWY", "HWY", "TRL"}
DIRECTIONALS = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
                "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE",
                "SOUTHWEST": "SW"}
ORDINALS = {"FIRST": "1ST", "SECOND": "2ND", "THIRD": "3RD", "FOURTH": "4TH",
            "FIFTH": "5TH", "SIXTH": "6TH", "SEVENTH": "7TH", "EIGHTH": "8TH",
            "NINTH": "9TH", "TENTH": "10TH"}
# A unit is not part of the street: "123 Main St Apt 4" is 123 MAIN ST.
UNIT_WORDS = {"APT", "APARTMENT", "UNIT", "STE", "SUITE", "#", "LOT", "BLDG", "BUILDING",
              "RM", "ROOM", "SPC", "SPACE", "TRLR"}


@dataclass(frozen=True)
class StreetKey:
    number: str
    name: tuple[str, ...]


def street_key(street: str | None) -> StreetKey | None:
    """Street number + normalised street name, or None when either is missing.

    "123 Main Street" and "123 MAIN ST." are the same key; so are "9811 Southwest 16th
    Street" and "9811 SW 16TH ST".
    """
    if not street:
        return None
    text = re.sub(r"[^A-Z0-9# ]+", " ", street.upper().replace("#", " # "))
    tokens = text.split()
    if not tokens or not tokens[0][0].isdigit():
        return None
    number, rest = tokens[0], []
    for tok in tokens[1:]:
        if tok in UNIT_WORDS:
            break
        tok = DIRECTIONALS.get(tok, tok)
        tok = ORDINALS.get(tok, tok)
        tok = SUFFIXES.get(tok, tok)
        rest.append(tok)
    if not rest:
        return None
    return StreetKey(number, tuple(rest))


def _zip5(value: str | None) -> str | None:
    m = re.match(r"^\s*(\d{5})", value or "")
    return m.group(1) if m else None


def keys_match(a: StreetKey, b: StreetKey) -> bool:
    """Equal, or one is the other with a city glued on after a complete street name.

    The second form is the AHS dispatch address, which writes
    ``14436 SW 95TH LN MIAMI, FL 33186`` with no comma between street and city, so the
    city stays inside our street column (DECISIONS.md, AHS amendment). It only counts
    when the shorter name ENDS in a street suffix — ``123 MAIN`` never swallows
    ``123 MAIN ST``.
    """
    if a.number != b.number:
        return False
    if a.name == b.name:
        return True
    short, long_ = (a.name, b.name) if len(a.name) < len(b.name) else (b.name, a.name)
    return long_[:len(short)] == short and short[-1] in SUFFIX_FORMS


def project_street(project: dict) -> str | None:
    addr = project.get("address") or {}
    return addr.get("street_address_1") if isinstance(addr, dict) else None


def project_address_line(project: dict) -> str:
    addr = project.get("address") or {}
    if not isinstance(addr, dict):
        return ""
    region = " ".join(x for x in (addr.get("state"), addr.get("postal_code")) if x)
    return ", ".join(x for x in (addr.get("street_address_1"), addr.get("city"), region)
                     if x)


def card_address(o: Opportunity) -> tuple[str | None, str | None]:
    """(street, postal code): the card's own address, else its contact's."""
    if (o.address_street or "").strip():
        return o.address_street, o.address_postal_code
    c = o.contact
    if c is not None and (c.address_street or "").strip():
        return c.address_street, c.address_postal_code
    return None, None


def _name_tokens(text: str | None) -> list[str]:
    return re.sub(r"[^A-Z0-9 ]+", " ", (text or "").upper()).split()


WORKIZ_NAME = re.compile(r"^Workiz (\S+) - ")


def workiz_job_number(project: dict) -> str | None:
    m = WORKIZ_NAME.match(project.get("name") or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------------- linking

@dataclass
class CardIndex:
    by_workiz: dict[str, list[int]] = field(default_factory=dict)
    by_number: dict[str, list[tuple[StreetKey, str | None, int]]] = field(
        default_factory=dict)
    by_name: dict[tuple[str, str], list[int]] = field(default_factory=dict)

    def address_matches(self, project: dict) -> set[int]:
        key = street_key(project_street(project))
        if key is None:
            return set()
        addr = project.get("address") or {}
        pzip = _zip5(addr.get("postal_code") if isinstance(addr, dict) else None)
        out = set()
        for card_key, czip, opp_id in self.by_number.get(key.number, []):
            # Same street, different ZIP: a different house. Only when both are known.
            if pzip and czip and pzip != czip:
                continue
            if keys_match(key, card_key):
                out.add(opp_id)
        return out

    def name_matches(self, project: dict) -> set[int]:
        tokens = _name_tokens(project.get("name"))
        out: set[int] = set()
        for pair in pairwise(tokens):
            out.update(self.by_name.get(pair, []))
        return out


def build_index(db: Session) -> CardIndex:
    index = CardIndex()
    for o in db.scalars(select(Opportunity).options(
            selectinload(Opportunity.contact))).all():
        wid = (o.custom_fields or {}).get(WORKIZ_ID)
        if isinstance(wid, str | int) and str(wid).strip():
            index.by_workiz.setdefault(str(wid).strip(), []).append(o.id)
        street, postal = card_address(o)
        key = street_key(street)
        if key is not None:
            index.by_number.setdefault(key.number, []).append((key, _zip5(postal), o.id))
        if o.contact is not None:
            first, last = _name_tokens(o.contact.first_name), _name_tokens(o.contact.last_name)
            if first and last:
                index.by_name.setdefault((first[-1], last[0]), []).append(o.id)
    return index


COUNT_KEYS = ("scanned", "linked_workiz_job", "linked_one", "linked_several",
              "name_only_review", "unmatched", "already_linked")


@dataclass
class ProjectPlan:
    project_id: str
    name: str | None
    address: str
    outcome: str                       # one of COUNT_KEYS[1:]
    method: str | None = None          # workiz_job | address
    new_links: list[int] = field(default_factory=list)
    candidates: list[int] = field(default_factory=list)


@dataclass
class Plan:
    projects: list[ProjectPlan] = field(default_factory=list)

    def counts(self) -> dict:
        c = dict.fromkeys(COUNT_KEYS, 0)
        c["scanned"] = len(self.projects)
        for p in self.projects:
            c[p.outcome] += 1
        return c


def _existing_links(db: Session) -> dict[str, set[int]]:
    """Every link ever recorded, unlinked ones INCLUDED: an admin's unlink must not be
    undone by the next sweep."""
    out: dict[str, set[int]] = {}
    for pid, oid in db.execute(select(CompanyCamLink.project_id,
                                      CompanyCamLink.opportunity_id)).all():
        out.setdefault(pid, set()).add(oid)
    return out


def plan_links(db: Session, projects: list[dict]) -> Plan:
    """Decide, without writing anything, what each project should link to.

    Rule order, and it matters: the Workiz job number first — it names ONE card
    exactly — and only a project that rule did not place falls through to the
    address rule.
    """
    index = build_index(db)
    existing = _existing_links(db)
    plan = Plan()
    seen: set[str] = set()
    for project in projects:
        pid = str(project["id"])
        if pid in seen:
            continue
        seen.add(pid)
        have = existing.get(pid, set())
        item = ProjectPlan(project_id=pid, name=project.get("name"),
                           address=project_address_line(project), outcome="unmatched")
        job = workiz_job_number(project)
        exact = set(index.by_workiz.get(job, [])) if job else set()
        if exact:
            new = sorted(exact - have)
            item.method = "workiz_job"
            item.outcome = "linked_workiz_job" if new else "already_linked"
            item.new_links = new
        else:
            matched = index.address_matches(project)
            new = sorted(matched - have)
            if new:
                item.method = "address"
                item.outcome = "linked_one" if len(matched) == 1 else "linked_several"
                item.new_links = new
            elif have:
                item.outcome = "already_linked"
            else:
                names = index.name_matches(project)
                if names:
                    item.outcome = "name_only_review"
                    item.candidates = sorted(names)
        plan.projects.append(item)
    return plan


def apply_plan(db: Session, plan: Plan, *, linked_by: str) -> None:
    """Write what `plan_links` decided. Flushes; the caller commits. Idempotent: a
    second application of a fresh plan writes nothing."""
    reviews = {r.project_id: r for r in db.scalars(select(CompanyCamReviewItem)).all()}
    for p in plan.projects:
        for opp_id in p.new_links:
            db.add(CompanyCamLink(opportunity_id=opp_id, project_id=p.project_id,
                                  method=p.method, project_name=_clip(p.name, 255),
                                  linked_by=linked_by))
        if p.new_links and p.project_id in reviews:
            db.delete(reviews[p.project_id])       # placed by a rule: nothing to review
        if p.outcome == "name_only_review":
            r = reviews.get(p.project_id)
            if r is None:
                db.add(CompanyCamReviewItem(
                    project_id=p.project_id, project_name=_clip(p.name, 255),
                    project_address=_clip(p.address, 500),
                    candidate_opportunity_ids=p.candidates))
            elif sorted(r.candidate_opportunity_ids or []) != p.candidates:
                r.candidate_opportunity_ids = p.candidates
    db.flush()


def _clip(text: str | None, n: int) -> str | None:
    return text[:n] if text else text


def link_all(db: Session, *, commit: bool, linked_by: str = "companycam_link") -> dict:
    """The one-off linking run. Reads every project; writes only with `commit`."""
    projects = list(iter_projects())
    plan = plan_links(db, projects)
    if commit:
        apply_plan(db, plan, linked_by=linked_by)
    return plan.counts()


# ---------------------------------------------------------------------- hourly check

def sync_state(db: Session) -> CompanyCamSyncState:
    state = db.get(CompanyCamSyncState, 1)
    if state is None:
        state = CompanyCamSyncState(id=1)
        db.add(state)
        db.flush()
    return state


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def sync_due(state: CompanyCamSyncState, now: datetime) -> tuple[bool, bool]:
    """(run now?, read every project?)"""
    started = _aware(state.last_started_at)
    run = started is None or now - started >= SYNC_INTERVAL
    swept = _aware(state.last_full_sweep_at)
    full = state.modified_cursor is None or swept is None \
        or now - swept >= FULL_SWEEP_INTERVAL
    return run, full


def sync_once(db: Session, *, now: datetime | None = None, full: bool | None = None) -> dict:
    """One hourly check. Records its heartbeat whatever happens, and commits.

    An outage is not an exception to the caller: it is `last_error` on the heartbeat,
    the cursor does not move, and the next hour asks again from the same place.
    """
    now = now or datetime.now(UTC)
    state = sync_state(db)
    _, due_full = sync_due(state, now)
    full = due_full if full is None else full
    state.last_started_at = now
    db.commit()
    since = None if full else max(0, (state.modified_cursor or 0) - SYNC_OVERLAP_SECONDS)
    counts: dict = {}
    try:
        projects = list(iter_projects(modified_since=since))
        plan = plan_links(db, projects)
        apply_plan(db, plan, linked_by="hourly sync")
        counts = {**plan.counts(), "full_sweep": full}
        state = sync_state(db)
        state.last_success_at = now
        state.modified_cursor = int(now.timestamp())
        if full:
            state.last_full_sweep_at = now
        state.last_error = None
    except CompanyCamError as exc:
        db.rollback()
        state = sync_state(db)
        state.last_error = str(exc)
        counts = {"full_sweep": full}
        log.warning("companycam hourly check failed: %s", exc)
    except Exception as exc:
        # Not CompanyCam's fault (a database error, a bug): still a heartbeat, not silence.
        db.rollback()
        state = sync_state(db)
        state.last_error = "internal: %s" % type(exc).__name__
        counts = {"full_sweep": full}
        log.exception("companycam hourly check crashed")
    state.last_finished_at = datetime.now(UTC)
    state.last_counts = counts
    db.commit()
    return counts


def heartbeat(db: Session) -> dict:
    state = db.get(CompanyCamSyncState, 1)
    iso = lambda d: _aware(d).isoformat() if d else None  # noqa: E731
    return {
        "last_started_at": iso(state.last_started_at) if state else None,
        "last_finished_at": iso(state.last_finished_at) if state else None,
        "last_success_at": iso(state.last_success_at) if state else None,
        "last_full_sweep_at": iso(state.last_full_sweep_at) if state else None,
        "last_counts": state.last_counts if state else None,
        "last_error": state.last_error if state else None,
    }


# ---------------------------------------------------------------------- creation

REQUEST_ORIGINS = ("created", "address_saved", "ahs_email")


def made_by_workiz_importer(o: Opportunity) -> bool:
    """A card the Workiz importer made. Its project already exists in CompanyCam
    (Workiz's integration made it), so it only ever links. An AHS email card that the
    importer later PAIRED keeps `created_by = "AHS email"` and is not this."""
    blob = o.custom_fields or {}
    return o.created_by == WORKIZ_CREATED_BY or (
        WORKIZ_ID in blob and o.created_by != AHS_EMAIL_CREATED_BY)


def request_project(db: Session, o: Opportunity, origin: str) -> CompanyCamProjectRequest | None:
    """Ask the worker to give this card a project. Called from exactly two doors —
    `create_opportunity` / `update_opportunity` (origin created / address_saved) and
    the AHS email ingest (ahs_email). Writes nothing when creation is off, the card
    has no street, it is a Workiz card, or it already asked or already has a project."""
    assert origin in REQUEST_ORIGINS
    if not create_enabled() or made_by_workiz_importer(o):
        return None
    if not (o.address_street or "").strip():
        return None
    if db.scalar(select(CompanyCamProjectRequest.id).where(
            CompanyCamProjectRequest.opportunity_id == o.id)) is not None:
        return None
    if db.scalar(select(CompanyCamLink.id).where(
            CompanyCamLink.opportunity_id == o.id)) is not None:
        return None
    req = CompanyCamProjectRequest(opportunity_id=o.id, origin=origin)
    db.add(req)
    db.flush()
    return req


def project_name_for(o: Opportunity) -> str:
    """Named the way Workiz names its projects (``Workiz <job #> - <customer>``,
    measured by the operator on all 441 Workiz-typed projects), with our own prefix:

        an AHS email card  ->  AHS <ahs_job_id> - <customer>
        any other card     ->  CRM <opportunity id> - <customer>

    The customer is the card's contact; a card with none uses its title.
    """
    customer = (o.contact.name if o.contact is not None else "").strip() or o.title
    ahs = (o.custom_fields or {}).get(AHS_JOB_ID)
    if o.created_by == AHS_EMAIL_CREATED_BY and ahs:
        return "AHS %s - %s" % (ahs, customer)
    return "CRM %s - %s" % (o.id, customer)


def project_payload(o: Opportunity) -> dict:
    body: dict[str, Any] = {
        "name": project_name_for(o)[:255],
        "address": {
            "street_address_1": o.address_street or "",
            "city": o.address_city or "",
            "state": o.address_state or "",
            "postal_code": o.address_postal_code or "",
            "country": "US",
        },
    }
    c: Contact | None = o.contact
    if c is not None:
        contact = {"name": c.name, "email": c.email, "phone_number": c.phone}
        body["primary_contact"] = {k: v for k, v in contact.items() if v}
    return body


def _link(db: Session, opp_id: int, project: dict, method: str, by: str) -> None:
    pid = str(project["id"])
    if db.scalar(select(CompanyCamLink.id).where(
            CompanyCamLink.opportunity_id == opp_id,
            CompanyCamLink.project_id == pid)) is None:
        db.add(CompanyCamLink(opportunity_id=opp_id, project_id=pid, method=method,
                              project_name=_clip(project.get("name"), 255), linked_by=by))


def existing_projects_at(o: Opportunity) -> list[dict]:
    key = street_key(o.address_street)
    if key is None:
        return []
    ozip = _zip5(o.address_postal_code)
    found = []
    for project in search_projects(o.address_street or ""):
        pkey = street_key(project_street(project))
        addr = project.get("address") or {}
        pzip = _zip5(addr.get("postal_code") if isinstance(addr, dict) else None)
        if pkey is None or (ozip and pzip and ozip != pzip):
            continue
        if keys_match(key, pkey):
            found.append(project)
    return found


def process_project_requests(db: Session, *, limit: int = 20) -> dict:
    """Work the pending requests: search, link what exists, else create once.

    Never two projects for one card: the request row is UNIQUE per card, a card that
    already holds a link is finished without a request to CompanyCam, and the state
    is committed as ``creating`` BEFORE the POST — so a crash between CompanyCam
    answering and our commit is followed by a SEARCH (which finds the project just
    made at that address) rather than a second create.
    """
    done = {"linked": 0, "created": 0, "skipped": 0, "failed": 0, "retry": 0}
    if not create_enabled():
        return done
    rows = db.scalars(select(CompanyCamProjectRequest)
                      .where(CompanyCamProjectRequest.state.in_(("pending", "creating")))
                      .order_by(CompanyCamProjectRequest.id).limit(limit)).all()
    for req in rows:
        o = db.get(Opportunity, req.opportunity_id)
        now = datetime.now(UTC)
        if o is None or made_by_workiz_importer(o) or not (o.address_street or "").strip():
            req.state, req.finished_at = "skipped", now
            done["skipped"] += 1
            db.commit()
            continue
        held = db.scalar(select(CompanyCamLink.project_id).where(
            CompanyCamLink.opportunity_id == o.id))
        if held is not None:
            req.state, req.project_id, req.finished_at = "linked", held, now
            done["linked"] += 1
            db.commit()
            continue
        req.attempts += 1
        db.commit()
        try:
            found = existing_projects_at(o)
            if found:
                for project in found:
                    _link(db, o.id, project, "address", "project request")
                req.state, req.project_id = "linked", str(found[0]["id"])
                done["linked"] += 1
            else:
                req.state = "creating"
                db.commit()
                project = create_project(project_payload(o))
                _link(db, o.id, project, "created", "project request")
                req.state, req.project_id = "created", str(project["id"])
                done["created"] += 1
            req.finished_at, req.last_error = datetime.now(UTC), None
        except CompanyCamError as exc:
            req.last_error = str(exc)
            if req.attempts >= CREATE_MAX_ATTEMPTS:
                req.state, req.finished_at = "failed", datetime.now(UTC)
                done["failed"] += 1
            else:
                done["retry"] += 1
            log.warning("companycam project request #%s: %s", req.id, exc)
        db.commit()
    return done


# ---------------------------------------------------------------------- the worker

def tick(session_factory) -> None:
    """One worker tick: creation requests every tick, the hourly check when due."""
    if not enabled():
        return
    db = session_factory()
    try:
        if create_enabled():
            process_project_requests(db)
        if sync_enabled():
            run, _ = sync_due(sync_state(db), datetime.now(UTC))
            db.commit()
            if run:
                sync_once(db)
    except Exception:
        db.rollback()
        log.exception("companycam tick failed")
    finally:
        db.close()


def start_worker_thread(session_factory, stop: threading.Event) -> threading.Thread:
    """A daemon thread beside the queue drainer, so a slow CompanyCam page never
    delays a customer's reminder."""
    def loop():
        while True:
            tick(session_factory)
            if stop.wait(WORKER_TICK_SECONDS):
                return
    t = threading.Thread(target=loop, name="companycam", daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------- photo reads

_cache: dict[tuple, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def _cached(key: tuple, ttl: float, refresh: bool, load):
    now = time.monotonic()
    if not refresh:
        with _cache_lock:
            hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = load()
    with _cache_lock:
        _cache[key] = (now + ttl, value)
    return value


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def get_project(project_id: str, *, refresh: bool = False) -> dict:
    return _cached(("project", project_id), PROJECT_CACHE_SECONDS, refresh,
                   lambda: _request("GET", "%s/%s" % (PROJECTS_PATH, project_id)))


def _uri(photo: dict, kind: str) -> str | None:
    for u in photo.get("uris") or []:
        if isinstance(u, dict) and u.get("type") == kind:
            return u.get("uri") or u.get("url")
    return None


def preferred_uri(photo: dict, variant: str) -> str | None:
    """The annotated image when one exists, else the plain one."""
    base = "thumbnail" if variant == "thumbnail" else "web"
    return _uri(photo, base + "_annotation") or _uri(photo, base)


def _remember_photo(photo: dict) -> None:
    with _cache_lock:
        _cache[("photo", str(photo["id"]))] = (time.monotonic() + PHOTO_INDEX_SECONDS, photo)


def photo_page(project_id: str, page: int, *, refresh: bool = False) -> list[dict]:
    """One page (50) of a project's photos, newest first within the page."""
    def load():
        rows = _request("GET", "%s/%s/photos" % (PROJECTS_PATH, project_id),
                        params={"per_page": PER_PAGE, "page": page})
        if not isinstance(rows, list):
            raise CompanyCamError("bad_response", "photos is not a list")
        return [p for p in rows if isinstance(p, dict) and p.get("id") is not None]
    rows = _cached(("photos", project_id, page), LIST_CACHE_SECONDS, refresh, load)
    for p in rows:
        _remember_photo(p)
    return sorted(rows, key=lambda p: (_unix(p.get("captured_at"))
                                       or _unix(p.get("created_at")) or 0), reverse=True)


def get_photo(photo_id: str) -> dict:
    """From the index the photo lists filled; else asked of CompanyCam."""
    with _cache_lock:
        hit = _cache.get(("photo", photo_id))
    if hit and hit[0] > time.monotonic():
        return hit[1]
    photo = _request("GET", "/photos/%s" % photo_id)
    if not isinstance(photo, dict) or photo.get("id") is None:
        raise CompanyCamError("bad_response", "photo")
    _remember_photo(photo)
    return photo


IMAGE_MAX_BYTES = 25 * 1024 * 1024


def fetch_image(url: str) -> tuple[bytes, str]:
    """The image bytes behind a URI CompanyCam gave us.

    The API token is NOT sent to wherever the image lives. Only if a host under
    companycam.com itself answers 401/403 is the request repeated with it — the token
    then goes back to the company that issued it, never to a third party.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise CompanyCamError("bad_response", "image uri is not https")
    host = parsed.hostname.lower()
    own_host = host == "companycam.com" or host.endswith(".companycam.com")
    for with_token in (False, True):
        if with_token and not own_host:
            break
        headers = {"Authorization": "Bearer " + token()} if with_token else {}
        try:
            with httpx.Client(transport=TRANSPORT, timeout=TIMEOUT_SECONDS,
                              follow_redirects=True) as client:
                resp = client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise CompanyCamError("unavailable", type(exc).__name__) from None
        if resp.status_code in (401, 403) and not with_token:
            continue
        if resp.status_code == 404:
            raise CompanyCamError("not_found", "image")
        if resp.status_code >= 400:
            raise CompanyCamError("unavailable", "image HTTP %d" % resp.status_code)
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if not ctype.startswith("image/"):
            raise CompanyCamError("bad_response", "not an image")
        if len(resp.content) > IMAGE_MAX_BYTES:
            raise CompanyCamError("bad_response", "image too large")
        return resp.content, ctype
    raise CompanyCamError("unauthorized", "image")
