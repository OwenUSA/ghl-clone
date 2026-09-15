"""The provider adapters, through the official SDKs, against a mock HTTP transport.

One interface in, one shape out: the engine never branches on who the provider is."""
import pytest
from app.ai import providers
from tests.ai_support import (
    anthropic_message,
    openai_call,
    openai_completion,
    text,
    tool_use,
)

TOOLS = [providers.ToolSpec("move_stage", "Move", {
    "type": "object", "properties": {"stage_id": {"type": "integer"}},
    "required": ["stage_id"], "additionalProperties": False})]


def test_anthropic_round_trips_tool_calls_usage_and_echoes_thinking(script):
    thinking = {"type": "thinking", "thinking": "", "signature": "sig-abc"}
    script.responses.extend([
        anthropic_message([thinking, text("Let me move it."), tool_use("move_stage",
                                                                      {"stage_id": 7})],
                          "tool_use", input_tokens=50, output_tokens=9, cache_write=40,
                          cache_read=11),
        anthropic_message([text("Done.")]),
    ])
    p = providers.build("anthropic", "sk-ant-x", None)
    messages = [{"role": "user", "content": "move it"}]
    turn = p.complete(system="SYS", messages=messages, tools=TOOLS, model="claude-opus-5")
    assert turn.stop == providers.TOOL_USE and turn.text == "Let me move it."
    call = turn.tool_calls[0]
    assert (call.name, call.arguments, call.parse_error) == ("move_stage", {"stage_id": 7}, None)
    assert (turn.usage.input_tokens, turn.usage.output_tokens, turn.usage.cache_write,
            turn.usage.cache_read) == (50, 9, 40, 11)
    messages += [{"role": "assistant", "text": turn.text, "tool_calls": turn.tool_calls,
                  "raw": turn.raw, "raw_provider": "anthropic"},
                 {"role": "tool_results", "results": [{"id": call.id, "name": call.name,
                                                       "content": "moved", "is_error": False}]}]
    final = p.complete(system="SYS", messages=messages, tools=TOOLS, model="claude-opus-5")
    assert final.stop == providers.END and final.text == "Done."
    sent = script.requests[1]["body"]
    assert sent["messages"][1]["content"][0] == thinking          # echoed unchanged
    assert sent["messages"][2]["content"][0] == {
        "type": "tool_result", "tool_use_id": "toolu_move_stage", "content": "moved"}
    assert sent["system"] == [{"type": "text", "text": "SYS",
                               "cache_control": {"type": "ephemeral"}}]
    for forbidden in ("temperature", "top_p", "top_k", "thinking"):
        assert forbidden not in sent


def test_openai_compatible_round_trips_tool_calls_and_usage(script):
    script.responses.extend([
        openai_completion(content=None, tool_calls=[openai_call("move_stage",
                                                                '{"stage_id": 7}')],
                          finish="tool_calls", prompt_tokens=80, completion_tokens=12,
                          cached=30, model="qwen2.5"),
        openai_completion(content="Done.", model="qwen2.5"),
    ])
    p = providers.build("openai_compatible", "local-key", "http://llm.lan/v1")
    messages = [{"role": "user", "content": "move it"}]
    turn = p.complete(system="SYS", messages=messages, tools=TOOLS, model="qwen2.5")
    assert turn.stop == providers.TOOL_USE
    assert turn.tool_calls[0].arguments == {"stage_id": 7}
    assert (turn.usage.input_tokens, turn.usage.cache_read, turn.usage.output_tokens) == (
        50, 30, 12)
    messages += [{"role": "assistant", "text": turn.text, "tool_calls": turn.tool_calls,
                  "raw": turn.raw, "raw_provider": "openai_compatible"},
                 {"role": "tool_results", "results": [{"id": "call_1", "name": "move_stage",
                                                       "content": "moved"}]}]
    final = p.complete(system="SYS", messages=messages, tools=TOOLS, model="qwen2.5")
    assert final.text == "Done."
    sent = script.requests[1]["body"]
    assert sent["messages"][0] == {"role": "system", "content": "SYS"}
    assert sent["messages"][2]["tool_calls"][0]["function"] == {
        "name": "move_stage", "arguments": '{"stage_id": 7}'}
    assert sent["messages"][3] == {"role": "tool", "tool_call_id": "call_1",
                                   "content": "moved"}
    assert sent["tools"][0]["function"]["parameters"] == TOOLS[0].input_schema
    assert script.requests[0]["url"] == "http://llm.lan/v1/chat/completions"


@pytest.mark.parametrize("arguments,error", [
    ('{"stage_id": 7', "not valid JSON"), ("[1, 2]", "not a JSON object")])
def test_bad_tool_arguments_are_marked_never_parsed_as_something_else(script, arguments, error):
    script.responses.append(openai_completion(tool_calls=[openai_call("move_stage", arguments)],
                                              finish="tool_calls"))
    turn = providers.build("openai", "sk", None).complete(
        system="S", messages=[{"role": "user", "content": "x"}], tools=TOOLS, model="gpt-5")
    assert turn.tool_calls[0].arguments is None and error in turn.tool_calls[0].parse_error


def test_stop_reasons_are_normalised(script):
    p = providers.build("anthropic", "sk", None)
    for raw, expected in (("end_turn", providers.END), ("max_tokens", providers.MAX_TOKENS),
                          ("refusal", providers.REFUSAL), ("pause_turn", providers.OTHER)):
        script.responses.append(anthropic_message([text("x")], raw))
        assert p.complete(system="S", messages=[{"role": "user", "content": "x"}], tools=[],
                          model="claude-haiku-4-5").stop == expected
    o = providers.build("openai", "sk", None)
    for finish, expected in (("length", providers.MAX_TOKENS),
                             ("content_filter", providers.REFUSAL)):
        script.responses.append(openai_completion(content="x", finish=finish))
        assert o.complete(system="S", messages=[{"role": "user", "content": "x"}], tools=[],
                          model="gpt-5").stop == expected
    assert "max_completion_tokens" in script.requests[-1]["body"]      # OpenAI's own API


def test_a_compatible_connection_without_a_base_url_is_refused():
    with pytest.raises(providers.ProviderError, match="base URL"):
        providers.build("openai_compatible", "k", None)
