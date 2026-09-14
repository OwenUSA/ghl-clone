"""CompanyCam job photos (2026-09-14) — behaviour, with CompanyCam faked at the HTTP boundary.

There is no CompanyCam token on the build machine and nothing here may call the real
API. `FakeCompanyCam` is an `httpx.MockTransport` handler installed as
`companycam.TRANSPORT`: every request the package makes goes through it and is
recorded, which is what lets the write guarantee below be asserted over EVERYTHING the
package did in a scenario rather than over one function.

What each group protects:

  * **Reading** pages 50 at a time until an empty list, and normalises addresses so
    "123 Main Street" is "123 MAIN ST".
  * **Linking**: the Workiz job number first, exactly one card; then the address — one
    card, several cards; a name-only match is listed, never linked; no match is left
    alone. A dry run writes nothing; a second commit changes nothing.
  * **The hourly check** links a new project and records its heartbeat; an outage or a
    bad token is a heartbeat error, not a crash.
  * **The Photos tab** lists newest first, prefers the annotated image, relays the
    bytes, and never shows the token or a CompanyCam image URI.
  * **Per-pipeline access** hides a hidden card's photos, projects and images.
  * **Creation**: on by default with a token; creates once; links an existing
    same-address project instead; never for a Workiz card; sets primary_contact; and the
    create POST is the ONLY non-GET the package ever sends.
"""
import hashlib
import io
import json
import re
import time
import tokenize
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app import companycam
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    CompanyCamLink,
    CompanyCamProjectRequest,
    CompanyCamReviewItem,
    CompanyCamSyncState,
    Contact,
    Opportunity,
    Pipeline,
    PipelinePermission,
    Role,
    Stage,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import event, func, inspect, select, text

TOKEN = "cc_live_TEST_TOKEN_must_never_leak_9f3a"
NOW = int(time.time())
API = "api.companycam.com"
IMG = "img.companycam.com"


# ================================================================== the fake

def project(pid, name, street, *, city="Bradenton", state="FL", postal="34205",
            updated_at=None, created_at=None, photo_count=0, integrations=()):
    return {
        "id": str(pid), "name": name, "status": "active", "archived": False,
        "address": {"street_address_1": street, "street_address_2": "", "city": city,
                    "state": state, "postal_code": postal, "country": "US"},
        "created_at": created_at or NOW - 86400 * 30, "updated_at": updated_at or NOW - 86400 * 30,
        "photo_count": photo_count, "primary_contact": None,
        "project_url": "https://app.companycam.com/projects/%s" % pid,
        "integrations": [{"type": t, "relation_id": "r"} for t in integrations],
    }


def photo(phid, project_id, captured_at, *, annotated=False, creator="Antonio Brown",
          description=""):
    uris = [{"type": "original", "uri": "https://%s/sig/orig/%s.jpg" % (IMG, phid)},
            {"type": "web", "uri": "https://%s/sig/web/%s.jpg" % (IMG, phid)},
            {"type": "thumbnail", "uri": "https://%s/sig/thumb/%s.jpg" % (IMG, phid)}]
    if annotated:
        uris += [{"type": "web_annotation", "url": "https://%s/sig/webann/%s.jpg" % (IMG, phid)},
                 {"type": "thumbnail_annotation",
                  "url": "https://%s/sig/thumbann/%s.jpg" % (IMG, phid)}]
    return {"id": str(phid), "project_id": str(project_id), "captured_at": captured_at,
            "created_at": captured_at, "creator_name": creator, "description": description,
            "internal": False, "processing_status": "processed", "status": "active",
            "uris": uris}


class FakeCompanyCam:
    """CompanyCam v2 as measured: per_page capped at 50, pages until an empty list."""

    def __init__(self):
        self.projects: list[dict] = []
        self.photos: dict[str, list[dict]] = {}
        self.requests: list[httpx.Request] = []
        self.fail: int | None = None           # HTTP status every API call returns
        self.down = False                      # connection refused
        self.next_id = 900000

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        host, path = request.url.host, request.url.path
        if host == IMG:
            kind = path.split("/")[2]
            return httpx.Response(200, content=("IMG:%s" % kind).encode() + path.encode(),
                                  headers={"content-type": "image/jpeg"})
        assert host == API, "the package called a host that is not CompanyCam: %s" % host
        assert request.headers.get("authorization") == "Bearer " + TOKEN
        if self.fail:
            return httpx.Response(self.fail, json={"error": "nope"})
        q = request.url.params
        per_page = min(int(q.get("per_page", 25)), 50)
        page = int(q.get("page", 1))

        def paged(rows):
            return rows[(page - 1) * per_page: page * per_page]

        if request.method == "POST" and path == "/v2/projects":
            body = json.loads(request.content)
            self.next_id += 1
            created = project(self.next_id, body["name"],
                              body["address"]["street_address_1"],
                              city=body["address"]["city"], state=body["address"]["state"],
                              postal=body["address"]["postal_code"], updated_at=NOW,
                              created_at=NOW)
            created["primary_contact"] = body.get("primary_contact")
            self.projects.append(created)
            return httpx.Response(201, json=created)
        assert request.method == "GET", "unexpected %s %s" % (request.method, path)
        if path == "/v2/projects":
            rows = self.projects
            if q.get("query"):
                digits = q["query"].split()[0]
                rows = [p for p in rows if p["address"]["street_address_1"].startswith(digits)]
            return httpx.Response(200, json=paged(rows))
        m = re.fullmatch(r"/v2/projects/([^/]+)/photos", path)
        if m:
            return httpx.Response(200, json=paged(self.photos.get(m.group(1), [])))
        m = re.fullmatch(r"/v2/projects/([^/]+)", path)
        if m:
            for p in self.projects:
                if p["id"] == m.group(1):
                    return httpx.Response(200, json=p)
            return httpx.Response(404, json={})
        m = re.fullmatch(r"/v2/photos/([^/]+)", path)
        if m:
            for rows in self.photos.values():
                for ph in rows:
                    if ph["id"] == m.group(1):
                        return httpx.Response(200, json=ph)
            return httpx.Response(404, json={})
        return httpx.Response(404, json={})

    def writes(self):
        return [(r.method, r.url.path) for r in self.requests if r.method != "GET"]


