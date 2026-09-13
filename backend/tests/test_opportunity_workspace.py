"""The opportunity modal's records: tabs, tasks, notes, followers, extra contacts.

Built 2026-09-13 to make the modal do what GoHighLevel's does. Every test here
asserts BEHAVIOUR — a count read back, a row re-read after a refusal, the jobs
table counted — never a status code on its own.
"""
from datetime import UTC, datetime, timedelta

import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Appointment,
    Contact,
    ContactTag,
    Conversation,
    ConversationEvent,
    CustomFieldDef,
    CustomFieldGroup,
    CustomFieldPipeline,
    Direction,
    EventType,
    Job,
    Opportunity,
    OpportunityContact,
    OpportunityFollower,
    OpportunityNote,
    OpportunityTask,
    Pipeline,
    Role,
    Stage,
    Tag,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select

OWEN_AND_WORKIZ = {
    "owen_call_id": "call-abc-123",
    "owen_campaign": "Spring",
    "owen_tracking_number": "+19415559999",
    "workiz_id": "4OES29",
    "workiz_job_number": "HPHUMS",
}


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    admin = User(email="a@x.test", name="Owen", role=Role.ADMIN)
    dispatcher = User(email="d@x.test", name="Dana", role=Role.DISPATCHER)
    tech = User(email="t@x.test", name="Tito", role=Role.TECH)
    db.add_all([admin, dispatcher, tech])

    jane = Contact(first_name="Jane", last_name="Doe", phone="+19415550004")
    bob = Contact(first_name="Bob", last_name="Roe", phone="+19415550005")
    db.add_all([jane, bob])
    extra = [Contact(first_name="Extra", last_name=str(i)) for i in range(12)]
    db.add_all(extra)

    ahs = Pipeline(name="AHS", position=0)
    retail = Pipeline(name="Retail", position=1)
    db.add_all([ahs, retail])
    db.flush()
    new_lead = Stage(pipeline_id=ahs.id, name="New Lead", position=0)
    inspection = Stage(pipeline_id=ahs.id, name="Inspection", position=1)
    retail_lead = Stage(pipeline_id=retail.id, name="Lead", position=0)
    db.add_all([new_lead, inspection, retail_lead])
    db.flush()

    deal = Opportunity(title="Jane roof", contact_id=jane.id, pipeline_id=ahs.id,
                       stage_id=new_lead.id, value_cents=950000,
                       custom_fields=dict(OWEN_AND_WORKIZ))
    gutters = Opportunity(title="Jane gutters", contact_id=jane.id,
                          pipeline_id=ahs.id, stage_id=new_lead.id, value_cents=1,
                          position=1)
    bare = Opportunity(title="Bob shed", contact_id=bob.id, pipeline_id=ahs.id,
                       stage_id=inspection.id)
    db.add_all([deal, gutters, bare])

    for name in ("ahs-job", "dispatch-service:roof"):
        t = Tag(name=name)
        db.add(t)
        db.flush()
        db.add(ContactTag(contact_id=jane.id, tag_id=t.id))

    # Jane's thread holds one staff NOTE (a Workiz merge note) and one SMS.
    conv = Conversation(contact_id=jane.id)
    db.add(conv)
    db.flush()
    db.add_all([
        ConversationEvent(conversation_id=conv.id, type=EventType.NOTE,
                          direction=Direction.OUTBOUND,
                          body="Merged from Workiz Client #123\nname: J. Doe"),
        ConversationEvent(conversation_id=conv.id, type=EventType.SMS,
                          direction=Direction.INBOUND, body="hello"),
    ])

    tokens = {}
    for key, u in (("admin", admin), ("dispatcher", dispatcher), ("tech", tech)):
        db.flush()
        plain, token = mint_api_token(u, name="test-" + key)
        db.add(token)
        tokens[key] = plain
    db.commit()
    ids = {"deal": deal.id, "gutters": gutters.id, "bare": bare.id,
           "jane": jane.id, "bob": bob.id, "extra": [c.id for c in extra],
           "ahs": ahs.id, "retail": retail.id, "new_lead": new_lead.id,
           "inspection": inspection.id, "retail_lead": retail_lead.id,
           "admin": admin.id, "dispatcher": dispatcher.id, "tech": tech.id,
           "conv": conv.id}
    db.close()

    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + tokens["admin"]
        c.ids = ids
        c.tokens = tokens
        yield c


