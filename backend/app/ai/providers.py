"""ONE provider interface, so the engine never branches on who the provider is.

    complete(system, messages, tools, model, max_tokens) -> Turn
    test(model) -> TestResult          one minimal request; writes nothing anywhere
    list_models() -> list[str]

`messages` is this module's own neutral shape, which each adapter translates:

    {"role": "user", "content": "<text>"}
    {"role": "assistant", "text": "<text>", "tool_calls": [ToolCall...], "raw": <provider>}
    {"role": "tool_results", "results": [{"id", "name", "content", "is_error"}]}

`raw` is what the provider returned for that assistant turn, sent back unchanged on the next
request when the same provider produced it — an Anthropic model's thinking blocks must be
echoed as they came.

Anthropic uses the official `anthropic` SDK (Messages API, custom tools, tool_result loop);
OpenAI and every OpenAI-compatible server use the official `openai` SDK with `base_url`
(chat.completions + tools). Both SDKs sit on `httpx2`. Tests replace the HTTP client through
`HTTP_CLIENT_FACTORY` — a mock transport at the HTTP boundary — and no test ever reaches a
provider (tests/test_ai_network_guard.py).

What is deliberately NOT sent to Anthropic: `temperature`, `top_p` or a thinking budget (a
400 on Claude 4.6+ / 5 models); thinking is left at the model's default. The stable system
prompt carries `cache_control`.

Errors come back as ProviderError with a sentence written here, never the SDK's own message
verbatim: an OpenAI 401 body quotes part of the key.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import anthropic
import openai

ANTHROPIC_BASE_URL = "https://api.anthropic.com"
OPENAI_BASE_URL = "https://api.openai.com/v1"
# The hosts a test must never reach. tests/test_ai_network_guard.py blocks them.
PROVIDER_HOSTS = ("api.anthropic.com", "api.openai.com")

TIMEOUT_SECONDS = 60.0
MAX_RETRIES = 1

# Tests set this to return an httpx2.Client on a mock transport. None = the SDK's own.
HTTP_CLIENT_FACTORY: Callable[[], object] | None = None


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict | None
    raw: str = ""
    # Set when the provider's arguments were not a JSON object; the call is never run.
    parse_error: str | None = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write: int = 0
    cache_read: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_write += other.cache_write
        self.cache_read += other.cache_read


END = "end"
TOOL_USE = "tool_use"
MAX_TOKENS = "max_tokens"
REFUSAL = "refusal"
OTHER = "other"


@dataclass
class Turn:
    text: str
    tool_calls: list[ToolCall]
    stop: str
    usage: Usage
    raw_stop: str | None = None
    raw: list | None = None
    model: str | None = None


@dataclass
class TestResult:
    ok: bool
    sentence: str
    latency_ms: int
    model: str = ""
    details: dict = field(default_factory=dict)


class ProviderError(Exception):
    def __init__(self, sentence: str, *, status: int | None = None):
        super().__init__(sentence)
        self.sentence = sentence
        self.status = status


_KEYISH = re.compile(r"(sk-[A-Za-z0-9_\-*.]{4,}|key[-_][A-Za-z0-9_\-*.]{6,})")


def _redact(text: str, secret: str) -> str:
    if secret:
        text = text.replace(secret, "••••")
    return _KEYISH.sub("••••", text)


def _provider_message(exc: Exception, secret: str) -> str:
    """The provider's own explanation of a 400, redacted and cut short."""
    body = getattr(exc, "body", None)
    msg = ""
    if isinstance(body, dict):
        err = body.get("error")
        msg = (err.get("message") if isinstance(err, dict) else None) or body.get("message") or ""
    return _redact(str(msg), secret)[:300]


