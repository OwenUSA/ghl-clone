"""The call Checklist in the browser (2026-09-14): executed where it can be.

**Executed under node.** `lib/customFields.ts` (which answers a Create sends, when a
details box opens, the "N / M answered" count, which questions span the row) and
`lib/opportunityModal.ts` (the card's badge) are import-free, so the shipped code
itself runs here.

**Asserted against source.** The modals, the card and the Settings panel are React,
which this project has no runner for; each assertion names the failure it would catch.
They were also driven in a real headless browser on a throwaway database — see
`.qa/state/checklist-done`.
"""
import json

from tests.test_opportunity_card_ui import _read, node, run_js

js = json.dumps

DEFS = [
    {"id": 1, "key": "checklist_second_phone", "label": "Second phone number",
     "field_type": "text", "options": [], "position": 0, "entity": "opportunity",
     "archived": False, "pipeline_ids": [1, 2], "group_id": 9,
     "script": "Always ask for a second number.", "linked_field": None, "details_when": []},
    {"id": 2, "key": "checklist_previous_repair", "label": "Any previous repair?",
     "field_type": "dropdown", "options": ["Yes", "No", "Unknown"], "position": 1,
     "entity": "opportunity", "archived": False, "pipeline_ids": [1, 2], "group_id": 9,
     "script": None, "linked_field": None, "details_when": ["Yes"]},
    {"id": 3, "key": "checklist_introduced_antonio", "label": "Introduced Antonio",
     "field_type": "boolean", "options": [], "position": 2, "entity": "opportunity",
     "archived": False, "pipeline_ids": [1], "group_id": 9, "script": None,
     "linked_field": None, "details_when": []},
    {"id": 4, "key": "checklist_email_verified", "label": "Email verified",
     "field_type": "boolean", "options": [], "position": 3, "entity": "opportunity",
     "archived": False, "pipeline_ids": [1, 2], "group_id": 9, "script": None,
     "linked_field": "contact_email", "details_when": []},
    {"id": 5, "key": "old_question", "label": "Old", "field_type": "text", "options": [],
     "position": 4, "entity": "opportunity", "archived": True, "pipeline_ids": [1, 2],
     "group_id": 9},
    {"id": 6, "key": "checklist_whats_happening", "label": "What's happening there?",
     "field_type": "paragraph", "options": [], "position": 5, "entity": "opportunity",
     "archived": False, "pipeline_ids": [1, 2], "group_id": 9},
]


# ---------------- executed ----------------

@node
def test_a_create_sends_only_the_chosen_pipelines_answers_and_no_blanks():
    """Answered on AHS, then switched to Retail: the AHS-only answer is not posted (the
    server would refuse it), details ride along with their field, blanks are dropped."""
    got = run_js("customFields.ts", """
        const defs = %s
        const answers = {
          checklist_second_phone: '941-555-0100',
          checklist_previous_repair: 'Yes',
          checklist_previous_repair__details: 'Patched 2021',
          checklist_introduced_antonio: false,
          checklist_email_verified: '',
          old_question: 'typed long ago',
          checklist_whats_happening: 'Line one\\nLine two',
        }
        out({ ahs: m.answersFor(defs, 1, answers), retail: m.answersFor(defs, 2, answers),
              none: m.answersFor(defs, null, answers) })
    """ % js(DEFS))
    assert got["ahs"] == {
        "checklist_second_phone": "941-555-0100", "checklist_previous_repair": "Yes",
        "checklist_previous_repair__details": "Patched 2021",
        "checklist_introduced_antonio": False,
        "checklist_whats_happening": "Line one\nLine two"}
    assert "checklist_introduced_antonio" not in got["retail"]
    assert "old_question" not in got["retail"] and "checklist_email_verified" not in got["ahs"]
    assert got["retail"]["checklist_previous_repair__details"] == "Patched 2021"
    assert got["none"] == {}