def _as(client, who):
    return {"Authorization": "Bearer " + client.tokens[who]}


def _count(model, *where) -> int:
    with SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where)) or 0


def _card(client, opp_id, who="admin", pipeline="ahs"):
    rows = client.get("/api/opportunities", headers=_as(client, who),
                      params={"pipeline_id": client.ids[pipeline]}).json()
    return next(r for r in rows if r["id"] == opp_id)


# ---------------- the card's counts ----------------

def test_card_counts_match_the_data(client):
    ids = client.ids
    for title in ("Call back Tuesday", "Order shingles"):
        client.post(f"/api/opportunities/{ids['deal']}/tasks", json={"title": title})
    done = client.post(f"/api/opportunities/{ids['deal']}/tasks",
                       json={"title": "Already done"}).json()
    client.patch(f"/api/tasks/{done['id']}", json={"done": True})
    client.post(f"/api/opportunities/{ids['deal']}/notes",
                json={"body": "Customer prefers mornings\nsecond line"})

    card = _card(client, ids["deal"])
    assert card["tags"] == ["ahs-job", "dispatch-service:roof"]
    assert card["open_tasks_count"] == 2, "a done task is still counted as open"
    # This deal's own note plus the contact's thread NOTE — never the SMS.
    assert card["notes_count"] == 2
    assert card["note_previews"] == ["Customer prefers mornings",
                                     "Merged from Workiz Client #123"]
    assert card["contact_id"] == ids["jane"]

    # A sibling deal for the SAME contact has its own tasks and notes list, and
    # sees only the contact's thread note.
    sibling = _card(client, ids["gutters"])
    assert sibling["open_tasks_count"] == 0
    assert sibling["notes_count"] == 1

    bare = _card(client, ids["bare"])
    assert bare["tags"] == [] and bare["open_tasks_count"] == 0
    assert bare["notes_count"] == 0 and bare["note_previews"] == []


def test_a_long_first_line_is_truncated_with_an_ellipsis(client):
    client.post(f"/api/opportunities/{client.ids['bare']}/notes",
                json={"body": "x" * 200})
    preview = _card(client, client.ids["bare"])["note_previews"][0]
    assert preview.endswith("…") and len(preview) <= 60


# ---------------- notes are STAFF-only on every path ----------------

def test_notes_belong_to_one_opportunity(client):
    ids = client.ids
    client.post(f"/api/opportunities/{ids['deal']}/notes", json={"body": "roof note"})
    client.post(f"/api/opportunities/{ids['gutters']}/notes",
                json={"body": "gutter note"})
    roof = client.get(f"/api/opportunities/{ids['deal']}/notes").json()
    gutters = client.get(f"/api/opportunities/{ids['gutters']}/notes").json()
    assert [n["body"] for n in roof["notes"]] == ["roof note"]
    assert [n["body"] for n in gutters["notes"]] == ["gutter note"]
    # Both show the contact's thread note underneath, read-only.
    for payload in (roof, gutters):
        assert [n["body"] for n in payload["contact_notes"]] == [
            "Merged from Workiz Client #123\nname: J. Doe"]
    assert roof["notes"][0]["created_by"] == "Owen"


