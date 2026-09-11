"""The Conversations inbox: the unread badge, marking read, and deleting a thread.

Three defects the owner reported, and every one of them was invisible to a
status-code test:

  * clicking into a thread left the blue badge sitting at 2 for ever, because
    nothing in the browser has ever called `PATCH /api/conversations/{id}` —
    the endpoint itself worked all along, and `ghl convos read` used it;
  * the Unread TAB's badge summed unread MESSAGES while the tab it labels
    filters CONVERSATIONS, so one thread holding two unread texts read "2"
    above a list of one row;
  * there was no supported way to delete a thread at all, so demo threads were
    removed straight from the production database.

The fixture is built so a sum and a count give DIFFERENT answers — three unread
messages across two unread conversations — because a fixture where they agree
cannot tell the fixed badge from the broken one.

The arithmetic lives in `frontend/src/lib/inbox.ts`, which imports nothing, so
node runs the real shipped module rather than this file pattern-matching the
page source. Same reasoning as `test_csv_export.py` and `test_calendar_grid.py`.
"""
import json
import os
import shutil
import subprocess
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Appointment,
    Calendar,
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    EventType,
    Opportunity,
    Pipeline,
    Role,
    Stage,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
INBOX_TS = FRONTEND / "lib" / "inbox.ts"

NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so inbox.ts cannot be executed. CI's backend job "
           "installs it precisely so this file is never skipped there — see "
           ".github/workflows/ci.yml.")


def utcnow():
    return datetime.now(UTC)


def run_js(body: str):
    """Execute `body` with the real module imported as `inbox`, return its JSON."""
    script = textwrap.dedent("""
        import * as inbox from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
    """) % json.dumps(INBOX_TS.as_posix()) + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True,
        env={**os.environ, "TZ": "America/New_York"})
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


