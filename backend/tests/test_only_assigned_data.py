"""GoHighLevel's "Only assigned data" — a technician sees and works only their own jobs.

The owner's rule (2026-09-15, DECISIONS.md). Security work in the class of per-pipeline
permissions, so there is a test per SURFACE, and at the bottom a test that enumerates
EVERY route in the app and fails until each one records how it enforces this.

The fixture (every name is what the test reads):

    Tess     TECH, Only assigned data ON. Owns calendar "Tess's calendar".
    Otto     TECH, ON. Owns "Otto's calendar".
    Terry    TECH, switch OFF — today's behaviour exactly.
    Dana     DISPATCHER, unrestricted.       Owen    ADMIN.
    Rita     DISPATCHER, switch ON — the role still decides what she may do.

    Main pipeline (open to all) · Secret pipeline (restricted to Dana).

    owned        Tess is the OWNER                      -> Tess's job
    visit        a booking on Tess's calendar           -> Tess's job
    assigned     a booking on Otto's calendar, ASSIGNED to Tess -> Tess's job
    cancelled    a CANCELLED booking on Tess's calendar -> NOT her job
    unrelated    Otto's job, nothing to do with Tess    -> NOT her job
    secret       Tess owns it, but it is in Secret      -> NOT visible (pipeline wins)

Every deal title carries "skylight", and so do the texts on the customers' threads, so
a search for it would find all of them if anything leaked.
"""
import inspect
from datetime import UTC, datetime, timedelta

import pytest
from app import (
    assigned_access,
    companycam,
    companycam_api,
    openphone,
    opportunity_workspace,
    softphone,
)
from app import (
    main as main_mod,
)
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Appointment,
    BlockedTime,
    Calendar,
    CompanyCamLink,
    Contact,
    Conversation,
    ConversationEvent,
    CustomFieldDef,
    CustomFieldPipeline,
    Direction,
    EventType,
    Job,
    NumberThread,
    NumberThreadEvent,
    Opportunity,
    OpportunityContact,
    OpportunityNote,
    OpportunityTask,
    Pipeline,
    PipelinePermission,
    Role,
    SavedView,
    Stage,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select

SOON = datetime.now(UTC).replace(microsecond=0) + timedelta(days=2)


@pytest.fixture()
def world():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    users = {
        "tess": User(email="tess@x.test", name="Tess", role=Role.TECH,
                     only_assigned_data=True),
        "otto": User(email="otto@x.test", name="Otto", role=Role.TECH,
                     only_assigned_data=True),
        "terry": User(email="terry@x.test", name="Terry", role=Role.TECH),
        "dana": User(email="dana@x.test", name="Dana", role=Role.DISPATCHER),
        "owen": User(email="owen@x.test", name="Owen", role=Role.ADMIN),
        "rita": User(email="rita@x.test", name="Rita", role=Role.DISPATCHER,
                     only_assigned_data=True),
    }
    db.add_all(users.values())
    db.flush()
    u = {k: v.id for k, v in users.items()}

    main = Pipeline(name="Main", position=0)
    secret = Pipeline(name="Secret", position=1)
    db.add_all([main, secret])
    db.flush()
    lead = Stage(pipeline_id=main.id, name="Lead", position=0)
    inspect_ = Stage(pipeline_id=main.id, name="Inspection", position=1)
    s1 = Stage(pipeline_id=secret.id, name="S1", position=0)
    db.add_all([lead, inspect_, s1])
    db.flush()
    db.add(PipelinePermission(pipeline_id=secret.id, user_id=u["dana"]))

    tess_cal = Calendar(name="Tess's calendar", user_id=u["tess"])
    otto_cal = Calendar(name="Otto's calendar", user_id=u["otto"])
    db.add_all([tess_cal, otto_cal])
    db.flush()

    def person(first, phone):
        c = Contact(first_name=first, last_name="Skylight", phone=phone,
                    email=first.lower() + "@cust.test")
        db.add(c)
        db.flush()
        return c

    people = {k: person(k.capitalize(), "+1941555%04d" % i) for i, k in enumerate(
        ("owned", "visit", "assigned", "cancelled", "unrelated", "secret", "spouse"))}

    def deal(key, *, owner=None, pipeline=main, stage=lead, value=10000, position=0):
        o = Opportunity(title="%s skylight job" % key.capitalize(),
                        contact_id=people[key].id, pipeline_id=pipeline.id,
                        stage_id=stage.id, value_cents=value, owner_id=owner,
                        position=position, custom_fields={"owen_call_id": "call-" + key})
        db.add(o)
        db.flush()
        return o

    deals = {
        "owned": deal("owned", owner=u["tess"], value=11100, position=0),
        "visit": deal("visit", value=22200, position=1),
        "assigned": deal("assigned", value=33300, position=2),
        "cancelled": deal("cancelled", value=44400, position=3),
        "unrelated": deal("unrelated", owner=u["otto"], value=55500, position=4),
        "secret": deal("secret", owner=u["tess"], pipeline=secret, stage=s1, value=66600),
    }
    db.add(OpportunityContact(opportunity_id=deals["owned"].id,
                              contact_id=people["spouse"].id))

    def booking(key, cal, *, assigned=None, status="confirmed", days=2):
        a = Appointment(title="Visit " + key, calendar_id=cal.id,
                        contact_id=people[key].id, opportunity_id=deals[key].id,
                        assigned_user_id=assigned, status=status, notes="gate code 4411",
                        starts_at=SOON + timedelta(days=days),
                        ends_at=SOON + timedelta(days=days, hours=1))
        db.add(a)
        db.flush()
        return a

    appts = {
        "visit": booking("visit", tess_cal, days=1),
        "assigned": booking("assigned", otto_cal, assigned=u["tess"], days=2),
        "cancelled": booking("cancelled", tess_cal, status="cancelled", days=3),
        "unrelated": booking("unrelated", otto_cal, assigned=u["otto"], days=4),
    }
    blocks = {
        "tess": BlockedTime(title="Tess day off", calendar_id=tess_cal.id,
                            starts_at=SOON, ends_at=SOON + timedelta(hours=8)),
        "otto": BlockedTime(title="Otto day off", calendar_id=otto_cal.id,
                            starts_at=SOON, ends_at=SOON + timedelta(hours=8)),
    }
    db.add_all(blocks.values())

    convs = {}
    for key in ("owned", "unrelated"):
        conv = Conversation(contact_id=people[key].id, unread_count=2)
        db.add(conv)
        db.flush()
        db.add_all([
            ConversationEvent(conversation_id=conv.id, type=EventType.SMS,
                              direction=Direction.INBOUND,
                              body="the skylight leaks at the %s house" % key),
            ConversationEvent(conversation_id=conv.id, type=EventType.NOTE,
                              direction=Direction.OUTBOUND,
                              body="thread note skylight %s" % key),
            ConversationEvent(conversation_id=conv.id, type=EventType.CALL,
                              direction=Direction.INBOUND, call_status="completed",
                              recording_url="%s/rec-%s" % (openphone.RECORDINGS_PATH, key),
                              transcript="skylight call %s" % key),
        ])
        convs[key] = conv
    number = NumberThread(phone="+19415559999", phone_key="9415559999", unread_count=1)
    db.add(number)
    db.flush()
    db.add(NumberThreadEvent(number_thread_id=number.id, type=EventType.SMS,
                             direction=Direction.INBOUND, body="skylight from a stranger"))

    tasks, notes = {}, {}
    for key in ("owned", "unrelated"):
        t_mine = OpportunityTask(opportunity_id=deals[key].id, contact_id=people[key].id,
                                 title="Tess's task on " + key, assigned_user_id=u["tess"])
        t_theirs = OpportunityTask(opportunity_id=deals[key].id,
                                   contact_id=people[key].id,
                                   title="Otto's task on " + key, assigned_user_id=u["otto"])
        n = OpportunityNote(opportunity_id=deals[key].id,
                            body="deal note skylight " + key, created_by_id=u["dana"])
        db.add_all([t_mine, t_theirs, n])
        db.flush()
        tasks[key], tasks[key + "_otto"], notes[key] = t_mine, t_theirs, n

    for key in ("owned", "unrelated"):
        db.add(CompanyCamLink(opportunity_id=deals[key].id, project_id="proj-" + key,
                              method="manual", project_name="Project " + key))
    db.add(SavedView(name="Everything open", status="open"))
    roof = CustomFieldDef(key="roof_age", label="Roof age", field_type="number")
    verified = CustomFieldDef(key="email_verified", label="Email verified",
                              field_type="boolean", linked_field="contact_email")
    db.add_all([roof, verified])
    db.flush()
    db.add_all([CustomFieldPipeline(field_id=roof.id, pipeline_id=main.id),
                CustomFieldPipeline(field_id=verified.id, pipeline_id=main.id)])

    tokens = {}
    for key, user in users.items():
        plain, token = mint_api_token(user, name="t-" + key)
        db.add(token)
        tokens[key] = plain
    db.commit()

    ids = {
        "users": u, "main": main.id, "secret": secret.id, "lead": lead.id,
        "inspection": inspect_.id, "s1": s1.id,
        "tess_cal": tess_cal.id, "otto_cal": otto_cal.id,
        "people": {k: c.id for k, c in people.items()},
        "deals": {k: o.id for k, o in deals.items()},
        "appts": {k: a.id for k, a in appts.items()},
        "blocks": {k: b.id for k, b in blocks.items()},
        "convs": {k: c.id for k, c in convs.items()},
        "number": number.id,
        "tasks": {k: t.id for k, t in tasks.items()},
        "notes": {k: n.id for k, n in notes.items()},
        "tokens": tokens,
    }
    db.close()

    clients = {}
    for key in users:
        c = TestClient(app)
        c.headers["Authorization"] = "Bearer " + tokens[key]
        clients[key] = c
    yield ids, clients


