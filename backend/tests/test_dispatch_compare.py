"""A spreadsheet in the Dispatch chat, read top to bottom and compared with Zuper (2026-10-01).

Pinned, by behaviour:
  * every sheet and row is read; the header is the first row with text, so title rows above it
    are skipped; dates become dates and job numbers stay "291", not "291.0";
  * a row matches its Zuper job by job number, then phone, then name, then address; "Work / note"
    is never taken for a work-order column;
  * the comparison names what differs (stage, address, phone, closed job) and lists the rows Zuper
    does not have; a board hidden from the reader is never matched;
  * through the chat: an upload is kept with the message, the assistant's compare_file tool
    returns the result and a downloadable workbook, and nobody else can open the file.
"""
import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from app.ai import vault
from app.auth import mint_api_token
from app.dispatch import ai, compare, sheets
from app.dispatch import config as c
from app.main import app
from app.models import AiConnection, DispatchChatFile, DispatchJob, Role, User
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from tests.ai_support import openai_call, openai_completion

NOW = datetime(2026, 9, 30, 18, 0, tzinfo=UTC)


def xlsx(rows_by_sheet: dict) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in rows_by_sheet.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


BOOK = {
    "To Invoice": [["Dream Team — invoices"], [],
                   ["#", "Customer", "Address", "Work / note", "Zuper WO", "Status"],
                   [1, "Jane Doe", "12 Palm Ave, Weston", "Roof Leaks", 291.0, "Repair Scheduled"],
                   [2, "John Roe", "5 Oak St, Miami", "Flat roof", None, "AHS Approved"],
                   [3, "Nobody Here", "1 Nowhere Rd", "", None, "Callback"]],
    "Calls": [["Customer", "Phone", "Status"], ["Ann Lee", "(941) 555-0177", "Callback"]],
}


class J:
    def __init__(self, **kw):
        base = {"job_uid": "u", "job_number": None, "board": c.INSPECTION_BOARD, "status": None,
                "customer_name": None, "phones": [], "address": None, "city": None,
                "is_open": True, "assigned": [], "technician": None, "status_since": NOW,
                "scheduled_start": None, "zuper_created_at": NOW - timedelta(days=5)}
        base.update(kw)
        self.__dict__.update(base)


JOBS = [J(job_uid="a", job_number="291", status="AHS Approved", customer_name="Jane Doe",
          address="12 Palm Ave"),
        J(job_uid="b", job_number="300", status="AHS Approved", customer_name="John Roe",
          address="5 Oak St", board=c.REPAIR_BOARD),
        J(job_uid="c", job_number="310", status="Call Back", customer_name="Ann Lee",
          phones=["9415550177"], board=c.RETAIL_BOARD)]


def test_every_sheet_is_read_from_the_header_down():
    got = sheets.parse(xlsx(BOOK), "list.xlsx")
    assert [s["name"] for s in got] == ["To Invoice", "Calls"]
    first = got[0]
    assert first["columns"][:2] == ["#", "Customer"]
    assert [r["values"].get("Customer") for r in first["rows"]] == ["Jane Doe", "John Roe",
                                                                    "Nobody Here"]
    assert first["rows"][0]["values"]["Zuper WO"] == "291"


def test_csv_and_refusals():
    got = sheets.parse(b"Customer,Phone\nAnn Lee,941-555-0177\n", "calls.csv")
    assert got[0]["rows"][0]["values"] == {"Customer": "Ann Lee", "Phone": "941-555-0177"}
    with pytest.raises(sheets.Unreadable):
        sheets.parse(b"hello", "notes.txt")
    with pytest.raises(sheets.Unreadable):
        sheets.parse(b"not really excel", "fake.xlsx")


def test_rows_match_by_job_number_phone_name_and_differences_are_named():
    result = compare.compare(sheets.parse(xlsx(BOOK), "list.xlsx"), JOBS, hidden=set(), now=NOW)
    rows = {(r["sheet"], r["customer"]): r for r in result["rows"]}
    jane = rows[("To Invoice", "Jane Doe")]
    assert (jane["job_number"], jane["matched_by"], jane["result"]) == ("291", "job #",
                                                                        "different")
    assert any("Repair Scheduled" in d and "AHS Approved" in d for d in jane["differences"])
    john = rows[("To Invoice", "John Roe")]
    assert (john["job_number"], john["matched_by"], john["result"]) == ("300", "name", "same")
    assert rows[("To Invoice", "Nobody Here")]["result"] == "not in Zuper"
    ann = rows[("Calls", "Ann Lee")]
    assert ann["matched_by"] == "phone" and ann["result"] == "same"    # Callback = Call Back
    assert compare.columns_of(BOOK["To Invoice"][2])["job"] == "Zuper WO"   # not "Work / note"
    assert result["counts"] == {"rows": 4, "matched": 3, "not_in_zuper": 1, "different": 1,
                                "same": 2, "not_a_customer_row": 0}


