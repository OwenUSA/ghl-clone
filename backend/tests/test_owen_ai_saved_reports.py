"""The after-inspection report, saved and editable (2026-11-12, owen_ai/saved_reports.py,
report.py's Good / Better / Best part, infographics.py, companycam_photos.py).

Pinned, by behaviour:
  * NO PRICES: a "$" figure anywhere in the Good / Better / Best part is refused, and the page
    carries no price table;
  * the report keeps its OWN copies of its photos (and of the rest of their folders), so it
    still renders after job_photos' cleanup removes the originals;
  * every save is a new version; a save from an older version is refused (Conflict / 409) and
    changes nothing; restore is a new version with the old content; Owen's page edit survives
    an Owen AI edit made from the latest version;
  * an option picture is made on the first save only when the switch is on, never on an
    ordinary edit (an edit only marks it stale), and again only when a save names it; no
    customer name, address or job number reaches OpenAI;
  * the routes are the owner's only (404 for anyone else) and serve only the report's own
    files; preview saves nothing; uploads must be real JPEG/PNG pictures;
  * companycam_photos reads the job's linked projects (GET only), plain and marked copies,
    numbered from 101, and downloads nothing twice.
"""
import copy
import io
import json
import shutil
from pathlib import Path

import httpx
import httpx2
import pytest
from app import companycam
from app.ai import providers
from app.models import CompanyCamLink, OwenAiReportVersion, ZuperMapping
from app.owen_ai import companycam_photos, report, saved_reports, tools
from PIL import Image
from tests.test_owen_ai import call, people  # noqa: F401  (fixture)
from tests.test_owen_ai_proposal_pictures import USAGE, grid_png
from tests.test_owen_ai_report import JOB_UID, env, pic, spec  # noqa: F401  (fixture)
from tests.zuper_support import zuper_env  # noqa: F401  (fixture)


def area(x0=0.2, y0=0.3, x1=0.7, y1=0.6, kind="covered", label="Leak area"):
    return {"points": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "kind": kind, "label": label}


def option(e, name, headline="Targeted Leak Repair Above the Bathroom"):
    return {
        "name": name, "headline": headline,
        "picture": {"steps": [{"title": "Find the Leak", "text": "Inspect from the attic."},
                              {"title": "Remove Tiles by Hand", "text": "So none break."},
                              {"title": "New Underlayment", "text": "Seams sealed."},
                              {"title": "Tiles Back", "text": "Same day, cleanup."}],
                    "callouts": ["Tiles Reset & Secured", "Matching Tile as Needed"],
                    "section_title": "Leak Repair",
                    "layers": ["Barrel Tile", "New Underlayment", "Roof Deck (Plywood)"],
                    "top_label": "Leak Area Above the Bathroom",
                    "roof": "terracotta barrel tile roof", "notes": "no vent pipe"},
        "scope": ["<b>Find the leak:</b> attic inspection.", "Tiles back on the same day."],
        "days": "1 day",
        "coverage": [{"photo": pic(e, 21), "areas": [area()], "caption": "Main roof"}]}


def gbb(e, names=("GOOD", "BETTER", "BEST")):
    return {"options": [option(e, n) for n in names],
            "compare": [{"label": "Leaks fixed", "GOOD": "1", "BETTER": "All 3",
                         "BEST": "All 3, wider"}]}


def full(e, **over):
    return spec(e, gbb=gbb(e), galleries=[{"title": "Inside the house", "photos": [
        {"photo": pic(e, 5), "caption": "Bathroom ceiling"}]}], **over)


class OpenAI:
    """OpenAI's image EDIT endpoint at the HTTP boundary (multipart; httpx2 as the SDK uses)."""

    def __init__(self):
        self.requests: list[bytes] = []
        self.png = __import__("base64").b64encode(grid_png()).decode()

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request.content)
        assert request.url.path.endswith("/images/edits"), request.url
        return httpx2.Response(200, json={"created": 0, "data": [{"b64_json": self.png}],
                                          "usage": USAGE})


