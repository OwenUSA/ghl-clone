"""Authentication and authorization.

LOCKED DECISION (DECISIONS.md): JWT in an httpOnly cookie, 15m access + 7d refresh,
roles ADMIN / DISPATCHER / TECH. Long-lived personal access tokens are an ADDITION
for the `ghl` CLI — a command line cannot hold a cookie session — not a replacement.

Two credentials, one resolver:

  Authorization: Bearer ghl_pat_...   the CLI and machine integrations
  Cookie: ghl_at=<jwt>                the browser

Password hashing is stdlib `hashlib.scrypt`, deliberately. bcrypt and argon2-cffi are
C extensions, and DECISIONS.md ("The SSL interception") records that this machine
MITMs TLS and that native wheels have broken here before. scrypt is memory-hard and
already in the standard library, so there is nothing to install and nothing to break.
"""
import base64
import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import ApiToken, Role, User

log = logging.getLogger("auth")


# ---------- passwords ----------

# maxmem MUST be passed explicitly. `hashlib.scrypt` defaults it to 0, which OpenSSL
# caps at 32 MB, and n=2**15/r=8/p=1 needs more than that — verified on this
# interpreter, it raises "ValueError: memory limit exceeded". Dropping to n=2**14 to
# fit the default cap would silently halve the work factor, which is the wrong fix.
_SCRYPT = {"n": 2**15, "r": 8, "p": 1, "dklen": 32, "maxmem": 134_217_728}


