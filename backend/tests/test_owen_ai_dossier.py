"""Owen AI's compact read tools — job_dossier and board_snapshot (2026-11-03, owen_ai/dossier.py).

Pinned, by behaviour:
  * the parsers turn live-shaped (anonymised) Zuper / Connect / Quo JSON into the compact
    dossier: stages with who moved them, only FILLED answers and fields, photo answers as a
    count, phones in E.164, times in America/New_York, each proposal option's total, deposit
    and lines, the chosen option, invoices;
  * one timeline, newest first, merges Zuper Connect texts + calls and Quo calls + texts, with
    who on our side, missed calls and call summaries; old entries (> 60 days) are left out;
  * the answer fits the size cap and says what it trimmed;
  * a source that fails is NAMED in `sources`, never a crash, and the job still comes back;
  * only reads go out: GETs to Zuper, the listed Connect reads, GETs to Quo — nothing else;
  * open_jobs lists only open jobs and raises (never a partial list) when Zuper fails;
  * raw zuper_get answers lose null / empty values before any cut (0 and false stay).
"""
import json
import re
from datetime import UTC, datetime

import httpx
import pytest
from app.owen_ai import dossier, quo, tools
from app.zuper import client
from tests.zuper_support import zuper_env  # noqa: F401  (fixture)

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
JOB_UID = "11111111-aaaa-4bbb-8ccc-000000000701"
CUST_UID = "22222222-aaaa-4bbb-8ccc-000000000001"
PHONE = "+15555550101"
OWEN = {"user_uid": "u-1", "first_name": "Owen", "last_name": "Boss", "email": "o@x.test"}
LUIS = {"user_uid": "u-2", "first_name": "Luis", "last_name": "Office"}


def job_record(**over):
    """GET /jobs/{uid} — the live shape, trimmed to the fields the dossier reads."""
    rec = {
        "job_uid": JOB_UID, "work_order_number": 701, "job_title": "TEST - leak check",
        "job_category": {"category_uid": dossier.SIMPLE_FLOW_UID,
                         "category_name": "AHS Simple Flow"},
        "current_job_status": {"status_name": "Proposal Creation", "status_type": "ESTIMATING"},
        "job_status": [
            {"status_name": "Got a job from AHS", "status_type": "NEW",
             "created_at": "2026-10-20T13:00:00.000Z", "done_by": OWEN, "time_on_status": 60,
             "checklist": [
                 {"question": "What is happening?", "answer": "Water stain in the kitchen",
                  "type": "MULTI_LINE"},
                 {"question": "Second phone", "answer": "", "type": "SINGLE_LINE"},
                 {"question": "Welcoming Call Pitch", "answer": "", "type": "HEADER"}]},
            {"status_name": "Day of Inspection", "status_type": "SCHEDULED",
             "created_at": "2026-10-22T14:00:00.000Z", "done_by": LUIS,
             "checklist": [
                 {"question": "Album 3 leak-location photos",
                  "answer": ["https://img.test/a.jpg", "https://img.test/b.jpg"],
                  "type": "MULTI_IMAGE"},
                 {"question": "Recommendation recorded", "answer": "Yes", "type": "RADIO"}]},
            {"status_name": "Proposal Creation", "status_type": "ESTIMATING",
             "created_at": "2026-10-25T16:30:00.000Z", "done_by": OWEN, "checklist": []},
        ],
        "custom_fields": [
            {"label": "Roof material", "value": "Tile", "type": "SINGLE_ITEM"},
            {"label": "AHS Job ID", "value": "", "type": "SINGLE_LINE"},
            {"label": "Secret", "value": "x", "hide_field": True},
            {"label": "Notes for the proposal", "value": "<p>Two leaks <b>near</b> chimney</p>"},
        ],
        "products": [{"product_id": "AHS-LEAK-REPAIR", "product_name": "AHS - Leak Repair",
                      "quantity": 1, "price": 1100, "total": 1100,
                      "product_description": "x" * 900}],
        "customer": {"customer_uid": CUST_UID, "customer_first_name": "Test",
                     "customer_last_name": "Person", "customer_email": "t@x.test",
                     "customer_contact_no": {"mobile": PHONE, "home": "", "work": None}},
        "customer_address": {"street": "1 Test St", "city": "Bradenton", "state": "FL"},
        "scheduled_start_time": "2026-10-22T14:00:00.000Z",
        "scheduled_end_time": "2026-10-22T15:00:00.000Z",
        "assigned_to": [{"user": LUIS}], "created_at": "2026-10-20T12:59:00.000Z",
        "job_total": 1100, "is_deleted": False,
    }
    rec.update(over)
    return rec


