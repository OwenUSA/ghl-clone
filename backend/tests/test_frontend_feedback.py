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


def test_add_opportunity_is_disabled_for_a_role_that_cannot_create():
    """`POST /api/opportunities` is STAFF. Same precedent as Add Contact: do not
    offer a role a form it will only be refused at the end of."""
    source = _read("pages", "OpportunitiesPage.tsx")
    assert "user.role !== 'TECH'" in source, (
        "the page never asks whether this role can create an opportunity")
    button = source.split("onClick={() => setShowAdd(true)}", 1)[1].split(
        "Add opportunity", 1)[0]
    assert "disabled={!canCreate" in button, (
        "Add opportunity is offered to a role that cannot create one")
    assert "title=" in button, "nothing tells the user why the button is dead"


def test_the_add_opportunity_dialog_survives_a_refused_create():
    """A failed POST must leave the dialog open with a sentence in it, not close as
    though it worked. `onDone` is what closes the dialog, so it must be reachable
    only from onSuccess."""
    source = _read("pages", "OpportunitiesPage.tsx")
    # to the end of the useMutation call - its inner braces are indented deeper
    mutation = source.split("const create = useMutation({", 1)[1].split(
        "\n  })", 1)[0]
    assert "onSuccess: () => onDone(" in mutation, "success never closes the dialog"
    assert "onError: (e: Error) => setError(e.message)" in mutation, (
        "the failure is dropped, so a refused create looks like nothing happened")
    # e.message is the sentence ApiError was given by readable(); the raw body
    # never reaches the dialog.
    assert "setError(error" not in mutation and "JSON.stringify" not in mutation
    dialog = source.split("function AddOpportunityDialog(", 1)[1]
    assert "{error && (" in dialog, "the dialog has nowhere to show the message"


def test_the_add_opportunity_dialog_cannot_be_submitted_twice():
    """Nothing on the server dedupes a create, so two clicks are two roofs on the
    board. The button has to be dead while the POST is in flight."""
    source = _read("pages", "OpportunitiesPage.tsx")
    submit = source.split("onClick={() => { setError(null); create.mutate() }}", 1)[1]
    submit = submit.split("Create", 1)[0]
    assert "disabled={!ready || create.isPending}" in submit, (
        "a second click while the first POST is in flight creates a second opportunity")


def test_a_new_opportunity_shows_up_on_the_board():
    """The card has to appear without a refresh, and it has to be visible: it may
    have been filed into another pipeline, and a board filtered to Won or Lost hides
    a new (always Open) opportunity entirely."""
    source = _read("pages", "OpportunitiesPage.tsx")
    done = source.split("onDone={(createdInPipelineId) => {", 1)[1].split("}}", 1)[0]
    assert "invalidateQueries({ queryKey: ['opportunities'] })" in done, (
        "the board is never refetched, so the new card only appears on reload")
    assert "invalidateQueries({ queryKey: ['pipelines'] })" in done, (
        "the stage header count and total keep their old numbers")
    assert "setPipelineId(createdInPipelineId)" in done, (
        "an opportunity filed into another pipeline vanishes on create")
    assert "setStatus('open')" in done, (
        "a board filtered to Won or Lost never shows the new card")


def test_the_stage_select_follows_the_pipeline():
    """A stage from the previous pipeline is a pair the backend refuses, so leaving
    it selected turns a pipeline change into a submit-time error."""
    source = _read("pages", "OpportunitiesPage.tsx")
    dialog = source.split("function AddOpportunityDialog(", 1)[1]
    change = dialog.split("onChange={(e) => { setPipelineId(", 1)[1].split("}}", 1)[0]
    assert "setStageId(null)" in change, "the stage keeps pointing at the old pipeline"
    assert "const stage = stages.find((s) => s.id === stageId) ?? stages[0]" in dialog, (
        "the stage does not default to the first stage of the chosen pipeline")


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


