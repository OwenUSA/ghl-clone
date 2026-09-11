"""Job questions on an opportunity — the definitions, and the answers.

The owner defines his own questions ("How many stories?", "What type of roof?"),
attaches each to one or more pipelines, and archives the ones he stops asking. The
definitions live in `custom_field_defs`; the answers stay where they always were,
in the free-form `Opportunity.custom_fields` JSON blob.

Everything here asserts BEHAVIOUR, per CLAUDE.md — a refusal is followed by
re-reading the deal and proving nothing moved, not by reading a status code. The
four things that must be true:

  1. **An answer is never destroyed.** Not by archiving the field, not by
     detaching its pipeline, not by moving the deal, not by a client that posts a
     shorter object than it received. Every one of those has a test that reads the
     answer back afterwards.
  2. **Validation is by type, at the API.** A number field refuses "abc" and the
     deal is unchanged; a dropdown refuses a value it does not offer.
  3. **The `owen_` namespace is untouchable.** No definition may claim it, no
     write may drop it, and `owen_call_id` still 400s on a change.
  4. **Defining is ADMIN.** A DISPATCHER and a TECH are refused, and the
     definition list is identical afterwards.
"""
import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Contact,
    CustomFieldDef,
    Opportunity,
    Pipeline,
    Role,
    Stage,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

FIELDS = "/api/custom-fields"