def estimate_detail(uid="est-1", no=139, status="AWAIT_RESPONSE", accepted=None):
    def opt(name, total, code):
        return {"option_name": name, "is_accepted": name == accepted, "total": total,
                "deposit": total / 2, "is_recommended": name == "BEST", "is_hidden": False,
                "line_items": [{"product_id": code, "name": name + " - Tile Leak Repair",
                                "quantity": 1, "unit_price": total, "total": total,
                                "description": "<p>Scope</p>" + "word " * 100,
                                "plain_text_description": "Scope " + "word " * 100}]}
    return {"estimate_uid": uid, "estimate_no": no, "prefix": "DTR-", "estimate_status": status,
            "proposal_title": "Proposal for Test", "total": 2000, "is_deleted": False,
            "created_at": "2026-10-26T15:56:17.953Z", "sent_date": "2026-10-26T15:56:59.148Z",
            "accepted_date": None, "expiry_date": "2026-11-25T15:56:17.907Z",
            "customer_viewed": True, "deposit": {"total": 1000, "status": "NOT_COLLECTED"},
            "job": {"job_uid": JOB_UID, "work_order_number": 701},
            "proposal_options": [opt("GOOD", 1500, "DTR-TILE-GOOD"),
                                 opt("BETTER", 1800, "DTR-TILE-BETTER"),
                                 opt("BEST", 2000, "DTR-TILE-BEST")]}


def estimate_row(**over):
    """GET /estimate list row: options carry NO totals or line items (live)."""
    d = estimate_detail(**over)
    d["proposal_options"] = [{k: o[k] for k in ("option_name", "is_accepted")}
                             for o in d["proposal_options"]]
    return d


INVOICE = {"invoice_uid": "inv-1", "invoice_no": 3, "prefix": "", "invoice_status":
           "AWAIT_PAYMENT", "total": 1500, "amount_due": 1500, "amount_paid": 0,
           "is_paid": False, "due_date": "2026-10-03T03:59:00.000Z", "paid_date": None,
           "payment_history": [], "is_deleted": False}

CONVERSATIONS = [
    {"conversation_uid": "conv-1", "recent_conversation_at": "2026-11-01T18:46:44.000Z",
     "recent_message": {"direction": "INCOMING"},
     "customer_number": {"customer_uid": CUST_UID, "number": "15555550101"}},
    # same keyword hit on another number: not this customer's
    {"conversation_uid": "conv-x", "recent_conversation_at": "2026-11-01T18:46:44.000Z",
     "customer_number": {"customer_uid": "other", "number": "15555550199"}},
]

MESSAGES = [
    {"message_type": "SMS", "direction": "INCOMING", "status": "DELIVERED",
     "created_at": "2026-11-01T18:46:44.000Z", "message": "Is Tuesday ok?", "user": None},
    {"message_type": "SMS", "direction": "OUTGOING", "status": "QUEUED",
     "created_at": "2026-10-30T13:00:00.000Z", "message": "Your appointment is confirmed",
     "user": {"first_name": "Zuper", "last_name": "Admin"}},
    {"message_type": "CALL", "direction": "INCOMING", "created_at": "2026-10-29T12:00:00.000Z",
     "call": {"call_uid": "zc-1", "status": "MISSED", "duration": 0}},
    {"message_type": "SMS", "direction": "INCOMING", "status": "DELIVERED",
     "created_at": "2026-07-01T12:00:00.000Z", "message": "too old"},
]

CALLS = [
    {"call_uid": "zc-2", "created_at": "2026-10-28T15:17:50.000Z", "direction": "OUTGOING",
     "status": "COMPLETED", "duration": 141, "is_recorded": True,
     "from": {"type": "user", "user": {"user_name": "Luis Office"}},
     "to": {"type": "customer", "customer": {"number": "15555550101"}}},
    {"call_uid": "zc-1", "created_at": "2026-10-29T12:00:00.000Z", "direction": "INCOMING",
     "status": "MISSED", "duration": 0, "is_recorded": False,
     "from": {"type": "customer"}, "to": {"type": "user", "user": None}},
]