JOBS = {"owned", "visit", "assigned"}
NOT_JOBS = {"cancelled", "unrelated", "secret"}


def _deal_ids(ids, keys):
    return {ids["deals"][k] for k in keys}


def _read(model, pk):
    db = SessionLocal()
    try:
        row = db.get(model, pk)
        db.expunge(row) if row is not None else None
        return row
    finally:
        db.close()


def _count(model):
    db = SessionLocal()
    try:
        return db.scalar(select(func.count()).select_from(model))
    finally:
        db.close()


# ---------- the definition of "their jobs" ----------

def test_their_jobs_are_owned_plus_a_visit_on_their_calendar_plus_an_assigned_visit(world):
    ids, c = world
    db = SessionLocal()
    try:
        tess = db.get(User, ids["users"]["tess"])
        jobs = assigned_access.job_ids(db, tess.id, frozenset({ids["secret"]}))
    finally:
        db.close()
    assert jobs == _deal_ids(ids, JOBS)


def test_a_cancelled_visit_does_not_make_a_job_but_uncancelling_it_does(world):
    ids, c = world
    cancelled = ids["deals"]["cancelled"]
    assert c["tess"].get("/api/opportunities/%d" % cancelled).status_code == 404
    db = SessionLocal()
    db.get(Appointment, ids["appts"]["cancelled"]).status = "confirmed"
    db.commit()
    db.close()
    assert c["tess"].get("/api/opportunities/%d" % cancelled).status_code == 200


def test_pipeline_permission_still_hides_a_job_she_owns(world):
    ids, c = world
    secret = ids["deals"]["secret"]
    assert c["tess"].get("/api/opportunities/%d" % secret).status_code == 404
    rows = c["tess"].get("/api/opportunities", params={"status": "all"}).json()
    assert secret not in {r["id"] for r in rows}
    # ...and an unrestricted user granted the pipeline sees it.
    assert c["dana"].get("/api/opportunities/%d" % secret).status_code == 200


# ---------- opportunities ----------

def test_the_board_lists_exactly_her_jobs(world):
    ids, c = world
    rows = c["tess"].get("/api/opportunities", params={"status": "all"}).json()
    assert {r["id"] for r in rows} == _deal_ids(ids, JOBS)
    board = c["tess"].get("/api/opportunities",
                          params={"pipeline_id": ids["main"]}).json()
    assert {r["id"] for r in board} == _deal_ids(ids, JOBS)


@pytest.mark.parametrize("key", sorted(JOBS))
def test_she_opens_each_of_her_jobs_by_id(world, key):
    ids, c = world
    r = c["tess"].get("/api/opportunities/%d" % ids["deals"][key])
    assert r.status_code == 200 and r.json()["id"] == ids["deals"][key]


@pytest.mark.parametrize("key", sorted(NOT_JOBS))
def test_a_job_that_is_not_hers_is_the_same_404_as_a_missing_one(world, key):
    ids, c = world
    r = c["tess"].get("/api/opportunities/%d" % ids["deals"][key])
    missing = c["tess"].get("/api/opportunities/999999")
    assert r.status_code == missing.status_code == 404
    assert r.json() == missing.json()


def test_stage_totals_on_the_pipeline_list_count_only_her_jobs(world):
    ids, c = world
    main = next(p for p in c["tess"].get("/api/pipelines").json() if p["id"] == ids["main"])
    lead = next(s for s in main["stages"] if s["id"] == ids["lead"])
    assert lead["count"] == 3
    assert lead["value_cents"] == 11100 + 22200 + 33300
    # Dana is unaffected: every card in the stage.
    main_d = next(p for p in c["dana"].get("/api/pipelines").json()
                  if p["id"] == ids["main"])
    assert next(s for s in main_d["stages"] if s["id"] == ids["lead"])["count"] == 5


def test_she_moves_the_stage_of_her_job_and_the_automation_path_runs(world):
    ids, c = world
    r = c["tess"].patch("/api/opportunities/%d" % ids["deals"]["owned"],
                        json={"stage_id": ids["inspection"], "position": 0})
    assert r.status_code == 200
    assert _read(Opportunity, ids["deals"]["owned"]).stage_id == ids["inspection"]


def test_she_cannot_move_a_job_that_is_not_hers_and_nothing_moves(world):
    ids, c = world
    r = c["tess"].patch("/api/opportunities/%d" % ids["deals"]["unrelated"],
                        json={"stage_id": ids["inspection"], "position": 0})
    assert r.status_code == 404
    assert _read(Opportunity, ids["deals"]["unrelated"]).stage_id == ids["lead"]


def test_her_drop_position_counts_her_cards_and_keeps_others_in_order(world):
    ids, c = world
    d = ids["deals"]
    # Move "assigned" (her third card) to the top of HER column.
    r = c["tess"].patch("/api/opportunities/%d" % d["assigned"],
                        json={"stage_id": ids["lead"], "position": 0})
    assert r.status_code == 200
    db = SessionLocal()
    order = [o.id for o in db.scalars(select(Opportunity).where(
        Opportunity.stage_id == ids["lead"]).order_by(Opportunity.position)).all()]
    db.close()
    # It lands just above her previously-first card; nobody else's card is reordered.
    assert order == [d["assigned"], d["owned"], d["visit"], d["cancelled"], d["unrelated"]]