@pytest.fixture()
def api():
    """One bearer client per role over a hand-built inbox.

    Jane's thread holds TWO unread texts, Bob's holds ONE, and Carol's is
    already read. So the unread tab shows two rows while the unread messages sum
    to three — the two numbers that used to be confused for each other.

    Jane also has an opportunity and an appointment, so "deleting a thread
    deletes only the thread" is a claim with something to check.
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    users = {}
    for key, role in (("admin", Role.ADMIN), ("dispatcher", Role.DISPATCHER),
                      ("tech", Role.TECH)):
        u = User(email="%s@x.test" % key, name=key.title(), role=role)
        db.add(u)
        users[key] = u
    db.flush()

    jane = Contact(first_name="Jane", last_name="Doe", phone="(941) 555-0101",
                   email="jane@roofmail.test")
    bob = Contact(first_name="Bob", last_name="Reyes", phone="(941) 555-0202",
                  email="bob@roofmail.test")
    carol = Contact(first_name="Carol", last_name="Nunez", phone="(941) 555-0303",
                    email="carol@roofmail.test")
    db.add_all([jane, bob, carol])
    db.flush()

    pipe = Pipeline(name="Dream Team Roofing AHS")
    db.add(pipe)
    db.flush()
    stage = Stage(pipeline_id=pipe.id, name="New Lead", position=0)
    db.add(stage)
    db.flush()

    opp = Opportunity(title="Jane roof replacement", contact_id=jane.id,
                      pipeline_id=pipe.id, stage_id=stage.id, position=0,
                      value_cents=950000,
                      # The telephony project's live join key. DECISIONS.md is
                      # explicit that nothing may delete an opportunity as a side
                      # effect of tidying something else.
                      custom_fields={"owen_call_id": "owen-4417"})
    db.add(opp)

    cal = Calendar(name="Owen's Personal Calendar", user_id=users["admin"].id)
    db.add(cal)
    db.flush()
    appt = Appointment(title="Roof inspection", calendar_id=cal.id,
                       contact_id=jane.id, starts_at=utcnow() + timedelta(days=1),
                       ends_at=utcnow() + timedelta(days=1, hours=1))
    db.add(appt)

    convs = {}
    for key, contact, unread in (("jane", jane, 2), ("bob", bob, 1),
                                 ("carol", carol, 0)):
        conv = Conversation(contact_id=contact.id, last_event_at=utcnow(),
                            unread_count=unread)
        db.add(conv)
        convs[key] = conv
    db.flush()

    def ev(conv, type_, body, minutes_ago, direction=Direction.INBOUND):
        db.add(ConversationEvent(
            conversation_id=conv.id, type=type_, direction=direction, body=body,
            occurred_at=utcnow() - timedelta(minutes=minutes_ago)))

    # Jane: two unread texts plus a call and an activity, so `event_count` is a
    # count of the WHOLE timeline and not of the unread part of it.
    ev(convs["jane"], EventType.SMS, "The skylight is leaking again", 10)
    ev(convs["jane"], EventType.SMS, "Are you coming Tuesday?", 9)
    ev(convs["jane"], EventType.CALL, None, 8)
    ev(convs["jane"], EventType.APPOINTMENT, "Appointment scheduled", 7,
       Direction.OUTBOUND)
    ev(convs["bob"], EventType.SMS, "Quote for the gutter run?", 6)
    ev(convs["carol"], EventType.SMS, "Thanks!", 5)

    tokens = {}
    for key, u in users.items():
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        tokens[key] = plain
    db.commit()

    ids = {"jane": jane.id, "bob": bob.id, "carol": carol.id,
           "conv_jane": convs["jane"].id, "conv_bob": convs["bob"].id,
           "conv_carol": convs["carol"].id,
           "opp": opp.id, "appt": appt.id, "pipeline": pipe.id}
    db.close()

    def client(who="admin"):
        c = TestClient(app)
        c.headers["Authorization"] = "Bearer " + tokens[who]
        return c

    yield {"client": client, "ids": ids}


# ---------------- the badge counts rows, not messages ----------------

@node
def test_the_unread_badge_equals_the_number_of_rows_the_unread_tab_shows(api):
    """The bug, stated as the assertion that catches it.

    Two unread conversations holding three unread messages between them. The old
    badge was the sum, so it said 3 above a list of 2 — and in the owner's own
    inbox, 2 above a list of 1.
    """
    c = api["client"]()
    rows = c.get("/api/conversations?tab=unread").json()
    assert len(rows) == 2, "fixture changed — two threads are supposed to be unread"

    messages = sum(r["unread_count"] for r in rows)
    assert messages == 3, (
        "the fixture no longer distinguishes a sum from a count, so it cannot "
        "tell the fixed badge from the broken one")

    badge = run_js("""
        const rows = %s
        out(inbox.unreadTabCount(rows))
    """ % json.dumps(rows))
    assert badge == len(rows)
    assert badge != messages


@node
def test_the_badge_and_the_list_are_computed_from_one_predicate(api):
    """`unreadTabCount` must BE `unreadRows().length`, over the same rows the
    server's `tab=unread` returns. Two independent rules is how they drift."""
    c = api["client"]()
    every = c.get("/api/conversations?tab=all").json()
    from_server = {r["id"] for r in c.get("/api/conversations?tab=unread").json()}

    shown = run_js("""
        const rows = %s
        out({ids: inbox.unreadRows(rows).map((r) => r.id),
             count: inbox.unreadTabCount(rows)})
    """ % json.dumps(every))
    assert set(shown["ids"]) == from_server, (
        "the client and the server disagree about which rows are unread")
    assert shown["count"] == len(shown["ids"])


@node
def test_a_thread_with_many_unread_messages_is_still_one_row():
    """The single case the owner reported, isolated from the API."""
    r = run_js("""
        const rows = [{id: 1, unread_count: 7}]
        out({badge: inbox.unreadTabCount(rows), rows: inbox.unreadRows(rows).length})
    """)
    assert r == {"badge": 1, "rows": 1}


# ---------------- opening a thread clears it ----------------

def test_opening_a_thread_sets_unread_count_to_zero_in_the_database(api):
    """The browser's mark-read is this PATCH and nothing else."""
    c = api["client"]()
    conv = api["ids"]["conv_jane"]
    assert c.get("/api/conversations?tab=all").json()
    r = c.patch("/api/conversations/%d" % conv, json={"read": True})
    assert r.status_code == 200
    assert r.json()["unread_count"] == 0

    db = SessionLocal()
    try:
        assert db.get(Conversation, conv).unread_count == 0
    finally:
        db.close()


@node
def test_marking_read_removes_the_row_from_the_unread_tab_and_the_badge(api):
    """The list the badge labels has to move with it, or they disagree again."""
    c = api["client"]()
    before = c.get("/api/conversations?tab=unread").json()
    assert len(before) == 2

    c.patch("/api/conversations/%d" % api["ids"]["conv_jane"], json={"read": True})

    after = c.get("/api/conversations?tab=unread").json()
    assert [r["id"] for r in after] == [api["ids"]["conv_bob"]]
    badge = run_js("""
        const rows = %s
        out(inbox.unreadTabCount(rows))
    """ % json.dumps(c.get("/api/conversations?tab=all").json()))
    assert badge == len(after) == 1