CALL_DETAILS = {"data": {"call_recordings": [{"call_summary": {
    "summary": "Luis confirmed the inspection for Thursday.", "next_action": "Send tech"}}]}}

QUO_CALLS = [
    {"id": "AC1", "direction": "incoming", "status": "completed", "duration": 75,
     "createdAt": "2026-10-31T16:32:07.025Z", "answeredAt": "2026-10-31T16:32:10.000Z",
     "answeredBy": "US1", "userId": "US1"},
    {"id": "AC2", "direction": "incoming", "status": "no-answer", "duration": 0,
     "createdAt": "2026-10-27T16:32:07.025Z", "answeredAt": None, "answeredBy": None,
     "userId": "US1"},
]
QUO_MESSAGES = [{"id": "M1", "direction": "outgoing", "text": "On my way", "status": "delivered",
                 "createdAt": "2026-10-27T17:00:00.000Z", "userId": "US1"}]


class Router:
    """Answers Zuper, Zuper Connect and Quo at the HTTP boundary; records every request."""

    def __init__(self, fail: set[str] | None = None, routes: dict | None = None):
        self.requests: list[httpx.Request] = []
        self.fail = fail or set()
        self.routes = routes or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        side = "quo" if host == "api.openphone.com" else \
            "connect" if "-connect." in host else "zuper"
        if side in self.fail:
            return httpx.Response(500, json={"message": "down"})
        if side == "quo":
            path = path.removeprefix("/v1")
        else:
            path = re.sub(r"^/api(/telephony)?", "", path)
        key = "%s %s %s" % (side, request.method, path)
        for pattern, answer in self.routes.items():
            if re.fullmatch(pattern, key):
                payload = answer(request) if callable(answer) else answer
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"message": "no route " + key})


def default_routes(**extra):
    routes = {
        r"zuper GET /jobs": lambda req: {"type": "success", "total_pages": 1, "data": [
            job_record(job_uid="j-7010", work_order_number=7010), job_record()]},
        r"zuper GET /jobs/" + JOB_UID: {"type": "success", "data": job_record()},
        r"zuper GET /jobs/[^/]+/note": {"type": "success", "data": [
            {"note_uid": "n1", "note": "<p>Customer wants <b>Tuesday</b></p>", "note_type": "TEXT",
             "created_by": LUIS, "created_at": "2026-10-26T12:00:00.000Z", "is_private": True},
            {"note_uid": "n2", "note_type": "TEXT", "created_by": OWEN,
             "created_at": "2026-10-27T12:00:00.000Z"}]},
        r"zuper GET /estimate": {"type": "success", "data": [estimate_row()]},
        r"zuper GET /estimate/est-1": {"type": "success", "data": estimate_detail()},
        r"zuper GET /invoice": {"type": "success", "data": [INVOICE]},
        r"connect POST /message/conversations": {"type": "success", "total_pages": 1,
                                                 "data": CONVERSATIONS},
        r"connect POST /messages": {"type": "success", "data": MESSAGES},
        r"connect POST /calls": {"type": "success", "data": CALLS},
        r"connect GET /calls/zc-2/details": CALL_DETAILS,
        r"quo GET /phone-numbers": {"data": [{"id": "PN1", "number": "+15555550000"}]},
        r"quo GET /users": {"data": [{"id": "US1", "firstName": "Owen", "lastName": "Boss"}]},
        r"quo GET /calls": {"data": QUO_CALLS, "nextPageToken": None},
        r"quo GET /messages": {"data": QUO_MESSAGES, "nextPageToken": None},
        r"quo GET /call-summaries/AC1": {"data": {"callId": "AC1", "status": "completed",
                                                  "summary": ["Customer asked for Tuesday."],
                                                  "nextSteps": ["Call back Monday"]}},
        r"quo GET /conversations": {"data": [
            {"participants": [PHONE], "lastActivityAt": "2026-11-02T10:00:00.000Z"}]},
    }
    routes.update(extra)
    return routes