def test_bulk_stage_on_her_jobs_works_and_one_foreign_id_refuses_the_whole_request(world):
    ids, c = world
    d = ids["deals"]
    r = c["tess"].post("/api/opportunities/bulk/stage",
                       json={"ids": [d["owned"], d["unrelated"]],
                             "stage_id": ids["inspection"]})
    assert r.status_code == 404 and str(d["unrelated"]) in r.json()["detail"]
    assert _read(Opportunity, d["owned"]).stage_id == ids["lead"], "nothing moved"
    ok = c["tess"].post("/api/opportunities/bulk/stage",
                        json={"ids": [d["owned"], d["visit"]], "stage_id": ids["inspection"]})
    assert ok.status_code == 200
    assert _read(Opportunity, d["visit"]).stage_id == ids["inspection"]


def test_rita_a_restricted_dispatcher_bulk_assigns_only_what_she_can_see(world):
    ids, c = world
    d = ids["deals"]
    r = c["rita"].post("/api/opportunities/bulk/owner",
                       json={"ids": [d["unrelated"]], "owner_id": ids["users"]["rita"]})
    assert r.status_code == 404
    assert _read(Opportunity, d["unrelated"]).owner_id == ids["users"]["otto"]


def test_a_saved_view_applied_by_her_still_shows_only_her_jobs(world):
    ids, c = world
    view = c["tess"].get("/api/saved-views").json()[0]
    rows = c["tess"].get("/api/opportunities", params={"status": view["status"]}).json()
    assert {r["id"] for r in rows} == _deal_ids(ids, JOBS)


# ---------- what she may and may not change on her own job ----------

def test_she_answers_her_jobs_questions_including_the_verified_tick(world):
    ids, c = world
    r = c["tess"].patch("/api/opportunities/%d/detail" % ids["deals"]["owned"],
                        json={"custom_fields": {"owen_call_id": "call-owned",
                                                "roof_age": 14, "email_verified": True}})
    assert r.status_code == 200, r.text
    blob = _read(Opportunity, ids["deals"]["owned"]).custom_fields
    assert blob["roof_age"] == 14 and blob["email_verified"] is True
    assert blob["owen_call_id"] == "call-owned"


def test_she_cannot_answer_the_questions_on_a_job_that_is_not_hers(world):
    ids, c = world
    r = c["tess"].patch("/api/opportunities/%d/detail" % ids["deals"]["unrelated"],
                        json={"custom_fields": {"roof_age": 99}})
    assert r.status_code == 404
    assert "roof_age" not in _read(Opportunity, ids["deals"]["unrelated"]).custom_fields


@pytest.mark.parametrize("body", [
    {"title": "Renamed"}, {"value_cents": 1}, {"status": "won"},
    {"owner_id": None}, {"pipeline_id": 2, "stage_id": 3}, {"follower_ids": [1]},
    {"source": "Web"}, {"business_name": "Acme"}, {"address_street": "1 Palm Ave"},
    {"contact_id": 1}, {"additional_contact_ids": []},
    {"custom_fields": {"roof_age": 3}, "title": "Smuggled beside an answer"},
])
def test_forbidden_edits_on_her_own_job_are_refused_and_nothing_changes(world, body):
    ids, c = world
    before = _read(Opportunity, ids["deals"]["owned"])
    r = c["tess"].patch("/api/opportunities/%d/detail" % ids["deals"]["owned"], json=body)
    assert r.status_code == 403
    after = _read(Opportunity, ids["deals"]["owned"])
    for col in ("title", "value_cents", "status", "owner_id", "pipeline_id", "stage_id",
                "source", "business_name", "address_street", "contact_id",
                "custom_fields"):
        assert getattr(after, col) == getattr(before, col), col


def test_she_cannot_edit_the_contacts_details_even_on_her_own_job(world):
    ids, c = world
    r = c["tess"].patch("/api/contacts/%d" % ids["people"]["owned"],
                        json={"email": "changed@x.test"})
    assert r.status_code == 403
    assert _read(Contact, ids["people"]["owned"]).email == "owned@cust.test"


@pytest.mark.parametrize("path", [
    "/api/opportunities/{owned}", "/api/contacts/{person}",
    "/api/appointments/{appt}", "/api/tasks/{task}", "/api/opportunity-notes/{note}",
])
def test_she_may_not_delete_anything_on_her_own_job(world, path):
    ids, c = world
    url = path.format(owned=ids["deals"]["owned"], person=ids["people"]["owned"],
                      appt=ids["appts"]["visit"], task=ids["tasks"]["owned"],
                      note=ids["notes"]["owned"])
    r = c["tess"].delete(url)
    assert r.status_code == 403
    assert _read(Opportunity, ids["deals"]["owned"]) is not None
    assert _read(Contact, ids["people"]["owned"]) is not None
    assert _read(Appointment, ids["appts"]["visit"]).status == "confirmed"
    assert _read(OpportunityTask, ids["tasks"]["owned"]) is not None
    assert _read(OpportunityNote, ids["notes"]["owned"]) is not None


def test_terry_a_tech_with_the_switch_off_keeps_todays_rules_exactly(world):
    ids, c = world
    d = ids["deals"]
    # Sees everything (except the pipeline he is not granted).
    rows = c["terry"].get("/api/opportunities", params={"status": "all"}).json()
    assert {r["id"] for r in rows} == _deal_ids(ids, {"owned", "visit", "assigned",
                                                       "cancelled", "unrelated"})
    # Cannot answer questions, add notes, add tasks, or read deal notes — as before.
    assert c["terry"].patch("/api/opportunities/%d/detail" % d["owned"],
                            json={"custom_fields": {"roof_age": 1}}).status_code == 403
    assert c["terry"].post("/api/opportunities/%d/tasks" % d["owned"],
                           json={"title": "x"}).status_code == 403
    assert c["terry"].get("/api/opportunities/%d/notes" % d["owned"]).status_code == 403
    assert c["terry"].post("/api/opportunities/%d/notes" % d["owned"],
                           json={"body": "x"}).status_code == 403
    assert "notes_count" not in rows[0]
    assert c["terry"].get("/api/dashboard/funnel").status_code == 200


# ---------- contacts ----------

def test_contacts_list_holds_only_her_jobs_people_and_the_total_agrees(world):
    ids, c = world
    page = c["tess"].get("/api/contacts", params={"page_size": 200}).json()
    want = {ids["people"][k] for k in ("owned", "visit", "assigned", "spouse")}
    assert {r["id"] for r in page["items"]} == want
    assert page["total"] == 4
    assert c["dana"].get("/api/contacts").json()["total"] == 7


def test_a_contact_not_on_her_jobs_is_404_and_her_customers_panel_lists_only_her_jobs(world):
    ids, c = world
    assert c["tess"].get("/api/contacts/%d" % ids["people"]["unrelated"]).status_code == 404
    assert c["tess"].get("/api/contacts/%d" % ids["people"]["cancelled"]).status_code == 404
    panel = c["tess"].get("/api/contacts/%d" % ids["people"]["visit"]).json()
    assert [o["id"] for o in panel["opportunities"]] == [ids["deals"]["visit"]]
    assert [a["id"] for a in panel["appointments"]] == [ids["appts"]["visit"]]


def test_a_shared_customer_panel_lists_only_her_job_not_the_other_techs(world):
    ids, c = world
    db = SessionLocal()
    # Otto's job moves onto Tess's customer: same person, two jobs, one is hers.
    db.get(Opportunity, ids["deals"]["unrelated"]).contact_id = ids["people"]["owned"]
    db.get(Appointment, ids["appts"]["unrelated"]).contact_id = ids["people"]["owned"]
    db.commit()
    db.close()
    panel = c["tess"].get("/api/contacts/%d" % ids["people"]["owned"]).json()
    assert [o["id"] for o in panel["opportunities"]] == [ids["deals"]["owned"]]
    assert panel["appointments"] == []
    full = c["dana"].get("/api/contacts/%d" % ids["people"]["owned"]).json()
    assert len(full["opportunities"]) == 2 and len(full["appointments"]) == 1