def test_new_appointment_is_disabled_for_a_role_that_cannot_create():
    """`POST /api/appointments` is STAFF, so a TECH's create is refused and the
    backend writes nothing. Offering the form anyway means filling seven fields
    to find out. Same gate, and same `disabled` + `title` shape, as Add Contact."""
    source = _read("pages", "CalendarsPage.tsx")
    assert "user.role !== 'TECH'" in source, (
        "the page never asks whether this role can create an appointment")
    # The whole toolbar button: from its opening tag to its label.
    at = source.index("            New\n")
    button = source[source.rindex("<button", 0, at):at]
    assert "book(defaultSlot(dayList, now))" in button, "retarget this — New moved"
    assert "disabled={!canCreate}" in button, (
        "New is offered to a role that cannot create")
    assert "title={canCreate" in button, "nothing tells the user why the button is dead"
    # ...and the double-click entry point has to be gated by the same flag, or a
    # TECH reaches the identical dialog by a different door.
    book = source.split("function book(at: Date) {", 1)[1].split("}", 1)[0]
    assert "if (!canCreate) return" in book, (
        "a TECH can still double-click a slot open, bypassing the disabled button")


def test_the_appointment_dialog_shows_a_refused_create_and_stays_open():
    """A 403 or a 400 has to land in the dialog as a sentence, with the typed
    values still there. Closing on failure throws away the form; printing the
    wire body is how someone read `500 Internal Server Error` in a dialog."""
    source = _read("components", "NewAppointmentDialog.tsx")
    assert 'role="alert"' in source, "the dialog has no error surface"
    mutation = source.split("const create = useMutation({", 1)[1].split("\n  })", 1)[0]
    assert "onError: (e: Error) => setError(e.message)" in mutation, (
        "a refused create is dropped on the floor")
    assert "onClose" not in mutation, (
        "the dialog closes on failure, discarding what the user typed")
    # `e.message` is a sentence only because api.ts made it one.
    assert "readable(" in _read("lib", "api.ts"), "retarget this — the formatter moved"


def test_the_appointment_dialog_offers_only_fields_the_backend_accepts():
    """`AppointmentCreate` in backend/app/main.py is the contract. A control the
    server ignores is worse than no control: `status` in particular would look
    like a choice and always land on `confirmed`."""
    api = _read("lib", "api.ts")
    body = api.split("export const createAppointment = (body: {", 1)[1].split("}", 1)[0]
    sent = {line.split(":")[0].strip("? ").strip() for line in body.splitlines()
            if ":" in line}
    accepted = {"title", "starts_at", "ends_at", "contact_id", "assigned_user_id",
                "calendar_id", "notes"}
    assert sent == accepted, (
        f"createAppointment sends {sorted(sent)}; POST /api/appointments accepts "
        f"{sorted(accepted)}")

    dialog = _read("components", "NewAppointmentDialog.tsx")
    status = dialog.split('<Field label="Status">', 1)[1].split("</Field>", 1)[0]
    assert "disabled" in status and "title=" in status, (
        "the Status control is either live (and ignored by the server) or hidden "
        "(and the default it lands on is invisible); it must be shown disabled "
        "with a title saying why")


def test_the_appointment_dialog_picks_a_contact_by_searching_for_one():
    """Asking for a contact id would ask the user to know a primary key, and a
    <select> would render every contact in the account (268 locally).

    The picker now lives in its own module because the detail panel asks the same
    question — see test_the_contact_picker_is_one_component_not_two below."""
    dialog = _read("components", "NewAppointmentDialog.tsx")
    assert "<ContactPicker" in dialog, "the create dialog has no contact picker"
    picker = _read("components", "ContactPicker.tsx")
    assert "function ContactPicker(" in picker, "there is no contact picker"
    assert "listContacts(" in picker, (
        "the picker does not search — it is not backed by the contacts endpoint")
    assert "enabled: q.trim().length > 0" in picker, (
        "the picker queries on an empty box, listing contacts nobody asked for")
    assert "onPick({ id: c.id, name: c.name })" in picker, (
        "picking a result does not record which contact was chosen")