@pytest.fixture()
def world(db, people, env, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setenv("OWEN_AI_REPORT_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("OWEN_AI_OPENAI_API_KEY", "sk-test-report-0000")
    monkeypatch.delenv("OWEN_AI_REPORT_PICTURES_ENABLED", raising=False)
    ai = OpenAI()
    monkeypatch.setattr(providers, "HTTP_CLIENT_FACTORY",
                        lambda: httpx2.Client(transport=httpx2.MockTransport(ai.handler)))
    return {"db": db, "people": people, "env": env, "ai": ai, "tmp": tmp_path}


def make(world, s=None):
    return saved_reports.create(world["db"], s or full(world["env"]), author="test")


def options_part(html: str) -> str:
    """The Good / Better / Best section itself (page 1's call to action names it too)."""
    return html[html.index(">Your repair options</h2>"):html.rindex("Until it")]


def latest_spec(world, row):
    return saved_reports.load_spec(row.id, saved_reports.version(world["db"], row.id).spec)


# ------------------------------------------------------------------------------ no prices

@pytest.mark.parametrize("where", ["headline", "scope", "compare", "step", "callout", "label"])
def test_a_price_anywhere_in_the_options_is_refused(world, where):
    s = full(world["env"])
    o = s["gbb"]["options"][0]
    if where == "headline":
        o["headline"] = "Leak repair for $300"
    elif where == "scope":
        o["scope"].append("You pay <b>$1,825</b> today.")
    elif where == "compare":
        s["gbb"]["compare"][0]["BEST"] = "$3,000"
    elif where == "step":
        o["picture"]["steps"][0]["text"] = "Only 300 dollars."
    elif where == "callout":
        o["picture"]["callouts"][0] = "Save $ 400"
    else:
        o["coverage"][0]["areas"][0]["label"] = "$150 area"
    with pytest.raises(report.SpecError) as e:
        report.validate(s, world["env"]["photos"])
    assert any("no prices" in p for p in e.value.problems), e.value.problems


def test_option_order_and_area_points_are_checked(world):
    s = full(world["env"])
    s["gbb"]["options"].reverse()
    with pytest.raises(report.SpecError) as e:
        report.validate(s, world["env"]["photos"])
    assert "GOOD, BETTER, BEST" in " | ".join(e.value.problems)
    s = full(world["env"])
    s["gbb"]["options"][0]["coverage"][0]["areas"][0]["points"][0] = [1.4, 0.2]
    with pytest.raises(report.SpecError) as e:
        report.validate(s, world["env"]["photos"])
    assert "between 0 and 1" in " | ".join(e.value.problems)


def test_the_page_has_the_options_coverage_and_comparison_and_no_price_table(world):
    row, v = make(world)
    html = (saved_reports.report_dir(row.id) / "v1" / "report.html").read_text("utf-8")
    for text in ("Your repair options", "Targeted Leak Repair Above the Bathroom",
                 "Area covered", "Scope of work", "Compare the options", "Inside the house",
                 "Warranty:</b> 3 months", "Warranty:</b> 1 year", "Time on site"):
        assert text in html, text
    options = options_part(html)
    assert "You pay" not in html and "AHS member price" not in html and "$" not in options
    assert v.number == 1 and v.source == "ai" and row.current_version == 1


# ------------------------------------------------------------------------------ photos

def test_the_report_keeps_its_own_photos_and_survives_the_cleanup(world):
    row, v = make(world)
    stored = json.dumps(v.spec)
    assert '"lib/021-' in stored and str(world["env"]["photos"]) not in stored
    lib = saved_reports.library(row.id)
    assert len(lib) == 4                     # every picture of the folder, not only the used ones
    shutil.rmtree(world["env"]["photos"] / "728")          # job_photos' 14-day cleanup
    s = latest_spec(world, row)
    s["what_happened"] = "Changed after the cleanup."
    v2 = saved_reports.update(world["db"], row.id, s, 1, source="page", author="Owen")
    assert v2.number == 2
    html = (saved_reports.report_dir(row.id) / "v2" / "report.html").read_text("utf-8")
    assert "Changed after the cleanup." in html


def test_a_photo_from_the_library_can_be_added_later(world):
    row, _ = make(world)
    s = latest_spec(world, row)
    unused = next(x for x in saved_reports.library(row.id) if x.startswith("lib/002-"))
    s["galleries"][0]["photos"].append({"photo": {"path": unused}, "caption": "Added"})
    v2 = saved_reports.update(world["db"], row.id, s, 1, source="page")
    assert v2.spec["galleries"][0]["photos"][1]["photo"]["path"] == unused


# ------------------------------------------------------------------------------ versions

def test_a_save_from_an_older_version_is_refused_and_changes_nothing(world):
    db = world["db"]
    row, _ = make(world)
    s = latest_spec(world, row)
    s["weather"] = "Owen's edit"
    saved_reports.update(db, row.id, s, 1, source="page", author="Owen")
    stale = latest_spec(world, row)
    stale["weather"] = "Owen AI from an old copy"
    with pytest.raises(saved_reports.Conflict):
        saved_reports.update(db, row.id, stale, 1, source="ai")
    assert db.query(OwenAiReportVersion).filter_by(report_id=row.id).count() == 2
    # Owen AI reads again (get_inspection_report) and changes only what was asked:
    fresh = json.loads(tools_call("get_inspection_report", {"report": str(row.id)}))
    assert fresh["base_version_for_update"] == 2
    fresh["spec"]["badge"]["text"] = "Asked by Owen in the chat."
    out = tools_call("update_inspection_report", {"report": str(row.id), "base_version": 2,
                                                  "spec": fresh["spec"], "note": "badge"})
    assert out.startswith("Saved report %d, version 3" % row.id), out
    last = saved_reports.version(db, row.id)
    assert last.spec["weather"] == "Owen's edit" and last.source == "ai"


def test_restore_is_a_new_version_with_the_old_content(world):
    db = world["db"]
    row, _ = make(world)
    s = latest_spec(world, row)
    s["weather"] = "Second"
    saved_reports.update(db, row.id, s, 1, source="page")
    v3 = saved_reports.restore(db, row.id, 1, 2, author="Owen")
    assert v3.number == 3 and v3.source == "restore" and v3.spec["weather"] == "Sunny, dry"
    assert saved_reports.version(db, row.id, 2).spec["weather"] == "Second"


# ------------------------------------------------------------------------------ pictures

def test_switch_off_no_openai_call_and_the_options_say_so(world):
    row, v = make(world)
    assert world["ai"].requests == []
    assert {k: p["state"] for k, p in v.pictures.items()} == dict.fromkeys(report.TIERS,
                                                                          "missing")
    assert any("OWEN_AI_REPORT_PICTURES_ENABLED" in w for w in v.warnings)


def test_pictures_are_made_once_marked_stale_on_edit_and_remade_only_when_asked(
        world, monkeypatch):
    monkeypatch.setenv("OWEN_AI_REPORT_PICTURES_ENABLED", "true")
    db, ai = world["db"], world["ai"]
    row, v1 = make(world)
    assert len(ai.requests) == 3 and v1.cost_usd and v1.cost_usd > 0
    assert {p["state"] for p in v1.pictures.values()} == {"current"}
    for blob in ai.requests:                    # only the work reaches OpenAI
        text = blob.decode("utf-8", "ignore")
        assert "Find the Leak" in text
        for private in ("Brett", "BRETT", "Edelson", "EDELSON", "Via Luria", "728", JOB_UID):
            assert private not in text, private

    s = latest_spec(world, row)
    s["what_happened"] = "Text only."               # an edit that does not touch a picture
    v2 = saved_reports.update(db, row.id, s, 1, source="page")
    assert len(ai.requests) == 3 and {p["state"] for p in v2.pictures.values()} == {"current"}

    s = latest_spec(world, row)
    s["gbb"]["options"][1]["picture"]["steps"][0]["title"] = "Find All 3 Leaks"
    v3 = saved_reports.update(db, row.id, s, 2, source="page")
    assert len(ai.requests) == 3 and v3.pictures["BETTER"]["state"] == "stale"
    assert v3.pictures["GOOD"]["state"] == "current"

    v4 = saved_reports.update(db, row.id, latest_spec(world, row), 3, source="page",
                              regenerate=["BETTER"])
    assert len(ai.requests) == 4 and v4.pictures["BETTER"]["state"] == "current"


def test_owen_ai_reads_a_small_copy_of_each_picture_never_the_big_png(world, monkeypatch):
    """A 2.5 MB PNG in a Read result ended Owen AI's turn (2026-11-12, the SDK's 1 MB limit)."""
    monkeypatch.setenv("OWEN_AI_REPORT_PICTURES_ENABLED", "true")
    out = tools_call("make_inspection_report", full(world["env"]))
    lines = [ln for ln in out.splitlines() if " picture (current): " in ln]
    assert len(lines) == 3, out
    for ln in lines:
        path = Path(ln.split(": ", 1)[1])
        assert path.name.endswith(".preview.jpg") and path.is_file()
        assert path.stat().st_size < 400_000
        with Image.open(path) as im:
            assert max(im.size) <= 1280


def test_the_sdk_message_limit_is_raised():
    from app.owen_ai import agent, config
    assert config.SDK_MAX_BUFFER_BYTES >= 16 * 1024 * 1024
    assert "max_buffer_size=config.SDK_MAX_BUFFER_BYTES" in Path(agent.__file__).read_text("utf-8")


def test_an_uploaded_picture_replaces_the_ai_one(world):
    row, _ = make(world)
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), (10, 120, 60)).save(buf, "JPEG")
    name = saved_reports.save_upload(row.id, buf.getvalue())
    s = latest_spec(world, row)
    s["gbb"]["options"][0]["picture_upload"] = name
    v2 = saved_reports.update(world["db"], row.id, s, 1, source="page")
    assert v2.pictures["GOOD"] == {"file": name, "hash": v2.pictures["GOOD"]["hash"],
                                   "state": "uploaded"}
    with pytest.raises(ValueError):
        saved_reports.save_upload(row.id, b"<svg onload=alert(1)>")


