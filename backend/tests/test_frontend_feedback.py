"""A refused write must be visible to the user.

The backend's role and CSRF checks are correct and well covered by test_auth.py.
What was not covered is the other half: the browser has to *say* that the write was
refused. Several screens dropped a 403 on the floor, and one of them cleared its
input as it did so, which reads as success.

Asserted against source because the project has no JS test runner; the behavioural
verification for each of these is a Playwright run recorded in
orchestrate/findings/fix-log.md.
"""
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def test_contact_panel_renders_a_failed_write():
    """A TECH's PATCH and tag POST both 403; the panel used to show nothing at all."""
    source = _read("components", "ContactDetailsPanel.tsx")
    assert 'role="alert"' in source, "the panel has no error surface"
    for mutation in ("patch", "tagAdd", "tagRemove"):
        assert f"{mutation}.error" in source, (
            f"{mutation}'s failure is never read, so it cannot be shown")


def test_contact_panel_clears_the_tag_input_only_once_the_add_lands():
    """Clearing on keystroke made a refused add look like a successful one."""
    source = _read("components", "ContactDetailsPanel.tsx")
    # The tag input's own handlers: everything between its value and its placeholder.
    # (An inline-edit field earlier in the file has an onKeyDown too.)
    tag_input = source.split("value={newTag}", 1)[1].split('placeholder="Add tag"', 1)[0]
    assert "tagAdd.mutate" in tag_input, "retarget this test — the handler moved"
    assert "setNewTag('')" not in tag_input, (
        "the tag input is cleared on keystroke, so a refused add still reads as success")
    tag_add = source.split("const tagAdd = useMutation({", 1)[1].split("})", 1)[0]
    assert "setNewTag('')" in tag_add, "the input is never cleared on a successful add"


def test_the_telephony_join_key_is_not_an_editable_input():
    """`owen_call_id` sat in the detail form as a plain text input, directly above
    the footnote warning that it is the join key. The form already has the readOnly
    precedent -- Pipeline, Followers and Tags all use it."""
    source = _read("components", "OpportunityDetail.tsx")
    # The custom-field loop, up to the footnote that closes the section.
    custom = source.split("{custom.map(", 1)[1].split("owen_* fields are", 1)[0]
    assert "owen_call_id" in custom, "the custom-field inputs no longer single it out"
    assert "readOnly=" in custom, "the join key is still a freely editable input"


def test_dashboard_cards_show_a_refusal_instead_of_zeros():
    """`/api/dashboard` is STAFF-only. A TECH got a fully drawn dashboard of zeros."""
    source = _read("pages", "DashboardPage.tsx")
    assert 'role="alert"' in source, "no card can report a failed query"
    for card, query in (("Opportunity status", "status"), ("Opportunity value", "value"),
                        ("Conversion rate", "conv")):
        block = source.split(f'<Card title="{card}"', 1)[1].split(">", 1)[0]
        assert f"error={{{query}.error}}" in block, (
            f"the {card} card never reads {query}.error, so a 403 renders as 0")


def test_reporting_says_the_report_failed_rather_than_showing_an_empty_one():
    source = _read("pages", "ReportingPage.tsx")
    assert 'role="alert"' in source, "Reporting has no error surface"
    assert "calls.error" in source and "appts.error" in source, (
        "a refused report is indistinguishable from a genuinely quiet period")


def test_a_refused_request_is_not_retried():
    """A 4xx is a decision, not a blip.

    Retrying one replays a request that cannot succeed and buries the failure
    behind seconds of backoff -- which is why the 403 looked like it was never
    surfaced at all rather than surfaced seven seconds late.
    """
    main = _read("main.tsx")
    retry = main.split("retry:", 1)[1].split("count < 3", 1)[0]
    assert "ApiError" in retry and "status < 500" in retry, (
        "the retry predicate still retries requests the server has refused")


def test_revoking_a_token_asks_first():
    """Revocation is irreversible -- only a sha256 is stored, so a mis-click is
    permanent. The CLI already requires --yes for it; the button fired on one click."""
    source = _read("pages", "SettingsPage.tsx")
    at = source.index("revoke.mutate(t.id)")
    handler = source[source.rindex("<button", 0, at):at]
    assert "confirm(" in handler, "the Revoke button destroys a credential with no prompt"


def test_api_errors_are_turned_into_sentences_not_wire_bodies():
    """Every error surface in the app renders `error.message`, so this is the one
    place that decides whether a user reads "a contact needs at least a phone or an
    email" or `400 {"detail":"a contact needs at least a phone or an email"}`."""
    source = _read("lib", "api.ts")
    assert "function readable(" in source, "no error-message formatting at all"
    failed = source.split("async function failed(", 1)[1].split("}", 1)[0]
    assert "readable(" in failed, "the thrown error still carries the raw wire body"
    assert "r.statusText" not in failed, "statusText tells the user nothing"

    body = source.split("function readable(", 1)[1].split("async function", 1)[0]
    for needed, why in (
        (".detail", "our own HTTPException messages are already written for a person"),
        ("Array.isArray", "422 validation bodies are a list and need assembling"),
        ("REFUSAL", "a body with nothing human in it needs a fallback sentence"),
    ):
        assert needed in body, f"{needed} missing: {why}"