def test_the_first_time_card_does_not_repeat_the_whole_windows_durations():
    """Both call cards were handed `avg_duration_seconds` / `total_duration_seconds`,
    so "First-time calls by status" showed the duration of EVERY call under a label
    saying otherwise. Two cards reading identically is exactly what made the screen
    look synthetic; the backend now returns the first-time figures separately."""
    source = _read("pages", "ReportingPage.tsx")
    first_card = source.split('<Card title="First-time calls by status">', 1)[1] \
                       .split("</Card>", 1)[0]
    assert "first_time_avg_duration_seconds" in first_card, (
        "the first-time card is still showing the whole window's average")
    assert "first_time_total_duration_seconds" in first_card, (
        "the first-time card is still showing the whole window's total")
    # The card above it keeps the unqualified figures — that one is every call.
    by_status_card = source.split('<Card title="Call by status">', 1)[1] \
                           .split("</Card>", 1)[0]
    assert "first_time_" not in by_status_card, (
        "'Call by status' is every call, not just the first-time ones")


def test_a_refused_card_move_snaps_the_card_back_and_says_why():
    """The board moves a card the instant it is dropped. That is only safe if a
    refusal puts it back — the failure being guarded against is 20cc838, where
    every drag was rejected for a missing CSRF token while the board went on
    showing the card in its new column until the next reload.

    The behavioural half of this is backend/tests/test_board_order.py, which runs
    the real prediction against the real endpoint. What is asserted here is the
    part that only exists in the page: the rollback, and the sentence."""
    source = _read("pages", "OpportunitiesPage.tsx")
    move = source.split("const move = useMutation({", 1)[1].split("\n  })", 1)[0]

    assert "onMutate" in move, "the card does not move until the server answers"
    assert "cancelQueries" in move, (
        "a refetch already in flight will land on top of the optimistic board")
    assert "getQueryData" in move and "previous" in move, (
        "nothing is snapshotted, so there is nothing to roll back to")
    assert "onError" in move, "a refused move is dropped on the floor"
    error = move.split("onError", 1)[1]
    assert "setQueryData(boardKey, ctx.previous)" in error, (
        "a refused move leaves the card sitting in the column it never reached")
    assert "setMoveError(err.message)" in error, (
        "the refusal is silent — the whole point is that it must not be")
    # `err.message` is api.ts's `readable()` sentence. Rendering the response
    # body is the regression 9d5e1d2 fixed everywhere else.
    for raw in ("await r.text()", "JSON.stringify(err", "String(err)"):
        assert raw not in error, "the wire body is being shown to the user"

    assert 'role="alert"' in source, "the refusal has nowhere to render"
    assert "{moveError}" in source, "the sentence is never drawn"


def test_the_board_sends_the_position_a_card_was_dropped_at():
    """Both halves of the original bug, pinned in the page itself.

    `onDragEnd` returned early on a same-column drop, and its mutate() call
    passed no position at all — so `moveOpportunity`'s default of 0 sent every
    cross-column drop to the top of the target column."""
    source = _read("pages", "OpportunitiesPage.tsx")
    handler = source.split("function onDragEnd(", 1)[1].split("\n  }", 1)[0]
    assert "moveForDrop(" in handler, (
        "the drop is not resolved through lib/boardOrder.ts — retarget this test")
    assert "o.stage_id === stageId" not in source, (
        "a drop back into the card's own column is being discarded again")

    # The column has to be a sortable list, or a drop has no index to report.
    assert "SortableContext" in source and "useSortable(" in source, (
        "the columns are drop targets only, so a reorder has no drop index")
    assert "useDraggable(" not in source, (
        "a card is still a bare draggable, which cannot report where it landed")

    api = _read("lib", "api.ts")
    signature = api.split("export const moveOpportunity =", 1)[1].split("\n", 1)[0]
    assert "position = 0" not in signature, (
        "moveOpportunity still defaults the position, so a caller that forgets "
        "it silently files the card at the top of the column")