# ------------------------------------------------------------------------------ the API

def test_the_page_can_read_edit_preview_restore_and_download(world):
    db, people_ = world["db"], world["people"]
    row, _ = make(world)
    r = call("GET", "/inspection-reports", "owner", people_)
    assert r.status_code == 200 and r.json()["reports"][0]["id"] == row.id
    d = call("GET", "/inspection-reports/%d" % row.id, "owner", people_).json()
    assert d["current_version"] == 1 and d["library"] and d["spec"]["weather"] == "Sunny, dry"
    s = copy.deepcopy(d["spec"])
    s["weather"] = "Preview only"
    pv = call("POST", "/inspection-reports/%d/preview" % row.id, "owner", people_,
              json={"spec": s})
    assert pv.status_code == 200 and "Preview only" in pv.json()["html"]
    assert db.query(OwenAiReportVersion).filter_by(report_id=row.id).count() == 1
    sv = call("POST", "/inspection-reports/%d/versions" % row.id, "owner", people_,
              json={"base_version": 1, "spec": s, "note": "weather"})
    assert sv.status_code == 200 and sv.json()["number"] == 2 and sv.json()["source"] == "page"
    again = call("POST", "/inspection-reports/%d/versions" % row.id, "owner", people_,
                 json={"base_version": 1, "spec": s})
    assert again.status_code == 409
    bad = copy.deepcopy(s)
    bad["gbb"]["options"][0]["headline"] = "Only $99"
    r422 = call("POST", "/inspection-reports/%d/versions" % row.id, "owner", people_,
                json={"base_version": 2, "spec": bad})
    assert r422.status_code == 422 and "no prices" in r422.json()["detail"]
    rs = call("POST", "/inspection-reports/%d/restore" % row.id, "owner", people_,
              json={"version": 1, "base_version": 2})
    assert rs.status_code == 200 and rs.json()["number"] == 3
    html = call("GET", "/inspection-reports/%d/files/v3/report.html" % row.id, "owner",
                people_)
    assert html.status_code == 200 and "Sunny, dry" in html.text
    assert html.headers["content-disposition"].startswith("inline")
    # "Download HTML" (2026-11-13): the same file, as a download.
    dl = call("GET", "/inspection-reports/%d/files/v3/report.html?download=1" % row.id, "owner",
              people_)
    assert dl.status_code == 200 and dl.content == html.content
    assert dl.headers["content-disposition"].startswith("attachment")
    assert ".html" in dl.headers["content-disposition"]
    photo = call("GET", "/inspection-reports/%d/files/%s" % (row.id, d["library"][0]), "owner",
                 people_)
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize("path", [
    "/files/lib/..%2F..%2Fx.jpg", "/files/v1/meta.json", "/files/etc/passwd.jpg",
    "/files/lib/nothing-here.jpg", "/files/v1/report.exe", "/files/pictures/%2E%2E%2Fv1.png"])