def test_tag_counts_count_only_her_contacts(world):
    ids, c = world
    db = SessionLocal()
    from app.models import ContactTag, Tag
    tag = Tag(name="VIP")
    db.add(tag)
    db.flush()
    db.add_all([ContactTag(contact_id=ids["people"][k], tag_id=tag.id)
                for k in ("owned", "unrelated", "secret")])
    db.commit()
    db.close()
    assert c["tess"].get("/api/tags").json()[0]["count"] == 1
    assert c["dana"].get("/api/tags").json()[0]["count"] == 3


def test_the_incoming_call_card_names_only_her_customers(world):
    ids, c = world
    mine = c["tess"].get("/api/softphone/caller", params={"number": "+19415550000"}).json()
    assert mine["contact"]["id"] == ids["people"]["owned"]
    theirs = c["tess"].get("/api/softphone/caller",
                           params={"number": "+19415550004"}).json()
    assert theirs["contact"] is None
    assert c["dana"].get("/api/softphone/caller",
                         params={"number": "+19415550004"}).json()["contact"] is not None


# ---------- conversations ----------

def test_inbox_holds_only_her_customers_threads_and_no_number_threads(world):
    ids, c = world
    rows = c["tess"].get("/api/conversations").json()
    assert [r["key"] for r in rows] == ["c%d" % ids["convs"]["owned"]]
    unread = c["tess"].get("/api/conversations", params={"tab": "unread"}).json()
    assert sum(r["unread_count"] for r in unread) == 2, "the badge is over her list"
    dana = {r["key"] for r in c["dana"].get("/api/conversations").json()}
    assert "n%d" % ids["number"] in dana and len(dana) == 3


def test_a_thread_not_hers_is_404_to_read_mark_send_or_call(world):
    ids, c = world
    conv = ids["convs"]["unrelated"]
    before = _count(ConversationEvent)
    assert c["tess"].get("/api/conversations/%d/events" % conv).status_code == 404
    assert c["tess"].patch("/api/conversations/%d" % conv,
                           json={"read": True}).status_code == 404
    assert c["tess"].post("/api/conversations/%d/messages" % conv,
                          json={"body": "hi"}).status_code == 404
    assert c["tess"].post("/api/contacts/%d/messages" % ids["people"]["unrelated"],
                          json={"body": "hi"}).status_code == 404
    assert c["tess"].post("/api/conversations/%d/call" % conv).status_code == 404
    assert c["tess"].post("/api/contacts/%d/call" % ids["people"]["unrelated"]
                          ).status_code == 404
    assert c["tess"].post("/api/contacts/%d/conversation" % ids["people"]["cancelled"]
                          ).status_code == 404
    assert _count(ConversationEvent) == before
    assert _read(Conversation, conv).unread_count == 2
    assert _count(Conversation) == 2, "no thread was created for the hidden contact"


def test_she_texts_and_reads_her_own_customers_thread_but_not_its_internal_notes(world):
    ids, c = world
    conv = ids["convs"]["owned"]
    events = c["tess"].get("/api/conversations/%d/events" % conv).json()
    assert {e["type"] for e in events} == {"SMS", "CALL"}, "thread NOTEs stay STAFF-only"
    sent = c["tess"].post("/api/conversations/%d/messages" % conv, json={"body": "On my way"})
    assert sent.status_code == 201 and sent.json()["id"]
    assert c["tess"].post("/api/conversations/%d/messages" % conv,
                          json={"body": "x", "type": "NOTE"}).status_code == 403


def test_the_dialer_rings_only_her_customers_and_writes_nothing_otherwise(world, monkeypatch):
    ids, c = world
    from app import crmlink
    placed = []
    monkeypatch.setattr(crmlink, "configured", lambda: True)
    monkeypatch.setattr(crmlink, "place_call", lambda to_number, operator=None: placed.append(
        to_number) or crmlink.LinkResult(ok=True, status=200, data={"linkedid": "L1"}))
    line = type("Line", (), {"from_number": "+19544829099"})()
    monkeypatch.setattr(crmlink, "current", lambda: line)
    before = (_count(ConversationEvent), _count(NumberThread), _count(Conversation))
    for number in ("+19415550004", "(941) 555-7777"):   # Otto's customer; nobody's number
        r = c["tess"].post("/api/calls/dial", json={"number": number}).json()
        assert r["placed"] is False and "Only assigned data" in r["reason"]
        assert r["contact_id"] is None and r["contact_name"] is None
    assert placed == [], "nothing was rung"
    assert (_count(ConversationEvent), _count(NumberThread), _count(Conversation)) == before
    mine = c["tess"].post("/api/calls/dial", json={"number": "+19415550000"}).json()
    assert mine["placed"] is True and mine["contact_id"] == ids["people"]["owned"]
    assert c["dana"].post("/api/calls/dial", json={"number": "(941) 555-7777"}).json()["placed"]


def test_new_message_texts_only_her_customers_and_writes_nothing_otherwise(world, monkeypatch):
    """"New message" (2026-09-15) follows the dialer's rule: a restricted technician may
    text a customer of her own jobs, never someone else's customer or an unknown number."""
    ids, c = world
    from app import transport
    sent = []

    class Recording:
        def send_sms(self, to, body, from_number):
            sent.append(to)
            return transport.MessageRef(provider_ref="m1", status=transport.DeliveryStatus.QUEUED)

    monkeypatch.setattr(transport, "get_transport", lambda: Recording())
    from app import automations
    monkeypatch.setattr(automations, "get_transport", lambda: Recording())
    before = (_count(ConversationEvent), _count(NumberThread), _count(NumberThreadEvent),
              _count(Conversation), _count(Contact))
    for number in ("+19415550004", "(941) 555-7777"):   # Otto's customer; nobody's number
        r = c["tess"].post("/api/messages/new", json={"number": number, "body": "hi"}).json()
        assert r["recorded"] is False and "Only assigned data" in r["reason"]
        assert r["contact_id"] is None and r["key"] is None
    assert sent == [], "nothing was texted"
    assert (_count(ConversationEvent), _count(NumberThread), _count(NumberThreadEvent),
            _count(Conversation), _count(Contact)) == before
    mine = c["tess"].post("/api/messages/new",
                          json={"number": "+19415550000", "body": "On my way"}).json()
    assert mine["recorded"] is True and mine["contact_id"] == ids["people"]["owned"]
    assert sent == ["+19415550000"]
    other = c["dana"].post("/api/messages/new",
                           json={"number": "(941) 555-7777", "body": "hello"}).json()
    assert other["recorded"] is True and other["kind"] == "number"


def test_number_threads_are_404_on_every_route(world):
    ids, c = world
    n = ids["number"]
    before = _count(NumberThreadEvent)
    for method, url, body in (
            ("get", "/api/number-threads/%d" % n, None),
            ("get", "/api/number-threads/%d/events" % n, None),
            ("patch", "/api/number-threads/%d" % n, {"starred": True}),
            ("post", "/api/number-threads/%d/messages" % n, {"body": "hi"}),
            ("post", "/api/number-threads/%d/call" % n, None)):
        r = getattr(c["tess"], method)(url, **({"json": body} if body else {}))
        assert r.status_code == 404, url
    assert _count(NumberThreadEvent) == before
    assert _read(NumberThread, n).starred is False