@node
def test_the_details_box_opens_only_for_its_trigger_option():
    got = run_js("customFields.ts", """
        const defs = %s
        const repair = defs[1], phone = defs[0]
        out([m.showsDetails(repair, 'Yes'), m.showsDetails(repair, 'No'),
             m.showsDetails(repair, ''), m.showsDetails(repair, undefined),
             m.showsDetails(phone, 'Yes'), m.showsDetails({ ...repair, details_when: [] }, 'Yes'),
             m.detailsKey('checklist_previous_repair')])
    """ % js(DEFS))
    assert got == [True, False, False, False, False, False,
                   "checklist_previous_repair__details"]


@node
def test_answered_counts_no_as_an_answer_and_a_blank_as_none():
    got = run_js("customFields.ts", """
        const defs = %s
        const ahs = m.modalSections(defs, [{ id: 9, name: 'Checklist', position: 0 }], 1)
        const retail = m.modalSections(defs, [{ id: 9, name: 'Checklist', position: 0 }], 2)
        const answers = { checklist_introduced_antonio: false, checklist_second_phone: '  ',
                          checklist_previous_repair__details: 'only details' }
        out({ ahs: m.answeredCount(ahs.groups[0].fields, answers),
              retail: m.answeredCount(retail.groups[0].fields, answers),
              names: [m.isChecklistGroup(' checklist '), m.isChecklistGroup('Roof Inspection')] })
    """ % js(DEFS))
    # AHS asks 5 live questions (the archived one is not asked), Retail 4.
    assert got["ahs"] == {"answered": 1, "total": 5}
    assert got["retail"] == {"answered": 0, "total": 4}
    assert got["names"] == [True, False]


@node
def test_long_answers_scripts_links_and_details_span_the_row():
    got = run_js("customFields.ts", """
        const defs = %s
        out(defs.map((d) => [d.key, m.spansRow(d)]))
    """ % js(DEFS))
    assert dict(got) == {"checklist_second_phone": True, "checklist_previous_repair": True,
                         "checklist_introduced_antonio": False,
                         "checklist_email_verified": True, "old_question": False,
                         "checklist_whats_happening": True}


@node
def test_an_edit_sends_a_changed_details_answer_and_nothing_else():
    got = run_js("customFields.ts", """
        out(m.changedAnswers(
          { checklist_previous_repair: 'Yes', checklist_previous_repair__details: 'old',
            owen_call_id: 'c-1' },
          { checklist_previous_repair: 'Yes', checklist_previous_repair__details: 'new',
            owen_call_id: 'c-1' }))
    """)
    assert got == {"checklist_previous_repair__details": "new"}


@node
def test_the_card_badge_says_n_of_m_and_opens_the_checklist_tab():
    got = run_js("opportunityModal.ts", """
        out([m.checklistBadge({ answered: 3, total: 16, group_id: 4 }),
             m.checklistBadge({ answered: 13, total: 13, group_id: 4 }).done,
             m.checklistBadge(null), m.checklistBadge(undefined),
             m.checklistBadge({ answered: 0, total: 0, group_id: 4 })])
    """)
    assert got[0] == {"text": "3/16", "tip": "Checklist: 3 of 16 answered", "done": False,
                      "request": {"tab": "group:4"}}
    assert got[1:] == [True, None, None, None]


# ---------------- asserted against source ----------------

def test_the_answers_component_draws_scripts_paragraphs_links_and_details():
    src = _read("components", "CustomFieldAnswers.tsx")
    body = src.split("export function CustomFieldAnswers(", 1)[1]
    # The script sits between the question and its control.
    label, rest = body.split("<div style={LABEL}>{def.label}</div>", 1)
    assert rest.index("data-script") < rest.index("{control(def)}"), (
        "the script is not drawn under the question")
    assert "def.field_type === 'paragraph'" in body and "<textarea" in body
    # A linked yes/no edits the REAL value through the caller, never an answer key.
    assert "linked.email.onChange(e.target.value)" in body
    assert "linked.address.onChange(" in body
    assert "set(def.key, e.target.checked ? true : '')" in body
    # The details box is gated on the chosen option and stored beside the answer.
    assert "showsDetails(def, answers[def.key]) && (" in body
    assert "set(detailsKey(def.key), e.target.value)" in body