def test_marking_read_touches_only_that_thread(api):
    """A mark-read that cleared the inbox would also pass "the badge cleared"."""
    c = api["client"]()
    c.patch("/api/conversations/%d" % api["ids"]["conv_jane"], json={"read": True})
    rows = {r["id"]: r["unread_count"]
            for r in c.get("/api/conversations?tab=all").json()}
    assert rows[api["ids"]["conv_jane"]] == 0
    assert rows[api["ids"]["conv_bob"]] == 1, "an unrelated thread was cleared too"


@node
def test_the_cached_list_clears_only_the_thread_that_was_opened():
    """`markReadIn` is what the browser applies to every cached list."""
    r = run_js("""
        const rows = [{id: 1, unread_count: 2}, {id: 2, unread_count: 1}]
        const after = inbox.markReadIn(rows, 1)
        out({after, badgeBefore: inbox.unreadTabCount(rows),
             badgeAfter: inbox.unreadTabCount(after),
             originalUntouched: rows[0].unread_count})
    """)
    assert r["after"] == [{"id": 1, "unread_count": 0}, {"id": 2, "unread_count": 1}]
    assert r["badgeBefore"] == 2 and r["badgeAfter"] == 1
    # React Query compares references; mutating the cached array in place would
    # leave the badge rendering the old number until something else re-rendered.
    assert r["originalUntouched"] == 2


# ---------------- a failed PATCH must not clear the badge ----------------

@node
def test_a_failed_patch_leaves_the_badge_unread():
    """The state the inbox renders after a refused mark-read — not the call.

    `markConversationRead` hands back the updater the page applies to its cached
    lists. On a failure that updater is identity, so the list still carries the
    unread row and the badge still reads 1. A badge that clears locally while the
    server still considers the thread unread is worse than one that never
    cleared: it is wrong AND it looks right.
    """
    r = run_js("""
        const rows = [{id: 1, unread_count: 2}, {id: 2, unread_count: 0}]
        const update = await inbox.markConversationRead(1, async () => {
          throw new Error('You do not have permission to do that.')
        })
        const shown = update.apply(rows)
        out({badge: inbox.unreadTabCount(shown),
             row: shown.find((r) => r.id === 1).unread_count,
             cleared: update.cleared,
             message: update.error.message})
    """)
    assert r["badge"] == 1, "the Unread badge cleared even though the PATCH failed"
    assert r["row"] == 2, "the row's own badge cleared even though the PATCH failed"
    assert r["cleared"] is False
    assert r["message"] == "You do not have permission to do that."


@node
def test_a_successful_patch_clears_the_badge_without_a_refetch():
    """The other half: the badge must clear in the list the user is looking at,
    not only in the database."""
    r = run_js("""
        const rows = [{id: 1, unread_count: 2}, {id: 2, unread_count: 0}]
        let asked = null
        const update = await inbox.markConversationRead(1, async (id) => {
          asked = id
          return {id, unread_count: 0}
        })
        const shown = update.apply(rows)
        out({asked, badge: inbox.unreadTabCount(shown),
             row: shown.find((r) => r.id === 1).unread_count,
             cleared: update.cleared, error: update.error})
    """)
    assert r == {"asked": 1, "badge": 0, "row": 0, "cleared": True, "error": None}


@node
def test_a_thread_open_on_a_background_tab_keeps_its_badge():
    """The decision behind "a new message on an already-open thread": the badge
    is cleared because somebody is LOOKING at the thread. Nobody is looking at a
    background tab, so the message stays unread until they come back."""
    r = run_js("""
        out({
          openAndVisible: inbox.shouldMarkRead(7, 2, true),
          openButHidden: inbox.shouldMarkRead(7, 2, false),
          alreadyRead: inbox.shouldMarkRead(7, 0, true),
          nothingOpen: inbox.shouldMarkRead(null, 2, true),
        })
    """)
    assert r == {"openAndVisible": True, "openButHidden": False,
                 "alreadyRead": False, "nothingOpen": False}


# ---------------- the page actually calls it ----------------