def test_call_and_message_search_cover_only_her_customers(world):
    ids, c = world
    calls = c["tess"].get("/api/calls", params={"q": "skylight"}).json()
    assert calls["total"] == 1 and calls["items"][0]["contact_id"] == ids["people"]["owned"]
    msgs = c["tess"].get("/api/messages", params={"q": "skylight"}).json()
    assert msgs["total"] == 1
    assert {m["contact_id"] for m in msgs["items"]} == {ids["people"]["owned"]}
    assert c["tess"].get("/api/calls", params={
        "contact_id": ids["people"]["unrelated"]}).json()["total"] == 0


def test_a_recording_on_a_thread_not_hers_is_the_no_recording_404(world, monkeypatch):
    ids, c = world
    fetched = []
    monkeypatch.setattr(openphone.crmlink, "configured", lambda: True)
    monkeypatch.setattr(openphone.crmlink, "fetch_openphone_recording",
                        lambda call_id: fetched.append(call_id) or (b"ID3", "audio/mpeg"))
    assert c["tess"].get("/api/openphone/recordings/rec-unrelated").status_code == 404
    assert fetched == [], "nothing was fetched for a call she may not hear"
    assert c["tess"].get("/api/openphone/recordings/rec-owned").status_code == 200
    assert c["dana"].get("/api/openphone/recordings/rec-unrelated").status_code == 200


# ---------- search (the ctrl+K palette) ----------

def test_the_palette_finds_only_her_jobs_people_and_threads_in_items_and_totals(world):
    ids, c = world
    body = c["tess"].get("/api/search", params={"q": "skylight", "limit": 25}).json()
    groups = {g["type"]: g for g in body["groups"]}
    assert {o["id"] for o in groups["opportunities"]["items"]} == _deal_ids(ids, JOBS)
    assert groups["opportunities"]["total"] == 3
    assert {p["id"] for p in groups["contacts"]["items"]} == {
        ids["people"][k] for k in ("owned", "visit", "assigned", "spouse")}
    assert groups["contacts"]["total"] == 4
    assert [m["contact_id"] for m in groups["messages"]["items"]] == [ids["people"]["owned"]]
    assert groups["messages"]["total"] == 1
    dana = {g["type"]: g["total"] for g in c["dana"].get(
        "/api/search", params={"q": "skylight"}).json()["groups"]}
    assert dana == {"contacts": 7, "opportunities": 6, "messages": 4}


def test_the_cli_goes_through_the_api_and_sees_only_her_jobs(world, monkeypatch):
    """`ghl` has no database access of its own; drive the real CLI against the app."""
    ids, c = world
    from ghl_cli import client as client_mod
    from ghl_cli import output
    from ghl_cli.main import app as cli
    from typer.testing import CliRunner

    real_init = client_mod.Client.__init__

    def through_the_app(self, *a, **kw):
        real_init(self, *a, **kw)
        self._http = TestClient(app, base_url=self.base_url)

    monkeypatch.setattr(client_mod.Client, "__init__", through_the_app)
    monkeypatch.setenv("GHL_API_URL", "http://testserver")
    output.set_json(False)

    def run(who, *args):
        monkeypatch.setenv("GHL_API_TOKEN", ids["tokens"][who])
        return CliRunner().invoke(cli, ["--json", *args])

    shown = run("tess", "opps", "show", str(ids["deals"]["unrelated"]))
    assert shown.exit_code == 4, shown.output
    assert "Unrelated skylight" not in shown.output
    mine = run("tess", "opps", "show", str(ids["deals"]["owned"]))
    assert mine.exit_code == 0 and "Owned skylight job" in mine.output
    listed = run("tess", "contacts", "list", "--q", "skylight")
    assert listed.exit_code == 0, listed.output
    assert "Unrelated" not in listed.output and "Owned" in listed.output
    assert "Unrelated" in run("dana", "contacts", "list", "--q", "skylight").output


# ---------- calendars ----------

def test_calendar_shows_her_calendar_and_her_assigned_visits_only(world):
    ids, c = world
    rows = c["tess"].get("/api/appointments").json()
    assert {a["id"] for a in rows} == {ids["appts"][k]
                                       for k in ("visit", "assigned", "cancelled")}
    cancelled = next(a for a in rows if a["id"] == ids["appts"]["cancelled"])
    assert cancelled["opportunity_id"] is None, "a cancelled visit lends no job title"
    assert c["tess"].get("/api/appointments/%d" % ids["appts"]["unrelated"]
                         ).status_code == 404
    detail = c["tess"].get("/api/appointments/%d" % ids["appts"]["assigned"]).json()
    assert detail["opportunity_id"] == ids["deals"]["assigned"]
    assert detail["notes"] is None, "an appointment's internal notes stay STAFF-only"
    assert len(c["dana"].get("/api/appointments").json()) == 4


def test_calendar_list_and_blocked_time_are_hers(world):
    ids, c = world
    cals = {x["id"] for x in c["tess"].get("/api/calendars").json()}
    assert cals == {ids["tess_cal"], ids["otto_cal"]}, "own + the one with her assignment"
    assert {x["id"] for x in c["otto"].get("/api/calendars").json()} == {ids["otto_cal"]}
    blocks = c["tess"].get("/api/blocked-times").json()
    assert [b["id"] for b in blocks] == [ids["blocks"]["tess"]]
    assert c["tess"].get("/api/blocked-times/%d" % ids["blocks"]["otto"]).status_code == 404
    assert len(c["dana"].get("/api/blocked-times").json()) == 2


def test_rita_cannot_edit_or_cancel_a_visit_or_block_she_cannot_see(world):
    ids, c = world
    appt = ids["appts"]["unrelated"]
    assert c["rita"].patch("/api/appointments/%d" % appt,
                           json={"title": "Moved"}).status_code == 404
    assert c["rita"].delete("/api/appointments/%d" % appt).status_code == 404
    assert _read(Appointment, appt).status == "confirmed"
    assert _read(Appointment, appt).title == "Visit unrelated"
    assert c["rita"].delete("/api/blocked-times/%d" % ids["blocks"]["otto"]
                            ).status_code == 404
    assert _read(BlockedTime, ids["blocks"]["otto"]) is not None
    assert c["rita"].post("/api/appointments", json={
        "title": "Probe", "starts_at": SOON.isoformat(),
        "ends_at": (SOON + timedelta(hours=1)).isoformat(),
        "opportunity_id": ids["deals"]["unrelated"]}).status_code == 404


# ---------- tasks and notes ----------

def test_tasks_she_reads_adds_and_completes_on_her_job(world):
    ids, c = world
    owned = ids["deals"]["owned"]
    assert len(c["tess"].get("/api/opportunities/%d/tasks" % owned).json()) == 2
    jobs_before = _count(Job)
    new = c["tess"].post("/api/opportunities/%d/tasks" % owned, json={"title": "Ladder"})
    assert new.status_code == 201
    assert _count(Job) == jobs_before, "a task notifies nobody"
    done = c["tess"].patch("/api/tasks/%d" % ids["tasks"]["owned"], json={"done": True})
    assert done.status_code == 200
    assert _read(OpportunityTask, ids["tasks"]["owned"]).completed_at is not None
    mine = c["tess"].patch("/api/tasks/%d" % new.json()["id"], json={"done": True})
    assert mine.status_code == 200, "a task she added is hers"


def test_she_cannot_complete_someone_elses_task_or_edit_one(world):
    ids, c = world
    otto = ids["tasks"]["owned_otto"]
    assert c["tess"].patch("/api/tasks/%d" % otto, json={"done": True}).status_code == 403
    assert _read(OpportunityTask, otto).completed_at is None
    mine = ids["tasks"]["owned"]
    assert c["tess"].patch("/api/tasks/%d" % mine,
                           json={"title": "Renamed", "done": True}).status_code == 403
    after = _read(OpportunityTask, mine)
    assert after.title == "Tess's task on owned" and after.completed_at is None


