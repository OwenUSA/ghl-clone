"""Owen AI answers several chats at once (2026-11-13) — and each answer keeps its own chat.

  * `claim` never takes a message whose chat already has an answer running, and never a newer
    message before an older one in the same chat;
  * three chats really run at the same time, each in its own worker, own DB session and own
    event loop, and a tool call inside each (through asyncio.to_thread, as the SDK does) sees
    ITS OWN chat — the bug a shared global would have caused;
  * each chat resumes its own Claude session; the second message of a chat waits for the first;
  * OWEN_AI_PARALLEL is read and clamped.
No test starts the Claude CLI: the turn is a fake.
"""
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app.db import SessionLocal
from app.models import OwenAiMessage, OwenAiThread
from app.owen_ai import agent, config, proposals, runner
from tests.test_owen_ai import people  # noqa: F401  (fixture)


def chat(db, people, title, *contents, session_id=None):  # noqa: F811
    th = OwenAiThread(user_id=people["owner"][0], kind="task", title=title,
                      session_id=session_id)
    db.add(th)
    db.flush()
    ms = [OwenAiMessage(thread_id=th.id, role="user", content=c, status="queued")
          for c in contents]
    db.add_all(ms)
    db.commit()
    return th, ms


def test_claim_skips_a_busy_chat_and_keeps_a_chats_order(db, people):  # noqa: F811
    a, (a1, a2) = chat(db, people, "A", "a1", "a2")
    b, (b1,) = chat(db, people, "B", "b1")
    first = runner.claim(db)
    second = runner.claim(db)
    assert (first.id, second.id) == (a1.id, b1.id)      # a2 waits: chat A is busy
    assert runner.claim(db) is None
    a1.status = "done"
    db.commit()
    assert runner.claim(db).id == a2.id


class Turn:
    """Holds every answer open until all three have started, so they provably overlap."""

    def __init__(self, expected):
        self.expected = expected
        self.lock = threading.Lock()
        self.seen = []
        self.running = 0
        self.peak = 0
        self.all_in = threading.Event()

    def __call__(self, prompt, session_id, note):
        # What a tool call sees: the SDK runs tools in its own loop via asyncio.to_thread.
        seen_by_tool = asyncio.run(asyncio.to_thread(proposals.current_thread))
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
            self.seen.append((prompt, session_id, seen_by_tool))
            if self.running >= self.expected:
                self.all_in.set()
        self.all_in.wait(10)
        time.sleep(0.05)
        with self.lock:
            self.running -= 1
        return agent.TurnResult(text="answer to " + prompt, session_id="s-" + prompt)


def run_until_idle(db, turn, slots):
    pool = ThreadPoolExecutor(max_workers=slots)
    active = set()
    deadline = time.time() + 20
    while time.time() < deadline:
        runner.dispatch(db, pool, active, turn, SessionLocal, slots)
        db.expire_all()
        left = db.query(OwenAiMessage).filter(
            OwenAiMessage.role == "user",
            OwenAiMessage.status.in_(["queued", "running"])).count()
        if not left and not active:
            break
        time.sleep(0.05)
    pool.shutdown(wait=True)


def test_three_chats_run_together_and_each_tool_sees_its_own_chat(db, people):  # noqa: F811
    chats = {}
    for name in ("A", "B", "C"):
        th, _ = chat(db, people, name, name.lower() + "1", session_id="old-" + name)
        chats[name.lower() + "1"] = th.id
    turn = Turn(expected=3)
    run_until_idle(db, turn, slots=3)
    assert turn.peak == 3                                # really at the same time
    for prompt, session_id, seen in turn.seen:
        assert seen == chats[prompt]                     # the tool saw ITS chat
        assert session_id == "old-" + prompt[0].upper()  # and resumed ITS session
    db.expire_all()
    for prompt, tid in chats.items():
        th = db.get(OwenAiThread, tid)
        reply = db.query(OwenAiMessage).filter_by(thread_id=tid, role="assistant").one()
        assert reply.content == "answer to " + prompt and th.session_id == "s-" + prompt
    assert proposals.current_thread() is None            # nothing leaked out of the answers


def test_a_chats_second_message_waits_for_its_first(db, people):  # noqa: F811
    th, (m1, m2) = chat(db, people, "A", "first", "second")
    order = []

    def turn(prompt, session_id, note):
        order.append((prompt, session_id))
        time.sleep(0.1)
        return agent.TurnResult(text="ok", session_id="s-" + prompt)
    run_until_idle(db, turn, slots=3)
    assert order == [("first", None), ("second", "s-first")]   # in order, memory carried


def test_the_parallel_setting_is_clamped(monkeypatch):
    for raw, n in (("", 3), ("2", 2), ("9", 4), ("0", 1), ("x", 3)):
        monkeypatch.setenv("OWEN_AI_PARALLEL", raw)
        assert config.parallel() == n
