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
    <select> would render every contact in the account (268 locally)."""
    dialog = _read("components", "NewAppointmentDialog.tsx")
    assert "function ContactPicker(" in dialog, "there is no contact picker"
    picker = dialog.split("function ContactPicker(", 1)[1].split("\nexport function", 1)[0]
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


# ---------------- Opportunities > Forecast ----------------

def test_the_forecast_tab_is_a_tab_and_not_a_label():
    """The four tab names were four `<div>`s with no handler at all.

    `i === 0` painted the first one blue forever, so the header said "you are on
    Opportunities" whatever the screen was showing.
    """
    source = _read("pages", "OpportunitiesPage.tsx")
    assert "i === 0 ? 'rgb(56,160,219)'" not in source, (
        "the active tab is still hardcoded to the first one")
    header = source.split("{TABS.map(", 1)[1].split("})}", 1)[0]
    assert "setTab(t)" in header, "clicking a tab does nothing"
    assert "tab === t" in header, "the header cannot show which tab is open"
    assert "<ForecastPanel" in source, "the Forecast tab renders nothing"


def test_every_tab_goes_somewhere():
    """All four labels were inert. Each one now renders a panel, and a tab that
    switched to a blank pane would be worse than the dimmed label it replaced."""
    source = _read("pages", "OpportunitiesPage.tsx")
    for tab, panel in (("Forecast", "<ForecastPanel"),
                       ("Pipelines", "<PipelinesPanel"),
                       ("Bulk Actions", "<BulkActionsBar")):
        assert "tab === '%s'" % tab in source or "selecting" in source, tab
        assert panel in source, "the %s tab renders nothing" % tab
    header = source.split("{TABS.map(", 1)[1].split("})}", 1)[0]
    assert "disabled={!live}" in header, "a tab a role cannot open still looks live"


def test_the_forecast_tab_is_hidden_from_a_role_that_cannot_read_it():
    """`GET /api/forecast` is STAFF, exactly like `/api/dashboard`. A TECH must not
    be able to open the tab and land on a refusal."""
    source = _read("pages", "OpportunitiesPage.tsx")
    assert "const canForecast = user.role !== 'TECH'" in source, (
        "the page never asks whether this role can read a forecast")
    header = source.split("{TABS.map(", 1)[1].split("})}", 1)[0]
    assert "!canForecast" in header, "the Forecast tab ignores the role"
    assert "Your role cannot view the forecast" in header, (
        "nothing tells the user why the tab is dead")


def test_the_forecast_reports_a_failed_query_instead_of_drawing_zeros():
    """A forecast of $0.00 and an unreachable API look identical on screen."""
    source = _read("components", "ForecastPanel.tsx")
    assert 'role="alert"' in source, "the panel has no error surface"
    assert "forecast.error" in source, (
        "a refused or failed forecast renders as a table of zeros")


def test_the_forecast_quotes_the_server_rather_than_recomputing():
    """The whole point of /api/forecast is that the Dashboard and this screen
    cannot disagree. Deriving a rate or a weighting in the browser would put that
    back — so the panel may format numbers and must not compute them."""
    source = _read("components", "ForecastPanel.tsx")
    body = source.split("export function ForecastPanel", 1)[1]
    for banned in ("conversion_rate *", "/ 100 *", "* conversion", "reduce("):
        assert banned not in body, (
            "the panel computes %r itself instead of using the server's figure"
            % banned)
    assert "weighted_value_cents" in body and "projected_value_cents" in body, (
        "the panel does not render the server's projection at all")


def test_the_forecast_says_what_the_weighting_is():
    """A weighted number whose weighting is invisible reads as a promise."""
    source = _read("components", "ForecastPanel.tsx")
    assert "conversion_rate.toFixed(2)" in source, (
        "the rate the money is weighted at is never shown")
    assert "Dashboard" in source, (
        "nothing tells the reader this is the Dashboard's own rate")


# ---------------- Opportunities > Bulk Actions ----------------

def test_the_bulk_bar_offers_no_delete_and_says_why():
    """The absence is the decision. A control that is simply missing reads as an
    oversight and gets "fixed" later by someone who does not know why."""
    source = _read("components", "BulkActionsBar.tsx")
    assert "No bulk delete" in source, (
        "nothing on the bar tells the user bulk delete is deliberate")
    # No import of a delete helper and no handler that could reach one. The API
    # client has no bulk delete to import, which is the other half of this.
    assert "deleteOpportunity" not in source and "bulkDelete" not in source, (
        "the bar wires up a delete after all")
    assert "bulkDelete" not in _read("lib", "api.ts")


def test_bulk_assign_is_disabled_for_a_role_that_cannot_edit():
    """`POST /api/opportunities/bulk/owner` is STAFF; the stage move is not.
    A TECH may move deals and may not re-assign them."""
    source = _read("components", "BulkActionsBar.tsx")
    assert "const canAssign = user.role !== 'TECH'" in source
    assign = source.split("assign.isPending ? 'Assigning", 1)[0].rsplit("<button", 1)[1]
    assert "!canAssign" in assign, "Assign owner is offered to a role that cannot"
    assert "Your role cannot change an opportunity owner" in assign, (
        "nothing tells the user why the button is dead")
    move = source.split("move.isPending ? 'Moving", 1)[0].rsplit("<button", 1)[1]
    assert "canAssign" not in move, (
        "the stage move borrowed the owner gate; a TECH can drag a card, so a "
        "TECH can move a selection")


def test_a_bulk_action_reports_what_it_actually_did():
    """"3 moved" when one of the four was already there is how a bulk action loses
    trust. The response distinguishes moved from unchanged; the bar has to say so,
    including how many customers were texted."""
    source = _read("components", "BulkActionsBar.tsx")
    assert "r.unchanged.length" in source and "already there" in source
    assert "notified" in source, "nothing says how many customers were messaged"
    assert "role={error ? 'alert' : 'status'}" in source, (
        "a refused bulk action has no error surface")
    assert "onError: (e: Error) => setError(e.message)" in source, (
        "a refused bulk action is dropped on the floor")


def test_the_export_writes_the_selection_and_not_the_board():
    """`chosen` is the selection narrowed to what the board is currently showing;
    exporting anything else would silently include rows the user cannot see."""
    page = _read("pages", "OpportunitiesPage.tsx")
    assert "const chosen = visible.filter((o) => selected.has(o.id))" in page, (
        "the selection is not narrowed to the visible rows")
    bar = _read("components", "BulkActionsBar.tsx")
    assert "opportunitiesCsv(chosen" in bar, "the export does not write the selection"


def test_selection_mode_does_not_open_the_detail_dialog():
    """A click that both ticks a card and opens its dialog is unusable."""
    page = _read("pages", "OpportunitiesPage.tsx")
    assert "selectable && onToggle ? onToggle(o.id) : onOpen(o.id)" in page, (
        "a card in selection mode still opens the opportunity")
    assert "selecting ? toggle(o.id) : setOpenOpp(o.id)" in page, (
        "a list row in selection mode still opens the opportunity")
    assert "e.stopPropagation(); onToggle?.(o.id)" in page, (
        "the checkbox and the card both fire, so the tick lands back where it was")


# ---------------- Opportunities: the ⋯ menu and the saved-list row ----------------

def test_the_overflow_items_that_work_are_no_longer_dimmed():
    """All four were `<div>`s with `cursor: not-allowed` and one shared tooltip.
    Three of them now do something, and the fourth still does not — which has to
    be visible, or "not implemented" becomes indistinguishable from "broken"."""
    source = _read("pages", "OpportunitiesPage.tsx")
    menu = source.split("{OVERFLOW.map(", 1)[1].split("</div>", 1)[0]
    assert "overflowAction(t)" in menu, "the menu items are still inert labels"
    assert "disabled={!item.run}" in menu, "a dead item still looks clickable"
    assert "item.run ? 'pointer' : 'not-allowed'" in menu

    actions = source.split("const overflowAction =", 1)[1].split("\n  }\n", 1)[0]
    for live in ("Export", "Manage smart lists", "Dashboard insights"):
        assert "'%s'" % live in actions, "%s does nothing" % live


def test_restore_opportunities_stays_dead_and_says_exactly_why():
    """It implies a trash. This codebase has no soft delete, and inventing one as
    a side quest is how a schema grows a `deleted_at` half the queries forget."""
    source = _read("pages", "OpportunitiesPage.tsx")
    actions = source.split("const overflowAction =", 1)[1].split("\n  }\n", 1)[0]
    assert "soft-deleted" in actions, (
        "nothing explains why Restore opportunities cannot work")
    assert "'Restore opportunities'" not in actions.split("return {\n      title:")[0], (
        "Restore opportunities was given a handler after all")
    # ...and no soft-delete column was invented to make it work.
    models = (Path(__file__).resolve().parents[1] / "app" / "models.py").read_text(
        encoding="utf-8")
    assert "deleted_at" not in models and "is_deleted" not in models


def test_export_writes_the_filtered_set_and_not_the_whole_pipeline():
    """The ⋯ Export means "what the board is showing", which is the point of it
    sitting next to the filters."""
    source = _read("pages", "OpportunitiesPage.tsx")
    fn = source.split("const exportFiltered =", 1)[1].split("\n  }", 1)[0]
    assert "opportunitiesCsv(visible" in fn, (
        "Export writes something other than the rows the board is showing")


def test_dashboard_insights_goes_to_the_dashboard():
    source = _read("pages", "OpportunitiesPage.tsx")
    assert "onNavigate('dashboard')" in source, (
        "Dashboard insights does not open the Dashboard")
    app_tsx = _read("App.tsx")
    assert "<OpportunitiesPage user={user} onNavigate={setActive} />" in app_tsx, (
        "the page is given no way to navigate, so the menu item cannot work")


def test_saving_a_list_is_disabled_for_a_role_that_cannot_write_one():
    """`POST /api/saved-views` is STAFF and a list is shared with everyone."""
    source = _read("components", "SavedViews.tsx")
    assert "const canSave = user.role !== 'TECH'" in source
    add = source.split("<span style={CHIP_TEXT}>List</span>", 1)[0].rsplit("<button", 1)[1]
    assert "disabled={!canSave}" in add and "Your role cannot save a shared list" in add


def test_deleting_a_list_is_admin_only_and_asks_first():
    """`DELETE /api/saved-views/{id}` is ADMIN. A shared list belongs to everyone,
    so removing one is not a click that should land on the first press."""
    source = _read("components", "SavedViews.tsx")
    assert "const canDelete = user.role === 'ADMIN'" in source
    assert "Only an admin can delete a shared list" in source
    assert "Really delete" in source, "the delete lands on a single click"
    assert "setConfirming(v.id)" in source


def test_the_built_in_open_opportunities_list_is_not_deletable():
    """It is the board's own default state, drawn as a chip, so it cannot be
    renamed or removed and there is no seeded row to keep in step."""
    source = _read("components", "SavedViews.tsx")
    row = source.split("export function SavedViewsRow", 1)[1].split(
        "views.data?.map", 1)[0]
    assert "Open opportunities" in row
    assert "deleteSavedView" not in row, "the built-in default offers a delete"


# ---------------- Opportunities > Pipelines ----------------

def test_managing_pipelines_is_disabled_rather_than_403_on_submit():
    """Every endpoint behind this panel is auth.ADMIN. The precedent set by
    d1f7c50 and b943f4b is a dead control with a title, never a form that refuses
    at the end."""
    source = _read("components", "PipelinesPanel.tsx")
    assert "const canManage = user.role === 'ADMIN'" in source
    assert "Only an admin can manage pipelines and stages" in source
    # Every write control consults it: add pipeline, add stage, rename, reorder,
    # and both deletes.
    assert source.count("!canManage") >= 6, (
        "a control on this panel does not ask whether the role may use it")


def test_the_pipelines_tab_stays_open_to_everyone_who_works_the_board():
    """The panel disables what a non-admin cannot do; hiding the structure from
    the people who work it every day would be worse."""
    page = _read("pages", "OpportunitiesPage.tsx")
    assert "tab === 'Pipelines' && <PipelinesPanel user={user} />" in page
    live = page.split("const live =", 1)[1].splitlines()[0]
    assert "Pipelines" not in live, "the Pipelines tab was gated on a role"


def test_deleting_a_populated_stage_is_not_offered_and_says_what_is_in_the_way():
    """The server answers 409 either way; this is so the admin knows before
    clicking, and knows what to move."""
    source = _read("components", "PipelinesPanel.tsx")
    assert "const removable = s.count === 0" in source, (
        "the Delete control ignores whether the stage holds anything")
    assert "holds ${s.count} opportunit" in source, (
        "the blocked title does not say how many are in the way")
    assert "Move ${" in source, "the message does not say what to do about it"


def test_deleting_a_pipeline_needs_it_to_be_completely_empty():
    """`Pipeline.stages` cascades delete-orphan, so this guard stands between a
    mis-click and every deal on the board."""
    source = _read("components", "PipelinesPanel.tsx")
    assert "p.stages.length === 0 && deals === 0" in source, (
        "a pipeline with stages can still be deleted from the browser")
    assert "Empty it first" in source


def test_a_structural_delete_always_asks_twice():
    source = _read("components", "PipelinesPanel.tsx")
    assert source.count("Really delete") == 2, (
        "a stage or a pipeline is deleted on a single click")
    assert "setConfirming('stage:'" in source and "setConfirming('pipeline:'" in source


def test_reordering_sends_the_whole_order_and_never_a_deal():
    """The endpoint takes a permutation, so a board that changed underneath is
    refused rather than half-applied. Nothing here touches an opportunity."""
    source = _read("components", "PipelinesPanel.tsx")
    swap = source.split("const swap =", 1)[1].split("\n  }", 1)[0]
    assert "stages.map((s) => s.id)" in swap, (
        "the reorder sends something other than the full stage list")
    assert "reorder.mutate(ids)" in swap
    assert "moveOpportunity" not in source and "stage_id" not in source, (
        "the pipelines panel touches an opportunity's stage")


def test_the_panel_says_names_may_repeat_rather_than_tidying_them_up():
    """Two distinct stages called "Call Back" are the measured shape, and the
    `ghl` CLI exits 5 rather than guess between them."""
    source = _read("components", "PipelinesPanel.tsx")
    assert "Two stages may share a name" in source
    assert "Call Back" in source, "nothing records why duplicate names are kept"


def test_the_panel_reports_a_refusal_instead_of_swallowing_it():
    source = _read("components", "PipelinesPanel.tsx")
    assert 'role="alert"' in source, "a refused structural change has no surface"
    assert "const failed = (e: Error) => setError(e.message)" in source
