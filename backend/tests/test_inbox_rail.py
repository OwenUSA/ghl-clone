"""The Conversations icon rail: scope, in-place search, and the active indicator.

Six icons that rendered with labels and no handlers at all. Three were genuinely
missing controls (Search, Assigned to me, Team inbox); two duplicated controls
that already exist three inches away, so they drive THE SAME state rather than a
parallel copy; one is left disabled with its reason on hover.

The rule these tests exist to pin is the last one. "Unread only" on the rail and
the "Unread" tab are one `tab`, not two flags — two flags is how a screen ends up
with a highlighted filter and an unfiltered list, and nothing on screen says
which of them is lying.

`frontend/src/lib/inbox.ts` imports nothing, so node executes the shipped
`railActive` and `emptyInboxMessage` rather than this file matching the page
source for a colour.
"""
from datetime import UTC, datetime, timedelta

import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    EventType,
    Role,
    User,
)
from fastapi.testclient import TestClient
from test_inbox import FRONTEND, node, run_js


def utcnow():
    return datetime.now(UTC)


@pytest.fixture()
def api():
    """Two staff who each own one contact, plus a contact nobody owns.

    That third contact is what makes "assigned to me" a real claim: the two
    per-user sets are disjoint AND neither of them is the whole inbox, so a
    filter that quietly did nothing would fail on the count as well as on the
    ids.
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    owen = User(email="owen@x.test", name="Owen", role=Role.ADMIN)
    dana = User(email="dana@x.test", name="Dana", role=Role.DISPATCHER)
    tess = User(email="tess@x.test", name="Tess", role=Role.TECH)
    db.add_all([owen, dana, tess])
    db.flush()

    jane = Contact(first_name="Jane", last_name="Doe", phone="+18135550102",
                   email="jane@roofmail.test", owner_id=owen.id)
    bob = Contact(first_name="Bob", last_name="Reyes", phone="(941) 555-0202",
                  email="bob@roofmail.test", owner_id=dana.id)
    nobody = Contact(first_name="Carol", last_name="Nunez", phone="(941) 555-0303",
                     email="carol@roofmail.test")
    db.add_all([jane, bob, nobody])
    db.flush()

    convs = {}
    for key, contact, unread in (("jane", jane, 2), ("bob", bob, 1),
                                 ("carol", nobody, 3)):
        c = Conversation(contact_id=contact.id, last_event_at=utcnow(),
                         unread_count=unread)
        db.add(c)
        convs[key] = c
    db.flush()
    for c in convs.values():
        db.add(ConversationEvent(conversation_id=c.id, type=EventType.SMS,
                                 direction=Direction.INBOUND, body="hello",
                                 occurred_at=utcnow() - timedelta(minutes=5)))

    tokens = {}
    for key, u in (("owen", owen), ("dana", dana), ("tess", tess)):
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        tokens[key] = plain
    db.commit()

    ids = {"owen": owen.id, "dana": dana.id, "tess": tess.id,
           "jane": jane.id, "bob": bob.id, "carol": nobody.id,
           **{"conv_" + k: v.id for k, v in convs.items()}}
    db.close()

    def client(who="owen"):
        c = TestClient(app)
        c.headers["Authorization"] = "Bearer " + tokens[who]
        return c

    yield {"client": client, "ids": ids}


def rows(client, **params):
    q = "&".join("%s=%s" % kv for kv in params.items())
    r = client.get("/api/conversations?" + q)
    assert r.status_code == 200, r.text
    return [row["id"] for row in r.json()]


# ---------------- Assigned to me / Team inbox ----------------

def test_assigned_to_me_is_the_contacts_owner_and_differs_per_user(api):
    """Two users, two owned contacts. A filter that did nothing would give both
    of them the same three rows."""
    ids = api["ids"]
    owen = rows(api["client"]("owen"), assigned="me")
    dana = rows(api["client"]("dana"), assigned="me")

    assert owen == [ids["conv_jane"]]
    assert dana == [ids["conv_bob"]]
    assert not set(owen) & set(dana), "the two users' inboxes overlap"


def test_a_contact_nobody_owns_is_in_nobodys_assigned_inbox(api):
    """`owner_id IS NULL` must not equal a user id. An unowned contact belongs to
    the team inbox and to no one's own."""
    ids = api["ids"]
    for who in ("owen", "dana", "tess"):
        assert ids["conv_carol"] not in rows(api["client"](who), assigned="me")
    assert ids["conv_carol"] in rows(api["client"]("owen"), assigned="all")


def test_assigned_to_me_is_empty_for_someone_who_owns_nothing(api):
    """The empty state has to have something to render."""
    assert rows(api["client"]("tess"), assigned="me") == []