@pytest.fixture()
def wire(zuper_env, monkeypatch):  # noqa: F811
    monkeypatch.setenv("OWEN_AI_QUO_API_KEY", "quo-test")
    dossier._QUO_CACHE.clear()

    def install(router: Router) -> Router:
        transport = httpx.MockTransport(router)
        client.TRANSPORT = transport
        monkeypatch.setattr(quo, "TRANSPORT", transport)
        return router
    yield install
    dossier._QUO_CACHE.clear()


def only_reads(router: Router) -> None:
    for req in router.requests:
        host, path = req.url.host, req.url.path
        if host == "api.openphone.com":
            assert req.method == "GET", path
        elif "-connect." in host:
            sub = path.removeprefix("/api/telephony")
            assert any(m == req.method and re.match(rx, sub)
                       for m, rx in client.CONNECT_READS), (req.method, path)
        else:
            assert req.method == "GET", (req.method, path)


# ------------------------------------------------------------------------------ parsing

def test_a_job_record_becomes_the_compact_job():
    j = dossier.parse_job(job_record())
    assert j["number"] == "701" and j["board"] == "AHS Simple Flow"
    assert j["stage"] == "Proposal Creation"
    assert j["stage_since"] == "2026-10-25 12:30"                 # EDT
    assert [(h["stage"], h["by"]) for h in j["stage_history"]] == [
        ("Got a job from AHS", "Owen Boss"), ("Day of Inspection", "Luis Office"),
        ("Proposal Creation", "Owen Boss")]
    assert j["checklist"] == {
        "Got a job from AHS": {"What is happening?": "Water stain in the kitchen"},
        "Day of Inspection": {"Album 3 leak-location photos": "2 files",
                              "Recommendation recorded": "Yes"}}
    assert j["custom_fields"] == {"Roof material": "Tile",
                                  "Notes for the proposal": "Two leaks near chimney"}
    assert j["customer"]["phones"] == [PHONE] and j["customer"]["has_email"] is True
    assert j["line_items"] == [{"code": "AHS-LEAK-REPAIR", "name": "AHS - Leak Repair",
                                "qty": 1, "price": 1100, "total": 1100}]
    assert j["assigned"] == ["Luis Office"]


def test_a_proposal_keeps_each_options_prices_and_lines():
    p = dossier.parse_proposal(estimate_detail(status="APPROVED", accepted="BETTER"))
    assert p["number"] == "DTR-139" and p["chosen"] == "BETTER"
    assert [(o["name"], o["total"], o["deposit"]) for o in p["options"]] == [
        ("GOOD", 1500, 750), ("BETTER", 1800, 900), ("BEST", 2000, 1000)]
    line = p["options"][1]["lines"][0]
    assert line["code"] == "DTR-TILE-BETTER" and line["price"] == 1800
    assert len(line["desc"]) <= dossier.DESC_MAX
    assert p["deposit"] == {"total": 1000, "status": "NOT_COLLECTED"}


def test_the_timeline_merges_every_channel_newest_first():
    since = datetime(2026, 9, 4, tzinfo=UTC)
    entries = (dossier.zuper_texts(MESSAGES, since)
               + dossier.zuper_calls(CALLS, since, {"zc-2": "Confirmed Thursday"})
               + dossier.quo_calls(QUO_CALLS, since, {"AC1": "Asked for Tuesday"},
                                   {"US1": "Owen Boss"})
               + dossier.quo_texts(QUO_MESSAGES, since, {"US1": "Owen Boss"}))
    tl = dossier.timeline(entries)
    assert [e["ts"] for e in tl] == sorted((e["ts"] for e in tl), reverse=True)
    assert {e["ch"] for e in tl} == {"zuper_text", "zuper_call", "quo_call", "quo_text"}
    assert not any(e.get("text") == "too old" for e in tl)            # older than the window
    assert sum(e["ch"] == "zuper_call" for e in tl) == 2     # the CALL message row is not doubled
    by_ch = {(e["ch"], e["dir"]): e for e in tl}
    assert by_ch[("zuper_call", "out")]["who"] == "Luis Office"
    assert by_ch[("zuper_call", "out")]["summary"] == "Confirmed Thursday"
    assert by_ch[("zuper_call", "in")]["missed"] is True
    assert by_ch[("zuper_text", "out")]["who"] == "Zuper automation"
    missed_quo = [e for e in tl if e["ch"] == "quo_call" and e.get("missed")]
    assert len(missed_quo) == 1 and "who" not in missed_quo[0]       # nobody answered it
    answered = [e for e in tl if e["ch"] == "quo_call" and e["answered"]]
    assert answered[0]["summary"] == "Asked for Tuesday" and answered[0]["who"] == "Owen Boss"
    assert all("status" not in e for e in tl if e["ch"].endswith("_text"))