@pytest.fixture()
def client():
    """Two pipelines, one deal in each, and the telephony keys on the first.

      AHS      New Lead      <- "AHS-101", custom_fields already carry owen_*
      Retail   New Lead      <- "R-201"
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    admin = User(email="a@x.test", name="Owen", role=Role.ADMIN)
    dispatcher = User(email="d@x.test", name="Dispatch", role=Role.DISPATCHER)
    tech = User(email="t@x.test", name="Tech", role=Role.TECH)
    db.add_all([admin, dispatcher, tech])

    person = Contact(first_name="Real", last_name="Customer", phone="(941) 555-0004")
    db.add(person)

    ahs = Pipeline(name="Dream Team Roofing AHS", position=0)
    retail = Pipeline(name="Retail", position=1)
    db.add_all([ahs, retail])
    db.flush()

    ahs_stage = Stage(pipeline_id=ahs.id, name="New Lead", position=0)
    ahs_next = Stage(pipeline_id=ahs.id, name="Inspection", position=1)
    retail_stage = Stage(pipeline_id=retail.id, name="New Lead", position=0)
    db.add_all([ahs_stage, ahs_next, retail_stage])
    db.flush()

    ahs_deal = Opportunity(
        title="AHS-101", contact_id=person.id, pipeline_id=ahs.id,
        stage_id=ahs_stage.id, value_cents=950000,
        custom_fields={"owen_call_id": "call-abc-123", "owen_campaign": "Spring",
                       "owen_tracking_number": "(941) 555-9999",
                       "owen_is_new_caller": True})
    retail_deal = Opportunity(
        title="R-201", contact_id=person.id, pipeline_id=retail.id,
        stage_id=retail_stage.id, value_cents=400000, custom_fields={})
    db.add_all([ahs_deal, retail_deal])

    tokens = {}
    for key, u in (("admin", admin), ("dispatcher", dispatcher), ("tech", tech)):
        plain, token = mint_api_token(u, name="test-" + key)
        db.add(token)
        tokens[key] = plain
    db.commit()
    ids = {"ahs": ahs.id, "retail": retail.id, "ahs_stage": ahs_stage.id,
           "ahs_next": ahs_next.id, "retail_stage": retail_stage.id,
           "ahs_deal": ahs_deal.id, "retail_deal": retail_deal.id,
           "contact": person.id}
    db.close()

    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + tokens["admin"]
        c.ids = ids
        c.tokens = tokens
        yield c


def _as(client, role: str) -> TestClient:
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + client.tokens[role]
    c.ids = client.ids
    return c


def _define(client, label, field_type="text", options=None, pipelines=None):
    body = {"label": label, "field_type": field_type,
            "pipeline_ids": pipelines if pipelines is not None
            else [client.ids["ahs"]]}
    if options is not None:
        body["options"] = options
    r = client.post(FIELDS, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _answers(client, deal_key):
    return client.get("/api/opportunities/%d" % client.ids[deal_key]).json()[
        "custom_fields"]


def _answer(client, deal_key, **values):
    """Post answers the way the detail form does: the whole blob, echoed back."""
    blob = {**_answers(client, deal_key), **values}
    return client.patch("/api/opportunities/%d/detail" % client.ids[deal_key],
                        json={"custom_fields": blob})


# ---------------- definitions ----------------

def test_a_field_defined_on_two_pipelines_is_asked_on_deals_in_both(client):
    """"Roof type" is useful everywhere. Defining it once is what keeps the
    answers comparable across pipelines, so the SAME options have to reach both."""
    field = _define(client, "What type of roof?", "dropdown",
                    options=["Shingle", "Tile", "Metal"],
                    pipelines=[client.ids["ahs"], client.ids["retail"]])
    assert field["key"] == "what_type_of_roof"

    for pipeline in ("ahs", "retail"):
        asked = client.get(FIELDS, params={"pipeline_id": client.ids[pipeline]}).json()
        assert [f["key"] for f in asked] == ["what_type_of_roof"], pipeline
        assert asked[0]["options"] == ["Shingle", "Tile", "Metal"], (
            "the two pipelines were offered different choices for one field")

    # And both deals actually accept an answer to it.
    assert _answer(client, "ahs_deal", what_type_of_roof="Tile").status_code == 200
    assert _answer(client, "retail_deal", what_type_of_roof="Metal").status_code == 200
    assert _answers(client, "ahs_deal")["what_type_of_roof"] == "Tile"
    assert _answers(client, "retail_deal")["what_type_of_roof"] == "Metal"


def test_a_field_is_asked_only_where_it_is_attached(client):
    """"AHS claim number" belongs to the warranty pipeline and nowhere else. An
    empty attachment set never means "everywhere"."""
    _define(client, "AHS claim number", pipelines=[client.ids["ahs"]])
    unattached = _define(client, "Who referred them?", pipelines=[])

    assert [f["key"] for f in
            client.get(FIELDS, params={"pipeline_id": client.ids["ahs"]}).json()] == [
        "ahs_claim_number"]
    assert client.get(FIELDS, params={"pipeline_id": client.ids["retail"]}).json() == []
    assert unattached["pipeline_ids"] == []


def test_the_display_order_is_the_admins_to_set(client):
    stories = _define(client, "How many stories?", "number")
    leak = _define(client, "Where is the leak located?")
    age = _define(client, "How old is the roof?", "number")
    assert [f["key"] for f in client.get(FIELDS).json()] == [
        "how_many_stories", "where_is_the_leak_located", "how_old_is_the_roof"]

    r = client.post(FIELDS + "/reorder",
                    json={"field_ids": [age["id"], stories["id"], leak["id"]]})
    assert r.status_code == 200, r.text
    assert [f["key"] for f in client.get(FIELDS).json()] == [
        "how_old_is_the_roof", "how_many_stories", "where_is_the_leak_located"]


def test_a_reorder_that_does_not_name_every_field_is_refused_entirely(client):
    """The permutation contract the stage reorder already uses: a list that
    changed underneath the user is refused rather than half-applied."""
    stories = _define(client, "How many stories?", "number")
    _define(client, "How old is the roof?", "number")
    before = [f["key"] for f in client.get(FIELDS).json()]

    r = client.post(FIELDS + "/reorder", json={"field_ids": [stories["id"]]})
    assert r.status_code == 400
    assert [f["key"] for f in client.get(FIELDS).json()] == before, (
        "a refused reorder still moved something")


def test_renaming_the_question_keeps_the_answers_attached_to_it(client):
    """The key is derived once and then frozen. If a rename moved it, every answer
    already given would be orphaned under the old key."""
    field = _define(client, "How old is the roof?", "number")
    _answer(client, "ahs_deal", how_old_is_the_roof=14)

    r = client.patch(FIELDS + "/%d" % field["id"],
                     json={"label": "Roof age in years"})
    assert r.status_code == 200, r.text
    assert r.json()["key"] == "how_old_is_the_roof", "the rename moved the key"
    assert r.json()["label"] == "Roof age in years"
    assert _answers(client, "ahs_deal")["how_old_is_the_roof"] == 14


def test_a_field_type_cannot_be_changed_after_answers_exist(client):
    """Retyping a dropdown as a number would leave every recorded answer failing
    its own field's validation. `field_type` is simply not in the patch model."""
    field = _define(client, "What type of roof?", "dropdown", options=["Tile"])
    r = client.patch(FIELDS + "/%d" % field["id"], json={"field_type": "number"})
    assert r.status_code == 200, r.text
    assert client.get(FIELDS).json()[0]["field_type"] == "dropdown", (
        "the type moved under the answers")


