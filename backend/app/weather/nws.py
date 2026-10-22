"""Every request the weather agent makes - to the National Weather Service only (api.weather.gov,
free, no key). Three reads, nothing else:

  GET /points/{lat},{lng}        the spot's 2.5 km forecast grid and its forecast / county zones
  GET {forecastGridData}         the grid's raw forecast: probabilityOfPrecipitation and
                                 quantitativePrecipitation (mm), each value with its validTime
  GET /alerts/active?area=FL     every official alert in Florida now, with the zones it covers

Only a rounded map position (2 decimals, ~1 km) ever leaves the CRM - never an address or a
name. Tests install an `httpx.MockTransport` in TRANSPORT; conftest refuses the real host.
"""
from __future__ import annotations

from datetime import datetime

import httpx

from . import config as c

TRANSPORT: httpx.BaseTransport | None = None
TIMEOUT = 20.0


class NwsError(Exception):
    pass


def _get(url: str, params: dict | None = None) -> dict:
    try:
        with httpx.Client(transport=TRANSPORT, timeout=TIMEOUT, headers={
                "User-Agent": c.USER_AGENT, "Accept": "application/geo+json"}) as h:
            r = h.get(url if url.startswith("http") else c.NWS + url, params=params)
    except httpx.HTTPError as e:
        raise NwsError("the weather service could not be reached (%s)" % type(e).__name__) \
            from None
    if r.status_code != 200:
        raise NwsError("the weather service answered %d" % r.status_code)
    try:
        return r.json()
    except ValueError:
        raise NwsError("the weather service answered something unreadable") from None


def _zone(url: str | None) -> str | None:
    return url.rstrip("/").rsplit("/", 1)[-1] if url else None


def point(lat: float, lng: float) -> tuple[str, list[str]]:
    """(the grid's forecast URL, [forecast zone, county zone, fire zone])."""
    p = _get("/points/%.4f,%.4f" % (lat, lng)).get("properties") or {}
    grid = p.get("forecastGridData")
    if not grid:
        raise NwsError("the weather service has no forecast grid for this spot")
    zones = [z for z in (_zone(p.get("forecastZone")), _zone(p.get("county")),
                         _zone(p.get("fireWeatherZone"))) if z]
    return grid, zones


def grid(url: str) -> tuple[list[dict], list[dict], datetime | None]:
    """(chance-of-rain values, rain-amount values, when the NWS made the forecast)."""
    p = _get(url).get("properties") or {}
    updated = None
    if p.get("updateTime"):
        try:
            updated = datetime.fromisoformat(str(p["updateTime"]))
        except ValueError:
            updated = None
    pop = (p.get("probabilityOfPrecipitation") or {}).get("values") or []
    qpf = p.get("quantitativePrecipitation") or {}
    if (qpf.get("uom") or "wmoUnit:mm") not in ("wmoUnit:mm", "unit:mm"):
        raise NwsError("the weather service sent rain in %s, not mm" % qpf.get("uom"))
    return pop, qpf.get("values") or [], updated


def alerts() -> list[dict]:
    """Every active official alert in the state: {id, event, severity, headline, area, zones,
    onset, ends}."""
    out = []
    for f in _get("/alerts/active", {"area": c.STATE}).get("features") or []:
        p = f.get("properties") or {}
        zones = [_zone(z) for z in p.get("affectedZones") or []]
        out.append({"id": p.get("id") or f.get("id"), "event": p.get("event") or "",
                    "severity": p.get("severity"), "headline": p.get("headline"),
                    "area": p.get("areaDesc"), "zones": [z for z in zones if z],
                    "onset": p.get("onset") or p.get("effective"),
                    "ends": p.get("ends") or p.get("expires")})
    return out