# ---------------- the appointment detail panel ----------------
#
# Clicking a booking opens AppointmentDetailDialog. The behaviour behind it is
# covered end-to-end in test_messaging.py (the row, the range query and above all
# the reminder queue) and the wording it reports is executed under node in
# test_reminder_sentence.py. What is asserted here is the wiring the browser owns:
# that a click reaches the panel at all, that a role which cannot write gets dead
# controls rather than a form, that a failure is a sentence, and that a cancel is
# confirmed by a prompt which names the appointment.
#
# THE PANEL'S LAYOUT IS OURS, NOT MEASURED GHL — see the 2026-09-10 amendment in
# DECISIONS.md. Nothing here may be read as a parity assertion.

def _detail_dialog() -> str:
    return _read("components", "AppointmentDetailDialog.tsx")


def test_clicking_an_appointment_opens_its_detail_panel():
    """The blocks used to carry `onDoubleClick` with nothing but
    `stopPropagation` in it — it existed so a double-click ON a booking did not
    create a new one underneath, and a click did nothing at all. Every place a
    booking is drawn has to be a way in, or the panel is unreachable from
    whichever view the user happens to be in."""
    page = _read("pages", "CalendarsPage.tsx")
    assert "AppointmentDetailDialog" in page, "the page never renders the panel"
    assert "function open(id: number, e: React.MouseEvent)" in page, (
        "there is no handler that opens a booking")
    assert "setOpenAppt(id)" in page, "the handler does not open anything"

    # Day/Week block, month chip, and a row of the Appointment list view.
    week_block = page.split("{buckets[i].map((a) => {", 1)[1].split("})}", 1)[0]
    assert "onClick={(ev) => open(a.id, ev)}" in week_block, (
        "a booking on the Day/Week grid still does nothing when clicked")
    # NB the chip's `title` interpolation contains "))}" — split on its closing
    # tag, not on the map's.
    month_chip = page.split("{shown.map((a) => (", 1)[1].split("{hidden > 0", 1)[0]
    assert "onClick={(e) => onOpen(a.id, e)}" in month_chip, (
        "a month-view chip still does nothing when clicked")
    list_row = page.split("{appts.data?.map((a: Appointment) => (", 1)[1] \
                   .split("</tr>", 1)[0]
    assert "onClick={(e) => open(a.id, e)}" in list_row, (
        "a row of the Appointment list view still does nothing when clicked")

    # ...and the click must not also be read as "book this slot": the day column
    # underneath listens for a double-click to create.
    handler = page.split("function open(id: number, e: React.MouseEvent) {", 1)[1] \
                  .split("}", 1)[0]
    assert "e.stopPropagation()" in handler, (
        "the click bubbles to the day column, which books empty slots")


def test_the_panel_draws_the_appointment_it_was_asked_for():
    """It must read the record, not be handed a row from the grid: the grid's
    rows carry no `notes` and no `contact_id`, so a panel built from one could
    not show the notes or re-pick the contact."""
    dialog = _detail_dialog()
    assert "getAppointment(appointmentId)" in dialog, (
        "the panel does not fetch the appointment")
    assert "queryKey: ['appointment', appointmentId]" in dialog, (
        "the panel's query is not keyed on which appointment it is showing")
    # Everything the owner asked the panel to show.
    for field in ('label="Title"', 'label="Contact"', 'label="Calendar"',
                  'label="Starts"', 'label="Ends"', 'label="Status"',
                  'label="Notes"'):
        assert field in dialog, "the panel does not show %s" % field
    page = _read("pages", "CalendarsPage.tsx")
    assert "key={openAppt}" in page, (
        "switching from one booking to another reuses the mounted panel, so an "
        "unsaved draft carries across to a different appointment")


