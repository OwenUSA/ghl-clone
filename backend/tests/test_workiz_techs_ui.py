"""The Workiz technicians in the opportunity modal (2026-09-15): executed where it can be.

**Executed under node.** `lib/customFields.ts` is import-free, so `workizTechnicians` — what
the modal draws from `custom_fields.workiz_tech` — runs here for real.

**Asserted against source.** The modal is React, which this project has no runner for.
It was also driven in a real headless Chromium against a throwaway database by
`python -m tests.browser_workiz_tech` — see `.qa/state/wztech-done`.
"""
from tests.test_opportunity_card_ui import _read, node, run_js


@node
def test_the_names_are_drawn_in_order_and_an_absent_or_odd_value_is_empty():
    got = run_js("customFields.ts", """
        out([
          m.workizTechnicians({ workiz_tech: ['Antonio Brown', ' Owen  Buzaglo '] }),
          m.workizTechnicians({ workiz_tech: ['Sheila & Leo'] }),
          m.workizTechnicians({ workiz_id: 'J1' }),
          m.workizTechnicians({ workiz_tech: [] }),
          m.workizTechnicians({ workiz_tech: [7, null, ''] }),
          m.workizTechnicians(null),
          m.isEmptyAnswer(m.workizTechnicians({})),
          m.changedAnswers({ workiz_tech: ['A'] }, { workiz_tech: ['B'] }),
        ])
    """)
    assert got == ["Antonio Brown, Owen Buzaglo", "Sheila & Leo", "", "", "", "", True, {}]


def test_the_modal_draws_the_field_read_only_and_hides_it_when_empty():
    modal = _read("components", "OpportunityDetail.tsx")
    body = modal.split("export function OpportunityDetail", 1)[1]
    assert "workizTechnicians(o.custom_fields)" in body, "the field is not read from the card"
    block = body.split("<Label>Workiz technician(s)</Label>", 1)
    assert len(block) == 2, "the modal does not label the field 'Workiz technician(s)'"
    before, after = block[0][-400:], block[1].split("/>", 1)[0]
    assert "shows(workizTechs)" in before, "Hide empty fields does not hide an empty field"
    assert "readOnly" in after and "onChange" not in after, "the field can be typed into"
    # Still no other reserved key on screen, and Update still posts only changed answers.
    assert "owen_" not in body and "changedAnswers(" in modal