def test_team_inbox_is_every_conversation(api):
    ids = api["ids"]
    expected = {ids["conv_jane"], ids["conv_bob"], ids["conv_carol"]}
    assert set(rows(api["client"]("owen"), assigned="all")) == expected
    # ...and it is the default, so the rail restores today's behaviour.
    assert set(rows(api["client"]("owen"))) == expected


def test_the_scope_widens_nothing_for_a_tech(api):
    """A TECH sees what a TECH saw before: this endpoint is ANY_USER and the
    rail only ever narrows it."""
    assert set(rows(api["client"]("tess"))) == set(rows(api["client"]("owen")))


# ---------------- in-place search ----------------

def test_search_narrows_the_list_and_clearing_it_restores_the_list(api):
    ids = api["ids"]
    c = api["client"]("owen")
    everything = rows(c)
    assert len(everything) == 3

    assert rows(c, q="Reyes") == [ids["conv_bob"]]
    assert rows(c, q="jane") == [ids["conv_jane"]]
    # Clearing the box sends no `q` at all, which is what the browser does.
    assert rows(c) == everything


def test_search_matches_a_full_name_and_is_case_insensitive(api):
    ids = api["ids"]
    c = api["client"]("owen")
    assert rows(c, q="jane%20doe") == [ids["conv_jane"]]
    assert rows(c, q="BOB") == [ids["conv_bob"]]


def test_search_finds_a_number_typed_in_a_different_shape_than_it_is_stored(api):
    """Jane's number is stored E.164 (`+18135550102`) and nobody types it that
    way. The last-ten-digits rule in app/phone_match.py is what makes the number
    on a caller ID find the row."""
    ids = api["ids"]
    c = api["client"]("owen")
    assert rows(c, q="(813)%20555-0102") == [ids["conv_jane"]]
    assert rows(c, q="8135550102") == [ids["conv_jane"]]


def test_search_matching_nothing_is_an_empty_list_not_an_error(api):
    assert rows(api["client"]("owen"), q="nobody-by-that-name") == []


def test_search_and_scope_and_tab_intersect(api):
    """"My conversations, unread, matching Doe" is one query, not three screens."""
    ids = api["ids"]
    c = api["client"]("owen")
    assert rows(c, assigned="me", tab="unread", q="Doe") == [ids["conv_jane"]]
    # Owen does not own Bob's contact, so scope wins over a matching search.
    assert rows(c, assigned="me", q="Reyes") == []
    # ...and the search wins over a matching scope.
    assert rows(c, assigned="me", q="Reyes", tab="all") == []


def test_the_unread_tab_still_narrows_inside_a_scope(api):
    ids = api["ids"]
    c = api["client"]("dana")
    assert rows(c, assigned="me", tab="unread") == [ids["conv_bob"]]
    # Dana's one contact is unread, so starred (which none are) must be empty —
    # otherwise "the tab narrowed" is unproven.
    assert rows(c, assigned="me", tab="starred") == []


# ---------------- one piece of state, not two ----------------

def test_the_rail_and_the_tab_ask_the_server_the_same_question(api):
    """There is one `tab` parameter. The rail cannot produce a different set
    because there is no second way to ask."""
    c = api["client"]("owen")
    assert rows(c, tab="unread") == rows(c, tab="unread")
    ids = set(rows(c, tab="unread"))
    assert ids == {api["ids"]["conv_jane"], api["ids"]["conv_bob"],
                   api["ids"]["conv_carol"]}


def _page() -> str:
    return (FRONTEND / "pages" / "ConversationsPage.tsx").read_text(encoding="utf-8")


def test_unread_only_writes_the_tab_state_rather_than_a_flag_of_its_own():
    """The forked-state failure this whole section exists to prevent."""
    source = _page()
    rail = source.split("const onRail =", 1)[1].split("\n  }", 1)[0]
    assert "setTab(" in rail, (
        "the rail's Unread only no longer drives the tab row's own state")
    for forked in ("unreadOnly", "setUnreadOnly", "onlyUnread"):
        assert forked not in source, (
            "%s is a second copy of the Unread filter — it will drift from the "
            "tab, and the one nobody fixes wins silently" % forked)
    # ...and the highlight is read from that same state, not from a stored index.
    assert "railActive(r.key, { scope, tab, searching })" in source, (
        "the rail highlight is no longer derived from the applied filters")
    assert "const [rail, setRail]" not in source, (
        "the old rail index is back — it is a highlight nothing else reads")


def test_the_scope_is_the_only_state_the_rail_owns():
    """Everything else it touches already belongs to a control on screen."""
    source = _page()
    assert "const [scope, setScope] = useState<InboxScope>('team')" in source
    assert "scope === 'mine' ? 'me' : 'all'" in source, (
        "the scope no longer reaches the query")
    key = source.split("queryKey: ['conversations',", 1)[1].split("]", 1)[0]
    assert "scope" in key and "q" in key, (
        "scope and the search term are not in the query key, so switching either "
        "shows the previous list while the new one loads: %s" % key)