def test_notes_can_be_edited_and_deleted(client):
    note = client.post(f"/api/opportunities/{client.ids['deal']}/notes",
                       json={"body": "first"}).json()
    client.patch(f"/api/opportunity-notes/{note['id']}", json={"body": "edited"},
                 headers=_as(client, "dispatcher"))
    listed = client.get(f"/api/opportunities/{client.ids['deal']}/notes").json()
    assert [n["body"] for n in listed["notes"]] == ["edited"]
    client.delete(f"/api/opportunity-notes/{note['id']}")
    assert _count(OpportunityNote) == 0
    blank = client.post(f"/api/opportunities/{client.ids['deal']}/notes",
                        json={"body": "   "})
    assert blank.status_code == 400 and _count(OpportunityNote) == 0


def test_a_tech_sees_no_note_on_any_path(client):
    ids = client.ids
    note = client.post(f"/api/opportunities/{ids['deal']}/notes",
                       json={"body": "skylight quote is secret"}).json()
    tech = _as(client, "tech")

    # the modal's Notes tab
    r = client.get(f"/api/opportunities/{ids['deal']}/notes", headers=tech)
    assert r.status_code == 403 and "skylight" not in r.text
    # the card: no count and no preview KEY at all, not a zero
    card = _card(client, ids["deal"], who="tech")
    assert "notes_count" not in card and "note_previews" not in card
    assert "skylight" not in str(card)
    # the detail payload the modal loads
    detail = client.get(f"/api/opportunities/{ids['deal']}", headers=tech)
    assert "skylight" not in detail.text and "Merged from Workiz" not in detail.text
    # the palette
    search = client.get("/api/search", params={"q": "skylight"}, headers=tech).json()
    assert search["total"] == 0
    thread_note = client.get("/api/search", params={"q": "Merged from"},
                             headers=tech).json()
    assert thread_note["total"] == 0
    # the message search
    msgs = client.get("/api/messages", params={"q": "Merged"}, headers=tech).json()
    assert msgs["total"] == 0

    # writes are refused and write nothing
    for method, url, body in (
            ("post", f"/api/opportunities/{ids['deal']}/notes", {"body": "tech"}),
            ("patch", f"/api/opportunity-notes/{note['id']}", {"body": "changed"}),
            ("delete", f"/api/opportunity-notes/{note['id']}", None)):
        r = client.request(method.upper(), url, json=body, headers=tech)
        assert r.status_code == 403
    with SessionLocal() as s:
        rows = s.scalars(select(OpportunityNote)).all()
        assert [n.body for n in rows] == ["skylight quote is secret"]

    # ...while a dispatcher, who is staff, reads it.
    staff = client.get(f"/api/opportunities/{ids['deal']}/notes",
                       headers=_as(client, "dispatcher")).json()
    assert [n["body"] for n in staff["notes"]] == ["skylight quote is secret"]


# ---------------- tasks ----------------

def test_tasks_crud_and_zero_jobs(client):
    ids = client.ids
    jobs_before = _count(Job)
    due = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    t = client.post(f"/api/opportunities/{ids['deal']}/tasks", json={
        "title": "  Measure the ridge  ", "description": "bring the ladder",
        "due_at": due, "assigned_user_id": ids["tech"]},
        headers=_as(client, "dispatcher")).json()
    assert t["title"] == "Measure the ridge"
    assert t["contact_id"] == ids["jane"], "the task is not attached to the contact"
    assert t["assigned_user_name"] == "Tito" and t["done"] is False

    edited = client.patch(f"/api/tasks/{t['id']}", json={
        "title": "Measure the whole roof", "assigned_user_id": ids["dispatcher"]}).json()
    assert edited["title"] == "Measure the whole roof"
    assert edited["assigned_user_name"] == "Dana"
    assert client.patch(f"/api/tasks/{t['id']}", json={"done": True}).json()["done"]
    reopened = client.patch(f"/api/tasks/{t['id']}", json={"done": False}).json()
    assert reopened["done"] is False and reopened["completed_at"] is None

    listed = client.get(f"/api/opportunities/{ids['deal']}/tasks",
                        headers=_as(client, "tech")).json()
    assert [x["id"] for x in listed] == [t["id"]], "a TECH cannot read tasks"

    client.delete(f"/api/tasks/{t['id']}")
    assert _count(OpportunityTask) == 0
    # A due date and an assignee, created, edited, completed, reopened and deleted:
    # and the queue never moved.
    assert _count(Job) == jobs_before == 0