def test_tasks_on_a_job_not_hers_are_404_and_nothing_is_written(world):
    ids, c = world
    unrelated = ids["deals"]["unrelated"]
    before = _count(OpportunityTask)
    assert c["tess"].get("/api/opportunities/%d/tasks" % unrelated).status_code == 404
    assert c["tess"].post("/api/opportunities/%d/tasks" % unrelated,
                          json={"title": "x"}).status_code == 404
    assert c["tess"].patch("/api/tasks/%d" % ids["tasks"]["unrelated"],
                           json={"done": True}).status_code == 404
    assert _count(OpportunityTask) == before
    assert _read(OpportunityTask, ids["tasks"]["unrelated"]).completed_at is None


def test_she_reads_and_adds_deal_notes_on_her_job_but_not_thread_notes(world):
    ids, c = world
    owned = ids["deals"]["owned"]
    body = c["tess"].get("/api/opportunities/%d/notes" % owned).json()
    assert [n["body"] for n in body["notes"]] == ["deal note skylight owned"]
    assert body["contact_notes"] == [], "thread NOTE events stay STAFF-only"
    assert body["can_edit"] is False
    added = c["tess"].post("/api/opportunities/%d/notes" % owned,
                           json={"body": "Flashing is loose"})
    assert added.status_code == 201
    assert c["tess"].patch("/api/opportunity-notes/%d" % added.json()["id"],
                           json={"body": "edited"}).status_code == 403
    assert _read(OpportunityNote, added.json()["id"]).body == "Flashing is loose"
    staff = c["dana"].get("/api/opportunities/%d/notes" % owned).json()
    assert len(staff["contact_notes"]) == 1 and staff["can_edit"] is True


def test_notes_on_a_job_not_hers_are_404_and_nothing_is_written(world):
    ids, c = world
    unrelated = ids["deals"]["unrelated"]
    before = _count(OpportunityNote)
    assert c["tess"].get("/api/opportunities/%d/notes" % unrelated).status_code == 404
    assert c["tess"].post("/api/opportunities/%d/notes" % unrelated,
                          json={"body": "x"}).status_code == 404
    assert _count(OpportunityNote) == before


def test_board_card_note_count_is_her_jobs_deal_notes_only(world):
    ids, c = world
    rows = {r["id"]: r for r in c["tess"].get("/api/opportunities").json()}
    owned = rows[ids["deals"]["owned"]]
    assert owned["notes_count"] == 1
    assert owned["note_previews"] == ["deal note skylight owned"]
    staff = {r["id"]: r for r in c["dana"].get("/api/opportunities").json()}
    assert staff[ids["deals"]["owned"]]["notes_count"] == 2, "deal note + thread note"


# ---------- CompanyCam photos ----------

@pytest.fixture()
def cam(monkeypatch):
    monkeypatch.setattr(companycam, "enabled", lambda: True)
    monkeypatch.setattr(companycam, "get_project", lambda pid, refresh=False: {
        "id": pid, "name": "Project", "created_at": 1})
    monkeypatch.setattr(companycam, "photo_page", lambda pid, page, refresh=False: [
        {"id": "ph-" + pid, "project_id": pid}])
    monkeypatch.setattr(companycam, "get_photo", lambda photo_id: {
        "id": photo_id, "project_id": photo_id[len("ph-"):],
        "uris": [{"type": "web", "uri": "https://img.test/x"},
                 {"type": "thumbnail", "uri": "https://img.test/x"}]})
    monkeypatch.setattr(companycam, "preferred_uri", lambda photo, v: "https://img.test/x")
    monkeypatch.setattr(companycam, "fetch_image", lambda uri: (b"JPEG", "image/jpeg"))


def test_photos_of_her_job_show_and_a_job_not_hers_is_404_down_to_the_image(world, cam):
    ids, c = world
    owned, unrelated = ids["deals"]["owned"], ids["deals"]["unrelated"]
    assert c["tess"].get("/api/opportunities/%d/companycam" % owned).json()["state"] == "ok"
    assert c["tess"].get("/api/opportunities/%d/companycam/projects/proj-owned/photos"
                         % owned).status_code == 200
    assert c["tess"].get("/api/companycam/photos/ph-proj-owned/web").content == b"JPEG"
    assert c["tess"].get("/api/opportunities/%d/companycam" % unrelated).status_code == 404
    assert c["tess"].get("/api/opportunities/%d/companycam/projects/proj-unrelated/photos"
                         % unrelated).status_code == 404
    assert c["tess"].get("/api/companycam/photos/ph-proj-unrelated/web").status_code == 404
    assert c["dana"].get("/api/companycam/photos/ph-proj-unrelated/web").status_code == 200


def test_contact_panel_projects_are_her_jobs_only(world, cam):
    ids, c = world
    assert c["tess"].get("/api/contacts/%d/companycam" % ids["people"]["unrelated"]
                         ).status_code == 404
    mine = c["tess"].get("/api/contacts/%d/companycam" % ids["people"]["owned"]).json()
    assert [p["id"] for p in mine["projects"]] == ["proj-owned"]


# ---------- Reporting, Dashboard, Forecast ----------

@pytest.mark.parametrize("url", [
    "/api/dashboard", "/api/dashboard/funnel", "/api/forecast?pipeline_id={main}",
    "/api/reports/calls", "/api/reports/appointments",
])
def test_reporting_dashboard_and_forecast_refuse_her_with_a_sentence(world, url):
    ids, c = world
    url = url.format(main=ids["main"])
    tess = c["tess"].get(url)
    assert tess.status_code == 403
    assert "Only assigned data" in tess.json()["detail"]
    rita = c["rita"].get(url)
    assert rita.status_code == 403 and "Only assigned data" in rita.json()["detail"]
    assert c["dana"].get(url).status_code == 200
    assert c["owen"].get(url).status_code == 200


# ---------- the setting itself ----------

def test_the_setting_defaults_on_for_a_new_tech_and_off_for_everyone_else(world):
    ids, c = world
    for role, want in (("TECH", True), ("DISPATCHER", False), ("ADMIN", False)):
        r = c["owen"].post("/api/users", json={"email": role.lower() + "@new.test",
                                               "name": role, "password": "longenough1",
                                               "role": role})
        assert r.status_code == 201 and r.json()["only_assigned_data"] is want, role
    r = c["owen"].post("/api/users", json={"email": "t2@new.test", "name": "T2",
                                           "password": "longenough1", "role": "TECH",
                                           "only_assigned_data": False})
    assert r.json()["only_assigned_data"] is False


def test_only_an_admin_can_change_it_and_a_refusal_changes_nothing(world):
    ids, c = world
    terry = ids["users"]["terry"]
    for who in ("dana", "tess", "terry"):
        r = c[who].patch("/api/users/%d" % terry, json={"only_assigned_data": True})
        assert r.status_code == 403
        assert _read(User, terry).only_assigned_data is False
    r = c["owen"].patch("/api/users/%d" % terry, json={"only_assigned_data": True})
    assert r.status_code == 200 and r.json()["only_assigned_data"] is True
    # It applies on the very next request, with no re-login.
    rows = c["terry"].get("/api/opportunities", params={"status": "all"}).json()
    assert rows == []


def test_an_admin_is_never_restricted(world):
    ids, c = world
    owen = ids["users"]["owen"]
    r = c["owen"].patch("/api/users/%d" % owen, json={"only_assigned_data": True})
    assert r.status_code == 400
    assert _read(User, owen).only_assigned_data is False
    # A restricted dispatcher promoted to ADMIN loses the switch.
    r = c["owen"].patch("/api/users/%d" % ids["users"]["rita"], json={"role": "ADMIN"})
    assert r.json()["only_assigned_data"] is False