def test_a_dropdown_without_options_offers_nothing_and_is_refused(client):
    r = client.post(FIELDS, json={"label": "What type of roof?",
                                  "field_type": "dropdown", "options": [],
                                  "pipeline_ids": []})
    assert r.status_code == 400
    assert "option" in r.json()["detail"]
    assert client.get(FIELDS).json() == []


def test_an_unknown_type_is_refused_and_names_the_five_that_exist(client):
    r = client.post(FIELDS, json={"label": "Pick several", "field_type": "multiselect",
                                  "pipeline_ids": []})
    assert r.status_code == 400
    detail = r.json()["detail"]
    for offered in ("text", "number", "dropdown", "date", "boolean"):
        assert offered in detail, detail
    assert client.get(FIELDS).json() == []


def test_two_fields_cannot_share_a_storage_key(client):
    """Two questions that slug to the same key would write answers on top of each
    other. Refused, and the refusal names the field already holding it."""
    _define(client, "How old is the roof?")
    r = client.post(FIELDS, json={"label": "  How old is the ROOF???  ",
                                  "field_type": "text", "pipeline_ids": []})
    assert r.status_code == 409
    assert "How old is the roof?" in r.json()["detail"]
    assert len(client.get(FIELDS).json()) == 1


# ---------------- answers survive ----------------

def test_an_answer_survives_a_move_to_a_pipeline_that_does_not_ask_it(client):
    """The owner's rule, and the one worth the most: moving a deal KEEPS the
    answer and hides it, and moving it back shows it again.

    No endpoint moves a deal between pipelines — a cross-pipeline stage is refused
    — so the move is made where it would actually be made today, in the database.
    What is asserted is the API's behaviour on either side of it.
    """
    _define(client, "AHS claim number", pipelines=[client.ids["ahs"]])
    _answer(client, "ahs_deal", ahs_claim_number="AHS-99887")
    assert _answers(client, "ahs_deal")["ahs_claim_number"] == "AHS-99887"

    def move(pipeline, stage):
        db = SessionLocal()
        o = db.get(Opportunity, client.ids["ahs_deal"])
        o.pipeline_id, o.stage_id = client.ids[pipeline], client.ids[stage]
        db.commit()
        db.close()

    move("retail", "retail_stage")
    # Hidden: Retail is not asked this question...
    assert [f["key"] for f in client.get(
        FIELDS, params={"pipeline_id": client.ids["retail"]}).json()] == []
    # ...and the answer is still there, untouched.
    assert _answers(client, "ahs_deal")["ahs_claim_number"] == "AHS-99887"

    # An ordinary save while it is hidden must not drop it either — the form posts
    # back what it holds, which no longer includes a control for this field.
    r = client.patch("/api/opportunities/%d/detail" % client.ids["ahs_deal"],
                     json={"custom_fields": {"owen_call_id": "call-abc-123"},
                           "value_cents": 123456})
    assert r.status_code == 200, r.text
    assert _answers(client, "ahs_deal")["ahs_claim_number"] == "AHS-99887"

    move("ahs", "ahs_stage")
    assert [f["key"] for f in client.get(
        FIELDS, params={"pipeline_id": client.ids["ahs"]}).json()] == [
        "ahs_claim_number"]
    assert _answers(client, "ahs_deal")["ahs_claim_number"] == "AHS-99887"


