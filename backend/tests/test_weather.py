"""The weather agent (app/weather), by behaviour - the important ones.

Rules: an NWS validTime spreads over its hours; a visit at 50%+ is at risk, 40% is not; dry days
need every working hour forecast and under 30%; heavy rain needs half an inch at 50%+; a storm
warning that ended over a roof's zone in the last day is found.
Pass (the weather service faked at HTTP): a booked visit tomorrow at 70% -> ONE Weather item +
the bell; a newer forecast at 20% resolves it; heavy rain over an open leak -> one summary item;
texts to the typed numbers are only recorded in Shadow; off on the server -> nothing read.
Scheduling: booking slots skip a rainy day and say each slot's chance.
"""
import json
import re
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from app import crmlink
from app.dispatch import booking
from app.models import (
    AgentRegistryEntry,
    AgentRegistryLog,
    AiAlert,
    DispatchItem,
    DispatchJob,
    Role,
    User,
)
from app.weather import nws, rules, service

NOW = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)            # Thu 10 AM New York
TOMORROW_9 = datetime(2026, 10, 23, 13, 0, tzinfo=UTC)     # Fri 9 AM New York


def values(start: datetime, hours: int, value, step: int = 1) -> list[dict]:
    return [{"validTime": "%s/PT%dH" % ((start + timedelta(hours=i)).isoformat(), step),
             "value": value} for i in range(0, hours, step)]


# ------------------------------------------------------------------------- rules

def test_reading_a_forecast():
    assert rules.span("2026-10-22T18:00:00+00:00/PT3H")[1] == 3
    assert rules.span("2026-10-22T18:00:00+00:00/P1DT6H")[1] == 30
    h = rules.hourly(values(TOMORROW_9, 6, 70, step=3), values(TOMORROW_9, 6, 6.0, step=3))
    assert h[rules.key(TOMORROW_9)] == [70, 2.0] and len(h) == 6     # 6 mm over 3 h
    assert rules.visit_risk(h, TOMORROW_9, TOMORROW_9 + timedelta(hours=2), 50).pop == 70
    assert rules.visit_risk(h, TOMORROW_9, TOMORROW_9 + timedelta(hours=2), 80) is None
    week = rules.hourly(values(NOW, 24 * 8, 10), values(NOW, 24 * 8, 0.0))
    rainy = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)               # Sat 11 AM
    week[rules.key(rainy)] = [60, 3.0]
    dry = [d for d, _ in rules.dry_days(week, date(2026, 10, 22))]
    assert date(2026, 10, 23) in dry and date(2026, 10, 24) not in dry
    wet = rules.hourly(values(NOW, 48, 80), values(NOW, 48, 0.5))
    assert rules.heavy_rain(wet, NOW)[1].mm == 24.0
    assert rules.heavy_rain(rules.hourly(values(NOW, 48, 80), values(NOW, 48, 0.1)), NOW) is None
    storm = {"event": "Severe Thunderstorm Warning", "zones": ["FLC081"],
             "ends": NOW - timedelta(hours=3), "id": "a1"}
    assert rules.storm_hit([storm], ["FLZ155", "FLC081"], NOW)["id"] == "a1"
    assert rules.storm_hit([storm], ["FLZ999"], NOW) is None


# ------------------------------------------------------------------------- the pass

class FakeNws:
    def __init__(self):
        self.pop, self.mm = 70, 0.0
        self.alerts: list[dict] = []
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        assert "Main St" not in url and "Smith" not in url          # positions only
        if re.search(r"/points/", url):
            return httpx.Response(200, json={"properties": {
                "forecastGridData": "https://api.weather.gov/gridpoints/TBW/80,90",
                "forecastZone": "https://api.weather.gov/zones/forecast/FLZ155",
                "county": "https://api.weather.gov/zones/county/FLC081"}})
        if "/gridpoints/" in url:
            start = NOW - timedelta(hours=1)
            return httpx.Response(200, json={"properties": {
                "updateTime": (NOW - timedelta(hours=1)).isoformat(),
                "probabilityOfPrecipitation": {"values": values(start, 24 * 8, self.pop)},
                "quantitativePrecipitation": {"uom": "wmoUnit:mm",
                                              "values": values(start, 24 * 8, self.mm)}}})
        if "/alerts/active" in url:
            return httpx.Response(200, json={"features": self.alerts})
        return httpx.Response(404)


@pytest.fixture()
def wx(db, monkeypatch):
    monkeypatch.setenv("WEATHER_ENABLED", "true")
    fake = FakeNws()
    monkeypatch.setattr(nws, "TRANSPORT", httpx.MockTransport(fake.handler))
    sent = []
    monkeypatch.setattr(crmlink, "configured", lambda: True)
    monkeypatch.setattr(crmlink, "send_sms", lambda *a, **k: sent.append(a) or
                        crmlink.LinkResult(True, 200, "", {}))
    fake.sent = sent
    db.add(User(email="boss@x.test", name="Boss", role=Role.ADMIN))
    db.add(AgentRegistryEntry(key="weather", config={"phones": ["9545550000"]}))
    db.add(DispatchJob(job_uid="job-1", job_number="701", board="AHS - Inspection",
                       status="Scheduled", customer_name="Ana Smith", phones=["9415550101"],
                       is_open=True, lat=27.4872771, lng=-82.5889012, city="Bradenton",
                       address="1 Main St", scheduled_start=TOMORROW_9,
                       scheduled_end=TOMORROW_9 + timedelta(hours=2)))
    db.commit()
    return fake


def test_a_rainy_visit_is_flagged_and_cleared_by_a_newer_forecast(db, wx):
    out = service.run(db, NOW)
    item = db.query(DispatchItem).one()
    assert (item.kind, item.queue, item.urgent) == ("weather_visit", "weather", True)
    assert "70%" in item.title and "NWS forecast made" in item.why
    assert db.query(AiAlert).count() == 1 and out["visits"] == 1
    texts = db.query(AgentRegistryLog).filter(AgentRegistryLog.what == "urgent_text").all()
    assert len(texts) == 1 and texts[0].reason == "shadow" and wx.sent == []   # Shadow
    wx.pop = 20
    service.run(db, NOW + timedelta(hours=3, minutes=5))
    assert db.query(DispatchItem).one().state == "resolved"


def test_heavy_rain_over_an_open_leak_is_one_summary(db, wx):
    wx.pop, wx.mm = 90, 0.6                         # ~29 mm in 48 h
    db.query(DispatchJob).one().scheduled_start = None
    db.commit()
    service.run(db, NOW)
    item = db.query(DispatchItem).one()
    assert item.kind == "weather_leak" and "1 customer with an open leak" in item.title
    assert "#701" in item.why


def test_off_on_the_server_reads_nothing(db, wx, monkeypatch):
    monkeypatch.setenv("WEATHER_ENABLED", "false")
    assert "WEATHER_ENABLED" in service.run(db, NOW)["stopped"] and wx.requests == []


# ------------------------------------------------------------------------- scheduling

def test_booking_skips_a_rainy_day_and_says_each_slots_chance():
    friday = date(2026, 10, 23)

    def rain(start, end):
        return 80 if start.astimezone(UTC).date() == friday else 10

    got = booking.slots(lat=26.2, lng=-80.2, kind="inspection", visits=[], now=NOW,
                        rain=rain, rain_pct=50)
    assert got and all(s.start.astimezone(UTC).date() != friday for s in got)
    assert got[0].rain_pct == 10 and "rain 10%" in got[0].why
    assert json.dumps(got[0].as_dict())
