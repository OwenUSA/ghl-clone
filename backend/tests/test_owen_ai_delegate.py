"""Sub-chats (app/owen_ai/delegate.py, 2026-11-19): one main chat hands each customer to its own
chat, gets ONE summary message when they are all done, and sends corrections to one of them.
No test starts the Claude CLI: the turn is a fake that records which chat it answered."""
import json

import pytest
from app.models import OwenAiMessage, OwenAiProposalDraft, OwenAiThread
from app.owen_ai import agent, delegate, proposals, runner, tools
from tests.test_owen_ai import people  # noqa: F401  (fixture)
from tests.test_owen_ai_parallel import run_until_idle


def main_chat(db, people):  # noqa: F811
    th = OwenAiThread(user_id=people["owner"][0], kind="task", title="Proposals Nov 19")
    db.add(th)
    db.commit()
    return th


class Turn:
    def __init__(self):
        self.prompts = []

    def __call__(self, prompt, session_id, note):
        self.prompts.append((proposals.current_thread(), prompt))
        return agent.TurnResult(text="done: " + prompt[:30], session_id="s",
                                cost_usd=0.01, num_turns=1)


def test_one_sub_chat_per_customer_queued_with_the_rules(db, people):  # noqa: F811
    main = main_chat(db, people)
    kids = delegate.start(db, main.id, [{"job": "#843", "instructions": "Good 1 leak, $625"},
                                        {"job": "836", "instructions": "As drafted"}])
    assert [k.job_number for k in kids] == ["843", "836"]
    assert all(k.parent_id == main.id and k.user_id == main.user_id for k in kids)
    m = db.query(OwenAiMessage).filter_by(thread_id=kids[0].id).one()
    assert m.status == "queued" and m.kind == delegate.KIND
    assert "job #843" in m.content and "Good 1 leak, $625" in m.content
    assert "Never send anything" in m.content


@pytest.mark.parametrize("items,expect", [
    ([], "1 to 15"), ([{"job": "", "instructions": "x"}], "job number"),
    ([{"job": "11", "instructions": "a"}, {"job": "#11", "instructions": "b"}], "Each job once"),
])
def test_bad_requests_start_nothing(db, people, items, expect):  # noqa: F811
    main = main_chat(db, people)
    with pytest.raises(ValueError, match=expect):
        delegate.start(db, main.id, items)
    assert db.query(OwenAiThread).filter_by(parent_id=main.id).count() == 0


def test_a_sub_chat_cannot_start_its_own(db, people):  # noqa: F811
    main = main_chat(db, people)
    (kid,) = delegate.start(db, main.id, [{"job": "843", "instructions": "x"}])
    with pytest.raises(ValueError, match="cannot start"):
        delegate.start(db, kid.id, [{"job": "11", "instructions": "x"}])


def test_all_done_queues_one_summary_in_the_main_chat_and_owen_comes_first(db, people):  # noqa: F811
    main = main_chat(db, people)
    kids = delegate.start(db, main.id, [{"job": j, "instructions": "x"} for j in ("11", "12", "13")])
    own = OwenAiThread(user_id=main.user_id, kind="task", title="Owen's other chat")
    db.add(own)
    db.flush()
    db.add(OwenAiMessage(thread_id=own.id, role="user", content="mine", status="queued"))
    db.commit()
    turn = Turn()
    run_until_idle(db, turn, slots=1)
    order = [tid for tid, _ in turn.prompts]
    assert order[0] == own.id                               # Owen's own message first
    assert set(order[1:4]) == {k.id for k in kids}          # then the sub-chats
    assert order[4] == main.id                              # then ONE summary in the main chat
    db.expire_all()
    follow = db.query(OwenAiMessage).filter_by(thread_id=main.id, role="user").all()
    assert len(follow) == 1 and follow[0].content.startswith(delegate.FOLLOW_UP_MARK)


def test_status_shows_each_sub_chat_and_its_draft(db, people):  # noqa: F811
    main = main_chat(db, people)
    (kid,) = delegate.start(db, main.id, [{"job": "843", "instructions": "x"}])
    run_until_idle(db, Turn(), slots=1)
    db.add(OwenAiProposalDraft(job_uid="j", job_number="843", customer_name="Edwardo",
                               template_key="short", template_uid="t", title="t",
                               options=[{"name": "GOOD", "price": 625.0}], spec={},
                               status="pending", target="new", thread_id=kid.id))
    db.commit()
    s = delegate.status(db, main.id)
    (row,) = s["sub_chats"]
    assert row["state"] == "finished" and row["draft"]["customer_pays"] == {"GOOD": 625.0}
    assert s["all_finished"] is True


def test_send_reaches_only_this_chats_own_sub_chat(db, people):  # noqa: F811
    main = main_chat(db, people)
    other = main_chat(db, people)
    (kid,) = delegate.start(db, main.id, [{"job": "843", "instructions": "x"}])
    with pytest.raises(ValueError, match="not one this chat started"):
        delegate.send(db, other.id, kid.id, "change it")
    delegate.send(db, main.id, kid.id, "Good is the closet leak only, $625")
    assert db.query(OwenAiMessage).filter_by(thread_id=kid.id, status="queued").count() == 2


def test_the_tools_use_the_chat_they_run_in(db, people):  # noqa: F811
    main = main_chat(db, people)
    with proposals.answering(main.id):
        out = json.loads(tools.delegate_jobs([{"job": "843", "instructions": "x"}]))
        assert out["started"][0]["job"] == "843"
        assert json.loads(tools.delegated_status())["sub_chats"][0]["state"] == "waiting its turn"
    names = {name for name, *_ in tools.SPECS}
    assert {"delegate_jobs", "delegated_status", "send_to_sub_chat"} <= names
    assert tools.delegate_jobs([{"job": "11", "instructions": "x"}]).startswith("ERROR")


def test_the_thread_list_carries_the_parent(db, people):  # noqa: F811
    from app.main import app
    from fastapi.testclient import TestClient
    main = main_chat(db, people)
    (kid,) = delegate.start(db, main.id, [{"job": "843", "instructions": "x"}])
    r = TestClient(app).get("/api/owen-ai/threads", headers=people["owner"][1])
    by_id = {t["id"]: t for t in r.json()}
    assert by_id[kid.id]["parent_id"] == main.id and by_id[main.id]["parent_id"] is None
    assert runner is not None