def _sentence(exc: Exception, who: str, model: str, secret: str) -> ProviderError:
    status = getattr(exc, "status_code", None)
    for sdk in (anthropic, openai):
        if isinstance(exc, sdk.AuthenticationError):
            return ProviderError("%s rejected the API key (401). Check the key." % who,
                                 status=status)
        if isinstance(exc, sdk.PermissionDeniedError):
            return ProviderError("%s refused this key permission (403)." % who, status=status)
        if isinstance(exc, sdk.NotFoundError):
            return ProviderError("%s does not know the model “%s”, or the URL is wrong (404)."
                                 % (who, model), status=status)
        if isinstance(exc, sdk.RateLimitError):
            return ProviderError("%s is rate limiting this key (429). Try again shortly."
                                 % who, status=status)
        if isinstance(exc, sdk.APITimeoutError):
            return ProviderError("%s did not answer within %d seconds."
                                 % (who, TIMEOUT_SECONDS))
        if isinstance(exc, sdk.APIConnectionError):
            return ProviderError("Could not reach %s. Check the base URL and the network."
                                 % who)
        if isinstance(exc, sdk.BadRequestError):
            detail = _provider_message(exc, secret)
            return ProviderError("%s refused the request (400)%s" % (
                who, ": " + detail if detail else "."), status=status)
        if isinstance(exc, sdk.APIStatusError):
            return ProviderError("%s answered with an error (%s)." % (who, status),
                                 status=status)
    return ProviderError("%s could not be used: %s" % (who, type(exc).__name__))


def _parse_arguments(value) -> tuple[dict | None, str, str | None]:
    """A tool call's arguments as a dict — or why not. Never run a call that fails this."""
    if isinstance(value, dict):
        return value, json.dumps(value, sort_keys=True), None
    raw = value if isinstance(value, str) else json.dumps(value)
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except (ValueError, TypeError):
        return None, raw, "the tool arguments were not valid JSON"
    if not isinstance(parsed, dict):
        return None, raw, "the tool arguments were not a JSON object"
    return parsed, raw, None


class Provider:
    who = "The provider"

    def __init__(self, api_key: str, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = base_url

    def complete(self, *, system: str, messages: list[dict], tools: list[ToolSpec],
                 model: str, max_tokens: int = 4096) -> Turn:
        raise NotImplementedError

    def list_models(self) -> list[str]:
        raise NotImplementedError

    def test(self, model: str) -> TestResult:
        started = time.monotonic()
        try:
            turn = self.complete(system="This is a connection test.",
                                 messages=[{"role": "user", "content": "Reply with OK."}],
                                 tools=[], model=model, max_tokens=64)
        except ProviderError as e:
            return TestResult(False, e.sentence, int((time.monotonic() - started) * 1000),
                              model)
        ms = int((time.monotonic() - started) * 1000)
        return TestResult(True, "Connected — %s answered with %s in %d ms." % (
            self.who, turn.model or model, ms), ms, turn.model or model,
            {"stop": turn.stop, "input_tokens": turn.usage.input_tokens,
             "output_tokens": turn.usage.output_tokens})

    def _http(self) -> dict:
        return {"http_client": HTTP_CLIENT_FACTORY()} if HTTP_CLIENT_FACTORY else {}


# ---------------------------------------------------------------- Anthropic

_ANTHROPIC_ECHO = {"text", "tool_use", "thinking", "redacted_thinking"}


class AnthropicProvider(Provider):
    who = "Anthropic"

    def _client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(api_key=self.api_key,
                                   base_url=self.base_url or ANTHROPIC_BASE_URL,
                                   timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES,
                                   **self._http())

    @staticmethod
    def _messages(messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                if m.get("raw_provider") == "anthropic" and m.get("raw"):
                    content = m["raw"]
                else:
                    content = ([{"type": "text", "text": m["text"]}] if m.get("text") else [])
                    content += [{"type": "tool_use", "id": c.id, "name": c.name,
                                 "input": c.arguments or {}} for c in m.get("tool_calls", [])]
                out.append({"role": "assistant", "content": content})
            elif m["role"] == "tool_results":
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"],
                     **({"is_error": True} if r.get("is_error") else {})}
                    for r in m["results"]]})
        return out

    def complete(self, *, system, messages, tools, model, max_tokens=4096) -> Turn:
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            # The compiled prompt is stable for a published version, so it is cached.
            "system": [{"type": "text", "text": system,
                        "cache_control": {"type": "ephemeral"}}],
            "messages": self._messages(messages),
        }
        if tools:
            kwargs["tools"] = [{"name": t.name, "description": t.description,
                                "input_schema": t.input_schema} for t in tools]
        try:
            resp = self._client().messages.create(**kwargs)
        except anthropic.AnthropicError as e:
            raise _sentence(e, self.who, model, self.api_key) from None
        texts, calls, raw = [], [], []
        for block in resp.content:
            btype = getattr(block, "type", "")
            if btype in _ANTHROPIC_ECHO:
                raw.append(block.model_dump(mode="json", exclude_none=True))
            if btype == "text":
                texts.append(block.text)
            elif btype == "tool_use":
                args, raw_args, err = _parse_arguments(block.input)
                calls.append(ToolCall(block.id, block.name, args, raw_args, err))
        u = resp.usage
        usage = Usage(input_tokens=u.input_tokens or 0, output_tokens=u.output_tokens or 0,
                      cache_write=getattr(u, "cache_creation_input_tokens", None) or 0,
                      cache_read=getattr(u, "cache_read_input_tokens", None) or 0)
        stop = {"end_turn": END, "stop_sequence": END, "tool_use": TOOL_USE,
                "max_tokens": MAX_TOKENS, "model_context_window_exceeded": MAX_TOKENS,
                "refusal": REFUSAL}.get(resp.stop_reason or "", OTHER)
        return Turn("\n".join(texts).strip(), calls, stop, usage, resp.stop_reason, raw,
                    resp.model)

    def list_models(self) -> list[str]:
        try:
            return sorted({m.id for m in self._client().models.list(limit=100)})
        except anthropic.AnthropicError as e:
            raise _sentence(e, self.who, "", self.api_key) from None


