"""The call Checklist (2026-09-14) — behaviour, read back.

The Checklist is ordinary custom fields in one tab called "Checklist", so every rule
custom fields already had still holds and is not re-tested here. What is new:

  * **Question settings round-trip** through `/api/custom-fields`: a paragraph type, a
    script on any question, a yes/no that shows the contact's email or the card's
    address, a dropdown whose chosen option opens a details box. A refused setting
    writes nothing.
  * **A details answer lives beside its field** and follows every rule the main answer
    does: kept when not sent, kept when the field is archived, refused on a pipeline
    that is not asked.
  * **The linked values are the REAL ones** — the contact's email and the card's
    address — so their rules are the contact PATCH's and the detail PATCH's: a TECH is
    refused and nothing moves; a bad email is refused and nothing moves.
  * **The card badge counts only this card's pipeline's questions**, and there is no
    badge where the pipeline asks none.
  * **The Add modal's one submit** files status, owner, followers, business name, source,
    the address and the checklist answers together, and the API still creates a card
    with no contact for the machine paths.
  * **`python -m app.checklist_seed`**: a dry run writes nothing, a commit creates one tab
    and 16 questions on the right pipelines, a second commit changes nothing, and the
    owner's later edits survive a re-run.
  * **The migration only adds** three nullable columns.
"""
import ast
import contextlib
import json
import pathlib
import tempfile

import pytest
from alembic import command
from app import checklist_seed
from app.db import SessionLocal
from app.models import (
    Contact,
    CustomFieldDef,
    CustomFieldGroup,
    CustomFieldPipeline,
    Opportunity,
    OpportunityFollower,
    User,
)
from sqlalchemy import create_engine, func, inspect, select, text
from tests import test_custom_fields as cf
from tests.test_migration_drift import _config

FIELDS, _as = cf.FIELDS, cf._as
# The custom-field suite's world: AHS and Retail, one deal on each, three roles.
client = cf.client

BACKEND = pathlib.Path(__file__).resolve().parent.parent
GROUPS = "/api/custom-field-groups"


def read(fn):
    db = SessionLocal()
    try:
        return fn(db)
    finally:
        db.close()


def defs_snapshot():
    return read(lambda db: [
        (d.id, d.key, d.label, d.field_type, d.options, d.script, d.linked_field,
         d.details_when, d.group_id, d.archived_at, d.position,
         sorted(p.pipeline_id for p in d.pipelines))
        for d in db.scalars(select(CustomFieldDef).order_by(CustomFieldDef.id)).all()])


def groups_snapshot():
    return read(lambda db: [(g.id, g.name, g.position) for g in db.scalars(
        select(CustomFieldGroup).order_by(CustomFieldGroup.id)).all()])


def blob(client, deal):
    return client.get("/api/opportunities/%d" % client.ids[deal]).json()["custom_fields"]


def patch_answers(client, deal, answers):
    return client.patch("/api/opportunities/%d/detail" % client.ids[deal],
                        json={"custom_fields": answers})


