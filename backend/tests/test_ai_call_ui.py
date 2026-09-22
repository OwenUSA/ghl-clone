"""The thread has to SHOW that an AI agent answered the call.

Phase 1 of the voice-agent amendment is a supervision feature: an agent speaks to a
customer on the company's behalf, and the only person who can judge whether that went well
is a dispatcher reading it afterwards. A call that arrives with the agent's name, its
outcome and what it wrote down, and renders as an ordinary anonymous call row, would mean
the whole ingest half was pointless.

Asserted against source, the way the other frontend tests here are (the project has no JS
test runner; see test_frontend_feedback.py).
"""
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def test_the_thread_draws_the_agent_record_on_a_call():
    page = _read("pages", "ConversationsPage.tsx")
    assert "AiCallRecord" in page, "an AI-answered call renders like any other call"
    assert "call={e.ai_call}" in page, "the record is drawn from the event's own field"


def test_the_record_names_the_agent_and_says_how_the_call_ended():
    source = _read("components", "AiCallRecord.tsx")
    assert "Answered by AI" in source, "a dispatcher cannot tell a person from an agent"
    for port in ("end_call", "transfer", "failed"):
        assert port in source, f"the outcome {port!r} would render as a raw port name"
    # `failed` is the one that must not read as the agent's doing: it means the agent never
    # spoke (at capacity, over the cost cap, or the voice service was down).
    assert "voicemail" in source, "a failed handoff must say where the caller actually went"


def test_what_the_agent_captured_is_shown_in_full():
    source = _read("components", "AiCallRecord.tsx")
    for field in ("name", "phone", "address", "intent", "urgency", "notes"):
        assert f"'{field}'" in source or f'"{field}"' in source, (
            f"{field} is captured by the agent and never shown")
    # owen-main owns this vocabulary. A field it starts sending must appear without
    # waiting for a frontend release, so unknown keys are rendered too.
    assert "FIELD_ORDER.includes" in source, "unknown captured fields are dropped silently"


def test_the_record_writes_nothing():
    """A capture is what the agent HEARD, not a customer record.

    While the agent is supervised, promoting a capture into a Contact or an Opportunity is
    a person's decision (DECISIONS.md, Q1/Q2). A component that could create one from the
    thread would make that decision by accident, so it has no mutation at all.
    """
    source = _read("components", "AiCallRecord.tsx")
    for forbidden in ("createContact", "useMutation", "fetch(", "api.post", "onSave"):
        assert forbidden not in source, (
            f"{forbidden} in the call record: it must not be able to write")


def test_the_event_type_carries_the_agent_record():
    api = _read("lib", "api.ts")
    assert "ai_call?: AiCall | null" in api, "the thread cannot read what it is not typed for"
    assert "export type AiCall" in api
    for key in ("agent", "version", "outcome", "campaign", "captured"):
        assert key in api, f"AiCall does not declare {key!r}"