def test_switching_it_off_restores_todays_view(world):
    ids, c = world
    c["owen"].patch("/api/users/%d" % ids["users"]["tess"],
                    json={"only_assigned_data": False})
    rows = c["tess"].get("/api/opportunities", params={"status": "all"}).json()
    assert len(rows) == 5
    assert c["tess"].get("/api/contacts").json()["total"] == 7
    assert c["tess"].get("/api/dashboard/funnel").status_code == 200
    assert c["tess"].get("/api/opportunities/%d/notes" % ids["deals"]["owned"]
                         ).status_code == 403, "back to today's STAFF-only notes"


def test_me_and_the_user_list_carry_the_setting_and_only_an_admin_reads_others(world):
    ids, c = world
    assert c["tess"].get("/api/auth/me").json()["user"]["only_assigned_data"] is True
    as_admin = {u["id"]: u for u in c["owen"].get("/api/users").json()}
    assert as_admin[ids["users"]["tess"]]["only_assigned_data"] is True
    as_tech = {u["id"]: u for u in c["tess"].get("/api/users").json()}
    assert as_tech[ids["users"]["terry"]]["only_assigned_data"] is None


def test_a_browser_session_is_restricted_too_not_only_a_token(world):
    ids, c = world
    from app.auth import hash_password
    db = SessionLocal()
    db.get(User, ids["users"]["tess"]).password_hash = hash_password("tess-password")
    db.commit()
    db.close()
    browser = TestClient(app)
    assert browser.post("/api/auth/login", json={"email": "tess@x.test",
                                                 "password": "tess-password"}
                        ).status_code == 200
    rows = browser.get("/api/opportunities", params={"status": "all"}).json()
    assert {r["id"] for r in rows} == _deal_ids(ids, JOBS)


# ---------- unaffected users: regression ----------

def test_dispatcher_and_admin_see_everything_they_did_before(world):
    ids, c = world
    for who, expected in (("dana", 6), ("owen", 6)):
        rows = c[who].get("/api/opportunities", params={"status": "all"}).json()
        assert len(rows) == expected, who
        assert c[who].get("/api/opportunities/%d" % ids["deals"]["unrelated"]
                          ).status_code == 200
        assert c[who].get("/api/contacts").json()["total"] == 7
        assert len(c[who].get("/api/conversations").json()) == 3
        assert c[who].get("/api/number-threads/%d" % ids["number"]).status_code == 200
        assert len(c[who].get("/api/appointments").json()) == 4
        assert len(c[who].get("/api/blocked-times").json()) == 2
        assert len(c[who].get("/api/calendars").json()) == 2
    # Dana still edits anything and TECH-only limits do not touch her.
    r = c["dana"].patch("/api/opportunities/%d/detail" % ids["deals"]["unrelated"],
                        json={"title": "Renamed by dispatch"})
    assert r.status_code == 200
    assert _read(Opportunity, ids["deals"]["unrelated"]).title == "Renamed by dispatch"


def test_scope_is_none_for_unrestricted_users_so_queries_are_unchanged(world):
    ids, c = world
    db = SessionLocal()
    try:
        for key in ("dana", "owen", "terry"):
            user = db.get(User, ids["users"][key])
            from app.auth import Principal
            p = Principal(user_id=user.id, email=user.email, name=user.name,
                          role=user.role, kind="pat", scopes=frozenset(),
                          only_assigned=bool(user.only_assigned_data))
            s = assigned_access.scope(db, p)
            assert s.job_ids is None and s.contact_ids is None, key
            # No hidden pipeline for these three would leave the query byte-identical;
            # the job filter must add nothing at all.
            stmt = select(Opportunity)
            unscoped = assigned_access.Scope(s.user_id, frozenset(), None, None)
            assert str(assigned_access.opportunities(stmt, unscoped)) == str(stmt)
            assert str(assigned_access.contacts(select(Contact), unscoped)) == str(
                select(Contact))
    finally:
        db.close()


# ---------- the audit: every route, recorded ----------

