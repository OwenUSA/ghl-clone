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
