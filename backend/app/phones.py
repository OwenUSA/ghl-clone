"""Phone numbers: store E.164, display formatted.

Owner's decision (2026-09-09): store `+18135550102`, show `(813) 555-0102`, and
apply it to NEW contacts and edits only. Existing rows are **not** backfilled —
production holds real records whose numbers were entered by hand, and rewriting
them is a separate decision the owner has not made yet. See DECISIONS.md.

Why a hand-written helper rather than `phonenumbers`:

* The library is ~5 MB of Google metadata for every country on earth. This is a
  single-tenant CRM for one roofing company in Bradenton FL; every customer number
  is US, and the one exception is the owner's own Venezuelan mobile.
* The rules we actually need are "is it 10 digits" and "does it already carry a
  country code", which is 30 lines and is exhaustively tested below the API.
* DECISIONS.md records that native wheels have broken on this host before (the SSL
  interception note). A pure-Python dependency for two rules is not worth it.

The cost of that choice, stated plainly: we do **not** validate that a `+<cc>`
number is a real, assignable number in its own country. We check length only. A
number typed as `+58` followed by nonsense is stored as given rather than refused.
That is deliberate — refusing a number the owner knows is correct is worse for a
4-user company than storing one that turns out to be wrong, and nothing in v1
dials anything (`LoggingTransport` is the only transport).
"""
import re

# The default country. Not a constant to be "configured": the business is in
# Bradenton FL, and a bare 10-digit number is a US number.
DEFAULT_COUNTRY_CODE = "1"

# ITU-T E.164 caps the whole number at 15 digits. The floor is the shortest
# national number that exists in practice; below this it is a typo, not a number.
E164_MIN_DIGITS = 8
E164_MAX_DIGITS = 15

# Everything a person types around the digits: spaces, dashes, dots, brackets,
# and the "ext"/"x" that some directories append.
_SEPARATORS = re.compile(r"[\s().\-/]")
_EXTENSION = re.compile(r"(?:ext|x|extension)\.?\s*\d+$", re.IGNORECASE)


class InvalidPhone(ValueError):
    """A number that cannot be stored. The message is shown to the user."""


def normalize_phone(raw: str | None) -> str | None:
    """Return `raw` as E.164 (`+18135550102`), or None for a blank.

    Raises `InvalidPhone` with a sentence a person can act on. It never guesses:
    a number that already carries a `+` keeps its own country code, so the owner's
    Venezuelan `+58...` is not re-homed to the US.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None

    # "941 555 0100 ext 12" — the extension is not part of the number we dial.
    # Dropping it silently would change what the user typed, so it is refused.
    if _EXTENSION.search(text):
        raise InvalidPhone(
            "extensions are not stored on a contact number — enter the direct line")

    # `00` is the international prefix in most of the world and means exactly
    # what `+` means. Accept it so a number copied off a European business card
    # is not refused for a reason the user cannot see.
    if text.startswith("00"):
        text = "+" + text[2:]

    international = text.startswith("+")
    body = _SEPARATORS.sub("", text[1:] if international else text)

    if not body:
        raise InvalidPhone("that is not a phone number")
    if not body.isdigit():
        # Name the offending characters: "not a valid phone number" sends the
        # user hunting through a string that looks fine to them.
        bad = "".join(sorted({ch for ch in body if not ch.isdigit()}))
        raise InvalidPhone(
            "a phone number cannot contain %s" % ", ".join("“%s”" % c for c in bad))

    if international:
        if len(body) < E164_MIN_DIGITS:
            raise InvalidPhone(
                "%s is too short for an international number" % raw.strip())
        if len(body) > E164_MAX_DIGITS:
            raise InvalidPhone(
                "a phone number cannot be longer than %d digits" % E164_MAX_DIGITS)
        return "+" + body

    # No country code given, so it is a US number (see the module docstring).
    if len(body) == 11 and body.startswith(DEFAULT_COUNTRY_CODE):
        body = body[1:]
    if len(body) != 10:
        raise InvalidPhone(
            "a US number needs 10 digits — %s has %d. "
            "For a number outside the US, start it with + and its country code."
            % (raw.strip(), len(body)))
    # NANP forbids 0 and 1 as the first digit of an area code or an exchange, so
    # a 10-digit string starting with either is a mistyped or truncated number
    # rather than a number we could ever dial.
    if body[0] in "01" or body[3] in "01":
        raise InvalidPhone(
            "%s is not a valid US number — area code and exchange cannot start "
            "with 0 or 1" % raw.strip())
    return "+" + DEFAULT_COUNTRY_CODE + body


def format_phone(stored: str | None) -> str | None:
    """Render a stored number for display: `+18135550102` -> `(813) 555-0102`.

    Anything that is not a US E.164 number is returned unchanged. That covers two
    real cases and they are both deliberate:

    * `+584121234567` — the owner's Venezuelan number. There is no correct
      national format for it here, and inventing one would be worse than showing
      the number as dialled.
    * `(941) 555-0100` — a row written before normalisation existed. Nothing
      backfilled it, so it must still render as whatever it holds.
    """
    if not stored:
        return stored
    if stored.startswith("+1") and len(stored) == 12 and stored[1:].isdigit():
        d = stored[2:]
        return "(%s) %s-%s" % (d[0:3], d[3:6], d[6:10])
    return stored