def test_add_contact_is_disabled_for_a_role_that_cannot_create():
    """`POST /api/contacts` is STAFF, so a TECH's create is refused. The button was
    fully enabled, so the only way to find out was to fill the dialog and submit.
    `Import` in the same header is the precedent: disabled, with a title saying why."""
    source = _read("pages", "ContactsPage.tsx")
    assert "user.role !== 'TECH'" in source, (
        "the page never asks whether this role can create a contact")
    # The header button, from its click handler to its label. The dialog further
    # down carries the same words as a heading.
    button = source.split("onClick={() => setShowAdd(true)}", 1)[1].split("Add Contact", 1)[0]
    assert "disabled=" in button, "Add Contact is offered to a role that cannot create"
    assert "title=" in button, "nothing tells the user why the button is dead"


def test_revoking_the_displayed_token_clears_the_mint_panel():
    """The mint panel shows a live secret. Revoking that token kills it, so leaving
    the plaintext on screen above a row reading `revoked` only invites confusion --
    someone copies a credential that can never authenticate again."""
    source = _read("pages", "SettingsPage.tsx")
    assert "useState<{ id: number; token: string } | null>(null)" in source, (
        "the panel stores only the plaintext, so it cannot tell which token was revoked")
    handler = source.split("mutationFn: revokeToken,", 1)[1].split("})", 1)[0]
    assert "minted?.id === id" in handler and "setMinted(null)" in handler, (
        "revoking the displayed token leaves its plaintext on screen")


def test_reporting_says_so_when_the_date_range_is_backwards():
    """`start > end` is legal on the wire: the backend answers 200 with zeros and the
    page draws the same eight tiles of `0` a genuinely quiet week gives. The report
    must not be *requested* for a range that cannot match, and the user must be told
    which of the two they are looking at."""
    source = _read("pages", "ReportingPage.tsx")
    assert "const inverted = startDate > endDate" in source, (
        "nothing compares the two dates, so a backwards range is never noticed")
    assert "function InvertedRange(" in source and 'role="alert"' in source, (
        "there is no surface saying the range is backwards")
    for query in ("'call' && !inverted", "'appointment' && !inverted"):
        assert query in source, (
            f"the {query.split(' ')[0]} report still asks the server for an empty range")
    assert source.count("<InvertedRange start={startDate} end={endDate} />") == 2, (
        "both the call and the appointment tab share the range, so both must warn")


# ---------------- the Contact Details "Actions" tab ----------------
#
# The tab shipped as the words "Actions are not implemented in v1." It now holds
# exactly one action, Delete contact, backed by DELETE /api/contacts/{id}. The
# backend behaviour is covered in test_api.py and test_auth.py; what is asserted
# here is that the panel drives that endpoint the way the endpoint is written --
# in particular that its 409 is treated as a confirmation step and not as a
# failure to swallow.

def _actions_tab() -> str:
    """The Actions branch of the panel, from its guard to the closing of the tab."""
    source = _read("components", "ContactDetailsPanel.tsx")
    assert "{tab === 'Actions' && (" in source, "retarget this test -- the tab moved"
    return source.split("{tab === 'Actions' && (", 1)[1].split("{tab === '", 1)[0]


def test_the_actions_tab_actually_offers_the_delete():
    tab = _actions_tab()
    source = _read("components", "ContactDetailsPanel.tsx")
    assert "Actions are not implemented in v1" not in source, (
        "the placeholder is still on screen")
    assert "Delete contact" in tab, "the tab offers no delete"
    assert "deleteContact" in source, "nothing in the panel calls the delete endpoint"


def test_the_actions_tab_invented_no_action_ghl_was_never_measured_for():
    """GHL's Actions tab was never captured (DECISIONS.md, 2026-09-09), so one
    action backed by a real endpoint is the whole of it. A merge/export/workflow
    menu here would be a guess presented as parity."""
    tab = _actions_tab()
    for absent in ("Merge", "Export", "workflow", "Add to campaign", "Bulk"):
        assert absent not in tab, (
            "%r appeared in the Actions tab and was never measured on GHL" % absent)


def test_delete_is_disabled_for_a_role_that_cannot_delete():
    """`DELETE /api/contacts/{id}` is auth.ADMIN. A DISPATCHER or TECH given a live
    button can only ever click through to a 403 -- the same trap d1f7c50 closed on
    Add Contact, and the same fix: disabled, with a title saying why."""
    source = _read("components", "ContactDetailsPanel.tsx")
    assert "const canDelete = user.role === 'ADMIN'" in source, (
        "the panel never asks whether this role may delete")
    button = _actions_tab().split("setConfirm('plain')", 1)[1].split(
        "Delete contact", 1)[0]
    assert "disabled={!canDelete}" in button, (
        "Delete contact is live for a role the backend will refuse")
    assert "title={canDelete ?" in button, "nothing tells them why it is dead"