def test_a_tech_cannot_write_a_task_and_nothing_is_written(client):
    ids = client.ids
    t = client.post(f"/api/opportunities/{ids['deal']}/tasks",
                    json={"title": "Keep me"}).json()
    tech = _as(client, "tech")
    assert client.post(f"/api/opportunities/{ids['deal']}/tasks",
                       json={"title": "tech task"}, headers=tech).status_code == 403
    assert client.patch(f"/api/tasks/{t['id']}", json={"done": True},
                        headers=tech).status_code == 403
    assert client.delete(f"/api/tasks/{t['id']}", headers=tech).status_code == 403
    with SessionLocal() as s:
        rows = s.scalars(select(OpportunityTask)).all()
        assert [(r.title, r.completed_at) for r in rows] == [("Keep me", None)]


def test_task_validation_refuses_and_writes_nothing(client):
    url = f"/api/opportunities/{client.ids['deal']}/tasks"
    assert client.post(url, json={"title": " "}).status_code == 400
    assert client.post(url, json={"title": "x",
                                  "assigned_user_id": 9999}).status_code == 400
    assert _count(OpportunityTask) == 0


def test_open_tasks_sort_before_done_ones_and_by_due_date(client):
    url = f"/api/opportunities/{client.ids['deal']}/tasks"
    now = datetime.now(UTC)
    late = client.post(url, json={"title": "later",
                                  "due_at": (now + timedelta(days=3)).isoformat()}).json()
    soon = client.post(url, json={"title": "soon",
                                  "due_at": (now + timedelta(days=1)).isoformat()}).json()
    undated = client.post(url, json={"title": "whenever"}).json()
    finished = client.post(url, json={"title": "finished"}).json()
    client.patch(f"/api/tasks/{finished['id']}", json={"done": True})
    order = [t["id"] for t in client.get(url).json()]
    assert order == [soon["id"], late["id"], undated["id"], finished["id"]]


# ---------------- followers and additional contacts ----------------

def test_followers_persist_and_replace(client):
    ids = client.ids
    url = f"/api/opportunities/{ids['deal']}/detail"
    client.patch(url, json={"follower_ids": [ids["dispatcher"], ids["tech"]]})
    detail = client.get(f"/api/opportunities/{ids['deal']}").json()
    assert [f["name"] for f in detail["followers"]] == ["Dana", "Tito"]
    client.patch(url, json={"follower_ids": [ids["tech"]]})
    assert [f["id"] for f in client.get(
        f"/api/opportunities/{ids['deal']}").json()["followers"]] == [ids["tech"]]
    # An unrelated save leaves the followers alone.
    client.patch(url, json={"title": "Jane roof (renamed)"})
    assert _count(OpportunityFollower) == 1
    assert client.patch(url, json={"follower_ids": [9999]}).status_code == 400
    assert _count(OpportunityFollower) == 1


def test_additional_contacts_refuse_an_eleventh(client):
    ids = client.ids
    url = f"/api/opportunities/{ids['deal']}/detail"
    ten = ids["extra"][:10]
    r = client.patch(url, json={"additional_contact_ids": ten})
    assert r.status_code == 200
    assert [c["id"] for c in r.json()["additional_contacts"]] == ten

    eleven = ids["extra"][:11]
    r = client.patch(url, json={"additional_contact_ids": eleven,
                                "title": "should not land"})
    assert r.status_code == 400 and "at most 10" in r.json()["detail"]
    # Refused WHOLE: still ten, and the title in the same save did not land.
    assert _count(OpportunityContact) == 10
    assert client.get(f"/api/opportunities/{ids['deal']}").json()["title"] == "Jane roof"


