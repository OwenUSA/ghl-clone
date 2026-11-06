"""Owen AI's job_photos — Claude LOOKS at a job's pictures (2026-11-06, owen_ai/photos.py).

Pinned, by behaviour:
  * pictures come from the gallery (album names only when the folder filter demonstrably
    narrowed), the notes and checklist photo answers, deduplicated; videos are counted, not kept;
  * `inspection` keeps photos up to the first proposal, leak / failure-point words first, then
    nearest the inspection; `repair` keeps photos after the accepted proposal; `all` everything;
  * each picked picture is a <=1024 px JPEG under the photo dir, with absolute paths in the answer
    and captions <= 120 characters; a second call downloads nothing again;
  * only GETs go out; a too-big or broken picture is one "failed", never a crash;
  * folders older than 14 days are removed at the start of a call;
  * the tool is registered after board_snapshot and agent.py can hand the folder to the CLI.
"""
import io
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from app.owen_ai import config, photos, tools
from app.zuper import client
from PIL import Image
from tests.zuper_support import zuper_env  # noqa: F401  (fixture)

JOB_UID = "11111111-aaaa-4bbb-8ccc-000000000701"
LUIS = {"user_uid": "u-2", "first_name": "Luis", "last_name": "Tech"}
FILES = "files.test"


def jpeg(w=2000, h=1500, color=(200, 60, 40)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), color).save(out, "JPEG", quality=90)
    return out.getvalue()


def job_record():
    return {
        "job_uid": JOB_UID, "work_order_number": 701, "job_title": "TEST - leak",
        "job_status": [
            {"status_name": "Got a job from AHS", "created_at": "2026-10-20T13:00:00.000Z",
             "checklist": []},
            {"status_name": "Inspection In Progress", "created_at": "2026-10-22T14:00:00.000Z",
             "done_by": LUIS, "checklist": [
                 {"question": "Album 3 leak-location photos", "type": "MULTI_IMAGE",
                  "answer": ["https://%s/c1.jpg" % FILES]},
                 {"question": "Recommendation recorded", "answer": "Yes", "type": "RADIO"}]},
            {"status_name": "Proposal Creation", "created_at": "2026-10-25T16:30:00.000Z",
             "checklist": []},
        ],
        "scheduled_start_time": "2026-10-22T14:00:00.000Z",
        "created_at": "2026-10-20T12:59:00.000Z",
    }


def att(uid, at, name, mime="image/jpeg"):
    return {"attachment_uid": uid, "attachment_path": "https://%s/%s" % (FILES, name),
            "attachment_name": name, "mime_type": mime, "created_at": at, "created_by": LUIS}


GALLERY = [
    att("a-n1", "2026-10-22T14:30:00.000Z", "n1.jpg"),          # also on a note
    att("a-g1", "2026-10-22T15:00:00.000Z", "g1.jpg"),          # album "Failure Point"
    att("a-g2", "2026-10-22T14:10:00.000Z", "IMG_2.jpg"),       # no words
    att("a-g3", "2026-10-29T15:00:00.000Z", "g3.jpg"),          # album "After Repair"
    att("a-v1", "2026-10-22T14:20:00.000Z", "v1.mp4", "video/mp4"),
]
FOLDERS = [{"folder_uid": "f-fail", "folder_name": "Failure Point", "attachment_count": 1},
           {"folder_uid": "f-after", "folder_name": "After Repair", "attachment_count": 1},
           {"folder_uid": "f-empty", "folder_name": "Gutters", "attachment_count": 0}]
NOTES = [{"note_uid": "n1", "note_type": "IMAGE", "created_by": LUIS,
          "note": "<p>Leak at the chimney flashing, " + "very long words " * 20 + "</p>",
          "created_at": "2026-10-22T14:31:00.000Z",
          "attachments": [{"attachment_uid": "a-n1", "attachment": "https://%s/n1.jpg" % FILES,
                           "file_name": "n1.jpg", "mime_type": "image/jpeg"}]}]
PROPOSALS = [{"estimate_uid": "e1", "created_at": "2026-10-25T17:00:00.000Z",
              "estimate_status": "ACCEPTED", "accepted_date": "2026-10-28T12:00:00.000Z"}]


class Router:
    def __init__(self, big: set[str] | None = None):
        self.requests: list[httpx.Request] = []
        self.big = big or set()

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if req.url.host == FILES:
            name = req.url.path.lstrip("/")
            size = (3000, 1000) if name in self.big else (2000, 1500)
            return httpx.Response(200, content=jpeg(*size), headers={"content-type":
                                                                     "image/jpeg"})
        path = re.sub(r"^/api", "", req.url.path)
        q = dict(req.url.params)
        ok = lambda data, **extra: httpx.Response(200, json={"type": "success",  # noqa: E731
                                                             "data": data, **extra})
        if path == "/jobs":
            return ok([{"job_uid": JOB_UID, "work_order_number": 701}])
        if path == "/jobs/" + JOB_UID:
            return ok(job_record())
        if path == "/jobs/%s/note" % JOB_UID:
            return ok(NOTES)
        if path == "/attachments/folders":
            return ok(FOLDERS)
        if path == "/attachments/group":
            rows = GALLERY
            if q.get("filter.folder_uid") == "f-fail":
                rows = [GALLERY[1]]
            elif q.get("filter.folder_uid") == "f-after":
                rows = [GALLERY[3]]
            return ok([{"date": "x", "attachments": rows}], total_records=len(rows),
                      total_pages=1)
        if path == "/estimate":
            return ok(PROPOSALS)
        return httpx.Response(404, json={"message": "no route " + path})