def test_detaching_a_pipeline_hides_the_question_without_losing_the_answers(client):
    """The same rule reached from the other side, and this one IS reachable
    through the UI: the admin takes the field off a pipeline."""
    field = _define(client, "How old is the roof?", "number",
                    pipelines=[client.ids["ahs"], client.ids["retail"]])
    _answer(client, "ahs_deal", how_old_is_the_roof=14)
    _answer(client, "retail_deal", how_old_is_the_roof=3)

    r = client.patch(FIELDS + "/%d" % field["id"],
                     json={"pipeline_ids": [client.ids["retail"]]})
    assert r.status_code == 200, r.text
    assert client.get(FIELDS, params={"pipeline_id": client.ids["ahs"]}).json() == []
    assert _answers(client, "ahs_deal")["how_old_is_the_roof"] == 14
    assert _answers(client, "retail_deal")["how_old_is_the_roof"] == 3

    client.patch(FIELDS + "/%d" % field["id"],
                 json={"pipeline_ids": [client.ids["ahs"], client.ids["retail"]]})
    assert [f["key"] for f in client.get(
        FIELDS, params={"pipeline_id": client.ids["ahs"]}).json()] == [
        "how_old_is_the_roof"]
    assert _answers(client, "ahs_deal")["how_old_is_the_roof"] == 14


def test_archiving_hides_the_question_from_new_deals_and_keeps_the_answers(client):
    """Deleting a field ARCHIVES it. The answers are customer information somebody
    typed, and no endpoint in this app destroys one."""
    field = _define(client, "How old is the roof?", "number")
    _answer(client, "ahs_deal", how_old_is_the_roof=14)

    r = client.delete(FIELDS + "/%d" % field["id"])
    assert r.status_code == 200, r.text
    assert r.json()["archived"] == field["id"]

    # Gone from what a deal is asked...
    assert client.get(FIELDS, params={"pipeline_id": client.ids["ahs"]}).json() == []
    # ...but the definition is still there, flagged, so the answer can be labelled.
    listed = client.get(FIELDS).json()
    assert [f["key"] for f in listed] == ["how_old_is_the_roof"]
    assert listed[0]["archived"] is True
    assert client.get(FIELDS, params={"include_archived": False}).json() == []
    # And the answer is readable exactly as before.
    assert _answers(client, "ahs_deal")["how_old_is_the_roof"] == 14

    # A new deal is not asked it.
    created = client.post("/api/opportunities", json={
        "title": "AHS-105", "pipeline_id": client.ids["ahs"],
        "stage_id": client.ids["ahs_stage"]})
    assert created.status_code == 201, created.text
    fresh = client.get("/api/opportunities/%d" % created.json()["id"]).json()
    assert fresh["custom_fields"] == {}


def test_unarchiving_puts_the_question_back_with_its_answers_intact(client):
    field = _define(client, "How old is the roof?", "number")
    _answer(client, "ahs_deal", how_old_is_the_roof=14)
    client.delete(FIELDS + "/%d" % field["id"])

    r = client.post(FIELDS + "/%d/restore" % field["id"])
    assert r.status_code == 200, r.text
    assert r.json()["archived"] is False
    assert [f["key"] for f in client.get(
        FIELDS, params={"pipeline_id": client.ids["ahs"]}).json()] == [
        "how_old_is_the_roof"]
    assert _answers(client, "ahs_deal")["how_old_is_the_roof"] == 14
    # ...and it is editable again.
    assert _answer(client, "ahs_deal", how_old_is_the_roof=15).status_code == 200
    assert _answers(client, "ahs_deal")["how_old_is_the_roof"] == 15


def test_an_archived_fields_answer_cannot_be_rewritten_from_the_form(client):
    """Visible and readable, but not editable — and the refusal says so out loud
    rather than dropping the value on the floor."""
    field = _define(client, "How old is the roof?", "number")
    _answer(client, "ahs_deal", how_old_is_the_roof=14)
    client.delete(FIELDS + "/%d" % field["id"])

    r = _answer(client, "ahs_deal", how_old_is_the_roof=99)
    assert r.status_code == 400
    assert "archived" in r.json()["detail"]
    assert _answers(client, "ahs_deal")["how_old_is_the_roof"] == 14