def test_the_edit_modal_counts_the_checklist_and_links_the_real_values():
    src = _read("components", "OpportunityDetail.tsx")
    assert "isChecklistGroup(group.group.name)" in src
    assert "answeredCount(group.fields, form.answers)" in src
    assert "{progress.answered} / {progress.total} answered" in src
    # The email is the Primary email state (saved on the contact by the save
    # mutation), the address the Address group's (saved by the detail PATCH), and a
    # TECH gets both dead.
    assert "email: { value: email, onChange: (v) => set('email', v)," in src
    # ...and, Zuper v2 (2026-09-16), dead with "Change this in Zuper" for a locked contact.
    assert "disabled: !form.contact || !canEdit || contactLock('email')," in src
    assert "address: { value: form.address, onChange: (v) => set('address', v)," in src
    assert src.count("linked={linked}") == 2, "a tab draws linked questions without values"


def test_the_add_modal_is_ghls_and_requires_a_contact():
    src = _read("components", "AddOpportunityModal.tsx")
    for text in ("Add new opportunity",
                 "Create new opportunity by filling in details and selecting a contact",
                 "Contact details", "Primary contact name", "Primary email", "Primary phone",
                 "Opportunity name", 'ariaLabel="Pipeline"', 'ariaLabel="Stage"',
                 'ariaLabel="Status"', 'aria-label="Value"', 'ariaLabel="Owner"',
                 'label="Followers"', "Business name", "Source", "Manage fields",
                 "Cancel", "Create"):
        assert text in src, text
    assert "<Label required>Primary contact name</Label>" in src
    assert "<Label required>Opportunity name</Label>" in src
    assert "const problem = !contact ? 'Choose a primary contact'" in src
    assert "disabled={!ready || create.isPending}" in src
    # Every custom tab is in the nav, so the Checklist is filled before Create.
    nav = src.split('aria-label="New opportunity sections"', 1)[1].split("</nav>", 1)[0]
    assert "sections.groups.map(" in nav and "Manage fields" in nav
    # One POST carries the answers, filtered to the chosen pipeline.
    mutation = src.split("const create = useMutation({", 1)[1].split("\n  })", 1)[0]
    assert mutation.count("createOpportunity(") == 1
    assert "custom_fields: answersFor(fields.data ?? [], pipeline.id, answers)" in mutation
    for key in ("status,", "owner_id:", "follower_ids:", "business_name:", "source:",
                "address_street:"):
        assert key in mutation, key
    # The contact's own email/phone are saved on the contact BEFORE the card exists.
    assert mutation.index("patchContact(") < mutation.index("createOpportunity(")


def test_the_page_opens_the_new_modal_and_no_longer_has_its_own_form():
    page = _read("pages", "OpportunitiesPage.tsx")
    assert "import { AddOpportunityDialog } from '../components/AddOpportunityModal'" in page
    assert "function AddOpportunityDialog(" not in page
    assert "Optional — an opportunity can be filed without a contact." not in page


def test_the_settings_panel_edits_every_new_setting_for_the_right_type():
    src = _read("components", "CustomFieldsPanel.tsx")
    assert "{ value: 'paragraph', label: 'Paragraph (multi-line text)' }" in src
    settings = src.split("function QuestionSettings(", 1)[1]
    assert "Script (optional)" in settings
    assert "{type === 'boolean' && (" in settings and "Show next to it" in settings
    assert "{type === 'dropdown' && (" in settings
    assert "Show a details box when the answer is" in settings
    assert src.count("<QuestionSettings") == 2, "New field and Edit must both offer them"
    for label in ("Nothing", "The contact's email", "The opportunity's address"):
        assert label in src


def test_the_card_badge_is_a_count_not_an_answer():
    src = _read("components", "OpportunityCard.tsx")
    face = src.split("function CardFace(", 1)[1].split("\nexport const CARD_SURFACE", 1)[0]
    assert "checklistBadge(o.checklist)" in face
    assert "{checklist && (" in face and "requestModalTab(o.id, checklist.request)" in face