def test_the_primary_contact_cannot_also_be_additional(client):
    ids = client.ids
    url = f"/api/opportunities/{ids['deal']}/detail"
    assert client.patch(url, json={
        "additional_contact_ids": [ids["jane"]]}).status_code == 400
    assert _count(OpportunityContact) == 0
    # Promoting an additional contact to primary drops the duplicate link.
    client.patch(url, json={"additional_contact_ids": [ids["bob"]]})
    client.patch(url, json={"contact_id": ids["bob"]})
    detail = client.get(f"/api/opportunities/{ids['deal']}").json()
    assert detail["contact_id"] == ids["bob"] and detail["additional_contacts"] == []


def test_the_primary_contact_is_required_on_an_edit(client):
    url = f"/api/opportunities/{client.ids['deal']}/detail"
    r = client.patch(url, json={"contact_id": None})
    assert r.status_code == 400
    assert client.get(f"/api/opportunities/{client.ids['deal']}").json()[
        "contact_id"] == client.ids["jane"]


# ---------------- pipeline changes ----------------

def test_changing_pipeline_requires_a_stage_in_the_new_pipeline(client):
    ids = client.ids
    url = f"/api/opportunities/{ids['deal']}/detail"
    no_stage = client.patch(url, json={"pipeline_id": ids["retail"]})
    wrong_stage = client.patch(url, json={"pipeline_id": ids["retail"],
                                          "stage_id": ids["inspection"]})
    assert no_stage.status_code == 400 and wrong_stage.status_code == 400
    with SessionLocal() as s:
        o = s.get(Opportunity, ids["deal"])
        assert (o.pipeline_id, o.stage_id) == (ids["ahs"], ids["new_lead"])

    moved = client.patch(url, json={"pipeline_id": ids["retail"],
                                    "stage_id": ids["retail_lead"]}).json()
    assert (moved["pipeline_id"], moved["stage_id"]) == (ids["retail"],
                                                          ids["retail_lead"])
    # The column it left is re-packed, so the board's positions stay contiguous.
    with SessionLocal() as s:
        left = s.scalars(select(Opportunity).where(
            Opportunity.stage_id == ids["new_lead"])).all()
        assert [o.position for o in left] == [0]
    # Moving the deal does not move a single reserved answer.
    assert moved["custom_fields"] == OWEN_AND_WORKIZ


# ---------------- owen_* / workiz_* ----------------

def test_owen_and_workiz_values_are_byte_identical_after_an_update(client):
    ids = client.ids
    with SessionLocal() as s:
        before = s.get(Opportunity, ids["deal"]).custom_fields
    # What the modal sends on Update: the edited fields plus the whole blob echoed.
    detail = client.get(f"/api/opportunities/{ids['deal']}").json()
    r = client.patch(f"/api/opportunities/{ids['deal']}/detail", json={
        "title": "Jane roof v2", "value_cents": 100,
        "follower_ids": [ids["tech"]], "custom_fields": detail["custom_fields"]})
    assert r.status_code == 200
    # ...and what it sends when the modal never mentions the blob at all.
    client.patch(f"/api/opportunities/{ids['deal']}/detail",
                 json={"source": "AHS"})
    with SessionLocal() as s:
        after = s.get(Opportunity, ids["deal"]).custom_fields
    assert after == before == OWEN_AND_WORKIZ


def test_no_definition_is_created_for_owen_or_workiz(client):
    for label in ("owen_call_id", "workiz_id", "Workiz job number"):
        r = client.post("/api/custom-fields", json={"label": label,
                                                    "field_type": "text"})
        assert r.status_code == 400
    assert _count(CustomFieldDef) == 0


# ---------------- custom field groups ----------------

def _field(client, label, group_id=None, pipelines=("ahs",)):
    return client.post("/api/custom-fields", json={
        "label": label, "field_type": "text", "group_id": group_id,
        "pipeline_ids": [client.ids[p] for p in pipelines]}).json()