def test_a_client_that_posts_a_shorter_object_does_not_delete_answers(client):
    """`merge_answers` merges. A key nobody mentioned keeps its value — the CLI
    patching one field must not silently clear the other four."""
    _define(client, "How old is the roof?", "number")
    _define(client, "Where is the leak located?")
    _answer(client, "ahs_deal", how_old_is_the_roof=14,
            where_is_the_leak_located="Back bedroom ceiling")

    r = client.patch("/api/opportunities/%d/detail" % client.ids["ahs_deal"],
                     json={"custom_fields": {"how_old_is_the_roof": 15}})
    assert r.status_code == 200, r.text
    after = _answers(client, "ahs_deal")
    assert after["how_old_is_the_roof"] == 15
    assert after["where_is_the_leak_located"] == "Back bedroom ceiling"
    assert after["owen_campaign"] == "Spring"


def test_an_answer_is_cleared_by_sending_it_empty_not_by_omitting_it(client):
    _define(client, "Where is the leak located?")
    _answer(client, "ahs_deal", where_is_the_leak_located="Back bedroom")
    r = _answer(client, "ahs_deal", where_is_the_leak_located="")
    assert r.status_code == 200, r.text
    assert "where_is_the_leak_located" not in _answers(client, "ahs_deal")


def test_removing_a_dropdown_option_does_not_make_old_deals_unsaveable(client):
    """The form posts the whole object back on every save. If an unchanged echo
    were re-validated, retiring "Tile" would lock every deal already holding it
    out of being edited at all."""
    field = _define(client, "What type of roof?", "dropdown",
                    options=["Shingle", "Tile", "Metal"])
    _answer(client, "ahs_deal", what_type_of_roof="Tile")
    client.patch(FIELDS + "/%d" % field["id"], json={"options": ["Shingle", "Metal"]})

    r = _answer(client, "ahs_deal")           # echo, plus nothing
    assert r.status_code == 200, r.text
    assert _answers(client, "ahs_deal")["what_type_of_roof"] == "Tile"
    # A real change is still held to the new list — including a second attempt to
    # set the retired value, which is no longer an echo of what is stored.
    assert _answer(client, "ahs_deal", what_type_of_roof="Thatch").status_code == 400
    assert _answers(client, "ahs_deal")["what_type_of_roof"] == "Tile"


# ---------------- validation ----------------

def test_a_number_field_rejects_words_and_the_deal_is_not_modified(client):
    _define(client, "How old is the roof?", "number")
    _answer(client, "ahs_deal", how_old_is_the_roof=14)
    before = client.get("/api/opportunities/%d" % client.ids["ahs_deal"]).json()

    r = _answer(client, "ahs_deal", how_old_is_the_roof="abc")
    assert r.status_code == 400
    assert "How old is the roof?" in r.json()["detail"], r.json()
    after = client.get("/api/opportunities/%d" % client.ids["ahs_deal"]).json()
    assert after == before, "a refused answer still changed the deal"


def test_a_number_field_takes_a_number_typed_as_a_string(client):
    """Every browser input hands back a string. Refusing "14" would mean no answer
    could ever be given through the form."""
    _define(client, "How old is the roof?", "number")
    assert _answer(client, "ahs_deal", how_old_is_the_roof="14").status_code == 200
    assert _answers(client, "ahs_deal")["how_old_is_the_roof"] == 14
    assert _answer(client, "ahs_deal", how_old_is_the_roof="2.5").status_code == 200
    assert _answers(client, "ahs_deal")["how_old_is_the_roof"] == 2.5


def test_a_dropdown_rejects_a_value_it_does_not_offer(client):
    _define(client, "What type of roof?", "dropdown",
            options=["Shingle", "Tile", "Metal"])
    _answer(client, "ahs_deal", what_type_of_roof="Tile")

    r = _answer(client, "ahs_deal", what_type_of_roof="Thatch")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "Shingle" in detail and "Tile" in detail and "Metal" in detail, detail
    assert _answers(client, "ahs_deal")["what_type_of_roof"] == "Tile"


