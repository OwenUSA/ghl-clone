"""Finding a contact by phone number, whatever shape the number was typed in.

The problem this solves: `/api/contacts?q=` matched `phone` with a raw
`ILIKE %q%` against the stored string, so a dispatcher who typed `8135550102`
found nobody at all if the row said `(813) 555-0102`. Typing a number the way
the phone shows it is the single most natural thing to do in a CRM, and it was
the one shape that never worked.

**The rule is the last ten digits, on both sides.** That is not a new invention:
it is the identity rule the telephony project on owen-main already uses to match
an inbound caller to a contact, and having two different answers to "is this the
same number" between two systems that share a database would be a bug waiting
to happen. `+18135550102`, `(813) 555-0102`, `813-555-0102` and `8135550102` are
one person.

This matters right now, not theoretically: `feature/phone-normalisation` stores
new numbers as E.164 (`+1813…`) while deliberately leaving the existing rows in
whatever shape they were typed in, so the production database holds **both
formats at once** and will for as long as those rows are not edited. Matching on
the stored string can only ever find one of the two.

Two deliberate limits, stated rather than discovered later:

* **Only a query that looks like a phone number is digit-matched**, and the test
  is "nothing but digits and phone punctuation". `Maria` is never stripped down
  to `""` and matched as a number — an empty match key would match every row.
* **The digit clause is added to the existing name/email/business ILIKE, never
  substituted for it.** `24/7 Roofing` is all digits once the `/` is dropped, and
  a business really is called that; it now matches phone numbers *and* still
  matches its own business_name, instead of losing the obvious hit to a clever
  rule.

Why the digits are extracted with nested `REPLACE` rather than a regex: SQLite
(tests, bootstrap) has no `regexp_replace`, and this has to give the same answer
there as it does on Postgres. The cost is that only the punctuation in
`STRIPPED` is removed — a stored number carrying letters, `(813) 555-0102 ext 4`,
is not digit-matched. Numbers like that are refused at the write path anyway
(`phones.InvalidPhone`, "extensions are not stored on a contact number").
"""
import re

from sqlalchemy import func, literal

# A national number. The country code sits in front of it and is exactly what
# differs between the two stored formats, so it is what we ignore.
NATIONAL_DIGITS = 10

# Below this a numeric query is noise rather than a search: two digits appear in
# most of the phone book. Four is the shortest fragment people actually quote —
# "the customer ending 0102" — and it is what a dispatcher types when reading a
# number off a missed-call list.
MIN_MATCH_DIGITS = 4

# Everything a person puts *around* the digits of a phone number. Kept in step
# with `STRIPPED` below: the Python side and the SQL side must agree on what a
# digit-only number is, or the client and the server disagree about duplicates.
_PUNCTUATION = " ()-./+"
STRIPPED = tuple(_PUNCTUATION)

_NON_DIGIT = re.compile(r"\D")
# Non-empty, and nothing but digits and the punctuation above.
_PHONE_SHAPED = re.compile(r"^[0-9%s]+$" % re.escape(_PUNCTUATION))


def digits(raw: str | None) -> str:
    """Every digit in `raw`, in order. `"(813) 555-0102"` -> `"8135550102"`."""
    return _NON_DIGIT.sub("", raw or "")


def looks_like_phone(q: str | None) -> bool:
    """Is this query a phone number rather than a name?

    True only when the query is made of digits and phone punctuation and carries
    at least `MIN_MATCH_DIGITS` of them. A name is never mangled into digits, and
    a query with no digits at all can never produce a match-everything key.
    """
    text = (q or "").strip()
    if not text or not _PHONE_SHAPED.match(text):
        return False
    return len(digits(text)) >= MIN_MATCH_DIGITS


def match_key(q: str | None) -> str:
    """The digits a stored number has to end with: at most the last ten.

    A full number keeps its national ten and drops the country code, so the two
    stored formats collapse onto the same key. A fragment is returned as typed
    and matched loosely by `phone_clause` — a dispatcher with four digits is not
    claiming to know the whole number.
    """
    return digits(q)[-NATIONAL_DIGITS:]


def same_number(a: str | None, b: str | None) -> bool:
    """Do these two numbers identify the same line?

    The whole rule in one place, for callers that hold both strings already (the
    duplicate check on a create). Two numbers shorter than ten digits are only
    the same if they are digit-for-digit equal; anything else compares the last
    ten, so a country code never makes two identical lines look different.
    """
    da, db = digits(a), digits(b)
    if not da or not db:
        return False
    return da[-NATIONAL_DIGITS:] == db[-NATIONAL_DIGITS:]


def digits_expr(col):
    """SQL for "this column with its punctuation removed".

    Nested `REPLACE` so SQLite and Postgres give the same answer; see the module
    docstring for why this is not `regexp_replace`.
    """
    expr = func.coalesce(col, "")
    for ch in STRIPPED:
        expr = func.replace(expr, ch, "")
    return expr


def phone_clause(col, q: str):
    """A WHERE clause matching `col` against the phone number in `q`.

    A complete number (ten digits or more) must be the **end** of the stored
    digits, which is precisely "the last ten digits are equal" — a stored
    `+18135550102` and a stored `8135550102` both end in the same ten. A shorter
    fragment is matched anywhere in the number, so both `0102` (the tail people
    read off a caller ID) and `813555` (the front they remember) find the row.

    Callers must check `looks_like_phone(q)` first; the key is digits only, so
    there is nothing here for a LIKE wildcard to escape.
    """
    key = match_key(q)
    pattern = "%" + key if len(digits(q)) >= NATIONAL_DIGITS else "%" + key + "%"
    return digits_expr(col).like(literal(pattern))