def test_groups_create_rename_and_reorder(client):
    a = client.post("/api/custom-field-groups", json={"name": "Roof Inspection"}).json()
    b = client.post("/api/custom-field-groups", json={"name": "Photo Checklist"}).json()
    c = client.post("/api/custom-field-groups",
                    json={"name": "Customer Journey"}).json()
    assert [g["name"] for g in client.get("/api/custom-field-groups").json()] == [
        "Roof Inspection", "Photo Checklist", "Customer Journey"]

    client.patch(f"/api/custom-field-groups/{c['id']}",
                 json={"name": "Customer Journey Checklist"})
    client.post("/api/custom-field-groups/reorder",
                json={"group_ids": [c["id"], a["id"], b["id"]]})
    listed = client.get("/api/custom-field-groups", headers=_as(client, "tech")).json()
    assert [g["name"] for g in listed] == [
        "Customer Journey Checklist", "Roof Inspection", "Photo Checklist"]

    # A partial permutation is refused whole.
    r = client.post("/api/custom-field-groups/reorder", json={"group_ids": [a["id"]]})
    assert r.status_code == 400
    assert [g["id"] for g in client.get("/api/custom-field-groups").json()] == [
        c["id"], a["id"], b["id"]]


def test_group_writes_are_admin_only_and_write_nothing(client):
    g = client.post("/api/custom-field-groups", json={"name": "Keep"}).json()
    for who in ("dispatcher", "tech"):
        h = _as(client, who)
        assert client.post("/api/custom-field-groups", json={"name": "x"},
                           headers=h).status_code == 403
        assert client.patch(f"/api/custom-field-groups/{g['id']}", json={"name": "x"},
                            headers=h).status_code == 403
        assert client.delete(f"/api/custom-field-groups/{g['id']}",
                             headers=h).status_code == 403
    assert [x["name"] for x in client.get("/api/custom-field-groups").json()] == ["Keep"]


def test_a_field_moves_between_groups(client):
    a = client.post("/api/custom-field-groups", json={"name": "A"}).json()
    b = client.post("/api/custom-field-groups", json={"name": "B"}).json()
    f = _field(client, "Roof age", group_id=a["id"])
    assert f["group_id"] == a["id"]
    moved = client.patch(f"/api/custom-fields/{f['id']}",
                         json={"group_id": b["id"]}).json()
    assert moved["group_id"] == b["id"]
    groups = {g["name"]: g["field_ids"] for g in
              client.get("/api/custom-field-groups").json()}
    assert groups == {"A": [], "B": [f["id"]]}
    back = client.patch(f"/api/custom-fields/{f['id']}", json={"group_id": None}).json()
    assert back["group_id"] is None
    assert client.patch(f"/api/custom-fields/{f['id']}",
                        json={"group_id": 999}).status_code == 400
    # An unrelated edit does not move it.
    client.patch(f"/api/custom-fields/{f['id']}", json={"group_id": a["id"]})
    client.patch(f"/api/custom-fields/{f['id']}", json={"label": "Roof age (years)"})
    assert client.get("/api/custom-fields").json()[0]["group_id"] == a["id"]


def test_deleting_a_group_moves_its_fields_out_and_keeps_every_answer(client):
    ids = client.ids
    g = client.post("/api/custom-field-groups", json={"name": "Roof Inspection"}).json()
    other = client.post("/api/custom-field-groups", json={"name": "Photos"}).json()
    f = _field(client, "Roof age", group_id=g["id"], pipelines=("ahs", "retail"))
    client.patch(f"/api/opportunities/{ids['deal']}/detail",
                 json={"custom_fields": {"roof_age": "14"}})
    with SessionLocal() as s:
        blob_before = dict(s.get(Opportunity, ids["deal"]).custom_fields)
        links_before = s.scalars(select(CustomFieldPipeline.pipeline_id).where(
            CustomFieldPipeline.field_id == f["id"])).all()
    assert blob_before["roof_age"] == "14"

    r = client.delete(f"/api/custom-field-groups/{g['id']}").json()
    assert r["moved_field_ids"] == [f["id"]]

    field = client.get("/api/custom-fields").json()[0]
    assert field["group_id"] is None and field["key"] == "roof_age"
    assert not field["archived"]
    with SessionLocal() as s:
        assert s.get(Opportunity, ids["deal"]).custom_fields == blob_before
        assert sorted(s.scalars(select(CustomFieldPipeline.pipeline_id).where(
            CustomFieldPipeline.field_id == f["id"])).all()) == sorted(links_before)
        assert s.scalar(select(func.count(CustomFieldGroup.id))) == 1
    # The surviving group is re-packed to position 0.
    assert client.get("/api/custom-field-groups").json() == [
        {"id": other["id"], "name": "Photos", "position": 0, "field_ids": []}]
    # And the answer is still editable on the deal, under Opportunity details.
    client.patch(f"/api/opportunities/{ids['deal']}/detail",
                 json={"custom_fields": {"roof_age": "15"}})
    with SessionLocal() as s:
        assert s.get(Opportunity, ids["deal"]).custom_fields["roof_age"] == "15"


