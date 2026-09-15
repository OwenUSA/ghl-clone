"""Provider API keys, encrypted at rest.

Fernet (AES-128-CBC + HMAC-SHA256, from `cryptography`) under the key in AI_SECRETS_KEY.
The plaintext key exists only inside a request that saves it and inside a run that uses it;
it is never returned by an API, never logged and never written to a run's transcript. What
anyone can read back is "•••• last4".

With AI_SECRETS_KEY unset, saving a connection is refused with a sentence saying what to set.
Nothing else in the CRM depends on it.
"""
import os

from cryptography.fernet import Fernet, InvalidToken

ENV = "AI_SECRETS_KEY"
GENERATE = ('python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"')
NOT_CONFIGURED = ("AI connections cannot be saved: the server has no AI_SECRETS_KEY. The "
                  "operator must set it (generate one with %s) and restart the API and the "
                  "worker." % GENERATE)
BAD_KEY = ("AI_SECRETS_KEY is set but is not a valid Fernet key (32 url-safe base64 bytes). "
           "Generate one with %s." % GENERATE)
CANNOT_DECRYPT = ("this connection's API key cannot be decrypted with the current "
                  "AI_SECRETS_KEY — it was saved under a different key. Enter the API key again.")


class SecretsUnavailable(Exception):
    """A sentence for a person: why a key cannot be stored or read."""


def _fernet() -> Fernet:
    raw = (os.getenv(ENV) or "").strip()
    if not raw:
        raise SecretsUnavailable(NOT_CONFIGURED)
    try:
        return Fernet(raw.encode())
    except (ValueError, TypeError):
        raise SecretsUnavailable(BAD_KEY) from None


def configured() -> bool:
    try:
        _fernet()
    except SecretsUnavailable:
        return False
    return True


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    f = _fernet()
    try:
        return f.decrypt(token.encode()).decode()
    except InvalidToken:
        raise SecretsUnavailable(CANNOT_DECRYPT) from None


def last4(plain: str) -> str:
    plain = plain.strip()
    return plain[-4:] if len(plain) >= 8 else ""


def masked(last: str) -> str:
    return "•••• " + last if last else "••••"