@pytest.fixture()
def cc(monkeypatch):
    fake = FakeCompanyCam()
    monkeypatch.setenv("COMPANYCAM_API_TOKEN", TOKEN)
    monkeypatch.delenv("COMPANYCAM_CREATE_PROJECTS", raising=False)
    monkeypatch.delenv("COMPANYCAM_SYNC_ENABLED", raising=False)
    monkeypatch.setattr(companycam, "TRANSPORT", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(companycam, "_sleep", lambda s: None)
    companycam.clear_cache()
    yield fake
    companycam.clear_cache()


# ================================================================== the CRM

@pytest.fixture()
def world(cc):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    users = {"admin": User(email="a@x.test", name="Owen", role=Role.ADMIN),
             "dispatcher": User(email="d@x.test", name="Dee", role=Role.DISPATCHER),
             "outsider": User(email="o@x.test", name="Outsider", role=Role.DISPATCHER),
             "tech": User(email="t@x.test", name="Tech", role=Role.TECH)}
    db.add_all(users.values())
    db.flush()
    retail = Pipeline(name="Retail", position=0)
    secret = Pipeline(name="Secret", position=1)
    db.add_all([retail, secret])
    db.flush()
    lead = Stage(pipeline_id=retail.id, name="New Lead", position=0)
    s1 = Stage(pipeline_id=secret.id, name="S1", position=0)
    db.add_all([lead, s1])
    db.flush()
    db.add(PipelinePermission(pipeline_id=secret.id, user_id=users["dispatcher"].id))

    ada = Contact(first_name="Ada", last_name="Rowe", phone="+19415550111",
                  email="ada@x.test", address_street="5 Bay Road", address_postal_code="34221")
    bo = Contact(first_name="Bo", last_name="Diaz", phone="+19415550122")
    cy = Contact(first_name="Cy", last_name="Nakamura", phone="+19415550133")
    dee = Contact(first_name="Dee", last_name="Quill", phone="+19415550144")
    db.add_all([ada, bo, cy, dee])
    db.flush()

    def card(title, contact, pipeline=retail, stage=lead, **kw):
        o = Opportunity(title=title, contact_id=contact.id, pipeline_id=pipeline.id,
                        stage_id=stage.id, **kw)
        db.add(o)
        db.flush()
        return o

    cards = {
        # One card at 123 Main St.
        "main": card("Bo roof", bo, address_street="123 Main St", address_postal_code="34205"),
        # Two cards sharing 5 Bay Rd: one by its own address, one by its contact's.
        "bay1": card("Ada roof 2024", ada, address_street="5 Bay Rd"),
        "bay2": card("Ada roof 2026", ada),
        # A Workiz card, job WZ77, whose street ALSO matches a hand-made card.
        "workiz": card("DEE QUILL", dee, address_street="77 Palm Ave",
                       created_by="Workiz import", custom_fields={"workiz_id": "WZ77"}),
        "palm_other": card("Other Palm", bo, address_street="77 Palm Avenue"),
        # Cy has no address anywhere: a project can only match him by name.
        "cy": card("Cy leak", cy),
        # A card in the restricted pipeline.
        "secret": card("Secret job", cy, pipeline=secret, stage=s1,
                       address_street="9 Hidden Ct"),
    }
    tokens = {}
    for key, u in users.items():
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        tokens[key] = plain
    db.commit()
    ids = {k: v.id for k, v in cards.items()}
    ids.update(retail=retail.id, secret_pipeline=secret.id, lead=lead.id, s1=s1.id,
               c_ada=ada.id, c_bo=bo.id, c_cy=cy.id, c_dee=dee.id)
    db.close()

    cc.projects = [
        project(1, "Bo Diaz", "123 MAIN STREET", postal="34205", photo_count=3),
        project(2, "Rowe", "5 Bay Rd.", postal="34221"),
        project(3, "Workiz WZ77 - DEE QUILL", "77 Palm Ave", integrations=["Workiz"]),
        project(4, "Cy Nakamura leak", "400 Somewhere Blvd"),
        project(5, "Nobody", "1 Nowhere Ln"),
        project(6, "Hidden", "9 Hidden Court"),
        # A Workiz-named project whose job number is not on any card: address rule.
        project(7, "Workiz NOPE1 - BO DIAZ", "123 Main St", postal="34205"),
    ]
    with TestClient(app) as c:
        c.tokens, c.ids = tokens, ids
        yield c


def h(c, who):
    return {"Authorization": "Bearer " + c.tokens[who]}


def read(fn):
    db = SessionLocal()
    try:
        return fn(db)
    finally:
        db.close()


def links():
    return read(lambda db: sorted(
        (link.project_id, link.opportunity_id, link.method)
        for link in db.scalars(select(CompanyCamLink)).all()))


def dump() -> str:
    """A hash of every row of every table: "changes nothing" means byte-identical."""
    def go(db):
        digest = hashlib.sha256()
        conn = db.connection()
        for table in sorted(inspect(conn).get_table_names()):
            for row in conn.execute(text('SELECT * FROM "%s" ORDER BY 1' % table)):
                digest.update(repr((table, tuple(row))).encode())
        return digest.hexdigest()
    return read(go)


def run_command(*argv):
    from app import companycam_link
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = companycam_link.main(list(argv))
    return code, out.getvalue(), err.getvalue()


# ================================================================== reading

def test_pagination_honours_the_50_cap_and_pages_until_an_empty_list(cc):
    cc.projects = [project(i, "P%d" % i, "%d Elm St" % i) for i in range(1, 121)]
    got = list(companycam.iter_projects())
    assert [p["id"] for p in got] == [str(i) for i in range(1, 121)]
    pages = [(int(r.url.params["page"]), int(r.url.params["per_page"])) for r in cc.requests]
    assert pages == [(1, 50), (2, 50), (3, 50), (4, 50)], \
        "50 a page, and the empty 4th page is what ends it"


def test_a_short_last_page_does_not_end_paging_early(cc):
    # CompanyCam's cap is measured; that a short page is the last one is not.
    cc.projects = [project(i, "P%d" % i, "%d Elm St" % i) for i in range(1, 51)]
    assert len(list(companycam.iter_projects())) == 50
    assert [r.url.params["page"] for r in cc.requests] == ["1", "2"]


@pytest.mark.parametrize("a,b", [
    ("123 Main Street", "123 MAIN ST"),
    ("123 Main St.", "123 main street"),
    ("9811 Southwest 16th Street", "9811 SW 16TH ST"),
    ("9811 SW 16TH ST Apt 4", "9811 Southwest 16th St"),
    ("40 North Oak Lane #2", "40 N OAK LN"),
    ("1 First Avenue", "1 1st Ave"),
    # AHS dispatch glues the city onto the street: still the same house.
    ("14436 SW 95TH LN MIAMI", "14436 Southwest 95th Lane"),
])
def test_address_normalisation_matches_the_same_street(a, b):
    ka, kb = companycam.street_key(a), companycam.street_key(b)
    assert ka is not None and companycam.keys_match(ka, kb)


@pytest.mark.parametrize("a,b", [
    ("123 Main St", "124 Main St"),
    ("123 Main St", "123 Maine St"),
    ("123 Main", "123 Main St Tampa"),     # an incomplete street swallows nothing
    ("123 North Main St", "123 South Main St"),
])
def test_address_normalisation_keeps_different_streets_apart(a, b):
    assert not companycam.keys_match(companycam.street_key(a), companycam.street_key(b))


def test_a_street_with_no_number_has_no_key():
    assert companycam.street_key("Main Street") is None
    assert companycam.street_key("") is None
    assert companycam.street_key("123") is None


# ================================================================== linking

def test_the_rules_link_each_project_the_right_way(world, cc):
    code, out, _ = run_command("--commit", "--json")
    assert code == 0
    counts = json.loads(out)
    ids = world.ids
    assert links() == sorted([
        ("1", ids["main"], "address"),
        ("2", ids["bay1"], "address"),
        ("2", ids["bay2"], "address"),                 # the contact's address counts
        ("3", ids["workiz"], "workiz_job"),            # NOT palm_other, same street
        ("6", ids["secret"], "address"),
        ("7", ids["main"], "address"),                 # unknown job number -> address
    ])
    assert counts == {"committed": True, "scanned": 7, "linked_workiz_job": 1,
                      "linked_one": 3, "linked_several": 1, "name_only_review": 1,
                      "unmatched": 1, "already_linked": 0}
    review = read(lambda db: [(r.project_id, r.candidate_opportunity_ids)
                              for r in db.scalars(select(CompanyCamReviewItem))])
    assert review == [("4", [ids["cy"], ids["secret"]])], \
        "the name-only project is listed with every card of that customer, and linked to none"


def test_the_workiz_rule_runs_before_the_address_rule(world, cc):
    """Project 3's street matches two cards. Its job number names one. The job number wins,
    and the other card at that street gets nothing from this project."""
    plan = read(lambda db: companycam.plan_links(db, [cc.projects[2]]))
    assert [(p.outcome, p.method, p.new_links) for p in plan.projects] == [
        ("linked_workiz_job", "workiz_job", [world.ids["workiz"]])]
    # The same project without the Workiz name falls to the address rule: both cards.
    renamed = {**cc.projects[2], "name": "Dee Quill"}
    plan = read(lambda db: companycam.plan_links(db, [renamed]))
    assert plan.projects[0].method == "address"
    assert sorted(plan.projects[0].new_links) == sorted([world.ids["workiz"],
                                                          world.ids["palm_other"]])


def test_a_different_zip_is_a_different_house(world, cc):
    cc.projects = [project(1, "Bo Diaz", "123 Main St", postal="33186")]
    run_command("--commit")
    assert links() == []


def test_the_dry_run_writes_nothing(world, cc):
    before = dump()
    code, out, _ = run_command()
    assert code == 0 and "DRY RUN" in out
    assert dump() == before
    assert links() == []


def test_a_second_commit_changes_nothing(world, cc):
    run_command("--commit")
    after_first = dump()
    code, out, _ = run_command("--commit", "--json")
    counts = json.loads(out)
    assert dump() == after_first
    assert counts["already_linked"] == 5 and counts["linked_one"] == 0 \
        and counts["linked_several"] == 0 and counts["linked_workiz_job"] == 0
    assert counts["name_only_review"] == 1 and counts["unmatched"] == 1


def test_the_report_prints_counts_and_no_customer_data(world, cc):
    _, out, _ = run_command()
    for secret in ("Diaz", "Rowe", "Nakamura", "Quill", "Main", "Bay", "Palm", "Nowhere",
                   "WZ77", TOKEN):
        assert secret.lower() not in out.lower(), secret
    for label in ("projects scanned", "linked by Workiz job number",
                  "linked to one card", "linked to several", "name-only, for review",
                  "unmatched", "already linked"):
        assert label in out


def test_linking_never_creates_a_contact_or_a_card(world, cc):
    count = lambda: read(lambda db: (db.scalar(select(func.count(Contact.id))),  # noqa: E731
                                     db.scalar(select(func.count(Opportunity.id)))))
    before = count()
    run_command("--commit")
    assert count() == before
    assert cc.writes() == []


def test_an_unlinked_project_is_not_linked_back_by_the_next_sweep(world, cc):
    run_command("--commit")
    r = world.delete("/api/opportunities/%d/companycam/projects/1" % world.ids["main"],
                     headers=h(world, "admin"))
    assert r.status_code == 200
    run_command("--commit")
    body = world.get("/api/opportunities/%d/companycam" % world.ids["main"],
                     headers=h(world, "admin")).json()
    assert [p["id"] for p in body["projects"]] == ["7"]


def test_unlinking_is_admin_only_and_a_refusal_changes_nothing(world, cc):
    run_command("--commit")
    before = links()
    r = world.delete("/api/opportunities/%d/companycam/projects/1" % world.ids["main"],
                     headers=h(world, "dispatcher"))
    assert r.status_code == 403
    assert links() == before
    assert read(lambda db: db.scalar(select(func.count()).where(
        CompanyCamLink.unlinked_at.is_not(None)))) == 0


def test_the_review_list_links_by_hand_or_dismisses(world, cc):
    run_command("--commit")
    assert world.get("/api/companycam/review", headers=h(world, "dispatcher")).status_code == 403
    items = world.get("/api/companycam/review", headers=h(world, "admin")).json()
    assert [i["project_name"] for i in items] == ["Cy Nakamura leak"]
    assert {c["title"] for c in items[0]["candidates"]} == {"Cy leak", "Secret job"}
    r = world.post("/api/companycam/review/%d/link" % items[0]["id"],
                   json={"opportunity_ids": [world.ids["cy"]]}, headers=h(world, "admin"))
    assert r.status_code == 200
    assert ("4", world.ids["cy"], "manual") in links()
    assert world.get("/api/companycam/review", headers=h(world, "admin")).json() == []
    # A second linking run does not put it back on the list.
    run_command("--commit")
    assert world.get("/api/companycam/review", headers=h(world, "admin")).json() == []


# ================================================================== hourly check

def test_the_hourly_check_links_a_new_project_and_records_a_heartbeat(world, cc, monkeypatch):
    monkeypatch.setenv("COMPANYCAM_SYNC_ENABLED", "true")
    first = datetime.now(UTC) - timedelta(hours=2)
    counts = read(lambda db: companycam.sync_once(db, now=first))
    assert counts["full_sweep"] is True and counts["scanned"] == 7
    assert len(links()) == 6

    # Later, a new card, and CompanyCam gains a project at its address.
    db = SessionLocal()
    lead = world.ids["lead"]
    new = Opportunity(title="Eve roof", contact_id=world.ids["c_bo"],
                      pipeline_id=world.ids["retail"],
                      stage_id=lead, address_street="600 Gulf Dr")
    db.add(new)
    db.commit()
    new_id = new.id
    db.close()
    cc.projects.append(project(8, "Eve", "600 Gulf Drive", updated_at=NOW))
    cc.requests.clear()

    companycam.tick(SessionLocal)          # due: the last run started two hours ago
    assert ("8", new_id, "address") in links()
    since = [int(r.url.params["modified_since"]) for r in cc.requests
             if r.url.path == "/v2/projects"]
    assert since and since[0] <= int(first.timestamp()), "asks only for what changed since"
    beat = world.get("/api/companycam/status", headers=h(world, "admin")).json()
    assert beat["heartbeat"]["last_error"] is None
    assert beat["heartbeat"]["last_success_at"] is not None
    assert beat["heartbeat"]["last_counts"]["linked_one"] == 1
    assert beat["heartbeat"]["last_counts"]["full_sweep"] is False
    assert TOKEN not in json.dumps(beat)

    # Not due again within the hour: the next tick reads nothing.
    cc.requests.clear()
    companycam.tick(SessionLocal)
    assert [r for r in cc.requests if r.url.path == "/v2/projects"] == []


def test_the_heartbeat_is_readable_from_the_command(world, cc):
    read(lambda db: companycam.sync_once(db))
    code, out, _ = run_command("--status")
    body = json.loads(out)
    assert code == 0 and body["heartbeat"]["last_success_at"] and body["token_set"] is True
    assert TOKEN not in out


@pytest.mark.parametrize("failure", ["http500", "http401", "down"])
def test_an_outage_or_a_bad_token_degrades_quietly(world, cc, failure):
    read(lambda db: companycam.sync_once(db))          # a good run first
    cursor = read(lambda db: db.get(CompanyCamSyncState, 1).modified_cursor)
    if failure == "down":
        cc.down = True
    else:
        cc.fail = 500 if failure == "http500" else 401
    companycam.clear_cache()

    counts = read(lambda db: companycam.sync_once(
        db, now=datetime.now(UTC) + timedelta(hours=2)))
    state = read(lambda db: db.get(CompanyCamSyncState, 1))
    assert state.last_error and counts == {"full_sweep": False}
    assert state.modified_cursor == cursor, "a failed hour is asked again from the same place"

    opp = world.ids["main"]
    tab = world.get("/api/opportunities/%d/companycam" % opp, headers=h(world, "tech"))
    assert tab.status_code == 200
    assert tab.json() == {"state": "unavailable", "message": "Photos are unavailable right now",
                          "projects": []}
    photos = world.get("/api/opportunities/%d/companycam/projects/1/photos" % opp,
                       headers=h(world, "tech")).json()
    assert photos["state"] == "unavailable" and photos["photos"] == []
    # Nothing else breaks: the card itself, the board and the command's exit code.
    assert world.get("/api/opportunities/%d" % opp, headers=h(world, "tech")).status_code == 200
    assert world.get("/api/opportunities?pipeline_id=%d" % world.ids["retail"],
                     headers=h(world, "tech")).status_code == 200
    code, _, err = run_command()
    assert code == 6 and "Nothing was written" in err and TOKEN not in err


def test_with_no_token_nothing_is_read_and_the_tab_says_so(world, cc, monkeypatch):
    run_command("--commit")
    monkeypatch.delenv("COMPANYCAM_API_TOKEN")
    cc.requests.clear()
    body = world.get("/api/opportunities/%d/companycam" % world.ids["main"],
                     headers=h(world, "admin")).json()
    assert body["state"] == "off" and body["projects"] == []
    companycam.tick(SessionLocal)
    world.post("/api/opportunities", headers=h(world, "admin"), json={
        "title": "No token", "pipeline_id": world.ids["retail"], "stage_id": world.ids["lead"],
        "address_street": "1 Token Rd"})
    assert cc.requests == []
    assert read(lambda db: db.scalar(select(func.count(CompanyCamProjectRequest.id)))) == 0


# ================================================================== the Photos tab

def seed_photos(cc):
    cc.photos["1"] = [photo("p-old", 1, NOW - 9000),
                      photo("p-new", 1, NOW - 100, annotated=True, description="Ridge vent"),
                      photo("p-mid", 1, NOW - 5000, creator="Luis")]
    cc.photos["7"] = [photo("q-1", 7, NOW - 50)]
    cc.photos["6"] = [photo("s-1", 6, NOW - 10)]


def test_the_photos_tab_lists_newest_first_and_prefers_the_annotated_image(world, cc):
    run_command("--commit")
    seed_photos(cc)
    opp = world.ids["main"]
    body = world.get("/api/opportunities/%d/companycam" % opp, headers=h(world, "tech")).json()
    assert body["state"] == "ok"
    assert {p["id"] for p in body["projects"]} == {"1", "7"}
    assert body["projects"][0]["project_url"].startswith("https://app.companycam.com/projects/")

    page = world.get("/api/opportunities/%d/companycam/projects/1/photos" % opp,
                     headers=h(world, "tech")).json()
    assert [p["id"] for p in page["photos"]] == ["p-new", "p-mid", "p-old"]
    newest = page["photos"][0]
    assert newest["annotated"] is True and newest["description"] == "Ridge vent"
    assert newest["creator_name"] == "Antonio Brown"
    assert newest["captured_at"] == datetime.fromtimestamp(NOW - 100, UTC).isoformat()
    assert page["has_more"] is False

    img = world.get(newest["image_url"], headers=h(world, "tech"))
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert img.content.startswith(b"IMG:webann"), "the annotated web image, relayed"
    thumb = world.get(page["photos"][2]["thumbnail_url"], headers=h(world, "tech"))
    assert thumb.content.startswith(b"IMG:thumb/"), "no annotation: the plain thumbnail"
    assert "private" in img.headers["cache-control"]

    # The token went to CompanyCam's API and nowhere else; the browser saw neither it
    # nor a single CompanyCam image URI.
    for r in cc.requests:
        if r.url.host == IMG:
            assert "authorization" not in r.headers
    for text_ in (json.dumps(body), json.dumps(page), str(dict(img.headers))):
        assert TOKEN not in text_ and IMG not in text_


def test_large_projects_are_paged_and_refresh_refetches(world, cc):
    run_command("--commit")
    cc.photos["1"] = [photo("p%03d" % i, 1, NOW - i) for i in range(120)]
    url = "/api/opportunities/%d/companycam/projects/1/photos" % world.ids["main"]
    first = world.get(url, headers=h(world, "tech")).json()
    assert len(first["photos"]) == 50 and first["has_more"] is True
    third = world.get(url + "?page=3", headers=h(world, "tech")).json()
    assert len(third["photos"]) == 20 and third["has_more"] is False

    cc.photos["1"].insert(0, photo("fresh", 1, NOW + 10))
    cc.requests.clear()
    cached = world.get(url, headers=h(world, "tech")).json()
    assert cc.requests == [] and "fresh" not in [p["id"] for p in cached["photos"]]
    refreshed = world.get(url + "?refresh=true", headers=h(world, "tech")).json()
    assert len(cc.requests) == 1 and "fresh" in [p["id"] for p in refreshed["photos"]]


def test_a_project_not_linked_to_the_card_is_not_served(world, cc):
    run_command("--commit")
    seed_photos(cc)
    r = world.get("/api/opportunities/%d/companycam/projects/1/photos" % world.ids["bay1"],
                  headers=h(world, "admin"))
    assert r.status_code == 404
    # A photo whose project is linked to no card at all is not relayed either.
    cc.photos["5"] = [photo("n-1", 5, NOW)]
    assert world.get("/api/companycam/photos/n-1/web", headers=h(world, "admin")).status_code == 404


def test_per_pipeline_permission_hides_photos_projects_and_images(world, cc):
    run_command("--commit")
    seed_photos(cc)
    secret = world.ids["secret"]
    for who, visible in (("outsider", False), ("tech", False), ("dispatcher", True),
                         ("admin", True)):
        tab = world.get("/api/opportunities/%d/companycam" % secret, headers=h(world, who))
        photos = world.get("/api/opportunities/%d/companycam/projects/6/photos" % secret,
                           headers=h(world, who))
        assert (tab.status_code == 200) is visible, who
        assert (photos.status_code == 200) is visible, who
        if not visible:
            assert tab.status_code == photos.status_code == 404
            assert tab.json() == {"detail": "opportunity not found"}, \
                "the same answer as a card that does not exist"
        # Warm the photo index as a reader who may see it, then ask as this user.
        world.get("/api/opportunities/%d/companycam/projects/6/photos" % secret,
                  headers=h(world, "admin"))
        img = world.get("/api/companycam/photos/s-1/web", headers=h(world, who))
        assert (img.status_code == 200) is visible, who
        panel = world.get("/api/contacts/%d/companycam" % world.ids["c_cy"],
                          headers=h(world, who)).json()
        assert ("6" in [p["id"] for p in panel["projects"]]) is visible, who
        assert ("Secret job" in json.dumps(panel)) is visible, who


def test_the_contact_panel_lists_every_project_on_the_customers_cards(world, cc):
    run_command("--commit")
    cc.requests.clear()
    body = world.get("/api/contacts/%d/companycam" % world.ids["c_ada"],
                     headers=h(world, "tech")).json()
    assert body["state"] == "ok"
    assert [(p["id"], p["name"], sorted(o["title"] for o in p["opportunities"]))
            for p in body["projects"]] == [("2", "Rowe", ["Ada roof 2024", "Ada roof 2026"])]
    assert cc.requests == [], "names come from the link rows: CompanyCam is not asked"
    assert world.get("/api/contacts/999999/companycam", headers=h(world, "tech")).status_code == 404


def test_deleting_a_card_takes_its_links_and_is_not_refused(world, cc):
    run_command("--commit")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    engine.dispose()
    try:
        assert read(lambda db: db.execute(text("PRAGMA foreign_keys")).scalar()) == 1
        r = world.delete("/api/opportunities/%d" % world.ids["main"], headers=h(world, "admin"))
        assert r.status_code == 200, r.text
        assert [link for link in links() if link[1] == world.ids["main"]] == []
        assert cc.writes() == [], "CompanyCam is not touched by a delete"
    finally:
        event.remove(engine, "connect", _fk_on)
        engine.dispose()


# ================================================================== creation

def add_card(c, who="admin", **kw):
    body = {"title": "New job", "pipeline_id": c.ids["retail"], "stage_id": c.ids["lead"],
            "contact_id": c.ids["c_bo"]}
    body.update(kw)
    r = c.post("/api/opportunities", json=body, headers=h(c, who))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def work():
    return read(lambda db: companycam.process_project_requests(db))


def test_creation_is_on_by_default_and_creates_exactly_once(world, cc):
    assert companycam.create_enabled() is True, "a token alone switches creation on"
    opp = add_card(world, address_street="321 Gulf Dr", address_city="Holmes Beach",
                   address_state="FL", address_postal_code="34217")
    assert cc.requests == [], "the request is recorded; the worker does the talking"
    assert work()["created"] == 1
    posts = [r for r in cc.requests if r.method == "POST"]
    assert len(posts) == 1
    sent = json.loads(posts[0].content)
    assert sent["name"] == "CRM %d - Bo Diaz" % opp
    assert sent["address"] == {"street_address_1": "321 Gulf Dr", "city": "Holmes Beach",
                               "state": "FL", "postal_code": "34217", "country": "US"}
    assert sent["primary_contact"] == {"name": "Bo Diaz", "phone_number": "+19415550122"}
    made = [lk for lk in links() if lk[1] == opp]
    assert len(made) == 1 and made[0][2] == "created"

    # Again, and again through every door: never a second project for this card.
    assert work() == {"linked": 0, "created": 0, "skipped": 0, "failed": 0, "retry": 0}
    r = world.patch("/api/opportunities/%d/detail" % opp, headers=h(world, "admin"),
                    json={"address_street": "322 Gulf Dr"})
    assert r.status_code == 200
    work()
    assert len([r for r in cc.requests if r.method == "POST"]) == 1
    assert read(lambda db: db.scalar(select(func.count(CompanyCamProjectRequest.id)).where(
        CompanyCamProjectRequest.opportunity_id == opp))) == 1


def test_an_existing_project_at_the_address_is_linked_instead_of_created(world, cc):
    cc.projects.append(project(55, "Workiz J55 - BO DIAZ", "808 Coquina Way", postal="34217"))
    opp = add_card(world, address_street="808 Coquina Way", address_postal_code="34217")
    assert work()["linked"] == 1
    assert cc.writes() == []
    assert ("55", opp, "address") in links()


def test_a_crash_after_creating_does_not_create_twice(world, cc):
    opp = add_card(world, address_street="12 Pine St")
    # The first attempt's POST reached CompanyCam but our commit never happened.
    cc.projects.append(project(77, "CRM %d - Bo Diaz" % opp, "12 Pine St", updated_at=NOW))
    db = SessionLocal()
    req = db.scalar(select(CompanyCamProjectRequest))
    req.state, req.attempts = "creating", 1
    db.commit()
    db.close()
    assert work()["linked"] == 1
    assert cc.writes() == []
    assert ("77", opp, "address") in links()


def test_a_card_without_an_address_creates_when_its_first_address_is_saved(world, cc):
    opp = add_card(world)
    assert work()["created"] == 0 and cc.writes() == []
    r = world.patch("/api/opportunities/%d/detail" % opp, headers=h(world, "admin"),
                    json={"address_street": "44 Sand Ct"})
    assert r.status_code == 200
    assert work()["created"] == 1
    assert cc.writes() == [("POST", "/v2/projects")]


def test_a_workiz_card_never_creates_it_only_links(world, cc, tmp_path):
    from tests import test_workiz_import as wz_t
    # The importer's own card, through the importer.
    db = SessionLocal()
    wz_t.do_import(db, tmp_path, [wz_t.client_row("C9", "Wiz Customer", phone="9415550199")],
                   [wz_t.job_row("J900", "Wiz Customer", phone="9415550199",
                                 address="900 Import Rd", city="Bradenton")])
    imported = db.scalar(select(Opportunity).where(
        Opportunity.custom_fields["workiz_id"].as_string() == "J900")).id
    db.close()
    # And the fixture's Workiz card, given an address by hand.
    for opp, street in ((world.ids["workiz"], "78 Palm Ave"), (imported, "901 Import Rd")):
        r = world.patch("/api/opportunities/%d/detail" % opp, headers=h(world, "admin"),
                        json={"address_street": None})
        r = world.patch("/api/opportunities/%d/detail" % opp, headers=h(world, "admin"),
                        json={"address_street": street})
        assert r.status_code == 200
    work()
    assert cc.writes() == []
    assert read(lambda db: db.scalar(select(func.count(CompanyCamProjectRequest.id)))) == 0
    # It still links, by its job number, on the next linking run.
    cc.projects.append(project(900, "Workiz J900 - WIZ CUSTOMER", "900 Import Rd"))
    run_command("--commit")
    assert ("900", imported, "workiz_job") in links()


def test_an_ahs_email_card_creates_its_project_named_after_the_job(world, cc):
    from tests import test_ahs_jobs as ahs_t
    db = SessionLocal()
    ahs = Pipeline(name="Dream Team Roofing AHS", position=5)
    db.add(ahs)
    db.flush()
    db.add(Stage(pipeline_id=ahs.id, name="New Lead", position=0))
    db.commit()
    db.close()
    r = world.post("/api/ahs-jobs", json=ahs_t.order(), headers=h(world, "dispatcher"))
    assert r.status_code == 201, r.text
    card = r.json()["opportunity"]["id"]
    assert work()["created"] == 1
    sent = json.loads(next(r for r in cc.requests if r.method == "POST").content)
    assert sent["name"] == "AHS 66450639 - Guillermo Escala"
    assert sent["primary_contact"] == {"name": "Guillermo Escala",
                                       "email": "scalas02@example.test",
                                       "phone_number": "+13059629757"}
    assert [lk[2] for lk in links() if lk[1] == card] == ["created"]
    # A repeat delivery of the same email asks for nothing more.
    assert world.post("/api/ahs-jobs", json=ahs_t.order(),
                      headers=h(world, "dispatcher")).status_code == 200
    work()
    assert len(cc.writes()) == 1


def test_the_switch_turns_creation_off(world, cc, monkeypatch):
    monkeypatch.setenv("COMPANYCAM_CREATE_PROJECTS", "false")
    add_card(world, address_street="1 Off Rd")
    work()
    assert cc.requests == []
    assert read(lambda db: db.scalar(select(func.count(CompanyCamProjectRequest.id)))) == 0


def test_a_failed_create_is_retried_then_given_up_and_breaks_nothing(world, cc):
    opp = add_card(world, address_street="5 Retry Ln")
    cc.fail = 503
    for _ in range(companycam.CREATE_MAX_ATTEMPTS):
        work()
    req = read(lambda db: db.scalar(select(CompanyCamProjectRequest)))
    assert req.state == "failed" and req.attempts == companycam.CREATE_MAX_ATTEMPTS
    assert world.get("/api/opportunities/%d" % opp, headers=h(world, "admin")).status_code == 200


# ================================================================== the one write

def test_the_create_post_is_the_only_non_get_this_package_ever_sends(world, cc, tmp_path,
                                                                      monkeypatch):
    """Everything the package does, in one scenario, through the recording transport."""
    monkeypatch.setenv("COMPANYCAM_SYNC_ENABLED", "true")
    from tests import test_ahs_jobs as ahs_t
    from tests import test_workiz_import as wz_t
    seed_photos(cc)
    a = SessionLocal()
    # The importer makes the AHS board, New Lead and all, which the AHS email then uses.
    wz_t.do_import(a, tmp_path, [wz_t.client_row("C1", "Wiz One", phone="9415550191")],
                   [wz_t.job_row("J1", "Wiz One", phone="9415550191", address="1 Wiz Way")])
    a.close()

    run_command()
    run_command("--commit")
    run_command("--status")
    companycam.tick(SessionLocal)
    hand = add_card(world, address_street="10 Hand St")
    later = add_card(world)
    world.patch("/api/opportunities/%d/detail" % later, headers=h(world, "admin"),
                json={"address_street": "11 Later St", "title": "Renamed"})
    ahs_card = world.post("/api/ahs-jobs", json=ahs_t.order(),
                          headers=h(world, "dispatcher")).json()["opportunity"]["id"]
    world.patch("/api/opportunities/%d/detail" % world.ids["workiz"],
                headers=h(world, "admin"), json={"address_street": None})
    world.patch("/api/opportunities/%d/detail" % world.ids["workiz"],
                headers=h(world, "admin"), json={"address_street": "79 Palm Ave"})
    for _ in range(2):
        companycam.tick(SessionLocal)
    for who in ("admin", "tech"):
        world.get("/api/opportunities/%d/companycam?refresh=true" % world.ids["main"],
                  headers=h(world, who))
        world.get("/api/opportunities/%d/companycam/projects/1/photos" % world.ids["main"],
                  headers=h(world, who))
        world.get("/api/companycam/photos/p-new/web", headers=h(world, who))
        world.get("/api/companycam/photos/p-old/thumbnail", headers=h(world, who))
        world.get("/api/contacts/%d/companycam" % world.ids["c_ada"], headers=h(world, who))
    world.get("/api/companycam/status", headers=h(world, "admin"))
    world.get("/api/companycam/review", headers=h(world, "admin"))
    world.delete("/api/opportunities/%d/companycam/projects/1" % world.ids["main"],
                 headers=h(world, "admin"))
    world.delete("/api/opportunities/%d" % world.ids["bay1"], headers=h(world, "admin"))

    posts = [r for r in cc.requests if r.method != "GET"]
    assert [(r.method, r.url.host, r.url.path) for r in posts] == \
        [("POST", API, "/v2/projects")] * 3
    names = sorted(json.loads(r.content)["name"] for r in posts)
    assert names == sorted(["CRM %d - Bo Diaz" % hand, "CRM %d - Bo Diaz" % later,
                            "AHS 66450639 - Guillermo Escala"])
    created_for = read(lambda db: sorted(
        r.opportunity_id for r in db.scalars(select(CompanyCamProjectRequest))
        if r.state == "created"))
    assert created_for == sorted([hand, later, ahs_card])
    assert any(r.url.host == IMG for r in cc.requests), "the scenario did relay images"


def test_every_other_method_is_refused_before_it_reaches_the_wire(cc):
    for method, path in (("DELETE", "/projects/1"), ("PUT", "/projects/1"),
                         ("PATCH", "/projects/1"), ("POST", "/projects/1/photos"),
                         ("POST", "/webhooks"), ("POST", "/photos/1/tags")):
        with pytest.raises(companycam.CompanyCamError) as err:
            companycam._request(method, path, body={})
        assert err.value.kind == "refused_write"
    assert cc.requests == []


def test_the_source_holds_one_write_and_one_place_that_talks_to_companycam():
    """Over the source: the only non-GET `_request` call is the create, and no other
    module in the package reaches CompanyCam's hosts or its token."""
    import pathlib

    import app as app_pkg

    def code_only(source: str) -> str:
        out = []
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                continue
            out.append(tok.string)
        return " ".join(out)

    root = pathlib.Path(app_pkg.__file__).parent
    cc_code = code_only((root / "companycam.py").read_text(encoding="utf-8"))
    methods = re.findall(r'_request \( "(\w+)"', cc_code)
    assert sorted(set(methods)) == ["GET", "POST"]
    assert methods.count("POST") == 1
    assert re.search(r'_request \( "POST" , PROJECTS_PATH', cc_code)
    assert not re.search(r"\.(post|put|patch|delete) \(", cc_code), \
        "every call to CompanyCam goes through _request's guard"
    for path in sorted(root.rglob("*.py")):
        if path.name == "companycam.py":
            continue
        src = path.read_text(encoding="utf-8")
        assert "api.companycam.com" not in src, path.name
        assert not re.search(r"(getenv|environ)[^\n]*COMPANYCAM_API_TOKEN", src), \
            "%s reads the CompanyCam token; only companycam.py may" % path.name