def test_only_the_reports_own_files_are_served(world, path):
    row, _ = make(world)
    assert call("GET", "/inspection-reports/%d%s" % (row.id, path), "owner",
                world["people"]).status_code == 404


@pytest.mark.parametrize("who", ["admin", "dispatcher"])
def test_anyone_but_the_owner_gets_404(world, who):
    row, _ = make(world)
    p = world["people"]
    for method, path, body in (
            ("GET", "/inspection-reports", None), ("GET", "/inspection-reports/%d" % row.id, None),
            ("GET", "/inspection-reports/%d/versions/1" % row.id, None),
            ("POST", "/inspection-reports/%d/versions" % row.id,
             {"base_version": 1, "spec": {}}),
            ("POST", "/inspection-reports/%d/restore" % row.id,
             {"version": 1, "base_version": 1}),
            ("POST", "/inspection-reports/%d/preview" % row.id, {"spec": {}}),
            ("GET", "/inspection-reports/%d/files/v1/report.html" % row.id, None)):
        assert call(method, path, who, p, json=body).status_code == 404, path
    assert world["db"].query(OwenAiReportVersion).filter_by(report_id=row.id).count() == 1


def test_an_upload_must_be_a_picture(world):
    row, _ = make(world)
    p = world["people"]
    bad = call("POST", "/inspection-reports/%d/uploads" % row.id, "owner", p,
               files={"file": ("x.jpg", b"not a picture", "image/jpeg")})
    assert bad.status_code == 422
    buf = io.BytesIO()
    Image.new("RGB", (50, 50)).save(buf, "PNG")
    ok = call("POST", "/inspection-reports/%d/uploads" % row.id, "owner", p,
              files={"file": ("x.png", buf.getvalue(), "image/png")})
    assert ok.status_code == 200 and ok.json()["name"].startswith("upload-")