# How each route enforces "Only assigned data". A route missing from here fails
# `test_every_route_is_on_the_audited_list`: somebody has to decide.
AUDITED = {
    # opportunities & pipelines
    "list_pipelines": "stage totals over scope.job_ids",
    "create_pipeline": "STAFF; totals over scope",
    "update_pipeline": "STAFF; totals over scope",
    "reorder_pipelines": "STAFF; totals over scope",
    "duplicate_pipeline": "STAFF; totals over scope",
    "get_pipeline_permissions": "ADMIN (never restricted)",
    "set_pipeline_permissions": "ADMIN (never restricted)",
    "delete_pipeline": "ADMIN (never restricted)",
    "create_stage": "STAFF; structure only, no deal read",
    "update_stage": "STAFF; structure only, no deal read",
    "delete_stage": "ADMIN (never restricted)",
    "reorder_stages": "STAFF; structure only",
    "list_custom_fields": "definitions only, no answers",
    "create_custom_field": "ADMIN", "update_custom_field": "ADMIN",
    "archive_custom_field": "ADMIN", "restore_custom_field": "ADMIN",
    "reorder_custom_fields": "ADMIN",
    "list_opportunities": "assigned_access.opportunities(scope)",
    "move_opportunity": "pipeline_access.get_opportunity -> 404; position over her cards",
    "get_opportunity": "pipeline_access.get_opportunity -> 404",
    "update_opportunity": "get_opportunity -> 404; TECH limited to TECH_JOB_FIELDS",
    "create_opportunity": "STAFF; creates",
    "delete_opportunity": "ADMIN (never restricted)",
    "bulk_move_stage": "_bulk_load assigned_access.opportunities -> 404",
    "bulk_assign_owner": "STAFF; _bulk_load -> 404",
    "list_saved_views": "names only; applying one goes through list_opportunities",
    "create_saved_view": "STAFF", "update_saved_view": "STAFF",
    "delete_saved_view": "ADMIN",
    # contacts
    "list_contacts": "assigned_access.contacts(scope), rows and total",
    "get_contact": "assigned_access.get_contact -> 404; _contact_detail(scope)",
    "update_contact": "STAFF; get_contact -> 404",
    "add_tag": "STAFF; get_contact -> 404",
    "remove_tag": "STAFF; get_contact -> 404",
    "create_contact": "STAFF; creates",
    "delete_contact": "ADMIN (never restricted)",
    "list_tags": "counts over scope contacts",
    "softphone_caller": "contact blanked unless scope.sees_contact",
    "softphone_credentials": "the caller's own SIP identity, no record",
    "open_contact_conversation": "get_contact -> 404",
    # conversations
    "list_conversations": "assigned_access.conversations(scope); no number threads",
    "conversation_events": "get_conversation -> 404; thread notes STAFF-only",
    "update_conversation": "get_conversation -> 404",
    "delete_conversation": "ADMIN (never restricted)",
    "send_to_contact": "get_contact -> 404",
    "send_to_conversation": "get_conversation -> 404",
    "call_contact": "get_contact -> 404",
    "call_conversation": "get_conversation -> 404",
    "get_number_thread": "_number_thread refuses -> 404",
    "number_thread_events": "_number_thread refuses -> 404",
    "update_number_thread": "_number_thread refuses -> 404",
    "delete_number_thread": "ADMIN; _number_thread",
    "send_to_number_thread": "_number_thread refuses -> 404",
    "call_number_thread": "_number_thread refuses -> 404",
    "dial_number": "restricted: only a number held by a scope contact, else refused, "
                   "nothing written (assigned_access.scope)",
    "new_message": "restricted: only a number held by a scope contact, else refused, "
                   "nothing written (assigned_access.scope)",
    "list_automations": "definitions only",
    "list_calls": "_search_events(scope)",
    "list_messages": "_search_events(scope)",
    "search": "contacts/opportunities/messages over one scope",
    "stream_recording": "event with that recording_url on a scope contact, else 404",
    "ingest_event": "EVENTS_INGEST (machine, staff)",
    "ingest_delivery_receipt": "EVENTS_INGEST (machine, staff)",
    "ingest_ahs_job": "EVENTS_INGEST (machine, staff)",
    "ingest_ahs_cancellation": "EVENTS_INGEST (machine, staff)",
    # calendars
    "list_calendars": "assigned_access.calendars(scope)",
    "list_appointments": "assigned_access.appointments(scope); deal link sees_job",
    "get_appointment": "assigned_access.get_appointment -> 404",
    "create_appointment": "STAFF; contact + deal must be in scope -> 404",
    "update_appointment": "STAFF; get_appointment -> 404",
    "cancel_appointment": "STAFF; get_appointment -> 404",
    "list_blocked_times": "assigned_access.blocked_times(scope)",
    "get_blocked_time": "assigned_access.get_blocked_time -> 404",
    "create_blocked_time": "STAFF; creates",
    "update_blocked_time": "STAFF; get_blocked_time -> 404",
    "delete_blocked_time": "STAFF; get_blocked_time -> 404",
    # reporting
    "dashboard": "refuse_reporting -> 403", "dashboard_funnel": "refuse_reporting -> 403",
    "forecast": "refuse_reporting -> 403", "report_calls": "refuse_reporting -> 403",
    "report_appointments": "refuse_reporting -> 403",
    # opportunity workspace
    "list_groups": "definitions only", "create_group": "ADMIN", "rename_group": "ADMIN",
    "reorder_groups": "ADMIN", "delete_group": "ADMIN",
    "list_tasks": "get_opportunity -> 404",
    "create_task": "_refuse_tech; get_opportunity -> 404",
    "update_task": "_get_task via get_opportunity -> 404; TECH done-only, own task",
    "delete_task": "STAFF; _get_task -> 404",
    "list_notes": "_refuse_notes; get_opportunity -> 404; thread notes STAFF-only",
    "create_note": "_refuse_notes; get_opportunity -> 404",
    "update_note": "_refuse_notes('edit'): STAFF-only",
    "delete_note": "_refuse_notes('delete'): STAFF-only",
    # companycam
    "companycam_projects_for_opportunity": "get_opportunity -> 404",
    "companycam_project_photos": "get_opportunity -> 404",
    "companycam_photo_image": "link to a card in assigned_access.opportunities(scope)",
    "companycam_projects_for_contact": "get_contact -> 404; opportunities(scope)",
    "companycam_status": "ADMIN", "companycam_review_list": "ADMIN",
    "companycam_review_link": "ADMIN", "companycam_review_dismiss": "ADMIN",
    "companycam_unlink": "ADMIN",
    # admin, auth, machinery — no customer record
    "list_users": "roster; only_assigned_data ADMIN-only", "create_user": "ADMIN",
    "update_user": "ADMIN", "list_jobs": "ADMIN", "retry_job": "ADMIN",
    "login": "exempt", "refresh": "exempt", "logout": "exempt", "logout_all": "own",
    "me": "own", "change_password": "own", "create_token": "own",
    "list_tokens": "own", "revoke_token": "own", "health": "exempt",
    "connection_status": "link health, no record",
    # AI Agents (app/ai/api.py, 2026-09-15): the whole module refuses a restricted user
    # 403 before anything is read (`_viewer`), whatever their role — pinned by
    # tests/test_ai_permissions.py route by route.
    **dict.fromkeys((
        "ai_catalogue", "ai_get_settings", "ai_put_settings", "ai_list_connections",
        "ai_connection_names", "ai_create_connection", "ai_update_connection",
        "ai_delete_connection", "ai_test_connection", "ai_load_models", "ai_list_folders",
        "ai_create_folder", "ai_rename_folder", "ai_delete_folder", "ai_list_agents",
        "ai_create_agent", "ai_get_agent", "ai_update_agent", "ai_preview_prompt",
        "ai_publish_agent", "ai_get_version", "ai_set_mode", "ai_delete_agent",
        "ai_duplicate_agent", "ai_try_agent", "ai_run_agent", "ai_list_templates",
        "ai_save_template", "ai_delete_template", "ai_list_kbs", "ai_create_kb",
        "ai_update_kb", "ai_delete_kb", "ai_list_kb_items", "ai_add_kb_item",
        "ai_upload_kb_file", "ai_get_kb_item", "ai_update_kb_item", "ai_delete_kb_item",
        "ai_search_kbs", "ai_list_gaps", "ai_resolve_gap", "ai_dismiss_gap", "ai_list_runs",
        "ai_get_run", "ai_metrics", "ai_list_suggestions", "ai_approve_suggestion",
        "ai_dismiss_suggestion", "ai_wake_agent", "ai_list_alerts", "ai_read_alert",
        "ai_read_all_alerts"), "AI_MODULE: restricted refused 403"),
}


def _routes():
    stack, out = list(app.routes), []
    while stack:
        r = stack.pop()
        stack.extend(getattr(getattr(r, "original_router", None), "routes", []) or [])
        stack.extend(getattr(r, "routes", []) or [])
        if getattr(r, "endpoint", None) is not None and getattr(r, "path", "").startswith(
                "/api"):
            out.append(r)
    return out


def test_every_route_is_on_the_audited_list():
    names = {r.endpoint.__name__ for r in _routes()}
    unaudited = names - set(AUDITED)
    assert not unaudited, (
        "these routes have not recorded how they enforce Only assigned data: %s — see "
        "app/assigned_access.py" % sorted(unaudited))
    assert set(AUDITED) <= names, sorted(set(AUDITED) - names)


# Audit answers that mean "this route reads no customer record for this user".
NO_CUSTOMER_RECORD_READ = {
    "ADMIN", "ADMIN (never restricted)", "EVENTS_INGEST (machine, staff)", "exempt",
    "own", "definitions only", "definitions only, no answers", "STAFF",
    "roster; only_assigned_data ADMIN-only",
    "names only", "the caller's own SIP identity, no record", "link health, no record",
    "STAFF; creates", "STAFF; structure only, no deal read", "STAFF; structure only",
}


def test_every_route_reading_customer_records_goes_through_a_scope():
    """The recorded answer must be TRUE in the source: a route that reads contacts,
    conversations, appointments or deals names assigned_access, pipeline_access's
    get_opportunity, a scoped helper, or is ADMIN/STAFF-refusing machinery."""
    markers = ("assigned_access", "get_opportunity", "_get_opportunity", "_get_task",
               "_get_note", "_number_thread(", "_bulk_load", "_search_events",
               "_contact_detail", "_pipeline_public", "_appointment_detail",
               # 2026-09-15: the booking route's checks moved into this service (shared
               # with AI agents); it resolves contact and deal through assigned_access.
               "book_appointment(")
    reads = ("Contact", "Conversation", "Appointment", "Opportunity", "BlockedTime",
             "NumberThread", "OpportunityTask", "OpportunityNote", "CompanyCamLink")
    exempt = {name for name, how in AUDITED.items()
              if how in NO_CUSTOMER_RECORD_READ}
    missing = []
    for r in _routes():
        fn = r.endpoint
        if fn.__name__ in exempt or fn.__module__ not in (
                main_mod.__name__, opportunity_workspace.__name__,
                companycam_api.__name__, softphone.__name__, openphone.__name__):
            continue
        src = inspect.getsource(fn)
        if any(m in src for m in reads) and not any(m in src for m in markers):
            missing.append(fn.__name__)
    assert not missing, missing