def _page() -> str:
    return (FRONTEND / "pages" / "ConversationsPage.tsx").read_text(encoding="utf-8")


def test_the_page_marks_a_thread_read_when_it_is_opened():
    """The endpoint has always worked; the browser never called it."""
    source = _page()
    assert "markConversationRead" in source, "nothing in the page marks a thread read"
    assert "patchConversation" in source and "read: true" in source, (
        "the page no longer sends {read: true} — retarget this test")
    assert "shouldMarkRead(active" in source, (
        "the auto mark-read no longer keys off the open thread")


def test_the_mark_as_read_button_is_wired_to_the_same_call():
    """It was `IconBtn title="Mark as read"` with no handler at all. Two
    implementations of "read" would be the thing to avoid here, so it runs the
    same function the open does."""
    source = _page()
    button = source.split('title="Mark as read"', 1)[1].split("</IconBtn>", 1)[0]
    assert "markRead(active)" in button, (
        "the Mark as read button still does nothing when clicked")


def test_the_badge_clears_by_patching_the_cache_rather_than_refetching_the_world():
    source = _page()
    mark = source.split("const markRead = async", 1)[1].split("\n  }", 1)[0]
    assert "setQueriesData" in mark, (
        "the cached list is not patched, so the badge waits on the next poll")
    assert "update.apply" in mark, (
        "the page clears the badge itself instead of applying the outcome — a "
        "failed PATCH would then clear it too")
    assert "queryKey: ['conversations'] }" in mark
    assert "invalidateQueries({ queryKey: ['events'" not in mark, (
        "marking read refetches the thread as well, which it has no reason to do")


def test_the_unread_tab_badge_counts_conversations():
    source = _page()
    assert "unreadTabCount(convs.data)" in source, (
        "the tab badge is no longer the shared count")
    assert "reduce((s, c) => s + c.unread_count, 0)" not in source, (
        "the message-sum badge is back on the Unread tab")
    # The per-row badge is the one place the message total is right: it labels a
    # single conversation, where "2" means two unread texts.
    row = source.split("{c.unread_count > 0 && (", 1)[1].split("</span>", 1)[0]
    assert "{c.unread_count}" in row, (
        "the row badge stopped showing the thread's unread message count")


# ---------------- deleting a thread ----------------

def test_the_row_carries_the_whole_timeline_count(api):
    """The confirmation names how much is about to go, so the number has to be on
    the row before anyone clicks. It counts the WHOLE thread — not the unread
    part, and not what the current `filter` happens to be showing."""
    c = api["client"]()
    rows = {r["id"]: r for r in c.get("/api/conversations?tab=all").json()}
    jane = rows[api["ids"]["conv_jane"]]
    assert jane["event_count"] == 4, "two texts, a call and an activity"
    assert jane["unread_count"] == 2, "the count is not the unread count"
    assert rows[api["ids"]["conv_carol"]]["event_count"] == 1


def test_deleting_a_conversation_removes_it_and_its_events(api):
    c = api["client"]()
    conv = api["ids"]["conv_jane"]

    r = c.delete("/api/conversations/%d" % conv)
    assert r.status_code == 200
    assert r.json()["deleted"] == conv
    assert r.json()["events_deleted"] == 4, "the caller is not told what it lost"

    ids = {row["id"] for row in c.get("/api/conversations?tab=all").json()}
    assert conv not in ids
    assert ids == {api["ids"]["conv_bob"], api["ids"]["conv_carol"]}

    db = SessionLocal()
    try:
        assert db.get(Conversation, conv) is None
        left = db.scalars(select(ConversationEvent).where(
            ConversationEvent.conversation_id == conv)).all()
        assert left == [], "the thread is gone but its events are orphaned rows"
    finally:
        db.close()


def test_deleting_a_conversation_keeps_the_contact_and_everything_on_it(api):
    """The rule the contact-delete endpoint states in reverse.

    `Conversation` and `Opportunity` are deliberately not cascade-deleted from
    `Contact`; the same care applies here. An opportunity carries
    `custom_fields.owen_call_id`, the telephony project's live join key, and
    losing one to a mailbox tidy-up would break attribution in another
    production system.
    """
    c = api["client"]()
    ids = api["ids"]
    c.delete("/api/conversations/%d" % ids["conv_jane"])

    db = SessionLocal()
    try:
        contact = db.get(Contact, ids["jane"])
        assert contact is not None, "deleting a thread deleted the customer"
        opp = db.get(Opportunity, ids["opp"])
        assert opp is not None, "deleting a thread deleted an opportunity"
        assert opp.contact_id == ids["jane"], "the opportunity was detached"
        assert opp.custom_fields.get("owen_call_id") == "owen-4417"
        appt = db.get(Appointment, ids["appt"])
        assert appt is not None, "deleting a thread deleted an appointment"
        assert appt.contact_id == ids["jane"]
        # Other people's threads are somebody else's correspondence.
        assert db.get(Conversation, ids["conv_bob"]) is not None
        assert db.scalars(select(ConversationEvent).where(
            ConversationEvent.conversation_id == ids["conv_bob"])).all()
    finally:
        db.close()