# ------------------------------------------------------------------------------ the tools

def tools_call(name, args):
    by = {s[0]: s for s in tools.SPECS}
    return by[name][3](args)


def test_the_tools_are_registered_and_make_saves_version_one(world):
    names = {s[0] for s in tools.SPECS}
    assert {"make_inspection_report", "get_inspection_report", "update_inspection_report",
            "companycam_photos"} <= names
    out = tools_call("make_inspection_report", full(world["env"]))
    assert out.startswith("Saved report") and "/api/owen-ai/inspection-reports/" in out, out
    assert "nothing was sent" in out
    bad = tools_call("update_inspection_report", {"report": "999", "base_version": 1,
                                                  "spec": {}, "note": "x"})
    assert bad.startswith("ERROR")


# ------------------------------------------------------------------------------ CompanyCam

class FakeCC:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.photos = [
            {"id": 9001, "captured_at": 1789070154, "creator_name": "Oren",
             "description": "Bathroom ceiling",
             "uris": [{"type": "web", "uri": "https://img.companycam.com/9001.jpg"}]},
            {"id": 9002, "captured_at": 1789070607, "creator_name": "Oren",
             "uris": [{"type": "web", "uri": "https://img.companycam.com/9002.jpg"},
                      {"type": "web_annotation", "uri": "https://img.companycam.com/9002a.jpg"}]}]

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        assert req.method == "GET"
        if req.url.path.endswith("/projects/555/photos"):
            page = int(req.url.params.get("page", "1"))
            return httpx.Response(200, json=self.photos if page == 1 else [])
        if req.url.host == "img.companycam.com":
            buf = io.BytesIO()
            Image.new("RGB", (64, 48), (200, 100, 50)).save(buf, "JPEG")
            return httpx.Response(200, content=buf.getvalue(),
                                  headers={"content-type": "image/jpeg"})
        return httpx.Response(404, json={})