def test_a_hidden_board_is_never_matched():
    result = compare.compare(sheets.parse(xlsx(BOOK), "list.xlsx"), JOBS,
                             hidden={c.RETAIL_BOARD}, now=NOW)
    ann = next(r for r in result["rows"] if r["customer"] == "Ann Lee")
    assert ann["result"] == "not in Zuper" and ann["job_number"] is None


# ---- through the chat ------------------------------------------------------------------------

@pytest.fixture()
def people(db, secrets_key):
    out = {}
    for key, role in (("dispatcher", Role.DISPATCHER), ("other", Role.DISPATCHER)):
        u = User(email="%s@x.test" % key, name=key.title(), role=role)
        db.add(u)
        db.flush()
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        out[key] = {"Authorization": "Bearer " + plain}
    conn = AiConnection(name="OpenAI", provider="openai", default_model="gpt-6-luna",
                        api_key_encrypted=vault.encrypt("sk-test-1"), api_key_last4="st-1")
    db.add(conn)
    db.flush()
    s = ai.settings(db, for_update=True)
    s.ai_enabled, s.connection_id = True, conn.id
    for j in JOBS:
        db.add(DispatchJob(job_uid=j.job_uid, job_number=j.job_number, board=j.board,
                           status=j.status, customer_name=j.customer_name, phones=j.phones,
                           address=j.address, is_open=True, status_since=NOW,
                           zuper_created_at=NOW - timedelta(days=5)))
    db.commit()
    return out


def test_upload_compare_and_download_through_the_chat(db, people, script):
    cl = TestClient(app)
    up = cl.post("/api/dispatch/chats/files", headers=people["dispatcher"],
                 files={"file": ("Customer_List.xlsx", xlsx(BOOK), "application/vnd.ms-excel")})
    assert up.status_code == 200, up.text
    fid = up.json()["id"]
    assert up.json()["total_rows"] == 4
    script.responses = [
        openai_completion(content=None, finish="tool_calls", tool_calls=[
            openai_call("compare_file", json.dumps({"file_id": fid}), "c1")]),
        openai_completion(content="3 of 4 rows are in Zuper; 2 differ."),
    ]
    r = cl.post("/api/dispatch/chats/messages", headers=people["dispatcher"], json={
        "chat_id": None, "content": "Compare it", "file_ids": [fid]}).json()
    assert r["user_message"]["files"][0]["filename"] == "Customer_List.xlsx"
    answer = r["assistant_message"]
    assert answer["content"].startswith("3 of 4")
    assert answer["downloads"][0]["url"] == "/api/dispatch/chats/files/%d/comparison.xlsx" % fid
    # The model was told about the file in the question it received.
    sent = script.requests[0]["body"]["messages"][-1]["content"]
    assert "Attached file #%d" % fid in sent and "Zuper WO" in sent
    xl = cl.get(answer["downloads"][0]["url"], headers=people["dispatcher"])
    assert xl.status_code == 200
    ws = load_workbook(io.BytesIO(xl.content))["Comparison"]
    assert ws.max_row == 5 and ws.cell(1, 7).value == "Zuper job #"
    # Nobody else can open the file or attach it.
    assert cl.get(answer["downloads"][0]["url"], headers=people["other"]).status_code == 404
    assert cl.post("/api/dispatch/chats/messages", headers=people["other"], json={
        "chat_id": None, "content": "mine now", "file_ids": [fid]}).status_code == 404
    assert db.get(DispatchChatFile, fid).chat_id == r["chat"]["id"]


def test_an_upload_that_is_not_excel_is_refused(db, people):
    cl = TestClient(app)
    assert cl.post("/api/dispatch/chats/files", headers=people["dispatcher"],
                   files={"file": ("notes.txt", b"x", "text/plain")}).status_code == 415
    assert cl.post("/api/dispatch/chats/files", headers=people["dispatcher"],
                   files={"file": ("bad.xlsx", b"not excel", "application/x")}).status_code == 422


def test_heading_rows_are_skipped_and_names_with_notes_or_two_people_still_match():
    """From the owner's real list (2026-10-01): blank / day-heading rows, "(Northlake Dr)"
    notes and "A / B" cells."""
    book = {"Schedule": [["Day", "Customer", "Status"], ["Thursday", None, None],
                         [None, "Jane Doe (back door)", "Callback - Needs to Schedule"],
                         [None, "Nobody Else / John Roe", "AHS Approved"]]}
    result = compare.compare(sheets.parse(xlsx(book), "s.xlsx"), JOBS, hidden=set(), now=NOW)
    assert result["counts"]["not_a_customer_row"] == 1
    got = {r["customer"]: r for r in result["rows"]}
    assert got["Jane Doe (back door)"]["job_number"] == "291"
    assert got["Nobody Else / John Roe"]["job_number"] == "300"
    assert compare.names_in("A Smith / B Jones (Main St)") == ["a smith", "b jones"]
