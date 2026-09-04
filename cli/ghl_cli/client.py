"""HTTP client for the GHL API.

Translates transport and HTTP failures into CliError subclasses so every command
exits with a meaningful code instead of a traceback.
"""
from typing import Any

import httpx

from . import config
from .errors import CliError, Forbidden, NotAuthenticated, NotFound, Rejected, Unreachable

START_HINT = (
    "start it with:  cd backend && uv run uvicorn app.main:app --port 8000\n"
    "  (if the port looks busy but nothing answers, a stale process is holding it "
    "— free it from PowerShell; `pkill` is a no-op on Windows)")


class Client:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 timeout: float = 20.0):
        self.base_url = (base_url or config.api_url()).rstrip("/")
        self.token = token if token is not None else config.token()
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)

    # -- plumbing ---------------------------------------------------------

    def _headers(self) -> dict:
        h = {"User-Agent": "ghl-cli"}
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        return h

    def request(self, method: str, path: str, *, params: dict | None = None,
                json: Any = None) -> Any:
        # Drop unset options rather than sending "?q=None". Passed through httpx's
        # params so an ISO timestamp's "+00:00" is encoded — concatenated into the
        # URL it decodes as a space and the datetime silently fails to parse.
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            r = self._http.request(method, path, params=clean, json=json,
                                   headers=self._headers())
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise Unreachable("backend not reachable at %s\n  %s"
                              % (self.base_url, START_HINT)) from exc
        except httpx.HTTPError as exc:
            raise CliError("request failed: %s" % exc) from exc
        return self._unwrap(r)

    def _unwrap(self, r: httpx.Response) -> Any:
        if r.is_success:
            if not r.content:
                return None
            try:
                return r.json()
            except ValueError:
                return r.text

        detail = self._detail(r)
        if r.status_code == 401:
            raise NotAuthenticated(
                "%s\n  run:  ghl auth login" % detail,
                {"status": 401})
        if r.status_code == 403:
            raise Forbidden(detail, {"status": 403})
        if r.status_code == 404:
            raise NotFound(detail, {"status": 404})
        if r.status_code in (400, 409, 422, 429):
            raise Rejected(detail, {"status": r.status_code})
        raise CliError("HTTP %d: %s" % (r.status_code, detail),
                       {"status": r.status_code})

    @staticmethod
    def _detail(r: httpx.Response) -> str:
        try:
            body = r.json()
        except ValueError:
            return r.text.strip() or r.reason_phrase
        if isinstance(body, dict):
            d = body.get("detail", body)
            if isinstance(d, list):        # pydantic validation errors
                return "; ".join(
                    "%s: %s" % (".".join(str(x) for x in e.get("loc", [])[1:]),
                                e.get("msg", "invalid"))
                    for e in d)
            return str(d)
        return str(body)

    # -- verbs ------------------------------------------------------------

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

    def patch(self, path, **kw):
        return self.request("PATCH", path, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)
