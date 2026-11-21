"""Zuper change lists and chat pictures in the browser (2026-11-21).

**Executed under node.** `lib/owenAi.ts` is import-free: a change list is drawn under the answer
that listed it (never under an older one), a list whose answer is not written yet goes last, and
a pictures-only message shows no "(pictures)" text.

**Asserted against source.** The card has no button — Owen OKs a list by saying so in the chat,
which is the only thing the server accepts — and the composer sends the pictures' ids.
"""
import pytest
from tests.test_owen_ai_routes_ui import _read, node, run_js


@node
def test_a_list_is_drawn_under_the_answer_that_listed_it():
    got = run_js("owenAi.ts", """
        const msgs = [{id: 1, role: 'user'}, {id: 2, role: 'assistant'}, {id: 3, role: 'user'},
                      {id: 4, role: 'assistant'}, {id: 5, role: 'user'}]
        const sets = [{id: 10, status: 'superseded', proposed_message_id: 1},
                      {id: 11, status: 'pending', proposed_message_id: 3},
                      {id: 12, status: 'pending', proposed_message_id: 5}]
        const placed = m.changeSetPlacement(msgs, sets)
        out({two: placed.get(2).map(s => s.id), four: placed.get(4).map(s => s.id),
             end: placed.get(-1).map(s => s.id), one: placed.get(1) ?? null})
    """)
    assert got == {"two": [10], "four": [11], "end": [12], "one": None}


@node
@pytest.mark.parametrize("content,pictures,expect", [
    ("(pictures)", 2, ""), ("(pictures)", 0, "(pictures)"), ("put these on 812", 2,
                                                             "put these on 812")])
def test_a_pictures_only_message_shows_no_placeholder(content, pictures, expect):
    assert run_js("owenAi.ts", "out(m.bubbleText(%r, %d))" % (content, pictures)) == expect


@node
def test_every_status_has_a_sentence_and_a_mark():
    got = run_js("owenAi.ts", """
        out(['pending','applying','applied','partial','failed','superseded','expired']
            .map(s => m.changeSetHeadline(s) !== s)
            .concat(['applied','failed','skipped','not_asked','applying','pending']
            .map(s => !!m.changeMark(s).mark)))
    """)
    assert all(got)


def test_the_card_has_no_button_owen_says_it_in_the_chat():
    card = _read("components", "owenAi", "ChangeSetCard.tsx")
    assert "<button" not in card and "onClick" not in card, \
        "an Approve button would bypass the server's rule: only Owen's later chat message OKs it"


def test_the_composer_uploads_pictures_and_sends_their_ids():
    conv = _read("components", "owenAi", "Conversation.tsx")
    assert "owenAiUploadPicture(threadId, file)" in conv
    assert "onSend(t, ready.map((p) => p.upload!.id))" in conv
    assert 'type="file"' in conv and "onPaste" in conv and "onDrop" in conv
    page = _read("pages", "OwenAiPage.tsx")
    assert "owenAiSend(v.id, v.content, v.uploadIds)" in page
