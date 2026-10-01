"""The Dispatch assistant's chats are SAVED (2026-10-01, the owner's ask: keep every chat and
what the assistant did, to improve it later; list them beside the conversation).

Pinned, by behaviour:
  * a question and its answer are stored, with every tool the assistant used; the history the
    model gets comes from the SERVER (the browser sends only the new question);
  * an unavailable AI (off, paused, over the cap) is still a saved answer, marked error;
  * a person sees and writes only their own chats — another's answers 404; an ADMIN may read
    everyone's (scope=all), read-only;
  * "delete" hides a chat from its owner's list and KEEPS it (an admin still reads it);
  * thumbs up / down and a note are stored on the answer.
The provider is mocked at the HTTP boundary (tests/ai_support.py).
"""
import json

import pytest
from app.ai import vault
from app.auth import mint_api_token
from app.dispatch import ai
from app.main import app
from app.models import AiConnection, DispatchChat, DispatchChatMessage, Role, User
from fastapi.testclient import TestClient
from tests.ai_support import openai_call, openai_completion


@pytest.fixture()
def people(db, secrets_key):
    out = {}
    for key, role in (("admin", Role.ADMIN), ("dispatcher", Role.DISPATCHER),
                      ("other", Role.DISPATCHER), ("tech", Role.TECH)):
        u = User(email="%s@x.test" % key, name=key.title(), role=role)
        db.add(u)
        db.flush()
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        out[key] = (u.id, {"Authorization": "Bearer " + plain})
    conn = AiConnection(name="OpenAI", provider="openai", default_model="gpt-6-luna",
                        api_key_encrypted=vault.encrypt("sk-test-1"), api_key_last4="st-1")
    db.add(conn)
    db.flush()
    s = ai.settings(db, for_update=True)
    s.ai_enabled, s.connection_id = True, conn.id
    db.commit()
    return out


def ask(who, content, chat_id=None, people=None):
    return TestClient(app).post("/api/dispatch/chats/messages",
                                json={"chat_id": chat_id, "content": content},
                                headers=people[who][1])


def test_a_conversation_is_saved_and_the_history_comes_from_the_server(db, people, script):
    script.responses = [
        openai_completion(content=None, finish="tool_calls", tool_calls=[
            openai_call("list_items", json.dumps({"queue": "new"}), "c1")]),
        openai_completion(content="Nothing new is waiting."),
        openai_completion(content="Yes — still nothing."),
    ]
    first = ask("dispatcher", "Any new jobs?", people=people).json()
    chat_id = first["chat"]["id"]
    assert first["chat"]["title"] == "Any new jobs?"
    assert first["assistant_message"]["steps"][0]["tool"] == "list_items"
    second = ask("dispatcher", "Still?", chat_id, people).json()
    assert second["chat"]["id"] == chat_id and second["chat"]["message_count"] == 4
    # The model received the saved history — not anything the browser sent.
    sent = [m for m in script.requests[2]["body"]["messages"] if m["role"] != "system"]
    assert [(m["role"], m.get("content")) for m in sent] == [
        ("user", "Any new jobs?"), ("assistant", "Nothing new is waiting."),
        ("user", "Still?")]
    listed = TestClient(app).get("/api/dispatch/chats", headers=people["dispatcher"][1]).json()
    assert [c["id"] for c in listed["chats"]] == [chat_id]


def test_an_unavailable_ai_is_still_a_saved_answer(db, people, script):
    ai.settings(db).ai_enabled = False
    db.commit()
    r = ask("dispatcher", "What is urgent?", people=people)
    assert r.status_code == 200 and r.json()["assistant_message"]["error"] is True
    assert "switched off" in r.json()["assistant_message"]["content"]
    assert script.calls == 0
    assert db.query(DispatchChatMessage).count() == 2


def test_only_your_own_chats_and_an_admin_reads_everyones_read_only(db, people, script):
    script.responses = [openai_completion(content="ok")]
    chat_id = ask("dispatcher", "Mine", people=people).json()["chat"]["id"]
    cl = TestClient(app)
    assert cl.get("/api/dispatch/chats/%d" % chat_id, headers=people["other"][1]).status_code \
        == 404
    assert ask("other", "Sneaking in", chat_id, people).status_code == 404
    assert cl.get("/api/dispatch/chats?scope=all", headers=people["other"][1]).status_code == 403
    assert cl.get("/api/dispatch/chats", headers=people["tech"][1]).status_code == 403
    every = cl.get("/api/dispatch/chats?scope=all", headers=people["admin"][1]).json()
    assert [(c["id"], c["user_name"]) for c in every["chats"]] == [(chat_id, "Dispatcher")]
    view = cl.get("/api/dispatch/chats/%d" % chat_id, headers=people["admin"][1]).json()
    assert view["can_write"] is False and len(view["messages"]) == 2
    assert ask("admin", "Writing into theirs", chat_id, people).status_code == 404


def test_delete_hides_the_chat_but_keeps_it(db, people, script):
    script.responses = [openai_completion(content="ok")]
    chat_id = ask("dispatcher", "Temporary", people=people).json()["chat"]["id"]
    cl = TestClient(app)
    assert cl.delete("/api/dispatch/chats/%d" % chat_id,
                     headers=people["dispatcher"][1]).json() == {"id": chat_id, "archived": True}
    assert cl.get("/api/dispatch/chats", headers=people["dispatcher"][1]).json()["chats"] == []
    assert db.get(DispatchChat, chat_id) is not None
    assert cl.get("/api/dispatch/chats/%d" % chat_id,
                  headers=people["admin"][1]).status_code == 200


def test_rename_and_feedback_are_stored(db, people, script):
    script.responses = [openai_completion(content="Call #707 first.")]
    r = ask("dispatcher", "what first", people=people).json()
    chat_id, answer_id = r["chat"]["id"], r["assistant_message"]["id"]
    cl = TestClient(app)
    h = people["dispatcher"][1]
    assert cl.patch("/api/dispatch/chats/%d" % chat_id, json={"title": "Morning plan"},
                    headers=h).json()["title"] == "Morning plan"
    fb = cl.post("/api/dispatch/chats/%d/messages/%d/feedback" % (chat_id, answer_id),
                 json={"rating": -1, "note": "It missed the AHS approvals"}, headers=h).json()
    assert fb["feedback"] == {"rating": -1, "note": "It missed the AHS approvals"}
    assert db.get(DispatchChatMessage, answer_id).rating == -1
    question_id = r["user_message"]["id"]
    assert cl.post("/api/dispatch/chats/%d/messages/%d/feedback" % (chat_id, question_id),
                   json={"rating": 1}, headers=h).status_code == 404
