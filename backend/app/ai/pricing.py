"""What a run cost.

Prices are US dollars per 1M tokens held as integer MICRO-dollars, and costs are integer
micro-dollars, so money is never a float (the rule `value_cents` follows). A connection with
no price has an UNKNOWN cost — `None`, drawn as "—" — never zero.

KNOWN_PRICES prefill the connection form and are editable there. Anthropic's are the list
prices for the current models; the OpenAI rows were the published list prices when this was
written and must be checked by the owner before they are relied on (DECISIONS.md).

Cached tokens: Anthropic reports cache writes and cache reads separately from `input_tokens`,
billed at 1.25x and 0.1x the input price (5-minute cache). OpenAI reports cached tokens as a
PART of prompt_tokens; the adapter moves them out of `input_tokens` into `cache_read`, billed
at 0.5x — the conservative end of OpenAI's discounts.
"""
from decimal import ROUND_HALF_UP, Decimal

MICRO = 1_000_000


def dollars(micros: int | None) -> str | None:
    if micros is None:
        return None
    return str((Decimal(micros) / MICRO).quantize(Decimal("0.000001")))


def to_micros(value) -> int | None:
    """A price as typed ("2.50", 2.5, None) -> micro-dollars; None/"" stays unknown."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    d = Decimal(str(value).strip())
    if d < 0:
        raise ValueError("a price cannot be negative")
    return int((d * MICRO).to_integral_value(ROUND_HALF_UP))


# model id -> (input, output) in micro-dollars per 1M tokens
KNOWN_PRICES: dict[str, tuple[int, int]] = {
    "claude-opus-5": (5_000_000, 25_000_000),
    "claude-sonnet-5": (2_000_000, 10_000_000),
    "claude-haiku-4-5": (1_000_000, 5_000_000),
    "gpt-5": (1_250_000, 10_000_000),
    "gpt-5-mini": (250_000, 2_000_000),
    "gpt-5-nano": (50_000, 400_000),
    "gpt-4.1": (2_000_000, 8_000_000),
    "gpt-4.1-mini": (400_000, 1_600_000),
    "gpt-4.1-nano": (100_000, 400_000),
    "gpt-4o": (2_500_000, 10_000_000),
    "gpt-4o-mini": (150_000, 600_000),
}

# Multipliers on the INPUT price for cached tokens, per provider family.
CACHE_WRITE = {"anthropic": Decimal("1.25"), "openai": Decimal(1)}
CACHE_READ = {"anthropic": Decimal("0.1"), "openai": Decimal("0.5")}


def family(provider: str) -> str:
    return "anthropic" if provider == "anthropic" else "openai"


def cost_micros(provider: str, price_in: int | None, price_out: int | None, *,
                input_tokens: int, output_tokens: int, cache_write: int = 0,
                cache_read: int = 0) -> int | None:
    """Micro-dollars for this usage, or None when either price is unknown."""
    if price_in is None or price_out is None:
        return None
    fam = family(provider)
    total = (Decimal(input_tokens) * price_in
             + Decimal(output_tokens) * price_out
             + Decimal(cache_write) * price_in * CACHE_WRITE[fam]
             + Decimal(cache_read) * price_in * CACHE_READ[fam]) / MICRO
    return int(total.to_integral_value(ROUND_HALF_UP))