def checklist_group(client, name="Checklist"):
    r = client.post(GROUPS, json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def define(client, **body):
    body.setdefault("pipeline_ids", [client.ids["ahs"]])
    r = client.post(FIELDS, json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ============================================================== settings

def test_the_new_settings_round_trip_through_the_api(client):
    para = define(client, label="What's happening there?", field_type="paragraph",
                  script="May you tell me what's happening there?")
    tick = define(client, label="Email verified", field_type="boolean",
                  linked_field="contact_email")
    repair = define(client, label="Any previous repair?", field_type="dropdown",
                    options=["Yes", "No", "Unknown"], details_when=["Yes"])

    listed = {d["key"]: d for d in client.get(FIELDS).json()}
    assert listed[para["key"]]["field_type"] == "paragraph"
    assert listed[para["key"]]["script"] == "May you tell me what's happening there?"
    assert listed[para["key"]]["linked_field"] is None
    assert listed[tick["key"]]["linked_field"] == "contact_email"
    assert listed[repair["key"]]["details_when"] == ["Yes"]
    assert listed[repair["key"]]["script"] is None

    # The owner rewords the script, moves the link, changes the trigger.
    r = client.patch("%s/%d" % (FIELDS, para["id"]), json={"script": "  What is going on? "})
    assert r.json()["script"] == "What is going on?"
    r = client.patch("%s/%d" % (FIELDS, tick["id"]),
                     json={"linked_field": "opportunity_address"})
    assert r.json()["linked_field"] == "opportunity_address"
    r = client.patch("%s/%d" % (FIELDS, repair["id"]), json={"details_when": ["Yes", "Unknown"]})
    assert r.json()["details_when"] == ["Yes", "Unknown"]
    # ...and clears them.
    client.patch("%s/%d" % (FIELDS, para["id"]), json={"script": ""})
    client.patch("%s/%d" % (FIELDS, tick["id"]), json={"linked_field": None})
    client.patch("%s/%d" % (FIELDS, repair["id"]), json={"details_when": []})
    listed = {d["key"]: d for d in client.get(FIELDS).json()}
    assert listed[para["key"]]["script"] is None
    assert listed[tick["key"]]["linked_field"] is None
    assert listed[repair["key"]]["details_when"] == []


@pytest.mark.parametrize("body, says", [
    ({"label": "Roof age", "field_type": "text", "linked_field": "contact_email"},
     "only a yes/no question"),
    ({"label": "Verified", "field_type": "boolean", "linked_field": "contact_phone"},
     "not something a question can show"),
    ({"label": "Repair", "field_type": "dropdown", "options": ["Yes", "No"],
      "details_when": ["Maybe"]}, "not one of this question's choices"),
    ({"label": "Repair notes", "field_type": "text", "details_when": ["Yes"]},
     "only a dropdown question can open a details box"),
    ({"label": "Too long", "field_type": "text", "script": "x" * 1001}, "limited to 1000"),
    ({"label": "Notes", "field_type": "essay"}, "is not a field type"),
])
def test_a_refused_setting_creates_nothing(client, body, says):
    before = defs_snapshot()
    r = client.post(FIELDS, json={**body, "pipeline_ids": [client.ids["ahs"]]})
    assert r.status_code == 400 and says in r.json()["detail"], r.text
    assert defs_snapshot() == before


def test_a_refused_setting_change_changes_nothing(client):
    tick = define(client, label="Email verified", field_type="boolean",
                  linked_field="contact_email", script="Read it back to them.")
    before = defs_snapshot()
    for body in ({"linked_field": "nope"}, {"details_when": ["Yes"]}):
        r = client.patch("%s/%d" % (FIELDS, tick["id"]), json=body)
        assert r.status_code == 400
    assert defs_snapshot() == before


def test_retiring_a_choice_stops_it_opening_the_details_box(client):
    repair = define(client, label="Any previous repair?", field_type="dropdown",
                    options=["Yes", "No", "Partly"], details_when=["Yes", "Partly"])
    r = client.patch("%s/%d" % (FIELDS, repair["id"]), json={"options": ["Yes", "No"]})
    assert r.json()["details_when"] == ["Yes"]


def test_only_an_admin_changes_a_setting(client):
    para = define(client, label="What's happening there?", field_type="paragraph",
                  script="Ask what is happening.")
    before = defs_snapshot()
    for role in ("dispatcher", "tech"):
        r = _as(client, role).patch("%s/%d" % (FIELDS, para["id"]),
                                    json={"script": "rewritten"})
        assert r.status_code == 403
        r = _as(client, role).post(FIELDS, json={
            "label": "Sneaky", "field_type": "paragraph", "pipeline_ids": []})
        assert r.status_code == 403
    assert defs_snapshot() == before


# ============================================================== answers

def test_a_paragraph_keeps_its_lines_and_has_a_limit(client):
    para = define(client, label="What's happening there?", field_type="paragraph")
    story = "Water in the bedroom ceiling.\nStarted after the storm.\n\nTwo stains."
    assert patch_answers(client, "ahs_deal", {para["key"]: story}).status_code == 200
    assert blob(client, "ahs_deal")[para["key"]] == story

    r = patch_answers(client, "ahs_deal", {para["key"]: "x" * 5001})
    assert r.status_code == 400 and "limited to 5000" in r.json()["detail"]
    assert blob(client, "ahs_deal")[para["key"]] == story


def test_the_details_answer_is_stored_beside_the_main_one(client):
    repair = define(client, label="Any previous repair?", field_type="dropdown",
                    options=["Yes", "No", "Unknown"], details_when=["Yes"],
                    pipelines=[client.ids["ahs"], client.ids["retail"]])
    key, details = repair["key"], repair["key"] + "__details"
    r = patch_answers(client, "ahs_deal", {key: "Yes", details: "  Patched in 2021  "})
    assert r.status_code == 200, r.text
    answers = blob(client, "ahs_deal")
    assert answers[key] == "Yes" and answers[details] == "Patched in 2021"
    # The telephony keys the fixture put on this deal are still there.
    assert answers["owen_call_id"] == "call-abc-123"

    # Not sent = kept. Changing the main answer does not throw the details away.
    patch_answers(client, "ahs_deal", {key: "No"})
    assert blob(client, "ahs_deal")[details] == "Patched in 2021"
    # Clearing it is explicit.
    patch_answers(client, "ahs_deal", {details: ""})
    assert details not in blob(client, "ahs_deal")

    # A details box is text: an object is refused, and nothing moves.
    before = blob(client, "ahs_deal")
    r = patch_answers(client, "ahs_deal", {details: {"nested": 1}})
    assert r.status_code == 400
    assert blob(client, "ahs_deal") == before


def test_archiving_a_question_keeps_its_answer_and_its_details(client):
    repair = define(client, label="Any previous repair?", field_type="dropdown",
                    options=["Yes", "No"], details_when=["Yes"])
    key, details = repair["key"], repair["key"] + "__details"
    patch_answers(client, "ahs_deal", {key: "Yes", details: "New flashing 2023"})

    assert client.delete("%s/%d" % (FIELDS, repair["id"])).status_code == 200
    # An unrelated save that posts a shorter blob loses neither.
    r = client.patch("/api/opportunities/%d/detail" % client.ids["ahs_deal"],
                     json={"title": "AHS-101 renamed", "custom_fields": {}})
    assert r.status_code == 200
    answers = blob(client, "ahs_deal")
    assert answers[key] == "Yes" and answers[details] == "New flashing 2023"
    # And it cannot be written while archived.
    r = patch_answers(client, "ahs_deal", {details: "changed"})
    assert r.status_code == 400 and "archived" in r.json()["detail"]
    assert blob(client, "ahs_deal")[details] == "New flashing 2023"
    # Restored, it is editable again with the answer intact.
    client.post("%s/%d/restore" % (FIELDS, repair["id"]))
    assert blob(client, "ahs_deal")[details] == "New flashing 2023"


def test_rewording_a_question_keeps_its_answer(client):
    q = define(client, label="Second phone number", field_type="text",
               script="Always ask for a second number.")
    patch_answers(client, "ahs_deal", {q["key"]: "(941) 555-0123"})
    client.patch("%s/%d" % (FIELDS, q["id"]), json={
        "label": "Another number to reach them", "script": "Best contact number?"})
    assert blob(client, "ahs_deal")[q["key"]] == "(941) 555-0123"


def test_a_details_answer_on_a_pipeline_that_does_not_ask_is_refused(client):
    repair = define(client, label="First time using AHS?", field_type="dropdown",
                    options=["Yes", "No"], details_when=["Yes"])   # AHS only
    before = blob(client, "retail_deal")
    r = patch_answers(client, "retail_deal", {repair["key"] + "__details": "hmm"})
    assert r.status_code == 400 and "not asked on this pipeline" in r.json()["detail"]
    assert blob(client, "retail_deal") == before


# ============================================================== the linked values

def test_the_linked_email_is_the_contacts_and_a_tech_cannot_change_it(client):
    """"Email verified" shows and edits the CONTACT's email, through the contact PATCH
    — so its rules are the contact's rules, read back after every refusal."""
    contact = "/api/contacts/%d" % client.ids["contact"]

    def email():
        return read(lambda db: db.get(Contact, client.ids["contact"]).email)

    r = _as(client, "tech").patch(contact, json={"email": "tech@example.test"})
    assert r.status_code == 403
    assert email() is None

    r = _as(client, "dispatcher").patch(contact, json={"email": "not an email"})
    assert r.status_code == 422
    assert email() is None

    r = _as(client, "dispatcher").patch(contact, json={"email": "real@example.test"})
    assert r.status_code == 200
    assert email() == "real@example.test"
    assert client.get("/api/opportunities/%d" % client.ids["ahs_deal"]).json()[
        "contact_email"] == "real@example.test"


def test_the_linked_address_is_the_cards_and_a_tech_cannot_change_it(client):
    url = "/api/opportunities/%d/detail" % client.ids["ahs_deal"]

    def address():
        o = read(lambda db: db.get(Opportunity, client.ids["ahs_deal"]))
        return (o.address_street, o.address_city)

    r = _as(client, "tech").patch(url, json={"address_street": "1 Tech Way",
                                             "custom_fields": {}})
    assert r.status_code == 403
    assert address() == (None, None)
    r = _as(client, "dispatcher").patch(url, json={"address_street": "12 Palm Ave",
                                                   "address_city": "Bradenton"})
    assert r.status_code == 200
    assert address() == ("12 Palm Ave", "Bradenton")


# ============================================================== the card badge

def test_the_card_badge_counts_only_the_questions_its_pipeline_asks(client):
    group = checklist_group(client)
    both = [client.ids["ahs"], client.ids["retail"]]
    a = define(client, label="Second phone number", field_type="text", group_id=group,
               pipeline_ids=both)
    b = define(client, label="Email verified", field_type="boolean", group_id=group,
               linked_field="contact_email", pipeline_ids=both)
    c = define(client, label="Introduced Antonio", field_type="boolean", group_id=group,
               pipeline_ids=[client.ids["ahs"]])
    gone = define(client, label="Old question", field_type="text", group_id=group,
                  pipeline_ids=both)
    # Not in the Checklist: never counted.
    define(client, label="Job type", field_type="text", pipeline_ids=both)
    patch_answers(client, "ahs_deal", {gone["key"]: "kept"})
    client.delete("%s/%d" % (FIELDS, gone["id"]))

    def badge(pipeline, deal):
        rows = client.get("/api/opportunities", params={"pipeline_id": client.ids[pipeline]})
        return next(r for r in rows.json() if r["id"] == client.ids[deal])["checklist"]

    assert badge("ahs", "ahs_deal") == {"answered": 0, "total": 3}
    assert badge("retail", "retail_deal") == {"answered": 0, "total": 2}

    # "No" is an answer; a blank is not.
    patch_answers(client, "ahs_deal", {a["key"]: "   ", c["key"]: False})
    assert badge("ahs", "ahs_deal") == {"answered": 1, "total": 3}
    patch_answers(client, "ahs_deal", {a["key"]: "941-555-0100", b["key"]: True})
    assert badge("ahs", "ahs_deal") == {"answered": 3, "total": 3}
    # Answers on the AHS deal never count on the Retail deal.
    assert badge("retail", "retail_deal") == {"answered": 0, "total": 2}
    # A TECH sees the same badge.
    rows = _as(client, "tech").get("/api/opportunities",
                                   params={"pipeline_id": client.ids["ahs"]}).json()
    assert rows[0]["checklist"] == {"answered": 3, "total": 3}


def test_no_badge_where_the_pipeline_asks_no_checklist_question(client):
    group = checklist_group(client)
    define(client, label="Introduced Antonio", field_type="boolean", group_id=group,
           pipeline_ids=[client.ids["ahs"]])
    other = checklist_group(client, "Roof Inspection")
    define(client, label="Photos taken", field_type="boolean", group_id=other,
           pipeline_ids=[client.ids["retail"]])
    rows = client.get("/api/opportunities", params={"pipeline_id": client.ids["retail"]})
    assert [r["checklist"] for r in rows.json()] == [None]


# ============================================================== one submit

def test_the_add_modal_files_the_whole_card_in_one_post(client):
    group = checklist_group(client)
    phone = define(client, label="Second phone number", field_type="text", group_id=group,
                   pipeline_ids=[client.ids["ahs"]])
    repair = define(client, label="Any previous repair?", field_type="dropdown",
                    options=["Yes", "No"], details_when=["Yes"], group_id=group,
                    pipeline_ids=[client.ids["ahs"]])
    users = read(lambda db: {u.role.value: u.id for u in db.scalars(select(User)).all()})
    before = read(lambda db: db.scalar(select(func.count(Opportunity.id))))

    r = _as(client, "dispatcher").post("/api/opportunities", json={
        "title": "Real Customer - roof leak", "pipeline_id": client.ids["ahs"],
        "stage_id": client.ids["ahs_stage"], "contact_id": client.ids["contact"],
        "value_cents": 125000, "status": "open", "owner_id": users["TECH"],
        "follower_ids": [users["ADMIN"], users["DISPATCHER"]],
        "business_name": " Palm Holdings ", "source": "Phone call",
        "address_street": "12 Palm Ave", "address_city": "Bradenton",
        "custom_fields": {phone["key"]: "(941) 555-0123", repair["key"]: "Yes",
                          repair["key"] + "__details": "Re-roofed half in 2019"}})
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]

    got = client.get("/api/opportunities/%d" % new_id).json()
    assert got["owner_id"] == users["TECH"] and got["business_name"] == "Palm Holdings"
    assert got["source"] == "Phone call" and got["address_street"] == "12 Palm Ave"
    assert {f["id"] for f in got["followers"]} == {users["ADMIN"], users["DISPATCHER"]}
    assert got["custom_fields"] == {phone["key"]: "(941) 555-0123", repair["key"]: "Yes",
                                    repair["key"] + "__details": "Re-roofed half in 2019"}
    assert read(lambda db: db.scalar(select(func.count(Opportunity.id)))) == before + 1


def test_a_refused_follower_files_no_card(client):
    before = read(lambda db: (db.scalar(select(func.count(Opportunity.id))),
                              db.scalar(select(func.count(OpportunityFollower.id)))))
    r = client.post("/api/opportunities", json={
        "title": "Ghost", "pipeline_id": client.ids["ahs"],
        "stage_id": client.ids["ahs_stage"], "contact_id": client.ids["contact"],
        "follower_ids": [99999]})
    assert r.status_code == 400
    r = client.post("/api/opportunities", json={
        "title": "Ghost", "pipeline_id": client.ids["ahs"],
        "stage_id": client.ids["ahs_stage"], "owner_id": 99999})
    assert r.status_code == 400
    assert read(lambda db: (db.scalar(select(func.count(Opportunity.id))),
                            db.scalar(select(func.count(OpportunityFollower.id))))) == before


def test_the_api_still_creates_a_card_with_no_contact_for_machine_paths(client):
    """The Add modal requires a contact; the API does not — the AHS relay, the Workiz
    import and the CLI create cards the modal never sees."""
    r = client.post("/api/opportunities", json={
        "title": "From a machine", "pipeline_id": client.ids["ahs"],
        "stage_id": client.ids["ahs_stage"]})
    assert r.status_code == 201, r.text
    o = read(lambda db: db.get(Opportunity, r.json()["id"]))
    assert o.contact_id is None and o.status == "open" and o.owner_id is None


# ============================================================== the seed command

def counts():
    return read(lambda db: {
        "defs": db.scalar(select(func.count(CustomFieldDef.id))),
        "groups": db.scalar(select(func.count(CustomFieldGroup.id))),
        "links": db.scalar(select(func.count(CustomFieldPipeline.id))),
        "opps": [(o.id, json.dumps(o.custom_fields, sort_keys=True)) for o in db.scalars(
            select(Opportunity).order_by(Opportunity.id)).all()]})


def seed(commit: bool):
    db = SessionLocal()
    try:
        return checklist_seed.run(db, commit=commit)
    finally:
        db.close()


def test_the_seed_dry_run_writes_nothing_and_says_what_it_would_create(client, capsys):
    define(client, label="Job type", field_type="text",
           pipeline_ids=[client.ids["ahs"], client.ids["retail"]])
    before, defs, groups = counts(), defs_snapshot(), groups_snapshot()
    assert checklist_seed.main([]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN — nothing was written" in out
    assert "questions: 16 to create, 0 already exist" in out
    assert "Service address verified (boolean, key checklist_service_address_verified)" in out
    assert "Dream Team Roofing AHS + Retail" in out
    assert "details box when: Yes" in out
    assert counts() == before and defs_snapshot() == defs and groups_snapshot() == groups


def test_the_seed_commit_creates_one_tab_and_sixteen_questions_and_a_rerun_changes_nothing(
        client):
    define(client, label="Job type", field_type="dropdown", options=["Leak", "Replace"],
           pipeline_ids=[client.ids["ahs"], client.ids["retail"]])
    before = counts()
    report = seed(commit=True)
    assert report["created"] == 16 and report["exists"] == 0
    after = counts()
    assert after["defs"] == before["defs"] + 16
    assert after["groups"] == before["groups"] + 1
    assert after["links"] == before["links"] + 13 * 2 + 3
    assert after["opps"] == before["opps"], "the seed touched an answer"

    fields = {d["key"]: d for d in client.get(FIELDS).json()}
    groups = client.get(GROUPS).json()
    [checklist] = [g for g in groups if g["name"] == "Checklist"]
    ours = [d for d in client.get(FIELDS).json() if d["group_id"] == checklist["id"]]
    assert [d["label"] for d in sorted(ours, key=lambda d: d["position"])] == [
        q[1] for q in checklist_seed.QUESTIONS]
    ahs_only = {d["label"] for d in ours if d["pipeline_ids"] == [client.ids["ahs"]]}
    assert ahs_only == {"First time using AHS for roofing?",
                        "Explained the AHS repair process", "Introduced Antonio"}
    assert all(sorted(d["pipeline_ids"]) == sorted([client.ids["ahs"], client.ids["retail"]])
               for d in ours if d["label"] not in ahs_only)
    assert fields["job_type"]["group_id"] is None, "job_type moved into the Checklist"
    assert fields["checklist_service_address_verified"]["linked_field"] == \
        "opportunity_address"
    assert fields["checklist_email_verified"]["linked_field"] == "contact_email"
    assert fields["checklist_whats_happening"]["field_type"] == "paragraph"
    assert fields["checklist_previous_repair"]["details_when"] == ["Yes"]
    assert fields["checklist_roof_age"]["options"][1] == "5\u201310 yrs"
    assert fields["checklist_introduced_antonio"]["script"].startswith("Antonio has over 28")

    # Retail asks 13, AHS 16.
    rows = client.get("/api/opportunities", params={"pipeline_id": client.ids["retail"]})
    assert rows.json()[0]["checklist"] == {"answered": 0, "total": 13}
    rows = client.get("/api/opportunities", params={"pipeline_id": client.ids["ahs"]})
    assert rows.json()[0]["checklist"] == {"answered": 0, "total": 16}

    snapshot, gsnap = defs_snapshot(), groups_snapshot()
    again = seed(commit=True)
    assert again["created"] == 0 and again["exists"] == 16
    assert again["group"]["action"] == "exists"
    assert defs_snapshot() == snapshot and groups_snapshot() == gsnap


def test_a_rerun_after_the_owner_edits_the_checklist_undoes_nothing(client):
    seed(commit=True)
    fields = {d["key"]: d for d in client.get(FIELDS).json()}
    client.patch("%s/%d" % (FIELDS, fields["checklist_second_phone"]["id"]),
                 json={"label": "Another number", "script": "Ask for another.",
                       "pipeline_ids": [client.ids["ahs"]]})
    client.delete("%s/%d" % (FIELDS, fields["checklist_introduced_antonio"]["id"]))
    snapshot = defs_snapshot()
    report = seed(commit=True)
    assert report["created"] == 0
    assert defs_snapshot() == snapshot


def test_the_seed_refuses_and_writes_nothing_when_it_would_have_to_guess(client, capsys):
    # A field somebody else made under one of the keys, with another type.
    db = SessionLocal()
    db.add(CustomFieldDef(key="checklist_leak_count", label="Leak count", field_type="text",
                          options=[], position=0, entity="opportunity"))
    db.commit()
    db.close()
    before, defs = counts(), defs_snapshot()
    assert checklist_seed.main(["--commit"]) == 1
    assert "refusing to take it over" in capsys.readouterr().err
    assert counts() == before and defs_snapshot() == defs


def test_the_seed_refuses_when_a_pipeline_is_missing(client, capsys):
    db = SessionLocal()
    from app.models import Pipeline
    db.get(Pipeline, client.ids["retail"]).name = "Retail (old)"
    db.commit()
    db.close()
    before = counts()
    assert checklist_seed.main(["--commit"]) == 1
    assert "no pipeline is named 'Retail'" in capsys.readouterr().err
    assert counts() == before


def test_the_seed_imports_nothing_that_can_send():
    tree = ast.parse((BACKEND / "app" / "checklist_seed.py").read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                for a in n.names} | {n.module or "" for n in ast.walk(tree)
                                     if isinstance(n, ast.ImportFrom)}
    assert not imported & {"automations", "queue", "transport"}


# ============================================================== the migration

PRODUCTION_HEAD = "b3e9a7c51d28"
REVISION = "a7d4c2e9f130"
SETTINGS = {"script", "linked_field", "details_when"}


@contextlib.contextmanager
def _database_at(revision: str):
    import app.db as db_mod

    saved = db_mod.DATABASE_URL
    with tempfile.TemporaryDirectory() as tmp:
        url = "sqlite:///" + str(pathlib.Path(tmp) / "checklist.db")
        db_mod.DATABASE_URL = url
        eng = create_engine(url)
        try:
            command.upgrade(_config(), revision)
            yield eng
        finally:
            eng.dispose()
            db_mod.DATABASE_URL = saved


def _dump(conn) -> dict:
    insp = inspect(conn)
    return {t: [dict(r._mapping)
                for r in conn.execute(text('SELECT * FROM "%s" ORDER BY 1' % t))]
            for t in insp.get_table_names()}


def _columns(conn) -> dict:
    insp = inspect(conn)
    return {t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names()}


def test_the_migration_only_adds_three_nullable_columns_and_keeps_every_row():
    with _database_at(PRODUCTION_HEAD) as eng:
        with eng.begin() as conn:
            # Production-shaped: ONE field (job_type), no groups, cards holding answers
            # and the telephony / Workiz / AHS keys.
            conn.execute(text("INSERT INTO pipelines (id, name, position) VALUES "
                              "(1, 'Dream Team Roofing AHS', 0), (2, 'Retail', 1)"))
            conn.execute(text("INSERT INTO stages (id, pipeline_id, name, position) "
                              "VALUES (1, 1, 'New Lead', 0), (2, 2, 'New Lead', 0)"))
            conn.execute(text(
                "INSERT INTO custom_field_defs (id, key, label, field_type, options, "
                "entity, position, created_at) VALUES (1, 'job_type', 'Job type', "
                "'dropdown', '[\"Leak\", \"Replace\"]', 'opportunity', 0, "
                "'2026-09-11 00:00:00')"))
            conn.execute(text("INSERT INTO custom_field_pipelines (id, field_id, pipeline_id) "
                              "VALUES (1, 1, 1), (2, 1, 2)"))
            for i, (pipe, blob_) in enumerate((
                    (1, '{"job_type": "Leak", "owen_call_id": "c-9", "ahs_job_id": "84745849"}'),
                    (2, '{"job_type": "Replace", "workiz_id": "J7"}')), start=1):
                conn.execute(text(
                    "INSERT INTO opportunities (id, title, pipeline_id, stage_id, "
                    "value_cents, status, position, custom_fields, address_street, "
                    "created_at, updated_at) VALUES (:id, :t, :p, :p, 100, 'open', 0, :b, "
                    "'12 Palm Ave', '2026-09-12 00:00:00', '2026-09-13 00:00:00')"),
                    {"id": i, "t": "Card %d" % i, "p": pipe, "b": blob_})
        with eng.connect() as conn:
            before_rows, before_cols = _dump(conn), _columns(conn)

        command.upgrade(_config(), REVISION)
        eng.dispose()
        with eng.connect() as conn:
            after_rows, after_cols = _dump(conn), _columns(conn)
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            nullable = {c["name"]: c["nullable"]
                        for c in inspect(conn).get_columns("custom_field_defs")}

        assert version == REVISION
        assert set(after_cols) == set(before_cols), "a table was created or dropped"
        for table, cols in before_cols.items():
            assert cols <= after_cols[table], "a column was dropped from %s" % table
            assert after_cols[table] - cols == (
                SETTINGS if table == "custom_field_defs" else set()), table
        assert all(nullable[k] for k in SETTINGS)
        for table, rows in before_rows.items():
            if table in ("custom_field_defs", "alembic_version"):
                continue
            assert after_rows[table] == rows, table
        [old], [new] = before_rows["custom_field_defs"], after_rows["custom_field_defs"]
        assert {k: v for k, v in new.items() if k not in SETTINGS} == old
        assert [new[k] for k in sorted(SETTINGS)] == [None, None, None]

        # And back down, and up again, cleanly.
        command.downgrade(_config(), PRODUCTION_HEAD)
        command.upgrade(_config(), REVISION)
        eng.dispose()
        with eng.connect() as conn:
            assert _dump(conn)["opportunities"] == before_rows["opportunities"]


def test_the_upgrade_is_three_add_columns_and_nothing_else():
    path = next((BACKEND / "migrations" / "versions").glob(REVISION + "_*.py"))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    upgrade = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "upgrade")
    calls = [n for n in ast.walk(upgrade) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
             and n.func.value.id == "op"]
    assert [c.func.attr for c in calls] == ["add_column"] * 3
    assert {c.args[0].value for c in calls} == {"custom_field_defs"}
    for c in calls:
        column = c.args[1]
        assert any(k.arg == "nullable" and k.value.value is True for k in column.keywords)
    assigned = {n.target.id: n.value.value for n in tree.body
                if isinstance(n, ast.AnnAssign) and isinstance(n.value, ast.Constant)}
    assert assigned["revision"] == REVISION
    assert assigned["down_revision"] == PRODUCTION_HEAD