@pytest.fixture()
def wire(zuper_env, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setenv("OWEN_AI_PHOTO_DIR", str(tmp_path / "photos"))

    def install(router: Router) -> Router:
        monkeypatch.setattr(client, "TRANSPORT", httpx.MockTransport(router))
        return router
    return install


def run(**kw) -> dict:
    out = photos.job_photos("#701", **kw)
    assert not out.startswith("ERROR"), out
    return json.loads(out)


def names(answer: dict) -> list[str]:
    return [p["source"] + ":" + (p.get("album") or p.get("caption") or "")[:12]
            for p in answer["photos"]]


def test_inspection_photos_leak_words_first_then_nearest_the_inspection(wire):
    router = wire(Router())
    a = run()
    assert a["job"] == "701" and a["which"] == "inspection"
    assert a["counts"]["images"] == 5 and a["counts"]["videos"] == 1      # n1 counted once
    assert a["counts"]["selected"] == 4 and a["counts"]["skipped"] == 1   # g3 is after
    # checklist (0 min, "leak"), note (30 min, "Leak"), album Failure Point (60 min), then g2
    srcs = [p["source"] for p in a["photos"]]
    assert srcs == ["checklist", "gallery+note", "album", "gallery"]
    assert a["photos"][2]["album"] == "Failure Point"
    assert a["photos"][1]["at"] == "2026-10-22 10:30"                    # America/New_York
    assert all(len(p.get("caption", "")) <= 120 for p in a["photos"])
    assert "Read" in a["next"]
    for p in a["photos"]:
        f = Path(p["path"])
        assert f.is_absolute() and f.is_file() and f.suffix == ".jpg"
        with Image.open(f) as im:
            assert im.format == "JPEG" and max(im.size) <= 1024
    assert [r.method for r in router.requests] == ["GET"] * len(router.requests)
    index = json.loads((Path(a["folder"]) / "index.json").read_text("utf-8"))
    assert len(index["files"]) == 4


def test_repair_and_all_windows(wire):
    wire(Router())
    rep = run(which="repair")
    assert [p.get("album") for p in rep["photos"]] == ["After Repair"]
    assert "accepted" in rep["window"]
    assert run(which="all")["counts"]["selected"] == 5
    assert run(which="all", since="2026-10-23")["counts"]["selected"] == 1
    assert photos.job_photos("701", which="before").startswith("ERROR")


def test_a_second_call_downloads_nothing_again(wire):
    router = wire(Router())
    first = run()
    n = sum(1 for r in router.requests if r.url.host == FILES)
    assert n == 4 and first["counts"]["downloaded"] == 4
    second = run()
    assert sum(1 for r in router.requests if r.url.host == FILES) == n
    assert second["counts"]["reused"] == 4 and second["counts"]["downloaded"] == 0
    assert [p["path"] for p in second["photos"]] == [p["path"] for p in first["photos"]]
    # widening the window adds only the new picture, with the next number
    wide = run(which="all")
    assert wide["counts"]["downloaded"] == 1 and wide["counts"]["reused"] == 4
    assert max(Path(p["path"]).name for p in wide["photos"]) == "005.jpg"


def test_resize_keeps_the_aspect_and_caps_the_longest_side(wire):
    wire(Router(big={"c1.jpg"}))
    a = run(max_photos=1)
    with Image.open(a["photos"][0]["path"]) as im:
        assert im.size == (1024, 341)


def test_size_cap_and_broken_pictures_are_failed_not_a_crash(wire, monkeypatch):
    wire(Router())
    monkeypatch.setattr(client, "FILE_MAX_BYTES", 1000)
    a = run()
    assert a["counts"]["failed"] == 4 and a["photos"] == []
    assert len(a["failed"]) == 4 and "large" in a["failed"][0]["why"]
    assert run(max_photos=500)["counts"]["selected"] <= photos.HARD_MAX


def test_old_folders_are_removed_at_the_start_of_a_call(wire, tmp_path):
    wire(Router())
    root = tmp_path / "photos"
    old, fresh = root / "123", root / "456"
    for d in (old, fresh):
        d.mkdir(parents=True)
        (d / "index.json").write_text("{}", "utf-8")
    stale = time.time() - 15 * 86400
    os.utime(old / "index.json", (stale, stale))
    a = run()
    assert not old.exists() and fresh.exists()
    assert a["old_folders_removed"] == 1


def test_selection_is_pure_and_falls_back_when_nothing_precedes_the_proposal():
    t = lambda s: datetime.fromisoformat(s).replace(tzinfo=UTC)  # noqa: E731
    imgs = [{"key": "a", "at": "2026-10-26T10:00:00Z", "source": "note", "name": "x"}]
    picked, window = photos.select(imgs, "inspection", t("2026-10-22T14:00"),
                                   t("2026-10-25T16:00"), None, None, 24)
    assert picked == imgs and "so all" in window
    picked, window = photos.select(imgs, "repair", None, None, None, None, 24)
    assert picked == [] and "no proposal" in window


def test_the_tool_is_registered_after_board_snapshot_and_read_can_open_the_folder(
        monkeypatch, tmp_path):
    order = [name for name, *_ in tools.SPECS]
    assert order.index("job_photos") == order.index("board_snapshot") + 1
    assert "mcp__owen__job_photos" in tools.TOOL_NAMES
    desc = {s[0]: s[1] for s in tools.SPECS}["job_photos"]
    assert "Read tool" in desc
    monkeypatch.delenv("OWEN_AI_PHOTO_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "home" / "claude"))
    assert config.photo_dir() == (tmp_path / "home" / "photos").resolve()
    sdk = pytest.importorskip("claude_agent_sdk")
    assert "add_dirs" in sdk.ClaudeAgentOptions.__dataclass_fields__