def _B64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str) -> str:
    """Return `scrypt$n$r$p$<salt_b64>$<hash_b64>`.

    Parameters travel with the hash so they can be raised later without
    invalidating existing passwords.
    """
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$%d$%d$%d$%s$%s" % (
        _SCRYPT["n"], _SCRYPT["r"], _SCRYPT["p"], _B64(salt), _B64(dk))


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verify. False for anything malformed or empty.

    The empty case is load-bearing: every user seeded before auth existed has
    `password_hash = ""` and must never be able to log in.
    """
    if not password or not stored:
        return False
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64),
                            n=int(n), r=int(r), p=int(p),
                            dklen=len(base64.b64decode(hash_b64)),
                            maxmem=_SCRYPT["maxmem"])
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, base64.b64decode(hash_b64))


# Burn the same time when the email is unknown, so a failed login takes as long as a
# real one. Without it, response latency is a user-enumeration oracle.
# Built lazily: one hash costs ~700ms on this host, and paying that at import time
# would slow every process start, including every pytest run.
_DUMMY_HASH: str | None = None


def waste_time_like_a_real_login() -> None:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("dummy-password-for-constant-time-login")
        return                      # the hash itself already cost a verify's worth
    verify_password("x", _DUMMY_HASH)


# ---------- JWT ----------

ALGO = "HS256"
ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=7)

COOKIE_ACCESS = "ghl_at"
COOKIE_REFRESH = "ghl_rt"
COOKIE_CSRF = "ghl_csrf"
REFRESH_PATH = "/api/auth"


def _load_secret() -> str:
    """Signing key: env first, else a generated one persisted next to the app.

    Generating a fresh key per process would log everyone out on every restart, and
    DECISIONS.md records that restarts are frequent here. A hard-coded fallback is
    worse still — a committed default key is a permanent token-forging key — so
    there isn't one.
    """
    env = os.getenv("JWT_SECRET")
    if env:
        return env
    path = Path(__file__).resolve().parent.parent / ".jwt_secret"
    if path.exists():
        return path.read_text().strip()
    generated = secrets.token_urlsafe(48)
    path.write_text(generated)
    log.warning("JWT_SECRET not set — generated one and wrote it to %s. "
                "Set JWT_SECRET in the environment for deployment.", path)
    return generated


SECRET = _load_secret()


def _make_token(user: User, typ: str, ttl: timedelta) -> str:
    now = datetime.now(UTC)
    return jwt.encode({
        "sub": str(user.id),
        "typ": typ,
        "tv": user.token_version,
        "role": user.role.value,
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_urlsafe(8),
    }, SECRET, algorithm=ALGO)


def make_access_token(user: User) -> str:
    return _make_token(user, "access", ACCESS_TTL)


def make_refresh_token(user: User) -> str:
    return _make_token(user, "refresh", REFRESH_TTL)


def decode_token(raw: str, expect: Literal["access", "refresh"]) -> dict:
    """Decode and validate. Raises HTTPException(401) with a distinguishable detail
    so the frontend can tell "refresh me" from "log in again"."""
    try:
        payload = jwt.decode(raw, SECRET, algorithms=[ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "%s token expired" % expect) from None
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid token") from None
    if payload.get("typ") != expect:
        raise HTTPException(401, "wrong token type")
    return payload


# ---------- API tokens ----------

TOKEN_PREFIX = "ghl_pat_"


def _digest(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def mint_api_token(user: User, *, name: str, scopes: str = "",
                   expires_in_days: int | None = None) -> tuple[str, ApiToken]:
    """Return (plaintext, unsaved ApiToken). The caller adds and commits.

    The secret is 256 bits of `secrets.token_urlsafe`, stored as a bare sha256. No
    salt and no KDF on purpose: a full-entropy random string is not guessable, so a
    slow hash would buy nothing and would add its cost to every CLI request.
    """
    plain = TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires_at = (datetime.now(UTC) + timedelta(days=expires_in_days)
                  if expires_in_days else None)
    token = ApiToken(
        user_id=user.id, name=name, token_hash=_digest(plain),
        prefix=plain[:len(TOKEN_PREFIX) + 8], scopes=scopes.strip(),
        expires_at=expires_at)
    return plain, token


def _resolve_api_token(db: Session, raw: str) -> tuple[User, ApiToken]:
    token = db.scalar(select(ApiToken).where(ApiToken.token_hash == _digest(raw)))
    if token is None:
        raise HTTPException(401, "unknown api token")
    if token.revoked_at is not None:
        raise HTTPException(401, "api token revoked")
    now = datetime.now(UTC)
    if token.expires_at is not None and as_aware(token.expires_at) <= now:
        raise HTTPException(401, "api token expired")

    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "token owner is inactive")

    # Coarse, so `ghl contacts list` in a loop does not turn every read into a write.
    last = as_aware(token.last_used_at) if token.last_used_at else None
    if last is None or (now - last) > timedelta(seconds=60):
        token.last_used_at = now
        db.commit()
    return user, token


def as_aware(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes even for timezone=True columns, so
    anything read from the database must be normalised before it is compared
    with a value built in Python. Shared with main.py rather than duplicated."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ---------- the principal ----------

@dataclass(frozen=True)
class Principal:
    user_id: int
    email: str
    name: str
    role: Role
    kind: Literal["cookie", "pat"]
    scopes: frozenset[str]        # empty = unrestricted, the role governs
    token_id: int | None = None


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Paths reachable without any credential. Small on purpose — everything not listed
# here is protected, including any route added in the future.
EXEMPT = {"/api/health", "/api/auth/login", "/api/auth/refresh", "/api/auth/logout"}

# Scoped tokens are blocked from the admin surface unless they carry "admin".
_ADMIN_PREFIXES = ("/api/users", "/api/jobs")


def _scope_allows(scopes: frozenset[str], method: str, path: str) -> bool:
    if not scopes:
        return True                                   # role governs
    # The telephony ingest surface. `/api/events` takes call and message events;
    # `/api/events/delivery` takes the carrier delivery receipts that answer "did
    # the text arrive". Both are the same feed from the same machine account, so
    # they sit behind the same single scope rather than making the owner mint a
    # second token to turn on delivery status.
    if ("events:write" in scopes and method == "POST"
            and path in ("/api/events", "/api/events/delivery")):
        return True
    if path.startswith(_ADMIN_PREFIXES):
        return "admin" in scopes
    if method in SAFE_METHODS:
        return bool(scopes & {"read", "write", "admin"})
    return bool(scopes & {"write", "admin"})


def check_csrf(request: Request) -> None:
    """Double-submit CSRF, for cookie-authenticated writes only.

    Cookies are ambient — a hostile page can cause the browser to send them. An
    `Authorization` header cannot be sent that way, so bearer-authenticated requests
    are exempt and the CLI needs no CSRF plumbing.
    """
    if request.method in SAFE_METHODS:
        return
    sent = request.headers.get("x-csrf-token")
    expected = request.cookies.get(COOKIE_CSRF)
    if not sent or not expected or not hmac.compare_digest(sent, expected):
        raise HTTPException(403, "csrf token missing or mismatched")


def current_principal(request: Request,
                      db: Session = Depends(get_db)) -> Principal | None:
    """Resolve either credential. Returns None for *absent*; raises for *bad*.

    A bearer token wins over a cookie when both are present, so a CLI call is never
    silently reinterpreted as a browser session.
    """
    # The exemption has to live HERE, not only in require_auth. FastAPI resolves
    # dependencies before the dependent runs, so an exemption checked only in
    # require_auth comes too late — this function would already have raised.
    # That broke refresh outright: POST /api/auth/refresh carries a deliberately
    # expired access cookie, and decoding it 401'd before the refresh logic ran,
    # so a session could never be renewed. Caught by test_expired_access_token_*.
    if request.method == "OPTIONS" or request.url.path in EXEMPT:
        return None

    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        raw = header[7:].strip()
        if raw.startswith(TOKEN_PREFIX):
            user, token = _resolve_api_token(db, raw)
            return Principal(user_id=user.id, email=user.email, name=user.name,
                             role=user.role, kind="pat",
                             scopes=frozenset(token.scopes.split()),
                             token_id=token.id)
        # A non-PAT bearer is an access JWT — the CLI never sends one, but curl might.
        payload = decode_token(raw, "access")
        return _principal_from_jwt(db, payload, request, csrf=False)

    cookie = request.cookies.get(COOKIE_ACCESS)
    if cookie:
        payload = decode_token(cookie, "access")
        return _principal_from_jwt(db, payload, request, csrf=True)

    return None


def _principal_from_jwt(db: Session, payload: dict, request: Request,
                        *, csrf: bool) -> Principal:
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(401, "user is inactive")
    # Immediate revocation: a password change or role change bumps token_version,
    # so outstanding tokens stop working now rather than in up to 15 minutes.
    if payload.get("tv") != user.token_version:
        raise HTTPException(401, "token has been invalidated — log in again")
    if csrf:
        check_csrf(request)
    return Principal(user_id=user.id, email=user.email, name=user.name,
                     role=user.role, kind="cookie", scopes=frozenset())


# ---------- the gate ----------

def require_auth(request: Request,
                 principal: Principal | None = Depends(current_principal)) -> None:
    """Applied app-wide via `FastAPI(dependencies=[Depends(require_auth)])`.

    One line, fail-closed: any route added later is protected by default, and the
    cost of forgetting is a 401 rather than a leak.

    Note this does NOT cover `/docs`, `/redoc` or `/openapi.json`. Those are
    registered as plain Starlette routes rather than APIRoutes, so they carry no
    dependant and app-level dependencies never run for them — verified against
    fastapi 0.141. They are left open deliberately (single-tenant, behind Caddy);
    pass `docs_url=None` if that ever stops being acceptable.
    """
    if request.method == "OPTIONS":
        return                                   # CORS preflight carries no credential
    path = request.url.path
    if path in EXEMPT:
        return
    if principal is None:
        raise HTTPException(401, "authentication required")
    if not _scope_allows(principal.scopes, request.method, path):
        raise HTTPException(403, "token scope does not allow this endpoint")


def require_role(*roles: Role):
    """Per-route authorization. Re-`Depends` the same resolver, which FastAPI caches
    per request, so the credential is parsed exactly once."""
    def dep(principal: Principal | None = Depends(current_principal)) -> Principal:
        if principal is None:
            raise HTTPException(401, "authentication required")
        if principal.role not in roles:
            raise HTTPException(
                403, "role %s may not do this" % principal.role.value)
        return principal
    return dep


def require_scope(scope: str):
    """For endpoints a machine token should reach, e.g. the telephony ingest."""
    def dep(principal: Principal | None = Depends(current_principal)) -> Principal:
        if principal is None:
            raise HTTPException(401, "authentication required")
        if principal.scopes and scope not in principal.scopes:
            raise HTTPException(403, "token lacks the %r scope" % scope)
        return principal
    return dep


def require_events_ingest(
        principal: Principal | None = Depends(current_principal)) -> Principal:
    """Authorization for POST /api/events, the telephony (OWEN) ingest.

    Two ways in: a machine token carrying `events:write` — the normal case — or an
    unscoped staff credential, so a human can replay an event by hand. A TECH cannot:
    ingesting a short inbound call fires the missed-call automation, which texts a
    customer.
    """
    if principal is None:
        raise HTTPException(401, "authentication required")

    # Role is checked FIRST, and unconditionally. Scopes only ever narrow what a
    # credential can do — they must never widen it. Any user may mint a token with
    # any scope string, so returning early on `"events:write" in scopes` let a TECH
    # self-issue a token and reach an endpoint their role forbids. Ingesting a
    # short inbound call fires the missed-call rule, which texts a customer.
    if principal.role not in (Role.ADMIN, Role.DISPATCHER):
        raise HTTPException(
            403, "role %s may not ingest events" % principal.role.value)

    if principal.scopes and "events:write" not in principal.scopes:
        raise HTTPException(403, "token lacks the 'events:write' scope")
    return principal


# Convenience dependencies, so a route gains authorization by adding one parameter
# rather than changing its decorator.
ADMIN = Depends(require_role(Role.ADMIN))
STAFF = Depends(require_role(Role.ADMIN, Role.DISPATCHER))
ANY_USER = Depends(require_role(Role.ADMIN, Role.DISPATCHER, Role.TECH))
EVENTS_INGEST = Depends(require_events_ingest)