def test_a_date_field_rejects_nonsense(client):
    _define(client, "When was it last replaced?", "date")
    assert _answer(client, "ahs_deal",
                   when_was_it_last_replaced="2019-04-02").status_code == 200
    r = _answer(client, "ahs_deal", when_was_it_last_replaced="last April")
    assert r.status_code == 400
    assert _answers(client, "ahs_deal")["when_was_it_last_replaced"] == "2019-04-02"
    assert _answer(client, "ahs_deal",
                   when_was_it_last_replaced="2019-13-45").status_code == 400


def test_a_yes_no_field_stores_a_boolean_however_it_is_spelled(client):
    _define(client, "Is there active leaking?", "boolean")
    for sent, stored in (("yes", True), ("no", False), (True, True),
                         ("false", False), ("1", True)):
        assert _answer(client, "ahs_deal",
                       is_there_active_leaking=sent).status_code == 200, sent
        assert _answers(client, "ahs_deal")["is_there_active_leaking"] is stored, sent
    r = _answer(client, "ahs_deal", is_there_active_leaking="maybe")
    assert r.status_code == 400
    assert _answers(client, "ahs_deal")["is_there_active_leaking"] is True


def test_the_add_opportunity_dialog_validates_the_same_way(client):
    """Two doors into the same blob. A dropdown that could be talked into an
    unlisted answer by creating the deal instead of editing it would be no rule."""
    _define(client, "What type of roof?", "dropdown", options=["Shingle", "Tile"])
    before = len(client.get("/api/opportunities",
                            params={"pipeline_id": client.ids["ahs"]}).json())

    r = client.post("/api/opportunities", json={
        "title": "AHS-106", "pipeline_id": client.ids["ahs"],
        "stage_id": client.ids["ahs_stage"],
        "custom_fields": {"what_type_of_roof": "Thatch"}})
    assert r.status_code == 400
    assert len(client.get("/api/opportunities",
                          params={"pipeline_id": client.ids["ahs"]}).json()) == before, (
        "a refused create still filed a card")

    ok = client.post("/api/opportunities", json={
        "title": "AHS-106", "pipeline_id": client.ids["ahs"],
        "stage_id": client.ids["ahs_stage"],
        "custom_fields": {"what_type_of_roof": "Tile"}})
    assert ok.status_code == 201, ok.text
    assert client.get("/api/opportunities/%d" % ok.json()["id"]).json()[
        "custom_fields"] == {"what_type_of_roof": "Tile"}


# ---------------- the owen_ namespace ----------------

def test_a_field_cannot_be_defined_in_the_telephony_namespace(client):
    for label in ("owen_campaign", "Owen campaign", "  owen tracking number  "):
        r = client.post(FIELDS, json={"label": label, "field_type": "text",
                                      "pipeline_ids": []})
        assert r.status_code == 400, "%r was accepted" % label
        assert "owen_" in r.json()["detail"], r.json()
    assert client.get(FIELDS).json() == [], "one of them was defined anyway"


def test_the_telephony_keys_cannot_be_archived_renamed_or_deleted(client):
    """They are not definitions at all — there is no row to archive. The point of
    the test is that nothing in the custom-fields UI can reach them, so a field id
    cannot be found for one and the blob keeps all four however the deal is saved.
    """
    assert client.get(FIELDS).json() == [], "the owen_* keys are not definitions"

    _define(client, "How old is the roof?", "number")
    before = _answers(client, "ahs_deal")
    assert set(before) == {"owen_call_id", "owen_campaign",
                           "owen_tracking_number", "owen_is_new_caller"}

    # A form save that mentions none of them keeps all four.
    r = client.patch("/api/opportunities/%d/detail" % client.ids["ahs_deal"],
                     json={"custom_fields": {"how_old_is_the_roof": 14}})
    assert r.status_code == 200, r.text
    after = _answers(client, "ahs_deal")
    for key, value in before.items():
        assert after[key] == value, "%s was lost by an unrelated save" % key

    # An explicit attempt to delete one is equally powerless.
    r = client.patch("/api/opportunities/%d/detail" % client.ids["ahs_deal"],
                     json={"custom_fields": {"owen_call_id": "call-abc-123"}})
    assert r.status_code == 200, r.text
    assert _answers(client, "ahs_deal")["owen_campaign"] == "Spring", (
        "owen_campaign was deleted by posting an object without it — call "
        "attribution would be broken and nothing would have said so")