@pytest.mark.parametrize("who", ["dispatcher", "tech"])
def test_a_dispatcher_and_a_tech_are_refused_and_nothing_is_removed(api, who):
    """A 403 that had already deleted the events would pass a status check."""
    ids = api["ids"]
    r = api["client"](who).delete("/api/conversations/%d" % ids["conv_jane"])
    assert r.status_code == 403

    db = SessionLocal()
    try:
        assert db.get(Conversation, ids["conv_jane"]) is not None
        assert len(db.scalars(select(ConversationEvent).where(
            ConversationEvent.conversation_id == ids["conv_jane"])).all()) == 4
    finally:
        db.close()

    # ...and it is still there through the API the refused user is looking at.
    rows = api["client"](who).get("/api/conversations?tab=all").json()
    assert ids["conv_jane"] in {r["id"] for r in rows}


def test_deleting_a_conversation_that_does_not_exist_is_a_clean_404(api):
    c = api["client"]()
    missing = max(api["ids"][k] for k in ("conv_jane", "conv_bob", "conv_carol")) + 500
    r = c.delete("/api/conversations/%d" % missing)
    assert r.status_code == 404
    assert r.json()["detail"] == "conversation not found"


def test_deleting_twice_is_a_404_rather_than_a_500(api):
    """The second click of a stale button, and the shape a retry takes."""
    c = api["client"]()
    conv = api["ids"]["conv_jane"]
    assert c.delete("/api/conversations/%d" % conv).status_code == 200
    assert c.delete("/api/conversations/%d" % conv).status_code == 404


# ---------------- the delete control in the page ----------------

def test_the_trash_icon_is_no_longer_labelled_not_implemented():
    source = _page()
    assert "Delete Conversation (not implemented in v1)" not in source, (
        "the trash icon still says it does nothing")
    assert "deleteConversation" in source, "the page cannot delete a conversation"


def test_a_role_that_may_not_delete_gets_a_disabled_control_with_a_reason():
    """Precedent d1f7c50 / b943f4b: never a form that 403s on submit."""
    source = _page()
    assert "const canDelete = user.role === 'ADMIN'" in source, (
        "the page no longer mirrors the ADMIN gate on DELETE /api/conversations")
    blocks = [b.split("</IconBtn>", 1)[0] for b in source.split("<IconBtn")[1:]]
    trash = next(b for b in blocks if "IconTrash" in b)
    assert "disabled={!canDelete" in trash, (
        "a dispatcher or a tech can still click delete and collect a 403")
    assert "Only an admin can delete a conversation" in trash, (
        "the disabled control does not say why it is disabled")


def test_the_confirmation_names_the_contact_and_the_message_count():
    """"Are you sure" tells nobody anything. This is irreversible."""
    source = _page()
    dialog = source.split('aria-label="Delete conversation"', 1)[1] \
                   .split("</div>\n            )}", 1)[0]
    assert "current.contact_name" in dialog, "the confirmation does not name who"
    assert "current.event_count" in dialog, "the confirmation does not say how much"
    assert "cannot be undone" in dialog
    assert "opportunities" in dialog and "appointments" in dialog, (
        "the confirmation does not say what it is NOT deleting")


def test_a_successful_delete_closes_the_thread_and_updates_the_list():
    source = _page()
    body = source.split("const onDelete = async", 1)[1].split("\n  }", 1)[0]
    assert "setSelected(null)" in body, "the pane keeps rendering a dead thread"
    assert "setQueriesData" in body, (
        "the list waits on a poll before the deleted row disappears")
    assert "removeQueries({ queryKey: ['events'" in body, (
        "the deleted thread's timeline is refetched and 404s over a closing pane")
    assert "setDeleteError" in body, "a refused delete is dropped on the floor"