# ------------------------------------------------------------------------------ the tools

def test_job_dossier_end_to_end_reads_only_and_fits(wire):
    router = wire(Router(routes=default_routes()))
    out = dossier.dossier("#701", now=NOW)
    only_reads(router)
    assert out["job"]["uid"] == JOB_UID
    # #701 picked by its exact number, not #7010 the keyword also matched
    assert [r.url.path.rsplit("/", 1)[-1] for r in router.requests[:2]] == ["jobs", JOB_UID]
    assert out["proposals"][0]["options"][2]["total"] == 2000
    assert out["invoices"][0]["due"] == 1500
    notes = {n["by"]: n for n in out["notes"]}
    assert notes["Luis Office"]["text"] == "Customer wants Tuesday"
    assert notes["Owen Boss"]["text"] == "[no text in the API answer]"
    assert "1 without text" in out["sources"]["notes"]
    chans = [e["ch"] for e in out["conversations"]]
    assert set(chans) == {"zuper_text", "zuper_call", "quo_call", "quo_text"}
    assert out["sources"]["zuper_texts"].startswith("ok (1 conversation")  # conv-x left out
    assert out["conversations"][0]["text"] == "Is Tuesday ok?"
    assert dossier.size(out) <= dossier.MAX_CHARS
    assert out["requests"]["zuper"] == 6 and out["requests"]["quo"] >= 4
    text = tools.SPECS[0][3]({"job": "701"})
    assert json.loads(text)["job"]["number"] == "701"


def test_a_job_uid_is_read_directly(wire):
    router = wire(Router(routes=default_routes()))
    out = dossier.dossier(JOB_UID, now=NOW)
    assert out["job"]["number"] == "701"
    assert router.requests[0].url.path.endswith("/jobs/" + JOB_UID)


def test_a_huge_job_is_trimmed_to_the_cap_and_says_so(wire):
    many = [{"message_type": "SMS", "direction": "INCOMING", "status": "DELIVERED",
             "created_at": "2026-10-%02dT%02d:00:00.000Z" % (5 + i % 25, i % 24),
             "message": "long words " * 60} for i in range(80)]
    big = job_record(custom_fields=[{"label": "Field %d" % i, "value": "v" * 150}
                                    for i in range(60)])
    router = wire(Router(routes=default_routes(**{
        r"connect POST /messages": {"data": many},
        r"zuper GET /jobs/" + JOB_UID: {"data": big}})))
    out = dossier.dossier("701", now=NOW)
    assert dossier.size(out) <= dossier.MAX_CHARS
    assert out["trimmed"] and len(out["conversations"]) <= dossier.TIMELINE_MAX
    only_reads(router)


@pytest.mark.parametrize("down,keys", [
    ("connect", ("zuper_texts", "zuper_calls")),
    ("quo", ("quo",)),
])
def test_a_failing_source_is_named_not_a_crash(wire, down, keys):
    wire(Router(fail={down}, routes=default_routes()))
    out = dossier.dossier("701", now=NOW)
    assert out["job"]["number"] == "701" and out["proposals"]
    for k in keys:
        assert out["sources"][k].startswith("unavailable"), out["sources"]
    assert out["conversations"]                                   # the other side still there


def test_no_quo_key_is_said_in_sources(wire, monkeypatch):
    wire(Router(routes=default_routes()))
    monkeypatch.delenv("OWEN_AI_QUO_API_KEY")
    out = dossier.dossier("701", now=NOW)
    assert "OWEN_AI_QUO_API_KEY" in out["sources"]["quo"]