def test_the_panel_only_sends_the_fields_that_changed():
    """`PATCH` is `exclude_unset`, and an unchanged `starts_at` must stay OUT of
    the body: the endpoint treats a start time that arrived and differs as a
    reschedule, and re-queueing the customer's reminders because someone fixed a
    typo in the title is churn at best."""
    dialog = _detail_dialog()
    assert "function changes(): AppointmentPatch | null" in dialog, (
        "nothing works out what actually changed")
    changes = dialog.split("function changes(): AppointmentPatch | null {", 1)[1] \
                    .split("\n  }", 1)[0]
    assert "const was = formOf(a)" in changes, (
        "the diff is not taken against what the server stored")
    for field in ("title", "status", "notes", "contact_id", "calendar_id",
                  "assigned_user_id", "starts_at", "ends_at"):
        assert field in changes, "%s can never be edited" % field
    # Both ends travel together, or the backend validates a new start against an
    # old end the user can no longer see.
    ends = changes.split("body.starts_at", 1)[1]
    assert "body.ends_at" in ends, (
        "a reschedule can send a new start with the stored end")
    # Save is dead until something has actually changed.
    assert "const dirty = !!body && Object.keys(body).length > 0" in dialog
    save = dialog.split("onClick={submit}", 1)[1].split("Save changes", 1)[0]
    assert "disabled={!canWrite || !dirty || save.isPending}" in save, (
        "Save is live with nothing to save, or can be double-submitted")


def test_a_reschedule_reports_what_happened_to_the_reminders():
    """The reason this task was deferred once. A reminder that silently did not
    follow the appointment is indistinguishable from one that did, so the panel
    has to say. `automation` is the backend's own answer."""
    dialog = _detail_dialog()
    assert "reminderSentence(updated.automation)" in dialog, (
        "the panel throws away the backend's reminder outcome")
    assert 'role="status"' in dialog, "there is nowhere to show it"
    assert "reminders_cancelled" in dialog, (
        "cancelling never says whether a pending reminder was withdrawn")
    # The wording itself is import-free and executed under node.
    assert "from '../lib/reminders'" in dialog


def test_the_panel_refreshes_the_grid_after_a_write():
    """An edited title, and above all a rescheduled booking, has to redraw
    without a manual refresh — and a reschedule may have moved it out of the
    window the current query key asks for, so one key is not enough."""
    page = _read("pages", "CalendarsPage.tsx")
    changed = page.split("onChanged={() => {", 1)[1].split("}}", 1)[0]
    assert "invalidateQueries({ queryKey: ['appointments'] })" in changed, (
        "the grid keeps drawing the booking in its old slot with its old title")


def test_editing_is_disabled_for_a_role_that_cannot_edit():
    """`PATCH` and `DELETE /api/appointments/{id}` are both `auth.STAFF`, and
    this work did not widen them. So a TECH gets dead controls with a title
    saying why — the precedent d1f7c50 and b943f4b set — not a form that fails
    on submit. Reading is `ANY_USER`, so the panel still opens."""
    dialog = _detail_dialog()
    assert "const canWrite = user.role !== 'TECH'" in dialog, (
        "the panel never asks whether this role may edit")
    save = dialog.split("onClick={submit}", 1)[1].split("Save changes", 1)[0]
    assert "disabled={!canWrite" in save and "title={canWrite" in save, (
        "Save changes is offered to a role the backend will refuse")
    cancel = dialog.split("setConfirmingCancel(true) }}", 1)[1] \
                   .split("Cancel appointment", 1)[0]
    assert "disabled={!canWrite" in cancel and "title={canWrite" in cancel, (
        "Cancel appointment is offered to a role the backend will refuse")
    # Every input, too: a form that accepts typing and then has no live Save is
    # its own kind of lie.
    assert dialog.count("disabled={!canWrite}") >= 8, (
        "the fields are still editable for a role that cannot save them")
    # ...and the page must not gate the click itself, or a TECH cannot even read
    # the job they are driving to.
    page = _read("pages", "CalendarsPage.tsx")
    handler = page.split("function open(id: number, e: React.MouseEvent) {", 1)[1] \
                  .split("}", 1)[0]
    assert "canCreate" not in handler, (
        "opening a booking to READ it was gated on the write role")