def test_the_join_key_still_refuses_a_change(client):
    """Unchanged from before this feature, and pinned again here: `owen_call_id`
    joins this deal to the telephony project's call record."""
    r = client.patch("/api/opportunities/%d/detail" % client.ids["ahs_deal"],
                     json={"custom_fields": {"owen_call_id": "CLOBBERED"}})
    assert r.status_code == 400
    assert "owen_call_id" in r.json()["detail"]
    assert _answers(client, "ahs_deal")["owen_call_id"] == "call-abc-123"


# ---------------- roles ----------------

@pytest.mark.parametrize("role", ["dispatcher", "tech"])
def test_only_an_admin_can_define_edit_or_archive_a_field(client, role):
    """And nothing changes when they try — the definitions are re-read and
    compared, not the status code alone."""
    field = _define(client, "How old is the roof?", "number")
    before = client.get(FIELDS).json()
    other = _as(client, role)

    attempts = [
        other.post(FIELDS, json={"label": "Sneaky", "field_type": "text",
                                 "pipeline_ids": []}),
        other.patch(FIELDS + "/%d" % field["id"], json={"label": "Renamed"}),
        other.patch(FIELDS + "/%d" % field["id"],
                    json={"pipeline_ids": [client.ids["retail"]]}),
        other.delete(FIELDS + "/%d" % field["id"]),
        other.post(FIELDS + "/%d/restore" % field["id"]),
        other.post(FIELDS + "/reorder", json={"field_ids": [field["id"]]}),
    ]
    assert [r.status_code for r in attempts] == [403] * 6, [
        (r.request.method, r.status_code) for r in attempts]
    assert client.get(FIELDS).json() == before, (
        "a %s changed the field definitions" % role)


@pytest.mark.parametrize("role", ["dispatcher", "tech"])
def test_every_role_can_READ_the_questions(client, role):
    """The form has to render them for whoever opens the deal. A TECH reading a
    job needs to see "2 stories, leak over the back bedroom" — the gate is on
    defining a field, not on being asked one."""
    _define(client, "How many stories?", "number")
    assert [f["key"] for f in _as(client, role).get(FIELDS).json()] == [
        "how_many_stories"]


def test_a_tech_still_cannot_answer_a_question(client):
    """`PATCH /detail` is STAFF and was not widened. A TECH may move a card
    between stages; they may not edit the record."""
    _define(client, "How many stories?", "number")
    r = _as(client, "tech").patch(
        "/api/opportunities/%d/detail" % client.ids["ahs_deal"],
        json={"custom_fields": {"how_many_stories": 2}})
    assert r.status_code == 403
    assert "how_many_stories" not in _answers(client, "ahs_deal")


# ---------------- pipeline structure interaction ----------------

def test_deleting_an_empty_pipeline_detaches_a_field_without_deleting_it(client):
    """The pipeline delete already detaches saved views and calendars and names
    what it detached. A field attachment is the same weak reference."""
    db = SessionLocal()
    empty = Pipeline(name="Storm damage", position=2)
    db.add(empty)
    db.commit()
    empty_id = empty.id
    db.close()

    field = _define(client, "What type of roof?", "dropdown", options=["Tile"],
                    pipelines=[client.ids["ahs"], empty_id])
    r = client.delete("/api/pipelines/%d" % empty_id)
    assert r.status_code == 200, r.text
    assert r.json()["detached_custom_fields"] == [field["id"]]

    survivor = client.get(FIELDS).json()
    assert [f["key"] for f in survivor] == ["what_type_of_roof"]
    assert survivor[0]["pipeline_ids"] == [client.ids["ahs"]]

    db = SessionLocal()
    assert db.scalar(select(CustomFieldDef).where(
        CustomFieldDef.id == field["id"])) is not None, "the definition was deleted"
    db.close()


def test_attaching_a_field_to_a_pipeline_that_does_not_exist_is_refused(client):
    r = client.post(FIELDS, json={"label": "Roof type", "field_type": "text",
                                  "pipeline_ids": [9999]})
    assert r.status_code == 400
    assert "9999" in r.json()["detail"]
    assert client.get(FIELDS).json() == []