def test_a_missing_job_is_an_error_sentence(wire):
    wire(Router(routes=default_routes(**{r"zuper GET /jobs": {"data": []}})))
    assert dossier.job_dossier("99999") == "ERROR: no Zuper job #99999"


def test_board_snapshot_lists_open_jobs_with_stage_counts(wire):
    rows = [job_record(),
            job_record(job_uid="j-2", work_order_number=702,
                       current_job_status={"status_name": "Job Completed",
                                           "status_type": "COMPLETED"}),
            job_record(job_uid="j-3", work_order_number=703, is_deleted=True),
            job_record(job_uid="j-4", work_order_number=704,
                       current_job_status={"status_name": "Got a job from AHS",
                                           "status_type": "NEW"})]
    router = wire(Router(routes=default_routes(**{
        r"zuper GET /jobs": {"total_pages": 1, "data": rows}})))
    snap = dossier.snapshot(now=NOW)
    only_reads(router)
    assert snap["open_jobs"] == 2
    assert snap["per_stage"] == {"Got a job from AHS": 1, "Proposal Creation": 1}
    table = [dict(zip(snap["columns"], r, strict=True)) for r in snap["rows"]]
    assert [t["number"] for t in table] == ["704", "701"]             # board order
    j701 = table[1]
    assert j701["days_in_stage"] == 8 and j701["proposal"] == "DTR-139 AWAIT_RESPONSE"
    assert j701["last_contact"] == "2026-11-02 05:00 quo"           # Quo newer than Zuper
    assert json.loads(tools.SPECS[1][3]({}))["board"] == "AHS Simple Flow"


def test_open_jobs_is_the_sweeps_list_and_never_partial(wire):
    wire(Router(routes=default_routes()))
    jobs = dossier.open_jobs("AHS Simple Flow", now=NOW)
    assert {(j["number"], j["stage"]) for j in jobs} == {("701", "Proposal Creation"),
                                                         ("7010", "Proposal Creation")}
    assert all(j["uid"] and not j["number"].startswith("#") for j in jobs)
    wire(Router(fail={"zuper"}, routes=default_routes()))
    with pytest.raises(client.ZuperError):
        dossier.open_jobs("AHS Simple Flow")


def test_another_board_is_found_by_name(wire):
    router = wire(Router(routes=default_routes(**{
        r"zuper GET /jobs/category": {"data": [
            {"category_uid": "cat-r", "category_name": "Retail"}]},
        r"zuper GET /jobs": lambda req: {"total_pages": 1, "data": [
            job_record()] if req.url.params.get("filter.category") == "cat-r" else []}})))
    assert [j["number"] for j in dossier.open_jobs("retail", now=NOW)] == ["701"]
    assert dossier.board_snapshot("Nope").startswith("ERROR: no Zuper board named")
    only_reads(router)


def test_writes_stay_impossible_inside_the_tools_scope(wire):
    wire(Router(routes=default_routes()))
    r = dossier.Reader()
    with client.owen_ai_read(), pytest.raises(client.ZuperError):
        client.request("POST", "/jobs", body={})
    with pytest.raises(client.ZuperError):
        r.connect("POST", "/message", {"message": []})                  # the send: not a read
    with pytest.raises(quo.QuoError):
        quo.get("/webhooks")


# ------------------------------------------------------------------------------ raw answers

def test_raw_answers_drop_null_and_empty_values_but_keep_zero_and_false(zuper_env):  # noqa: F811
    payload = {"type": "success", "data": [{"a": None, "b": "", "c": [], "d": {}, "e": 0,
                                            "f": False, "g": {"h": None, "i": [None, "", 1]},
                                            "tags": [{}]}], "next": None}
    client.TRANSPORT = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    assert json.loads(tools.zuper_get("/jobs")) == {"type": "success",
                                                    "data": [{"e": 0, "f": False,
                                                              "g": {"i": [1]}}]}
    assert tools.compact({"data": []}) == {"data": []}             # an empty answer stays one


def test_the_new_tools_are_registered_first():
    names = [s[0] for s in tools.SPECS]
    assert names[:2] == ["job_dossier", "board_snapshot"]
    assert {"zuper_get", "zuper_connect", "quo_get"} <= set(names)
    assert "FIRST" in tools.SPECS[0][1] and "FIRST" in tools.SPECS[1][1]
