"""Optional step pictures on a CREATED Owen AI proposal (2026-11-09, app/owen_ai/pictures.py).

Pinned, by behaviour:
  * refused (409, no OpenAI call, no Zuper request) unless the draft is created, both server
    switches are on and the registry allows it — and while another generation runs;
  * generate calls OpenAI once per option (mocked at the HTTP boundary) and makes NO Zuper
    request; an OpenAI failure keeps nothing;
  * attach makes exactly upload x3 + ONE PUT /estimate/{the row's own uid} whose options differ
    from the read-back only by option_image, then reads it back; a changed price is refused
    before a connection;
  * discard writes nothing to Zuper;
  * anyone but the owner gets 404;
  * the banner is 1536x512 with the scope's own captions, the tier's tags and the warranty;
  * the chat tool goes through the same functions.
"""
import base64
import copy
import io
import json
import re

import httpx
import httpx2
import pytest
from app.ai import providers
from app.models import OwenAiProposalDraft
from app.owen_ai import pictures, tools
from app.registry import catalog
from app.zuper import client
from app.zuper.client import ZuperError
from PIL import Image
from tests.test_owen_ai_proposals import FakeZ, call, people, propose  # noqa: F401 (fixture)
from tests.zuper_support import zuper_env  # noqa: F401  (fixture)

GOOD_SCOPE = ("<p><b>Scope of Work</b></p><ul><li>Price: $650 (regular $900)</li>"
              "<li>Remove the roof tiles around the plumbing vent pipe above the bathroom.</li>"
              "<li>Remove the worn, water-stained underlayment around the base of the pipe.</li>"
              "<li>Install a new pipe flashing (lead or flexible boot) and seal it to the "
              "pipe.</li>"
              "<li>Clean up the work area and send before-and-after photos.</li></ul>")
BETTER_SCOPE = ("<ul><li>Everything in GOOD.</li>"
                "<li>Open the roof from the vent down to the roof edge above the bathroom "
                "wall.</li>"
                "<li>Replace stained or soft plywood at the roof edge - only if damaged.</li>"
                "<li>Install peel &amp; stick waterproof underlayment over the whole area.</li>"
                "<li>Clean up the work area and send before-and-after photos.</li></ul>")
BEST_SCOPE = ("<ul><li>Everything in BETTER.</li>"
              "<li>Two layers of peel &amp; stick underlayment across the repair area.</li>"
              "<li>Rebuild the cracked mortar at the end of the hip cap.</li>"
              "<li>Re-seal the ridge vent.</li></ul>")
OPTIONS = [
    {"name": "GOOD", "title": "Targeted vent repair", "price": 650,
     "warranty": "30 days on the vent flashing", "scope_html": GOOD_SCOPE},
    {"name": "BETTER", "title": "Vent + roof edge", "price": 1450,
     "warranty": "Workmanship Warranty: 6 months", "scope_html": BETTER_SCOPE},
    {"name": "BEST", "title": "Complete repair", "price": 3000, "warranty": "1 year",
     "scope_html": BEST_SCOPE}]
USAGE = {"input_tokens": 450, "input_tokens_details": {"image_tokens": 0, "text_tokens": 450},
         "output_tokens": 5488, "output_tokens_details": {"image_tokens": 5488, "text_tokens": 0},
         "total_tokens": 5938}
ONE_COST = round((450 * 5 + 5488 * 30) / 1e6, 4)


def grid_png() -> bytes:
    """A 1536x1024 'AI grid': 2 rows x 4 coloured panels on white gutters."""
    img = Image.new("RGB", (1536, 1024), (255, 255, 255))
    colours = [(200, 60, 40), (40, 120, 200), (60, 160, 60), (200, 160, 40), (120, 60, 160),
               (40, 160, 160), (160, 80, 80), (90, 90, 90)]
    for i, c in enumerate(colours):
        r, k = divmod(i, 4)
        img.paste(Image.new("RGB", (370, 490), c), (10 + k * 382, 12 + r * 502))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class OpenAI:
    """OpenAI's image endpoint at the HTTP boundary (httpx2, as the SDK uses)."""

    def __init__(self):
        self.requests: list[dict] = []
        self.fail: int | None = None
        self.png = base64.b64encode(grid_png()).decode()

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append({"url": str(request.url), "body": json.loads(request.content)})
        if self.fail:
            return httpx2.Response(self.fail, json={"error": {"message": "Billing hard limit"}})
        return httpx2.Response(200, json={"created": 0, "data": [{"b64_json": self.png}],
                                          "usage": USAGE})


