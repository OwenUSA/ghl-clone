"""Owen AI's make_inspection_report (2026-11-07, owen_ai/report.py).

Pinned, by behaviour:
  * the spec is validated strictly: unknown keys, photo paths outside the job_photos folder,
    unknown mark shapes / colors, boxes outside 0-1, long labels, tags other than <b>/<i> are
    refused with a list of what to fix;
  * every text is escaped; only balanced <b> / <i> survive;
  * the FACTS (customer, address, date, inspector, prices, warranty, deposit) come from the
    Zuper job and the proposal — the spec cannot carry them, and an unparseable figure is shown
    as a dash, never invented;
  * the page embeds the logo, says DRAFT unless final, and the PDF is skipped cleanly without
    a Chromium (and printed from the same HTML with one);
  * the files are served to the owner only, as attachments named for the customer; another
    admin, a dispatcher, other file names and path tricks get 404.
"""
import io
import json
import os
import re
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from app.owen_ai import config, report, tools
from app.zuper import client
from PIL import Image
from tests.test_owen_ai import call, people  # noqa: F401  (fixture)
from tests.zuper_support import zuper_env  # noqa: F401  (fixture)

JOB_UID = "28ac4bf2-2967-400e-97a0-34d3916226f8"
EST_UID = "a382c925-1fbf-4464-83a9-70115324105c"
MINUS = report.MINUS
LUIS = {"user_uid": "u-2", "first_name": "Luis", "last_name": "Tech"}


def desc(retail, member, credit, pays, warranty, deposit):
    return (f"**Scope of Work**\n\n(Retail Value: ${retail:,} | Special AHS Member Price: "
            f"${member:,} | AHS Authorized Credit: -${credit:,} | Customer Pays Out of Pocket: "
            f"${pays:,} | Total Savings: $1)\n\n*   Targeted Leak Inspection: look at the leak."
            "\n    \n*   Tile Removal & Substrate Inspection: lift tiles.\n\nWorkmanship "
            f"Warranty: {warranty} This warranty covers workmanship.\n\nPayment Terms: 50% "
            f"Upfront (${deposit:,}.00) - due upon acceptance.")