def test_a_refused_edit_shows_a_sentence_and_keeps_the_form():
    """A 400 or a 403 has to land in the panel as a readable sentence with the
    typed values still there. Printing the wire body is how someone read
    `500 Internal Server Error` in a dialog (9d5e1d2)."""
    dialog = _detail_dialog()
    assert 'role="alert"' in dialog, "the panel has no error surface"
    mutation = dialog.split("const save = useMutation({", 1)[1].split("\n  })", 1)[0]
    assert "onError: (e: Error) => { setSaved(null); setError(e.message) }" in mutation, (
        "a refused edit is dropped on the floor, or leaves a stale success line")
    assert "onClose" not in mutation, (
        "the panel closes on failure, discarding what the user typed")
    assert "JSON.stringify" not in dialog, "the wire body can reach the screen"
    assert "readable(" in _read("lib", "api.ts"), "retarget this — the formatter moved"


def test_cancelling_is_confirmed_by_a_prompt_that_names_the_appointment():
    """Cancelling is irreversible from here and it withdraws the customer's
    reminder. A bare "are you sure" over a calendar of similar-looking blocks
    does not tell the user which one they are about to cancel."""
    dialog = _detail_dialog()
    assert "confirmingCancel" in dialog, "the cancel button fires without asking"
    prompt = dialog.split("{confirmingCancel ? (", 1)[1].split("Keep it", 1)[0]
    assert "{a.title}" in prompt, "the confirmation does not name the appointment"
    assert "{a.contact_name" in prompt, "the confirmation does not name the customer"
    assert "when(new Date(a.starts_at))" in prompt, (
        "the confirmation does not say which slot is being cancelled")
    assert "cannot be undone" in prompt
    assert "reminder" in prompt, (
        "the confirmation does not say the customer's reminder goes with it")
    # Only the second click writes.
    assert "onClick={() => cancel.mutate()}" in dialog
    assert dialog.count("cancel.mutate()") == 1, (
        "there is a second, unconfirmed path to cancelling")


def test_a_saved_edit_reaches_the_confirmation_that_names_the_appointment():
    """A real bug, found by driving the panel in a browser.

    The summary line and the cancel confirmation are written from the LOADED
    record (`a`), not from the draft. A save re-seeded the draft and left `a`
    stale until the 30s refetch — so editing a booking and then cancelling it
    showed "Cancel Roof inspection … on Sun, Sep 13, 2:00 PM?" for a booking that
    had just been renamed and moved to Thursday. A destructive prompt describing
    a slot that no longer exists is worse than no prompt: it is a prompt that
    reads as being about a different appointment.

    The PATCH response IS the fresh record, so it is written into the cache
    rather than invalidated — invalidating would round-trip to learn what the
    server just said, leaving the stale window open in the middle of it.
    """
    dialog = _detail_dialog()
    ok = dialog.split("const save = useMutation({", 1)[1].split("onError:", 1)[0]
    assert "qc.setQueryData(['appointment', appointmentId], updated)" in ok, (
        "a saved edit never reaches the loaded record, so the cancel "
        "confirmation and the summary line keep naming the old time")
    assert "invalidateQueries({ queryKey: ['appointment'" not in ok, (
        "the panel refetches what the PATCH response already told it, and stays "
        "stale while it does")
    # The confirmation must read from the loaded record, which is the thing that
    # is now kept fresh — not from the draft, which is what the user typed and
    # has not necessarily been accepted by the server.
    prompt = dialog.split("{confirmingCancel ? (", 1)[1].split("Keep it", 1)[0]
    assert "form.title" not in prompt, (
        "the confirmation names what the user typed rather than what is stored")