class PicZ(FakeZ):
    """FakeZ that keeps whole options on a PUT and answers /misc/upload."""

    def __init__(self, existing=None):
        super().__init__(existing)
        self.uploads: list[bytes] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/api")
        if req.method == "POST" and path == "/misc/upload":
            self.requests.append(req)
            self.uploads.append(req.content)
            return self.ok({"attachment_path": "https://s3.amazonaws.com/z/att-%d.jpg"
                                               % len(self.uploads)})
        if req.method == "PUT" and "option_image" in (req.content or b"").decode():
            self.requests.append(req)
            uid = path.rsplit("/", 1)[1]
            body = json.loads(req.content)["estimate"]
            self.estimates[uid] = {**self.estimates[uid],
                                   "proposal_options": body["proposal_options"]}
            return self.ok({})
        return super().__call__(req)


@pytest.fixture()
def world(db, people, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setenv("OWEN_AI_PROPOSAL_PICTURES_ENABLED", "true")
    monkeypatch.setenv("OWEN_AI_OPENAI_API_KEY", "sk-test-pictures-0000")
    monkeypatch.setenv("OWEN_AI_PICTURE_DIR", str(tmp_path / "pictures"))
    ai = OpenAI()
    monkeypatch.setattr(providers, "HTTP_CLIENT_FACTORY",
                        lambda: httpx2.Client(transport=httpx2.MockTransport(ai.handler)))
    z = PicZ()
    client.TRANSPORT = httpx.MockTransport(z)
    return {"ai": ai, "z": z, "db": db, "people": people}


def created(world) -> OwenAiProposalDraft:
    db, who, z = world["db"], world["people"], world["z"]
    row = propose(db, who, z, options=copy.deepcopy(OPTIONS))
    r = call("POST", "/proposals/%d/approve" % row.id, "owner", who)
    assert r.json()["status"] == "created", r.json()
    db.refresh(row)
    # Zuper's read-back carries more than we sent: each option keeps its own fields.
    rec = z.estimates[row.estimate_uid]
    rec["proposal_title"] = row.title
    for i, o in enumerate(rec["proposal_options"]):
        o.update({"option_uid": "ou-%d" % i, "deposit": o["total"] / 2, "is_recommended": False,
                  "option_image": ""})
    return row


def gen(world, row, who="owner"):
    return call("POST", "/proposals/%d/pictures" % row.id, who, world["people"])


# ------------------------------------------------------------------------------- refusals

@pytest.mark.parametrize("how", ["pending", "pictures_flag", "proposals_flag", "registry"])
def test_refused_unless_created_and_both_switches_on(world, monkeypatch, how):
    z, ai = world["z"], world["ai"]
    if how == "pending":
        row = propose(world["db"], world["people"], z, options=copy.deepcopy(OPTIONS))
    else:
        row = created(world)
    if how == "pictures_flag":
        monkeypatch.setenv("OWEN_AI_PROPOSAL_PICTURES_ENABLED", "false")
    elif how == "proposals_flag":
        monkeypatch.setenv("OWEN_AI_PROPOSALS_ENABLED", "false")
    elif how == "registry":
        from app.registry import service
        monkeypatch.setattr(service, "level", lambda db, key, action: catalog.NEVER)
    before = len(z.requests)
    for path in ("/proposals/%d/pictures", "/proposals/%d/pictures/attach"):
        r = call("POST", path % row.id, "owner", world["people"])
        assert r.status_code == 409, r.text
        expect = {"pending": "pending", "pictures_flag": "OWEN_AI_PROPOSAL_PICTURES_ENABLED",
                  "proposals_flag": "OWEN_AI_PROPOSALS_ENABLED", "registry": "Never"}[how]
        assert expect in r.json()["detail"]
    assert ai.requests == [] and len(z.requests) == before
    world["db"].refresh(row)
    assert row.pictures_status is None and row.pictures_cost_usd is None


def test_one_generation_at_a_time(world):
    row = created(world)
    db = world["db"]
    row.pictures_status, row.pictures_at = "generating", pictures._now()
    db.commit()
    r = gen(world, row)
    assert r.status_code == 409 and "being made" in r.json()["detail"]
    assert world["ai"].requests == []


def test_no_key_is_a_sentence_and_no_call(world, monkeypatch):
    row = created(world)
    monkeypatch.delenv("OWEN_AI_OPENAI_API_KEY")
    r = gen(world, row)
    assert r.status_code == 409 and "OWEN_AI_OPENAI_API_KEY" in r.json()["detail"]
    assert world["ai"].requests == []


# ------------------------------------------------------------------------------- generate

def test_generation_calls_openai_per_option_and_makes_no_zuper_request(world):
    row = created(world)
    z, ai = world["z"], world["ai"]
    before = len(z.requests)
    r = gen(world, row)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["pictures_status"] == "ready" and out["pictures_error"] is None
    assert len(z.requests) == before                       # Zuper untouched
    assert len(ai.requests) == 3
    for q in ai.requests:
        assert q["url"] == "https://api.openai.com/v1/images/generations"
        b = q["body"]
        assert (b["model"], b["quality"], b["size"], b["output_format"], b["n"]) == (
            "gpt-image-2", "high", "1536x1024", "png", 1)
        assert "ABSOLUTELY NO TEXT" in b["prompt"] and "barrel" in b["prompt"]
    prompts = " ".join(q["body"]["prompt"] for q in ai.requests)
    assert "Rebuild the cracked mortar" in prompts and "$650" not in prompts
    assert out["pictures_cost_usd"] == pytest.approx(3 * ONE_COST)
    world["db"].refresh(row)
    assert [c["cost_usd"] for c in row.pictures["calls"]] == [ONE_COST] * 3
    assert [p["name"] for p in out["pictures"]] == ["GOOD", "BETTER", "BEST"]
    for p in out["pictures"]:
        assert re.fullmatch(r"/api/owen-ai/proposals/%d/pictures/[a-z]+-[0-9a-f]{8}\.jpg"
                            % row.id, p["url"])
        got = call("GET", p["url"].removeprefix("/api/owen-ai"), "owner", world["people"])
        assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
        assert Image.open(io.BytesIO(got.content)).size == (1536, 512)
    # A name that is not this row's current banner is 404, as is a path trick.
    for bad in ("good-00000000.jpg", "..%2F..%2Fsecret.jpg", "good.png"):
        assert call("GET", "/proposals/%d/pictures/%s" % (row.id, bad), "owner",
                    world["people"]).status_code == 404


def test_an_openai_failure_keeps_nothing_and_attach_is_refused(world):
    row = created(world)
    world["ai"].fail = 400
    out = gen(world, row).json()
    assert out["pictures_status"] == "failed" and "HTTP 400" in out["pictures_error"]
    assert "Billing hard limit" in out["pictures_error"] and out["pictures"] == []
    assert len(world["ai"].requests) == 3                  # one each, never retried
    before = len(world["z"].requests)
    r = call("POST", "/proposals/%d/pictures/attach" % row.id, "owner", world["people"])
    assert r.status_code == 409 and len(world["z"].requests) == before


# ------------------------------------------------------------------------------- attach

def test_attach_is_three_uploads_and_one_put_with_only_option_image_then_read_back(world):
    row = created(world)
    z = world["z"]
    assert gen(world, row).status_code == 200
    before_opts = copy.deepcopy(z.estimates[row.estimate_uid]["proposal_options"])
    start = len(z.requests)
    r = call("POST", "/proposals/%d/pictures/attach" % row.id, "owner", world["people"])
    assert r.status_code == 200, r.text
    assert r.json()["pictures_status"] == "attached", r.json()
    mine = z.requests[start:]
    writes = [(q.method, q.url.path) for q in mine if q.method != "GET"]
    assert writes == [("POST", "/api/misc/upload")] * 3 + [("PUT", "/api/estimate/"
                                                           + row.estimate_uid)]
    assert mine[0].method == "GET" and mine[-1].method == "GET"
    assert mine[-1].url.path == "/api/estimate/" + row.estimate_uid
    for q in mine:
        assert q.url.query == b""
    for up in z.uploads:
        assert b'name="attachment_type"' in up and b"ATTACHMENT" in up
        assert b"Content-Type: image/jpeg" in up and b"\xff\xd8\xff" in up
    put = json.loads(next(q for q in mine if q.method == "PUT").content)
    est = put["estimate"]
    assert set(est) == {"estimate_uid", "is_proposal", "proposal_title", "proposal_options"}
    assert est["estimate_uid"] == row.estimate_uid and est["is_proposal"] is True
    for b, a in zip(before_opts, est["proposal_options"], strict=True):
        assert {k: v for k, v in a.items() if k != "option_image"} == \
            {k: v for k, v in b.items() if k != "option_image"}
        assert a["option_image"].startswith("https://s3.amazonaws.com/z/att-")
    def keys(v):
        if isinstance(v, dict):
            for k, x in v.items():
                yield k
                yield from keys(x)
        elif isinstance(v, list):
            for x in v:
                yield from keys(x)
    for k in keys(put):
        assert not re.search(r"status|send|mail|notif", k, re.IGNORECASE), k
    again = call("POST", "/proposals/%d/pictures/attach" % row.id, "owner", world["people"])
    assert again.status_code == 409 and len(z.requests) == len(mine) + start


def test_a_read_back_with_a_changed_total_is_failed_not_attached(world):
    row = created(world)
    z = world["z"]
    gen(world, row)
    real = z.__class__.__call__

    def drift(req):
        resp = real(z, req)
        if req.method == "PUT":
            z.estimates[row.estimate_uid]["proposal_options"][0]["total"] = 1.0
        return resp
    client.TRANSPORT = httpx.MockTransport(drift)
    out = call("POST", "/proposals/%d/pictures/attach" % row.id, "owner", world["people"]).json()
    assert out["pictures_status"] == "failed" and "total" in out["pictures_error"]


def _scope_with_baseline(z, uid):
    rec = {"estimate_uid": uid, "estimate_status": "DRAFT", "proposal_title": "T",
           "is_proposal": True,
           "proposal_options": [{"option_name": "GOOD", "total": 650, "option_image": "",
                                 "line_items": [{"total": 650, "description": "d"}]}]}
    z.estimates[uid] = rec
    return rec


def _put(uid, opts, **extra):
    return {"estimate": {"estimate_uid": uid, "is_proposal": True, "proposal_title": "T",
                         "proposal_options": opts, **extra}}


@pytest.mark.parametrize("change", ["price", "line", "extra_key", "status", "other_uid",
                                    "no_read", "not_https"])
def test_the_scope_refuses_anything_but_option_image_before_a_connection(world, change):
    z = world["z"]
    rec = _scope_with_baseline(z, "e1")
    _scope_with_baseline(z, "e2")
    with client.owen_ai_proposal_pictures("e1"):
        if change != "no_read":
            client.request("GET", "/estimate/e1")
        opts = copy.deepcopy(rec["proposal_options"])
        opts[0]["option_image"] = "https://s3.amazonaws.com/z/a.jpg"
        uid, extra = "e1", {}
        if change == "price":
            opts[0]["total"] = 1
        elif change == "line":
            opts[0]["line_items"][0]["total"] = 1
        elif change == "extra_key":
            opts[0]["deposit"] = 100
        elif change == "status":
            extra = {"estimate_status": "DRAFT"}
        elif change == "other_uid":
            uid = "e2"
        elif change == "not_https":
            opts[0]["option_image"] = "javascript:alert(1)"
        n = len(z.requests)
        with pytest.raises(ZuperError) as exc:
            client.request("PUT", "/estimate/" + uid, body=_put(uid, opts, **extra))
        assert exc.value.kind == "refused"
        assert len(z.requests) == n
        if change == "price":                               # the same body, priced right, goes
            opts[0]["total"] = 650
            client.request("PUT", "/estimate/e1", body=_put("e1", opts))
            assert [q.method for q in z.requests[n:]] == ["PUT"]


def test_the_scope_refuses_other_writes_and_extra_uploads(world, monkeypatch):
    z = world["z"]
    jpg = b"\xff\xd8\xff" + b"0" * 50
    up = {"files": {"attachment": ("a.jpg", jpg, "image/jpeg")},
          "form": {"attachment_type": "ATTACHMENT"}}
    with client.owen_ai_proposal_pictures("e1"):
        for _ in range(3):
            client.request("POST", "/misc/upload", **up)
        for method, path, kw in (
                ("POST", "/misc/upload", up),                                     # a 4th
                ("POST", "/estimate/e1/send", {"body": {"template_uid": "t"}}),
                ("POST", "/estimate", {"body": {"estimate": {"is_proposal": True}}}),
                ("DELETE", "/estimate/e1", {}),
                ("POST", "/misc/upload", {
                    "files": {"attachment": ("a.png", b"\x89PNG", "image/png")},
                    "form": {"attachment_type": "ATTACHMENT"}})):
            with pytest.raises(ZuperError):
                client.request(method, path, **kw)
    assert [q.method for q in z.requests] == ["POST"] * 3
    with pytest.raises(ZuperError, match="multipart"):           # outside the scope: never
        client.request("POST", "/misc/upload", **up)
    monkeypatch.setenv("OWEN_AI_PROPOSAL_PICTURES_ENABLED", "false")
    with client.owen_ai_proposal_pictures("e1"), pytest.raises(
            ZuperError, match="OWEN_AI_PROPOSAL_PICTURES_ENABLED"):
        client.request("POST", "/misc/upload", **up)
    assert len(z.requests) == 3


# ------------------------------------------------------------------------------- discard

def test_discard_writes_nothing_to_zuper(world):
    row = created(world)
    out = gen(world, row).json()
    z = world["z"]
    before = len(z.requests)
    r = call("POST", "/proposals/%d/pictures/discard" % row.id, "owner", world["people"])
    assert r.status_code == 200 and r.json()["pictures_status"] == "discarded"
    assert r.json()["pictures"] == [] and len(z.requests) == before
    assert not pictures.row_dir(row.id).exists()
    url = out["pictures"][0]["url"].removeprefix("/api/owen-ai")
    assert call("GET", url, "owner", world["people"]).status_code == 404
    assert call("POST", "/proposals/%d/pictures/attach" % row.id, "owner",
                world["people"]).status_code == 409
    assert len(z.requests) == before


# ------------------------------------------------------------------------------- access

@pytest.mark.parametrize("who", ["admin", "dispatcher"])
def test_anyone_but_the_owner_gets_404(world, who):
    row = created(world)
    out = gen(world, row).json()
    for method, path in (("POST", "/proposals/%d/pictures"),
                         ("POST", "/proposals/%d/pictures/attach"),
                         ("POST", "/proposals/%d/pictures/discard")):
        assert call(method, path % row.id, who, world["people"]).status_code == 404
    url = out["pictures"][0]["url"].removeprefix("/api/owen-ai")
    assert call("GET", url, who, world["people"]).status_code == 404
    assert len(world["ai"].requests) == 3
    world["db"].refresh(row)
    assert row.pictures_status == "ready"


# ------------------------------------------------------------------------------- the banner

def _row(options):
    from types import SimpleNamespace
    return SimpleNamespace(id=1, template_key="tile", title="t", options=options)


def test_the_plan_comes_from_the_scope_only_new_steps_per_tier():
    stored = [{"name": o["name"], "option_description": o["title"], "warranty": o["warranty"],
               "scope_html": o["scope_html"]} for o in OPTIONS]
    good, better, best = pictures.plan(_row(stored))
    assert [s["caption"] for s in good["steps"]] == [
        "Remove roof tiles", "Remove worn water-stained underlayment",
        "Install new pipe flashing", "Clean up work area"]
    # BETTER: only what GOOD lacks (the repeated clean-up bullet is not a new step).
    assert [s["caption"] for s in better["steps"]] == [
        "Open roof from vent", "Replace stained or soft plywood",
        "Install peel & stick waterproof underlayment"]
    assert better["first"] == {"label": "1-4", "caption": "Everything in GOOD"}
    assert best["first"] == {"label": "1-7", "caption": "Everything in GOOD + BETTER"}
    assert best["start"] == 8 and best["tag"][0] == "PREMIUM" and better["tag"][0] == "UPGRADE"
    assert good["tag"] is None and good["title"] == "GOOD - Targeted vent repair"
    assert [p["warranty"] for p in (good, better, best)] == [
        "30-day warranty", "6-month warranty", "1-year warranty"]
    for p in (good, better, best):
        assert all(len(s["caption"].replace(" & ", "&").split()) <= 5 for s in p["steps"])


def test_the_banner_has_the_right_tiles_tags_and_text_from_the_scope():
    stored = [{"name": o["name"], "option_description": o["title"], "warranty": o["warranty"],
               "scope_html": o["scope_html"]} for o in OPTIONS]
    plans = pictures.plan(_row(stored))
    tile = Image.new("RGB", (300, 300), (180, 90, 60))
    seen = {}
    for p in plans:
        img, texts = pictures.render_banner(p, [tile] * len(p["steps"]), tile, tile)
        assert img.size == (1536, 512)
        seen[p["name"]] = texts
        roles = [t["role"] for t in texts]
        joined = " ".join(t["text"] for t in texts if t["role"] == "caption")
        for s in p["steps"]:
            assert all(w in joined for w in s["caption"].split()), s["caption"]
        assert roles.count("final") == 1 and roles.count("disclaimer") == 1
        assert [t["text"] for t in texts if t["role"] == "warranty"] == [p["warranty"]]
        numbers = [t["text"] for t in texts if t["role"] == "number"]
        assert numbers[-len(p["steps"]):] == [str(p["start"] + i) for i in range(len(p["steps"]))]
        # Pixels: the title bar is navy, the bottom bar is navy.
        assert img.getpixel((700, 5)) == pictures.NAVY and img.getpixel((700, 505)) == pictures.NAVY
    tags = {n: [t["text"] for t in seen[n] if t["role"] == "tag"] for n in seen}
    assert tags == {"GOOD": [], "BETTER": ["UPGRADE"] * 3, "BEST": ["PREMIUM"] * 3}
    assert next(t["text"] for t in seen["BETTER"] if t["role"] == "number") == "1-4"
    assert "Everything in GOOD" in " ".join(t["text"] for t in seen["BETTER"]
                                            if t["role"] == "everything")
    assert not any(t["role"] == "everything" for t in seen["GOOD"])


def test_split_grid_finds_the_white_gutters():
    src = Image.open(io.BytesIO(grid_png())).convert("RGB")
    cells = pictures.split_grid(src, 2, 4, 7)
    assert len(cells) == 7
    for c in cells:                                # every cell is one solid panel colour
        lo, hi = c.convert("L").getextrema()
        assert hi - lo <= 2 and hi < 230, (lo, hi)


# ------------------------------------------------------------------------------- the chat tool

def test_the_tool_generates_by_dtr_number_and_attaches_through_the_same_checks(world):
    assert "proposal_step_pictures" in [s[0] for s in tools.SPECS]
    pending = propose(world["db"], world["people"], world["z"], options=copy.deepcopy(OPTIONS))
    assert pictures.tool_pictures({"proposal": str(pending.id), "action": "generate"}).startswith(
        "ERROR: Step pictures are only made for a proposal that is approved")
    assert world["ai"].requests == []
    row = created(world)
    out = pictures.tool_pictures({"proposal": row.estimate_number, "action": "generate"})
    assert not out.startswith("ERROR"), out
    links = re.findall(r"\[(GOOD|BETTER|BEST) steps\]\((/api/owen-ai/proposals/%d/pictures/"
                       r"[a-z]+-[0-9a-f]{8}\.jpg)\)" % row.id, out)
    assert [n for n, _ in links] == ["GOOD", "BETTER", "BEST"] and "NOT on the proposal" in out
    z = world["z"]
    start = len(z.requests)
    done = pictures.tool_pictures({"proposal": "DTR-%s" % row.estimate_number[4:],
                                   "action": "attach"})
    assert "now on %s" % row.estimate_number in done and "nothing was sent" in done
    assert [q.method for q in z.requests[start:] if q.method != "GET"] == ["POST"] * 3 + ["PUT"]
    assert pictures.tool_pictures({"proposal": row.id, "action": "attach"}).startswith("ERROR")
    assert pictures.tool_pictures({"proposal": "x", "action": "send"}).startswith("ERROR")