def option(name, total, retail, member, warranty, recommended=False, text=None):
    return {"option_name": name, "option_description": "Repair package " + name,
            "total": total, "deposit": total / 2, "is_recommended": recommended,
            "is_accepted": False, "is_hidden": False,
            "line_items": [{"name": name, "unit_price": total, "total": total,
                            "markdown_description": text if text is not None else
                            desc(retail, member, 1000, total, warranty, total // 2)}]}


def job():
    return {"job_uid": JOB_UID, "work_order_number": 728,
            "customer": {"customer_first_name": "BRETT ", "customer_last_name": "EDELSON"},
            "customer_address": {"street": "7236 Via Luria, EE. UU.", "city": "Greenacres",
                                 "state": "Florida", "zip_code": "33467",
                                 "country": "United States"},
            "job_status": [
                {"status_name": "Scheduled", "created_at": "2026-10-05T17:26:44.000Z"},
                {"status_name": "Inspection In Progress", "created_at": "2026-10-13T13:58:04Z",
                 "done_by": LUIS}],
            "scheduled_start_time": "2026-11-04T17:00:00.000Z"}


def estimate(options=None):
    return {"estimate_uid": EST_UID, "prefix": "DTR-", "estimate_no": 129,
            "created_at": "2026-10-21T21:44:45.172Z", "estimate_status": "APPROVED",
            "proposal_options": options if options is not None else [
                option("GOOD", 600, 2500, 1600, "3 Months"),
                option("BETTER", 1400, 4500, 2400, "6 Months"),
                option("BEST", 3000, 8500, 4000, "1 Year", recommended=True)]}


class Router:
    def __init__(self, est=None):
        self.est = est or estimate()
        self.requests: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path = re.sub(r"^/api", "", req.url.path)
        ok = lambda data: httpx.Response(200, json={"type": "success", "data": data})  # noqa: E731
        if path == "/jobs/" + JOB_UID:
            return ok(job())
        if path == "/estimate":
            return ok([{k: self.est[k] for k in ("estimate_uid", "prefix", "estimate_no",
                                                  "created_at", "estimate_status")}])
        if path == "/estimate/" + EST_UID:
            return ok(self.est)
        return httpx.Response(404, json={"message": "no route " + path})


@pytest.fixture()
def env(zuper_env, monkeypatch, tmp_path):  # noqa: F811
    photos = tmp_path / "photos"
    (photos / "728").mkdir(parents=True)
    for n, size in ((21, (1024, 768)), (5, (1024, 768)), (7, (768, 1024)), (2, (1024, 768))):
        Image.new("RGB", size, (90, 120, 160)).save(photos / "728" / ("%03d.jpg" % n), "JPEG")
    (tmp_path / "outside.jpg").write_bytes((photos / "728" / "021.jpg").read_bytes())
    monkeypatch.setenv("OWEN_AI_PHOTO_DIR", str(photos))
    monkeypatch.setenv("OWEN_AI_REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("OWEN_AI_CHROMIUM", str(tmp_path / "no-such-browser"))
    for k in ("PHONE", "LICENSE", "CITY", "INSPECTOR_DEFAULT"):
        monkeypatch.delenv("OWEN_AI_REPORT_" + k, raising=False)
    router = Router()
    monkeypatch.setattr(client, "TRANSPORT", httpx.MockTransport(lambda r: router(r)))
    return {"photos": photos, "tmp": tmp_path, "router": router}


def pic(env, n, marks=()):
    return {"path": str(env["photos"] / "728" / ("%03d.jpg" % n)), "marks": list(marks)}


def spec(env, **over):
    s = {
        "job_uid": JOB_UID,
        "badge": {"severity": "now", "text": "Water has gotten into your garage ceiling."},
        "what_happened": "Rain gets in at the <b>wall joint</b>.",
        "hero": {"cause_photo": pic(env, 21), "inside_photo": pic(env, 5, [
            {"shape": "ellipse", "box": [0.29, 0.38, 0.97, 0.64], "label": "Stain"}]),
                 "captions": ["Most likely main leak (Photo 21).", "Inside (Photo 5)."]},
        "location": {"photo": pic(env, 2), "where_text": "On the garage roof."},
        "findings": [{"severity": "now", "title": "Where the roof meets the wall",
                      "photos": [pic(env, 21), pic(env, 7, [
                          {"shape": "line", "box": [0.05, 0.45, 0.7, 0.34], "color": "orange",
                           "label": "Gap at the wall"}])],
                      "refs": "20, 21", "saw": "Open flashing.", "means": "Water gets in.",
                      "fix": "Reseal the wall flashing. In all three options."}],
        "inside": {"items": [{"state": "yes", "text": "Stain by the hatch (Photo 5)"},
                             {"state": "not_checked", "text": "Attic"}]},
        "not_done": "attic, water test", "weather": "Sunny, dry",
        "inspected_how": "inside the garage · walked the roof · 24 photos",
    }
    s.update(over)
    return s


def problems(env, s) -> list[str]:
    with pytest.raises(report.SpecError) as e:
        report.validate(s, env["photos"])
    return e.value.problems


# ------------------------------------------------------------------------------ the spec

def test_a_good_spec_validates_and_num_defaults_from_the_file_name(env):
    s = report.validate(spec(env), env["photos"])
    assert s.hero.cause_photo.num == 21 and s.findings[0].photos[1].num == 7


@pytest.mark.parametrize("path", ["../outside.jpg", "OUTSIDE", "728/999.jpg", "728/../../x.jpg",
                                  "C:/Windows/win.ini", "/etc/passwd"])
def test_photo_paths_must_be_job_photos_files(env, path):
    s = spec(env)
    s["hero"]["cause_photo"]["path"] = (str(env["tmp"] / "outside.jpg") if path == "OUTSIDE"
                                        else path)
    assert any(p.startswith("hero.cause_photo.path") for p in problems(env, s))


@pytest.mark.parametrize("mark,where", [
    ({"shape": "ellipse", "box": [0.1, 0.1, 1.2, 0.5]}, "between 0 and 1"),
    ({"shape": "ellipse", "box": [-0.1, 0.1, 0.5, 0.5]}, "between 0 and 1"),
    ({"shape": "rect", "box": [0.6, 0.1, 0.5, 0.5]}, "x0 < x1"),
    ({"shape": "ellipse", "box": [0.1, 0.2, 0.5]}, "at least 4"),
    ({"shape": "circle", "box": [0.1, 0.1, 0.5, 0.5]}, "ellipse"),
    ({"shape": "ellipse", "box": [0.1, 0.1, 0.5, 0.5], "color": "pink"}, "red"),
    ({"shape": "ellipse", "box": [0.1, 0.1, 0.5, 0.5], "label": "x" * 25}, "at most 24"),
    ({"shape": "ellipse", "box": [10, 10, 500, 400]}, "between 0 and 1"),     # pixels, not 0-1
])
def test_bad_marks_are_refused(env, mark, where):
    s = spec(env)
    s["findings"][0]["photos"][0]["marks"] = [mark]
    got = problems(env, s)
    assert any(p.startswith("findings.0.photos.0.marks.0") and where in p for p in got), got


def test_a_line_may_run_in_any_direction(env):
    s = spec(env)
    s["findings"][0]["photos"][0]["marks"] = [{"shape": "line", "box": [0.9, 0.9, 0.1, 0.1]}]
    report.validate(s, env["photos"])


def test_unknown_keys_and_facts_in_the_spec_are_refused(env):
    s = spec(env, customer="Somebody Else", prices={"GOOD": 1})
    s["findings"][0]["photos"][0]["zoom"] = 2
    s["badge"]["color"] = "red"
    got = problems(env, s)
    for loc in ("customer", "prices", "findings.0.photos.0.zoom", "badge.color"):
        assert any(p.startswith(loc + ":") and "Extra inputs" in p for p in got), (loc, got)


def test_counts_and_required_parts(env):
    assert any(p.startswith("findings") for p in problems(env, spec(env, findings=[])))
    many = spec(env)
    many["findings"] = many["findings"] * 9
    assert any(p.startswith("findings") for p in problems(env, many))
    two = spec(env)
    two["findings"][0]["photos"] = [pic(env, 21)] * 3
    assert any(p.startswith("findings.0.photos") for p in problems(env, two))
    nobadge = spec(env)
    del nobadge["badge"]
    assert "badge: Field required" in problems(env, nobadge)
    assert any("one caption per hero photo" in p for p in problems(env, spec(env, hero={
        "cause_photo": pic(env, 21), "captions": ["a", "b"]})))
    assert problems(env, ["not", "a", "dict"]) == ["spec: must be a JSON object"]


@pytest.mark.parametrize("text", ["<script>alert(1)</script>", "<b>open", "a </b> b",
                                  "<b><i>x</b></i>", '<a href="x">x</a>', "<img src=x>",
                                  "<!-- x -->", "<B onclick=x>y</B>"])
def test_only_balanced_b_and_i_survive_validation(env, text):
    got = problems(env, spec(env, what_happened=text))
    assert any(p.startswith("what_happened:") for p in got), got


def test_plain_fields_take_no_html(env):
    s = spec(env)
    s["findings"][0]["title"] = "<b>Wall</b>"
    assert any(p.startswith("findings.0.title") for p in problems(env, s))


# ------------------------------------------------------------------------------ facts

def test_facts_come_from_zuper_and_the_proposal(env):
    f = report.fetch_facts(JOB_UID)
    assert f.customer == "Brett Edelson" and f.last_name == "Edelson"
    assert f.address == "7236 Via Luria, Greenacres, FL 33467"
    assert f.job_number == "728" and f.proposal == "DTR-129"
    assert f.date.strftime("%Y-%m-%d") == "2026-10-13"          # the inspection, not the visit
    assert f.inspector == "Luis Tech"
    good, better, best = f.options
    assert (good.total, good.deposit, good.retail, good.member, good.credit) == (
        600, 300, 2500, 1600, -1000)
    assert (better.warranty, best.warranty, best.recommended) == ("6 months", "1 year", True)
    assert good.steps == ["Targeted Leak Inspection", "Tile Removal & Substrate Inspection"]
    assert [r.method for r in env["router"].requests] == ["GET"] * len(env["router"].requests)
    assert sum("OWEN_AI_REPORT_" in w for w in f.warnings) == 3   # phone, license, city


@pytest.mark.parametrize("street,city,state,zp,want", [
    ("7236 Via Luria, EE. UU.", "Greenacres", "Florida", "33467",
     "7236 Via Luria, Greenacres, FL 33467"),
    ("123 MAIN ST, Bradenton, FL 34205, USA", "Bradenton", "FL", "34205",
     "123 Main St, Bradenton, FL 34205"),
    ("55 Oak Ave Apt 2, Sarasota, Florida 34236", "Sarasota", "Florida", "34236",
     "55 Oak Ave Apt 2, Sarasota, FL 34236"),
    ("9 Elm Rd", "", "", "", "9 Elm Rd"),
])
def test_the_street_is_cleaned(street, city, state, zp, want):
    assert report.clean_address({"street": street, "city": city, "state": state,
                                 "zip_code": zp}) == want


def test_the_html_has_the_logo_customer_prices_and_no_draft_banner(env):
    out = report.write(spec(env))
    page = Path(out["html_path"]).read_text("utf-8")
    assert report.logo_uri() in page and 'alt="Dream Team Roofing"' in page
    for text in ("Brett Edelson", "7236 Via Luria, Greenacres, FL 33467", "#728",
                 "Tue, Oct 13, 2026", "Luis Tech",
                 "We did not inspect the attic or do a water test", "on Oct 13, 2026",
                 "Inside (Photo 5)."):
        assert text in page, text
    assert "$3,000" not in page and "proposal DTR-129" not in page      # no options table
    table, _ = options_table(env, spec(env))
    for text in ("Best — recommended", "$2,500", "$8,500", "$1,600", "$4,000", MINUS + "$1,000",
                 "<b>$600</b>", "<b>$3,000</b>", "3 months", "1 year",
                 "50% to start ($300), 50% when done", "proposal DTR-129"):
        assert text in table, text
    assert page.count('class="zoom"') == 5 and 'id="lb"' in page      # every photo zoomable
    assert "EE. UU." not in page and "BRETT" not in page
    # The customer's copy never shows a draft banner or "[placeholder]" (owner, 2026-11-09):
    # missing header values are left out and reported to Owen as warnings instead.
    assert "DRAFT" not in page and "[placeholder]" not in page and "Phone:" not in page
    assert any("OWEN_AI_REPORT_PHONE" in w for w in out["warnings"])


def test_settings_fill_the_header(env, monkeypatch):
    monkeypatch.setenv("OWEN_AI_REPORT_PHONE", "(941) 555-0100")
    monkeypatch.setenv("OWEN_AI_REPORT_LICENSE", "CCC1234567")
    monkeypatch.setenv("OWEN_AI_REPORT_CITY", "Bradenton, FL")
    out = report.write(spec(env))
    page = Path(out["html_path"]).read_text("utf-8")
    assert "Phone: (941) 555-0100<br>License #: CCC1234567<br>Office: Bradenton, FL" in page
    assert "[placeholder]" not in page and not any("OWEN_AI_REPORT" in w for w in
                                                    out["warnings"])


def test_text_is_escaped(env):
    s = spec(env, weather='5 < 6 & "dry"', what_happened="Fine & <b>bold</b> <i>it</i> > 2")
    s["findings"][0]["saw"] = "x < y; 'quote' & \"double\""
    page = Path(report.write(s)["html_path"]).read_text("utf-8")
    assert "5 &lt; 6 &amp; &quot;dry&quot;" in page
    assert "Fine &amp; <b>bold</b> <i>it</i> &gt; 2" in page
    assert "x &lt; y; &#x27;quote&#x27; &amp; &quot;double&quot;" in page


def options_table(env, s: dict) -> tuple[str, list[str]]:
    """The options table is not in the customer's page any more (owner, 2026-11-09); its parsing
    still guards the numbers Claude sees, so test options_html directly."""
    f = report.fetch_facts(s["job_uid"], s.get("proposal"))
    return report.options_html(report.validate(s, env["photos"]), f), f.warnings


def test_the_page_has_no_options_table_and_no_signature(env):
    page = Path(report.write(spec(env))["html_path"]).read_text("utf-8")
    assert '<table class="t">' not in page and "Your repair options" not in page
    assert "Inspector signature" not in page and "Questions? Call" not in page
    assert "review the repair proposal we send you" in page


def test_an_unparseable_price_is_a_dash_and_a_warning_never_a_guess(env):
    env["router"].est = estimate([option("GOOD", 900, 0, 0, "", text="Fix it. No numbers here."),
                                  option("BETTER", 1400, 4500, 2400, "6 Months")])
    page, warnings = options_table(env, spec(env))
    out = {"warnings": warnings}
    good = re.findall(r"<tr><th>(?:Regular price|AHS member price|AHS credit|Warranty "
                      r"\(workmanship\))</th><td[^>]*>([^<]*)</td>", page)
    assert good == ["—", "—", "—", "—"]
    assert "<b>$900</b>" in page and "$4,500" in page
    assert any("GOOD: could not read the retail value, member price, AHS credit, warranty" in w
               for w in out["warnings"])


def test_spec_option_text_overrides_wording_not_numbers(env):
    s = spec(env, option_text={"good": {"fixes": "The <b>main</b> leak", "includes": "Lift tiles"},
                               "EXTRA": {"fixes": "x"}})
    page, warnings = options_table(env, s)
    out = {"warnings": warnings}
    assert "<td>The <b>main</b> leak</td>" in page and "<td>Lift tiles</td>" in page
    assert "<td>Repair package BETTER</td>" in page                    # the proposal's own
    assert any("EXTRA" in w for w in out["warnings"])


def test_no_proposal_means_no_table_and_a_warning(env):
    env["router"].est = estimate([])
    out = report.write(spec(env))
    page = Path(out["html_path"]).read_text("utf-8")
    assert '<table class="t">' not in page and "About AHS" not in page
    assert any("no options" in w for w in out["warnings"])


# ------------------------------------------------------------------------------ files

def test_without_chromium_the_pdf_is_skipped_cleanly(env):
    out = report.write(spec(env))
    assert out["pdf_path"] is None and out["pdf_url"] is None
    folder = Path(out["html_path"]).parent
    assert sorted(p.name for p in folder.iterdir()) == ["meta.json", "report.html"]
    assert folder.parent == config.report_dir() and report.ID_RE.fullmatch(out["id"])
    assert out["id"].startswith("728-")
    assert any(w.startswith("PDF not made: no Chromium") for w in out["warnings"])
    text = report.make_inspection_report(spec(env))
    assert "[Download report (.html)](/api/owen-ai/reports/" in text
    assert ".pdf)" not in text and "PDF not made" in text and not text.startswith("ERROR")


def test_with_chromium_the_pdf_is_printed_from_the_same_html(env, monkeypatch):
    browser = env["tmp"] / "chromium"
    browser.write_text("")
    monkeypatch.setenv("OWEN_AI_CHROMIUM", str(browser))
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        out = next(a for a in cmd if a.startswith("--print-to-pdf="))[len("--print-to-pdf="):]
        Path(out).write_bytes(b"%PDF-1.4 fake")
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(report.subprocess, "run", fake_run)
    out = report.write(spec(env))
    assert out["pdf_path"] and Path(out["pdf_path"]).read_bytes().startswith(b"%PDF")
    assert seen["cmd"][0] == str(browser) and "--headless=new" in seen["cmd"]
    assert seen["cmd"][-1] == Path(out["html_path"]).resolve().as_uri()
    text = report.make_inspection_report(spec(env))
    assert re.search(r"\[Download report \(\.pdf\)\]\(/api/owen-ai/reports/728-\d{8}-\d{6}/"
                     r"report\.pdf\)", text)


def test_two_reports_in_the_same_second_get_two_ids(env):
    from datetime import UTC, datetime
    now = datetime(2026, 11, 7, 15, 0, 0, tzinfo=UTC)
    a, b = report.write(spec(env), now=now), report.write(spec(env), now=now)
    assert a["id"] == "728-20261107-100000" and b["id"] == "728-20261107-100001"


def test_reports_older_than_90_days_are_removed(env):
    root = config.report_dir()
    old = root / "728-20260101-000000"
    old.mkdir(parents=True)
    stamp = time.time() - 91 * 86400
    os.utime(old, (stamp, stamp))
    keep = root / "728-20261101-000000"
    keep.mkdir()
    (root / "not-a-report").mkdir()
    assert report.cleanup(root) == 1
    assert not old.exists() and keep.exists() and (root / "not-a-report").exists()


def test_errors_are_sentences_for_claude(env):
    assert report.make_inspection_report({"job_uid": JOB_UID}).startswith(
        "ERROR: the spec is not valid")
    other = spec(env, job_uid="11111111-aaaa-4bbb-8ccc-000000000701")
    assert report.make_inspection_report(other).startswith("ERROR")


def test_the_tool_is_registered_with_its_schema():
    by = {s[0]: s for s in tools.SPECS}
    name, desc_, schema, fn = by["make_inspection_report"]
    assert "mcp__owen__make_inspection_report" in tools.TOOL_NAMES
    assert "DRAFT" in desc_ and "FRACTIONS" in desc_ and "job_photos" in desc_
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) >= {"job_uid", "badge", "what_happened", "hero", "findings"}
    json.dumps(schema)


# ------------------------------------------------------------------------------ the API

def _report(env, last="Edelson") -> str:
    out = report.write(spec(env))
    Path(out["html_path"]).with_name("report.pdf").write_bytes(b"%PDF-1.4 fake")
    return out["id"]


def test_the_owner_downloads_the_files_as_attachments(db, people, env):  # noqa: F811
    rid = _report(env)
    r = call("GET", "/reports/%s/report.pdf" % rid, "owner", people)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;")
    assert 'filename="Inspection Report - Job 728 - Edelson.pdf"' in cd
    h = call("GET", "/reports/%s/report.html" % rid, "owner", people)
    assert h.status_code == 200 and "Brett Edelson" in h.text
    hd = h.headers["content-disposition"]
    assert 'filename="Inspection Report - Job 728 - Edelson.html"' in hd
    lst = call("GET", "/reports?job_uid=" + JOB_UID, "owner", people).json()["reports"]
    assert [x["id"] for x in lst] == [rid]
    assert lst[0]["pdf_url"] == "/api/owen-ai/reports/%s/report.pdf" % rid
    assert call("GET", "/reports?job_uid=someone-else", "owner", people).json() == {
        "reports": []}


@pytest.mark.parametrize("who", ["admin", "dispatcher"])
def test_anyone_else_gets_404(db, people, env, who):  # noqa: F811
    rid = _report(env)
    for path in ("/reports/%s/report.pdf" % rid, "/reports/%s/report.html" % rid,
                 "/reports?job_uid=" + JOB_UID, "/reports"):
        assert call("GET", path, who, people).status_code == 404, path


@pytest.mark.parametrize("path", [
    "/reports/{rid}/meta.json", "/reports/{rid}/REPORT.PDF", "/reports/{rid}/report.pdf.bak",
    "/reports/..%2F..%2Fphotos/report.html", "/reports/{rid}%2F..%2F..%2Fx/report.html",
    "/reports/{rid}/..%2Fmeta.json", "/reports/..%5C..%5Cx/report.html",
    "/reports/728-2026/report.pdf", "/reports/728-20261107-999999/report.pdf",
    "/reports/{rid}/%2E%2E%2Fmeta.json"])
def test_path_tricks_are_refused(db, people, env, path):  # noqa: F811
    rid = _report(env)
    r = call("GET", path.format(rid=rid), "owner", people)
    assert r.status_code == 404 and b"%PDF" not in r.content and b"job_uid" not in r.content


def test_the_reports_folder_lines_up_with_the_api_mount(monkeypatch):
    """Both containers: CLAUDE_CONFIG_DIR=/var/lib/owen-ai/claude (Dockerfile.api) and the
    owen_ai_home volume at /var/lib/owen-ai (rw in owen_ai, ro in api)."""
    monkeypatch.delenv("OWEN_AI_REPORT_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/var/lib/owen-ai/claude")
    assert config.report_dir().as_posix().endswith("/var/lib/owen-ai/reports")
    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.prod.yml").read_text("utf-8")
    assert compose.count("owen_ai_home:/var/lib/owen-ai") == 2
    assert "CLAUDE_CONFIG_DIR=/var/lib/owen-ai/claude" in (root / "Dockerfile.api").read_text(
        "utf-8")


def test_marks_are_drawn_where_the_fractions_say(env):
    p = report.validate(spec(env), env["photos"]).hero.inside_photo
    p.marks[0].label = ""
    p.crop_gps = False
    data = report.annotate(p).split(",", 1)[1]
    import base64
    im = Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")
    assert im.size == (1024, 768)
    r, g, b = im.getpixel((round(0.63 * 1024), round(0.38 * 768) + 3))   # top of the ellipse
    assert r > 180 and g < 90