def test_an_already_cancelled_appointment_cannot_be_cancelled_again():
    """`DELETE` on a cancelled booking is a 200 that changes nothing and reports
    0 reminders withdrawn, which reads as a successful cancel of something that
    was already off."""
    dialog = _detail_dialog()
    cancel = dialog.split("setConfirmingCancel(true) }}", 1)[1] \
                   .split("Cancel appointment", 1)[0]
    assert "a.status === 'cancelled'" in cancel, (
        "Cancel appointment is live on a booking that is already cancelled")
    assert "already cancelled" in cancel, "nothing says why the button is dead"


def test_the_panel_does_not_offer_a_status_the_grid_cannot_explain():
    """`blocked` is what the Manage view's "Blocked slots" filter selects — a
    slot that is not an appointment. Offering it in the status dropdown would let
    a customer's booking be moved out of the Appointments view from a control
    that looks like the report's status tiles. A booking that already carries an
    unlisted status keeps it, so opening one cannot silently rewrite it."""
    dialog = _detail_dialog()
    statuses = dialog.split("const STATUSES = [", 1)[1].split("]", 1)[0]
    assert "'blocked'" not in statuses, (
        "the status dropdown can turn a booking into a blocked slot")
    for measured in ("booked", "confirmed", "cancelled", "new", "showed",
                     "no-show", "invalid", "rescheduled"):
        assert "'%s'" % measured in statuses, (
            "%s is a measured Appointment report tile and is not offered" % measured)
    assert "(STATUSES as readonly string[]).includes(form.status)" in dialog, (
        "opening a booking whose status is not in the list silently rewrites it")


def test_a_cancelled_booking_is_still_drawn_but_not_as_a_live_one():
    """Cancelling is a status change, not a delete — GHL's own Appointment
    report has a Cancelled tile, so the row is a record and the calendar keeps
    it. It has to be visibly not a live booking, or the slot reads as taken."""
    page = _read("pages", "CalendarsPage.tsx")
    assert page.count("'line-through'") == 3, (
        "the Day/Week block, the month chip and the list row must all show a "
        "cancelled booking as cancelled")
    week_block = page.split("{buckets[i].map((a) => {", 1)[1].split("})}", 1)[0]
    assert "a.status === 'cancelled'" in week_block


def test_the_contact_picker_is_one_component_not_two():
    """The create dialog and the detail panel ask the same question. Two copies
    of a search-as-you-type picker drift apart — the same reason the contact
    panel is one component used in two places (DECISIONS.md)."""
    picker = _read("components", "ContactPicker.tsx")
    assert "export function ContactPicker(" in picker
    for host in ("NewAppointmentDialog.tsx", "AppointmentDetailDialog.tsx"):
        source = _read("components", host)
        assert "from './ContactPicker'" in source, (
            "%s does not use the shared picker" % host)
        assert "function ContactPicker(" not in source, (
            "%s still carries its own copy of the picker" % host)
    assert "disabled" in picker, (
        "the picker cannot be shown read-only, so the detail panel has to hide "
        "who the booking is with from a role that cannot edit it")


def test_dragging_a_booking_on_the_grid_was_not_built():
    """Rescheduling is done through the panel's form. The owner chose that
    deliberately over drag-to-move, and the Week grid is a MEASURED surface —
    adding drag handlers to it is a change to the thing parity is judged on."""
    page = _read("pages", "CalendarsPage.tsx")
    for dnd in ("draggable", "onDragStart", "onDragEnd", "onDrop", "useDraggable"):
        assert dnd not in page, (
            "%r appeared on the calendar grid; drag-to-move is out of scope" % dnd)