def test_the_search_box_appears_only_when_the_search_is_open():
    """This screen is measured. Nothing may move until the operator asks for it."""
    source = _page()
    assert "{searching && (" in source, (
        "the search input is rendered unconditionally, which moves the measured "
        "list pane for everyone")


# ---------------- the active indicator ----------------

@node
def test_the_active_indicator_matches_the_filter_actually_applied():
    r = run_js("""
        const keys = ['conversations','search','mine','team','filters','unread']
        const lit = (s) => keys.filter((k) => inbox.railActive(k, s))
        out({
          fresh: lit({scope: 'team', tab: 'all', searching: false}),
          mine: lit({scope: 'mine', tab: 'all', searching: false}),
          mineUnread: lit({scope: 'mine', tab: 'unread', searching: false}),
          searching: lit({scope: 'team', tab: 'all', searching: true}),
          starred: lit({scope: 'team', tab: 'starred', searching: false}),
        })
    """)
    assert r["fresh"] == ["conversations", "team"]
    assert r["mine"] == ["conversations", "mine"], "the scope indicator is wrong"
    # Scope and tab compose, so both are lit — they are independent facts.
    assert r["mineUnread"] == ["conversations", "mine", "unread"]
    assert r["searching"] == ["conversations", "search", "team"]
    # A tab that is not Unread must not light the Unread-only row.
    assert "unread" not in r["starred"]
    # Filters is disabled, so it is never lit — it was the permanently-highlighted
    # kind of lie this replaces.
    for state in r.values():
        assert "filters" not in state


@node
def test_the_two_scopes_are_never_lit_together():
    r = run_js("""
        const both = (s) => [inbox.railActive('team', s), inbox.railActive('mine', s)]
        out({team: both({scope:'team', tab:'all', searching:false}),
             mine: both({scope:'mine', tab:'all', searching:false})})
    """)
    assert r == {"team": [True, False], "mine": [False, True]}


def test_the_filters_row_is_disabled_and_says_why():
    source = _page()
    assert "blocked:" in source, "the Filters row no longer carries a reason"
    assert "disabled={Boolean(blocked)}" in source, (
        "the dead Filters row is clickable again")
    assert "title={blocked ?? r.label}" in source, (
        "the disabled row does not explain itself on hover")


# ---------------- empty states ----------------

@node
def test_every_way_of_emptying_the_inbox_says_which_one_it_was():
    r = run_js("""
        const m = (s) => inbox.emptyInboxMessage(s)
        out({
          mine: m({scope:'mine', tab:'all', searching:false, q:''}),
          mineUnread: m({scope:'mine', tab:'unread', searching:false, q:''}),
          search: m({scope:'team', tab:'all', searching:true, q:'  reyes '}),
          searchInMine: m({scope:'mine', tab:'all', searching:true, q:'reyes'}),
          team: m({scope:'team', tab:'unread', searching:false, q:''}),
        })
    """)
    assert "assigned to you" in r["mine"], (
        "an empty Assigned to me renders nothing that explains itself")
    assert "Owner" in r["mine"], "it does not say how a contact gets assigned"
    assert "unread" in r["mineUnread"] and "assigned to you" in r["mineUnread"]
    # The query is quoted back, trimmed, so the user can see what was searched.
    assert r["search"] == "No conversations match “reyes”."
    assert r["searchInMine"] == "No conversations match “reyes”.", (
        "a search that matched nothing is the specific answer, even inside a scope")
    assert r["team"] == "No conversations in unread."


def test_the_page_renders_the_empty_state_rather_than_a_blank_pane():
    source = _page()
    assert "emptyInboxMessage({ scope, tab, searching, q })" in source, (
        "the list no longer explains an empty result")
    assert "No conversations in {tab}." not in source, (
        "the old one-size empty message is back")


def test_the_two_search_controls_do_not_share_an_accessible_name():
    """The sidebar's ctrl+K row is also called "Search", and they do different
    jobs — that one finds anything anywhere, this one narrows the list beside
    it. Sharing one name makes them indistinguishable to a screen reader and to
    anything driving the page by role and name."""
    rail_entry = _page().split("key: 'search'", 1)[1].split("},", 1)[0]
    assert "aria: 'Search this inbox'" in rail_entry, (
        "the rail's Search is announced as plain 'Search', the same name the "
        "sidebar's palette row already uses")
    sidebar = (FRONTEND / "components" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert 'aria-label="Search"' in sidebar, (
        "retarget this test — the sidebar's Search no longer claims that name")