def test_companycam_photos_reads_the_linked_project_plain_and_marked(world, monkeypatch,
                                                                    opportunity):
    db = world["db"]
    monkeypatch.setenv("COMPANYCAM_API_TOKEN", "cc-test")
    companycam.clear_cache()
    fake = FakeCC()
    monkeypatch.setattr(companycam, "TRANSPORT", httpx.MockTransport(fake.handler))
    o = opportunity
    db.add(ZuperMapping(crm_type="opportunity", crm_id=o.id, zuper_type="job",
                        zuper_uid=JOB_UID))
    db.add(CompanyCamLink(opportunity_id=o.id, project_id="555", method="address",
                          project_name="Workiz 728 - Brett"))
    db.commit()
    monkeypatch.setattr(companycam_photos, "find_job", lambda r, job: {
        "job_uid": JOB_UID, "work_order_number": 728})
    out = json.loads(companycam_photos.companycam_photos("728"))
    assert out["count"] == 2 and out["marked"] == 1, out
    first, second = out["photos"]
    assert first["path"].endswith("101.jpg") and first["description"] == "Bathroom ceiling"
    assert second["path"].endswith("102.jpg") and second["marked_path"].endswith("102a.jpg")
    n = len(fake.requests)
    companycam.clear_cache()
    json.loads(companycam_photos.companycam_photos("728"))
    assert not [r for r in fake.requests[n:] if r.url.host == "img.companycam.com"]
    # A saved report can use them: '101a.jpg' numbers itself 101.
    s = full(world["env"])
    s["galleries"][0]["photos"][0]["photo"] = {"path": second["marked_path"]}
    row, v = make(world, s)
    assert "lib/102a-" in json.dumps(v.spec)


def test_companycam_off_is_a_sentence(world, monkeypatch):
    monkeypatch.delenv("COMPANYCAM_API_TOKEN", raising=False)
    assert companycam_photos.companycam_photos("728").startswith("ERROR: CompanyCam is not")


# ------------------------------------------------------------- prices and inspector (2026-11-12)

def test_prices_show_only_when_switched_on_and_come_from_the_proposal(world):
    s = full(world["env"])
    s["gbb"]["show_prices"] = True
    row, v = make(world, s)
    html = (saved_reports.report_dir(row.id) / "v1" / "report.html").read_text("utf-8")
    options = options_part(html)
    # The proposal's figures (tests' DTR-129: GOOD 600 / BETTER 1,400 / BEST 3,000).
    for text in ("You pay $600", "You pay $1,400", "You pay $3,000", "$2,500 retail",
                 "AHS member price $1,600", "50% to start ($300), 50% when done"):
        assert text in options, text
    assert ("<tr class=\"gpayrow\"><td>You pay</td><td>$600</td><td>$1,400</td>"
            "<td>$3,000</td>") in options
    # Off again: no figure at all.
    s2 = latest_spec(world, row)
    s2["gbb"]["show_prices"] = False
    saved_reports.update(world["db"], row.id, s2, 1, source="page")
    html2 = (saved_reports.report_dir(row.id) / "v2" / "report.html").read_text("utf-8")
    assert "$" not in options_part(html2)


def test_prices_on_without_a_proposal_warn_and_show_nothing(world):
    s = full(world["env"])
    s["gbb"]["show_prices"] = True
    f = report.fetch_facts(JOB_UID)
    f.options = []
    row, v = saved_reports.create(world["db"], s, author="test", facts=f)
    html = (saved_reports.report_dir(row.id) / "v1" / "report.html").read_text("utf-8")
    assert "You pay" not in html
    assert any("no proposal with options" in w for w in v.warnings), v.warnings


def test_the_inspector_can_be_named(world):
    row, _ = make(world)
    html = (saved_reports.report_dir(row.id) / "v1" / "report.html").read_text("utf-8")
    assert "Luis Tech" in html                       # read from Zuper (who moved the stage)
    s = latest_spec(world, row)
    s["inspector"] = "Antonio Brown"
    saved_reports.update(world["db"], row.id, s, 1, source="ai")
    html2 = (saved_reports.report_dir(row.id) / "v2" / "report.html").read_text("utf-8")
    assert "Antonio Brown" in html2 and "Luis Tech" not in html2


def test_every_report_ends_with_the_pictures_disclaimer(world):
    """Owner, 2026-11-12: the pictures are for reference only - at the end of every report."""
    for s in (full(world["env"]), spec(world["env"])):          # with and without the options
        row, _ = make(world, s)
        html = (saved_reports.report_dir(row.id) / "v1" / "report.html").read_text("utf-8")
        legal = html[html.index('<div class="legal">'):]
        assert "<b>About the pictures.</b> " + report.PICTURES_DISCLAIMER in legal