# ---------------------------------------------------------------- OpenAI / compatible

class OpenAIProvider(Provider):
    who = "OpenAI"
    compatible = False

    def _client(self) -> openai.OpenAI:
        return openai.OpenAI(api_key=self.api_key,
                             base_url=self.base_url or OPENAI_BASE_URL,
                             timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES,
                             **self._http())

    @staticmethod
    def _messages(system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                msg: dict = {"role": "assistant", "content": m.get("text") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.name, "arguments": c.raw or "{}"}}
                        for c in m["tool_calls"]]
                out.append(msg)
            elif m["role"] == "tool_results":
                out.extend({"role": "tool", "tool_call_id": r["id"], "content": r["content"]}
                           for r in m["results"])
        return out

    def complete(self, *, system, messages, tools, model, max_tokens=4096) -> Turn:
        kwargs: dict = {"model": model, "messages": self._messages(system, messages)}
        # OpenAI's own current models take max_completion_tokens; most compatible servers
        # still only understand max_tokens.
        kwargs["max_tokens" if self.compatible else "max_completion_tokens"] = max_tokens
        if tools:
            kwargs["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.input_schema}}
                for t in tools]
        try:
            resp = self._client().chat.completions.create(**kwargs)
        except openai.OpenAIError as e:
            raise _sentence(e, self.who, model, self.api_key) from None
        if not resp.choices:
            raise ProviderError("%s returned no answer." % self.who)
        choice = resp.choices[0]
        msg = choice.message
        calls = []
        for tc in msg.tool_calls or []:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            args, raw_args, err = _parse_arguments(fn.arguments or "")
            calls.append(ToolCall(tc.id, fn.name, args, raw_args, err))
        u = resp.usage
        cached = 0
        if u is not None and getattr(u, "prompt_tokens_details", None) is not None:
            cached = getattr(u.prompt_tokens_details, "cached_tokens", None) or 0
        usage = Usage(input_tokens=max(0, (u.prompt_tokens if u else 0) - cached),
                      output_tokens=u.completion_tokens if u else 0, cache_read=cached)
        finish = choice.finish_reason or ""
        if getattr(msg, "refusal", None):
            stop = REFUSAL
        else:
            stop = {"stop": END, "tool_calls": TOOL_USE, "function_call": TOOL_USE,
                    "length": MAX_TOKENS, "content_filter": REFUSAL}.get(finish, OTHER)
            if stop == OTHER and calls:
                stop = TOOL_USE
        text = msg.content or (msg.refusal if getattr(msg, "refusal", None) else "") or ""
        return Turn(text.strip(), calls, stop, usage, finish, None, resp.model)

    def list_models(self) -> list[str]:
        try:
            return sorted({m.id for m in self._client().models.list()})
        except openai.OpenAIError as e:
            raise _sentence(e, self.who, "", self.api_key) from None


class CompatibleProvider(OpenAIProvider):
    who = "The OpenAI-compatible server"
    compatible = True


def build(provider: str, api_key: str, base_url: str | None) -> Provider:
    if provider == "anthropic":
        return AnthropicProvider(api_key)
    if provider == "openai":
        return OpenAIProvider(api_key)
    if provider == "openai_compatible":
        if not base_url:
            raise ProviderError("An OpenAI-compatible connection needs a base URL.")
        return CompatibleProvider(api_key, base_url)
    raise ProviderError("Unknown provider %r." % provider)
