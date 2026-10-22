"""One pass of the weather agent (2026-10-22). Read `__init__.py` first.

    run(db, now) -> {"fetched", "visits", "leaks", "storms", "resolved", "texts", "stopped"?}

Every 3 hours it re-reads the NWS forecast for every spot a job sits on (one request per 2.5 km
grid, the spots looked up once and kept) and the official alerts for Florida, then keeps ONE
Dispatch item per finding in the "Weather" queue, each saying the NWS numbers it used and when the
NWS made them:
  * a booked visit in the next 7 days with a chance of rain at or above `rain_pct` during its
    hours - resolved by itself when the forecast drops or the visit moves;
  * heavy rain (half an inch or more, 50%+) in the next 48 h over customers with an open leak
    not yet repaired - one item per rainy day, listing them;
  * an official storm warning that just ended over roofs repaired in the last 60 days - one
    item per storm, listing them to check on.
Anything within 48 h rings the bell once. Texts go only to the numbers typed in its settings
(registry "Text the technician", Shadow by default): a morning summary and a text when an urgent
flag first appears. A failed read flags nothing new and keeps the last good forecast.
Never texts a customer, never writes to Zuper.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crmlink
from ..ai import engine as ai_engine
from ..dispatch import alerts
from ..dispatch import briefs as dbriefs
from ..dispatch import config as dc
from ..dispatch import rules as drules
from ..models import (
    AgentRegistryEntry,
    AgentRegistryLog,
    DispatchItem,
    DispatchJob,
    WeatherAlert,
    WeatherGrid,
    WeatherPoint,
    ZuperStatusHistory,
)
from ..registry import catalog as rcat
from ..registry import service as registry
from . import config as c
from . import nws, rules

log = logging.getLogger("weather")
QUEUE = "weather"
K_VISIT, K_LEAK, K_STORM = "weather_visit", "weather_leak", "weather_storm"
KINDS = (K_VISIT, K_LEAK, K_STORM)
NOT_REPAIRED_YET = set(dc.CLOSED) | set(dc.AFTER_REPAIR_STAGES) | set(dbriefs.INVOICED)


def settings(db: Session) -> c.Settings:
    row = db.get(AgentRegistryEntry, c.KEY)
    return c.Settings.from_config(row.config if row is not None else None)


def point_key(lat: float, lng: float) -> str:
    return "%.2f,%.2f" % (lat, lng)


def _when(dt: datetime) -> str:
    t = dc.local(dt)
    return t.strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


# ------------------------------------------------------------------------- reading the NWS

def point(db: Session, lat: float, lng: float, *, fetch: bool) -> WeatherPoint | None:
    k = point_key(lat, lng)
    row = db.scalar(select(WeatherPoint).where(WeatherPoint.point_key == k))
    if row is not None and (row.grid_url or not fetch):
        return row
    if not fetch:
        return None
    if row is None:
        row = WeatherPoint(point_key=k, lat=round(lat, 2), lng=round(lng, 2))
        db.add(row)
    try:
        row.grid_url, row.zones = nws.point(round(lat, 2), round(lng, 2))
        row.error = None
    except nws.NwsError as e:
        row.error = str(e)[:300]
    db.commit()
    return row


def refresh(db: Session, spots: list[tuple[float, float]], now: datetime, out: dict) -> None:
    grids = {p.grid_url for p in (point(db, la, ln, fetch=True) for la, ln in spots)
             if p is not None and p.grid_url}
    for url in sorted(grids):
        g = db.scalar(select(WeatherGrid).where(WeatherGrid.grid_url == url)) or WeatherGrid(
            grid_url=url)
        db.add(g)
        try:
            pop, qpf, updated = nws.grid(url)
            g.hours = rules.merge(g.hours, rules.hourly(pop, qpf), now)
            g.fetched_at, g.nws_updated_at, g.error = now, updated, None
            out["fetched"] += 1
        except nws.NwsError as e:
            g.error = str(e)[:300]                # the last good forecast stays
        db.commit()
    try:
        for a in nws.alerts():
            if not a.get("id") or db.scalar(select(WeatherAlert.id).where(
                    WeatherAlert.alert_id == a["id"])):
                continue
            db.add(WeatherAlert(alert_id=a["id"][:300], event=a["event"][:120],
                                severity=a.get("severity"), headline=a.get("headline"),
                                area=a.get("area"), zones=a.get("zones"),
                                onset=_parse(a.get("onset")), ends=_parse(a.get("ends")),
                                first_seen=now))
        db.commit()
    except nws.NwsError as e:
        log.info("alerts not read: %s", e)


def _parse(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def forecast(db: Session, lat: float | None, lng: float | None) -> tuple[dict, WeatherGrid | None]:
    """The kept forecast at a spot, never a network call (the booking slots, the chat)."""
    if lat is None or lng is None:
        return {}, None
    p = point(db, lat, lng, fetch=False)
    if p is None or not p.grid_url:
        return {}, None
    g = db.scalar(select(WeatherGrid).where(WeatherGrid.grid_url == p.grid_url))
    return (g.hours or {}) if g is not None else {}, g


def made(g: WeatherGrid | None) -> str:
    if g is None or g.fetched_at is None:
        return "no forecast read yet"
    at = g.nws_updated_at or g.fetched_at
    return "NWS forecast made %s" % _when(dc.aware(at))


# ------------------------------------------------------------------------- the flags

def _item(db: Session, key: str, kind: str, now: datetime, *, title: str, why: str, todo: str,
          urgent: bool, job: DispatchJob | None = None, evidence: dict | None = None
          ) -> tuple[DispatchItem, bool]:
    """(the open item for this key - updated, or created; created?)."""
    item = db.scalar(select(DispatchItem).where(DispatchItem.key == key))
    if item is not None and item.state != "open":
        return item, False                       # Done / Wrong stand
    new = item is None
    if new:
        item = DispatchItem(key=key, kind=kind, queue=QUEUE, state="open", created_at=now,
                            due_at=now, job_uid=job.job_uid if job else None,
                            job_number=job.job_number if job else None,
                            board=job.board if job else None,
                            phone=(job.phones or [None])[0] if job else None)
        db.add(item)
    item.title, item.why, item.todo = title[:300], why, todo
    ev = dict(item.evidence or {})
    ev.update(evidence or {})
    item.evidence, item.updated_at = ev, now
    if urgent and not ev.get("rang"):
        item.urgent = True
        db.flush()
        alerts.fire(db, [drules.Event(key=key, kind=kind, title=item.title, body=why,
                                      job_uid=item.job_uid, urgent=True)],
                    ring=True, boards={item.job_uid or "": item.board or ""})
        item.evidence = dict(ev, rang=True)
    return item, new


def visits(db: Session, s: c.Settings, now: datetime, out: dict, keep: set[str]) -> None:
    end = now + timedelta(days=c.HORIZON_DAYS)
    for j in db.scalars(select(DispatchJob).where(
            DispatchJob.is_open.is_(True), DispatchJob.scheduled_start.isnot(None),
            DispatchJob.lat.isnot(None))):
        start = dc.aware(j.scheduled_start)
        if not (now <= start <= end) or (j.status or "") in dc.CLOSED:
            continue
        hours, g = forecast(db, j.lat, j.lng)
        w = rules.visit_risk(hours, start, dc.aware(j.scheduled_end), s.rain_pct)
        if w is None:
            continue
        key = "weather:visit:%s:%s" % (j.job_uid, start.strftime("%Y%m%dT%H%M"))
        keep.add(key)
        stop = dc.aware(j.scheduled_end) or start + timedelta(hours=2)
        title = "Rain likely during #%s's visit %s (%d%%)" % (
            j.job_number or "?", _when(start), w.pop or 0)
        why = ("%s at the house between %s and %s%s - %s, %s." % (
            rules.pct_text(w), dc.local(start).strftime("%I:%M %p").lstrip("0"),
            dc.local(stop).strftime("%I:%M %p").lstrip("0"),
            "" if w.known == w.hours else " (the forecast covers %d of %d hours)" % (
                w.known, w.hours),
            rules.certainty(start, now), made(g)))
        _, new = _item(db, key, K_VISIT, now, title=title, why=why, job=j,
                       todo="Decide whether to keep or move the visit; tell the customer and "
                            "the technician if it moves.",
                       urgent=start - now <= timedelta(hours=c.URGENT_HOURS),
                       evidence={"pop": w.pop, "mm": w.mm, "start": start.isoformat()})
        out["visits"] += int(new)


def leaks(db: Session, now: datetime, out: dict, keep: set[str]) -> None:
    by_day: dict = {}
    for j in db.scalars(select(DispatchJob).where(
            DispatchJob.is_open.is_(True), DispatchJob.lat.isnot(None))):
        if (j.status or "") in NOT_REPAIRED_YET:
            continue
        hours, g = forecast(db, j.lat, j.lng)
        hit = rules.heavy_rain(hours, now)
        if hit is None:
            continue
        day, w = hit
        by_day.setdefault(day, []).append((j, w, g))
    for day, rows in sorted(by_day.items()):
        key = "weather:leaks:%s" % day.isoformat()
        keep.add(key)
        rows.sort(key=lambda r: -r[1].mm)
        lines = ["#%s %s, %s - %s" % (j.job_number or "?", j.customer_name or "",
                                     j.city or "", rules.pct_text(w))
                 for j, w, _ in rows[:40]]
        title = "Heavy rain %s: %d customer%s with an open leak" % (
            day.strftime("%a %b %d").replace(" 0", " "), len(rows), "" if len(rows) == 1 else "s")
        why = "Next 48 h at each house (%s):\n%s%s" % (
            made(rows[0][2]), "\n".join(lines),
            "\n... and %d more" % (len(rows) - 40) if len(rows) > 40 else "")
        _, new = _item(db, key, K_LEAK, now, title=title, why=why, urgent=True,
                       todo="Call them first: offer a tarp or the earliest visit before the rain.",
                       evidence={"jobs": [j.job_number for j, _, _ in rows]})
        out["leaks"] += int(new)


def storms(db: Session, now: datetime, out: dict, keep: set[str]) -> None:
    recent = [{"event": a.event, "zones": a.zones or [], "ends": dc.aware(a.ends), "area": a.area,
                   "headline": a.headline, "id": a.alert_id}
              for a in db.scalars(select(WeatherAlert).where(
                  WeatherAlert.event.in_(c.STORM_EVENTS), WeatherAlert.ends.isnot(None),
                  WeatherAlert.ends >= now - timedelta(hours=c.STORM_LOOKBACK_HOURS)))]
    if not recent:
        return
    repaired: dict[str, datetime] = {}
    for m in db.scalars(select(ZuperStatusHistory).where(
            ZuperStatusHistory.status_name == "Repair Complete",
            ZuperStatusHistory.changed_at >= now - timedelta(days=c.REPAIRED_LOOKBACK_DAYS))):
        repaired[m.job_uid] = dc.aware(m.changed_at)
    by_storm: dict = {}
    for uid, at in repaired.items():
        j = db.scalar(select(DispatchJob).where(DispatchJob.job_uid == uid))
        if j is None or j.lat is None:
            continue
        p = point(db, j.lat, j.lng, fetch=True)
        hit = rules.storm_hit(recent, (p.zones or []) if p else [], now)
        if hit:
            by_storm.setdefault(hit["id"], (hit, []))[1].append((j, at))
    for sid, (hit, rows) in by_storm.items():
        key = "weather:storm:%s" % sid[-80:]
        keep.add(key)
        lines = ["#%s %s, %s - repaired %s" % (j.job_number or "?", j.customer_name or "",
                                                j.city or "", dc.local(at).strftime("%b %d"))
                 for j, at in sorted(rows, key=lambda r: r[1], reverse=True)]
        title = "After the %s: %d recently repaired roof%s to check" % (
            hit["event"], len(rows), "" if len(rows) == 1 else "s")
        why = "%s (ended %s).\n%s" % (hit.get("headline") or hit["event"], _when(hit["ends"]),
                                      "\n".join(lines))
        _, new = _item(db, key, K_STORM, now, title=title, why=why, urgent=True,
                       todo="Call to check the roof held; book a check where it did not.",
                       evidence={"jobs": [j.job_number for j, _ in rows], "alert": sid})
        out["storms"] += int(new)


def resolve_gone(db: Session, now: datetime, keep: set[str], out: dict) -> None:
    for item in db.scalars(select(DispatchItem).where(
            DispatchItem.kind.in_((K_VISIT, K_LEAK)), DispatchItem.state == "open")):
        if item.key not in keep:
            item.state, item.closed_at = "resolved", now
            item.close_note = "The newer forecast no longer shows it (or the visit moved)."
            out["resolved"] += 1


# ------------------------------------------------------------------------- texts to staff

def _send(db: Session, s: c.Settings, body: str, what: str, now: datetime, out: dict) -> None:
    lv = registry.level(db, c.KEY, "text_staff")
    if lv == rcat.NEVER or not s.phones:
        return
    shadow = lv == rcat.SHADOW
    db.add(AgentRegistryLog(at=now, entry_key=c.KEY, what=what, target=",".join(s.phones),
                            new_value=body, reason="shadow" if shadow else "sending"))
    db.commit()                                  # recorded BEFORE anything is sent
    if shadow or not crmlink.configured():
        return
    for phone in s.phones:
        r = crmlink.send_sms("+1" + phone, body[:1200])
        if not r.ok:
            registry.alert_failure(db, c.KEY, "a weather text failed: %s" % r.reason, now)
            return
    out["texts"] += 1


def urgent_texts(db: Session, s: c.Settings, now: datetime, out: dict) -> None:
    fresh = [i for i in db.scalars(select(DispatchItem).where(
        DispatchItem.kind.in_(KINDS), DispatchItem.state == "open",
        DispatchItem.urgent.is_(True))) if not (i.evidence or {}).get("texted")]
    if not fresh:
        return
    body = "DTR weather alert:\n" + "\n".join("- " + i.title for i in fresh[:8])
    _send(db, s, body, "urgent_text", now, out)
    for i in fresh:
        i.evidence = dict(i.evidence or {}, texted=True)


def digest(db: Session, s: c.Settings, now: datetime, out: dict) -> None:
    local = dc.local(now)
    if local.hour < s.digest_hour or db.scalar(select(AgentRegistryLog.id).where(
            AgentRegistryLog.entry_key == c.KEY, AgentRegistryLog.what == "digest",
            AgentRegistryLog.at >= now - timedelta(hours=local.hour + 1,
                                                    minutes=local.minute))):
        return
    items = list(db.scalars(select(DispatchItem).where(
        DispatchItem.kind.in_(KINDS), DispatchItem.state == "open")
        .order_by(DispatchItem.created_at)))
    body = "DTR weather %s: %s" % (local.strftime("%a %m/%d"), (
        "\n" + "\n".join("- " + i.title for i in items[:12])) if items
        else "no visits at risk of rain, no heavy rain over open leaks.")
    if s.phones and registry.level(db, c.KEY, "text_staff") != rcat.NEVER:
        _send(db, s, body, "digest", now, out)
    else:
        db.add(AgentRegistryLog(at=now, entry_key=c.KEY, what="digest", new_value=body,
                                reason="no numbers to text"))
        db.commit()


# ------------------------------------------------------------------------- the pass

def due_fetch(db: Session, now: datetime) -> bool:
    last = db.scalar(select(WeatherGrid.fetched_at).order_by(WeatherGrid.fetched_at.desc())
                     .limit(1))
    return last is None or now - dc.aware(last) >= timedelta(hours=c.FETCH_EVERY_HOURS)


def run(db: Session, now: datetime | None = None, *, force: bool = False) -> dict:
    now = dc.aware(now or datetime.now(UTC))
    out = {"fetched": 0, "visits": 0, "leaks": 0, "storms": 0, "resolved": 0, "texts": 0}
    if not c.enabled():
        return dict(out, stopped=c.OFF_SENTENCE)
    if ai_engine.settings(db).paused:
        return dict(out, stopped="All AI agents are paused (Settings -> AI Connections).")
    s = settings(db)
    if force or due_fetch(db, now):
        spots = {(round(j.lat, 2), round(j.lng, 2)) for j in db.scalars(select(DispatchJob).where(
            DispatchJob.is_open.is_(True), DispatchJob.lat.isnot(None)))}
        refresh(db, sorted(spots), now, out)
        if out["fetched"] == 0 and spots:
            registry.alert_failure(db, c.KEY, "the NWS forecast could not be read; the last good "
                                              "one is kept", now)
        keep: set[str] = set()
        for step in (lambda: visits(db, s, now, out, keep), lambda: leaks(db, now, out, keep),
                     lambda: storms(db, now, out, keep)):
            try:
                step()
                db.commit()
            except Exception:
                db.rollback()
                log.exception("a weather step failed")
        if out["fetched"]:
            resolve_gone(db, now, keep, out)     # only on a fresh forecast
        db.commit()
    for step in (lambda: urgent_texts(db, s, now, out), lambda: digest(db, s, now, out)):
        try:
            step()
            db.commit()
        except Exception:
            db.rollback()
            log.exception("a weather text step failed")
    return out


def tick(session_factory, now: datetime | None = None) -> None:
    if not c.enabled():
        return
    db = session_factory()
    try:
        run(db, now)
    except Exception:
        db.rollback()
        log.exception("weather pass failed")
    finally:
        db.close()


def start_thread(session_factory, stop: threading.Event) -> threading.Thread:
    def loop():
        while True:
            tick(session_factory)
            if stop.wait(c.TICK_SECONDS):
                return
    t = threading.Thread(target=loop, name="weather", daemon=True)
    t.start()
    return t