def test_deleting_always_asks_first_and_the_first_ask_does_not_force():
    """Two separate guarantees.

    Deletion is irreversible, so even a contact with nothing attached is confirmed
    once. And that first confirm must send force=false: forcing straight away would
    detach opportunities without the user ever being told there were any, which is
    the entire reason the endpoint answers 409.
    """
    tab = _actions_tab()
    assert "setConfirm('plain')" in tab, "the delete button fires without asking"
    assert "del.mutate(confirm === 'detach')" in tab, (
        "the confirm button does not derive force from which confirmation was shown")


def test_the_409_becomes_a_confirmation_naming_the_opportunities():
    """The 409 is the endpoint refusing to detach opportunities behind the user's
    back. Rendered as a red error it reads as "delete is broken"; the user needs to
    be told which opportunities survive and asked again."""
    source = _read("components", "ContactDetailsPanel.tsx")
    handler = source.split("const del = useMutation({", 1)[1].split(
        "const fields = [", 1)[0]
    assert "e.status === 409" in handler and "setConfirm('detach')" in handler, (
        "a 409 is not turned into the detach confirmation")
    assert "setDeleteError(null)" in handler.split("setConfirm('detach')", 1)[1].split(
        "return", 1)[0], "the 409 is also left sitting in the error banner"

    assert "function detachSentence(" in source, (
        "nothing composes the sentence that names the opportunities")
    sentence = source.split("function detachSentence(", 1)[1]
    assert "opps.length === 1" in sentence, (
        "'1 opportunities' -- the same plural bug ce48b4b fixed in the count pill")
    assert "will be kept" in sentence and "detached" in sentence, (
        "the sentence does not say the opportunities survive")

    # It names them from the contact's own list, not by parsing the error prose.
    assert "opportunities: ContactOpportunity[]" in _read("lib", "api.ts"), (
        "the loaded contact does not carry its opportunities, so the only way to "
        "name them is to scrape the 409's wording")


def test_the_delete_does_not_ride_the_panels_shared_write_banner():
    """`writeError` is rendered as a red alert at the top of the panel. Joining the
    delete to it would paint the 409 -- a step in the flow -- as a failure."""
    source = _read("components", "ContactDetailsPanel.tsx")
    line = [ln for ln in source.splitlines() if ln.startswith("  const writeError")]
    assert len(line) == 1, "retarget this test -- writeError moved"
    assert "del.error" not in line[0], "the delete's 409 renders as a write failure"
    assert 'role="alert"' in _actions_tab(), (
        "a delete that fails for a real reason has no surface of its own")


def test_a_successful_delete_clears_the_panel_and_the_list():
    """The user must not be left looking at a contact that no longer exists, and the
    row must go without a manual refresh."""
    source = _read("components", "ContactDetailsPanel.tsx")
    ok = source.split("const del = useMutation({", 1)[1].split("onError:", 1)[0]
    assert "onDeleted?.()" in ok, "nothing tells the host the contact is gone"
    assert "removeQueries({ queryKey: ['contact', contactId] })" in ok, (
        "invalidating the dead contact refetches it and 404s over the closing panel")
    for key in ("['contacts']", "['conversations']", "['opportunities']"):
        assert "invalidateQueries({ queryKey: %s })" % key in ok, (
            "%s still shows the deleted contact until a manual refresh" % key)

    # Both hosts of the panel have to act on it: the Contacts list keeps the open
    # contact id, and Conversations keeps the selected thread -- whose conversation
    # was deleted along with the contact.
    assert "onDeleted={() => setOpenContact(null)}" in _read("pages", "ContactsPage.tsx")
    assert "onDeleted={() => setSelected(null)}" in _read("pages", "ConversationsPage.tsx")


def test_delete_is_offered_nowhere_but_the_actions_tab():
    """Bulk delete was deliberately deferred: this CRM is in real daily use and the
    list checkboxes are the easiest way to lose real customer records."""
    for page in ("ContactsPage.tsx", "ConversationsPage.tsx"):
        source = _read("pages", page)
        assert "deleteContact" not in source, (
            "%s can delete a contact outside the Actions tab" % page)
    contacts = _read("pages", "ContactsPage.tsx")
    checkbox = contacts.split('<input type="checkbox" />', 1)[0].rsplit("<td", 1)[1]
    assert "onClick={(e) => e.stopPropagation()}" in checkbox, (
        "retarget this test -- the row checkbox moved")
    assert "checked=" not in checkbox and "onChange=" not in checkbox, (
        "the row checkboxes were wired up; bulk actions are out of scope")