# ---------------- deletes stay honest ----------------

def test_deleting_an_opportunity_removes_its_own_records_and_nothing_else(client):
    ids = client.ids
    client.post(f"/api/opportunities/{ids['deal']}/notes", json={"body": "n"})
    client.post(f"/api/opportunities/{ids['deal']}/tasks", json={"title": "t"})
    client.post(f"/api/opportunities/{ids['gutters']}/notes", json={"body": "keep"})
    client.patch(f"/api/opportunities/{ids['deal']}/detail", json={
        "follower_ids": [ids["tech"]], "additional_contact_ids": [ids["bob"]]})
    with SessionLocal() as s:
        s.add(Appointment(title="Visit", opportunity_id=ids["deal"],
                          contact_id=ids["jane"],
                          starts_at=datetime.now(UTC) + timedelta(days=3),
                          ends_at=datetime.now(UTC) + timedelta(days=3, hours=1)))
        s.commit()

    r = client.delete(f"/api/opportunities/{ids['deal']}").json()
    assert r["notes_deleted"] == 1 and r["tasks_deleted"] == 1
    assert r["followers_removed"] == 1 and r["additional_contacts_removed"] == 1
    assert _count(OpportunityNote) == 1, "another deal's note went with it"
    assert _count(Contact, Contact.id == ids["bob"]) == 1
    assert _count(ConversationEvent, ConversationEvent.type == EventType.NOTE) == 1
    assert _count(Appointment) == 1


def test_deleting_a_contact_takes_it_off_deals_it_was_additional_on(client):
    ids = client.ids
    client.patch(f"/api/opportunities/{ids['deal']}/detail",
                 json={"additional_contact_ids": [ids["bob"]]})
    task = client.post(f"/api/opportunities/{ids['bare']}/tasks",
                       json={"title": "Bob's task"}).json()
    r = client.delete(f"/api/contacts/{ids['bob']}", params={"force": True})
    assert r.status_code == 200
    assert r.json()["removed_from_opportunities"] == [ids["deal"]]
    assert _count(OpportunityContact) == 0
    with SessionLocal() as s:
        t = s.get(OpportunityTask, task["id"])
        assert t is not None and t.contact_id is None


# ---------------- view conversations ----------------

def test_view_conversations_finds_or_creates_one_thread_and_sends_nothing(client):
    ids = client.ids
    jobs_before, events_before = _count(Job), _count(ConversationEvent)
    found = client.post(f"/api/contacts/{ids['jane']}/conversation",
                        headers=_as(client, "tech")).json()
    assert found == {"conversation_id": ids["conv"], "created": False}
    made = client.post(f"/api/contacts/{ids['bob']}/conversation").json()
    again = client.post(f"/api/contacts/{ids['bob']}/conversation").json()
    assert made["created"] is True and again["created"] is False
    assert made["conversation_id"] == again["conversation_id"]
    assert _count(Conversation, Conversation.contact_id == ids["bob"]) == 1
    assert (_count(Job), _count(ConversationEvent)) == (jobs_before, events_before)
