"""Exit codes and the errors that map to them.

The CLI's main caller is an agent, so a failure has to be actionable without
reading prose. Every failure exits with a distinct code and, under --json, prints
a single JSON object on stderr.

    0  ok
    1  unexpected error
    2  usage error, or a guarded command run without --yes
    3  not authenticated / token expired or revoked
    4  not found (404, or a name that matched nothing)
    5  ambiguous name — several records matched
    6  backend unreachable
    7  forbidden (your role may not do this)
    8  rejected (validation, or a conflict such as 409)
"""

OK = 0
ERROR = 1
USAGE = 2
AUTH = 3
NOT_FOUND = 4
AMBIGUOUS = 5
UNREACHABLE = 6
FORBIDDEN = 7
REJECTED = 8


class CliError(Exception):
    """Base class. `code` is the process exit code; `detail` is merged into the
    JSON error object so a caller can act on it (e.g. the candidate list)."""
    code = ERROR
    kind = "error"

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class Unreachable(CliError):
    code = UNREACHABLE
    kind = "unreachable"


class NotAuthenticated(CliError):
    code = AUTH
    kind = "unauthenticated"


class Forbidden(CliError):
    code = FORBIDDEN
    kind = "forbidden"


class NotFound(CliError):
    code = NOT_FOUND
    kind = "not_found"


class Ambiguous(CliError):
    code = AMBIGUOUS
    kind = "ambiguous"


class Rejected(CliError):
    code = REJECTED
    kind = "rejected"


class NeedsConfirmation(CliError):
    code = USAGE
    kind = "needs_confirmation"
