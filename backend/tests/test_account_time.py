"""The Book appointment modal's times, executed under node rather than read.

`frontend/src/lib/accountTime.ts` imports nothing, like calendarGrid.ts, so node
imports the shipped module directly. Every script runs with the PROCESS timezone
set somewhere other than the account's — Berlin, Caracas, Tokyo — because the
bug this module exists to prevent is a laptop's own zone leaking into a booking:
picking 1:30 PM must mean 1:30 PM in America/New_York, on either side of a DST
change, whatever the browser thinks.

Also pinned here: the timezone label is computed (GMT-04:00 EDT in summer,
GMT-05:00 EST in winter) and the time list covers 5:00 AM to 11:00 PM at least.
"""
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
TIME_TS = FRONTEND / "lib" / "accountTime.ts"
NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so accountTime.ts cannot be executed. CI's backend "
           "job installs it — see .github/workflows/ci.yml.")

BROWSER_ZONES = ["Europe/Berlin", "America/Caracas", "Asia/Tokyo", "America/New_York"]


def run_js(body: str, tz: str):
    script = textwrap.dedent("""
        import * as t from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
    """) % json.dumps(TIME_TS.as_posix()) + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True, env={**os.environ, "TZ": tz})
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


@node
@pytest.mark.parametrize("tz", BROWSER_ZONES)
def test_1_30_pm_picked_is_1_30_pm_eastern_in_both_seasons(tz):
    got = run_js("""
        const summer = t.instantOf({year: 2026, month: 9, day: 15, hour: 13, minute: 30})
        const winter = t.instantOf({year: 2027, month: 1, day: 15, hour: 13, minute: 30})
        out({summer: summer.toISOString(), winter: winter.toISOString(),
             backS: t.wallOf(summer), backW: t.wallOf(winter),
             labelS: t.pickerLabel(summer), labelW: t.pickerLabel(winter)})
    """, tz)
    assert got["summer"] == "2026-09-15T17:30:00.000Z", "EDT is UTC-4"
    assert got["winter"] == "2027-01-15T18:30:00.000Z", "EST is UTC-5"
    assert got["backS"] == {"year": 2026, "month": 9, "day": 15, "hour": 13, "minute": 30}
    assert got["backW"] == {"year": 2027, "month": 1, "day": 15, "hour": 13, "minute": 30}
    # Screenshot 29's format.
    assert got["labelS"] == "Sep 15, 2026, 1:30 PM"
    assert got["labelW"] == "Jan 15, 2027, 1:30 PM"


@node
@pytest.mark.parametrize("tz", BROWSER_ZONES)
def test_the_timezone_label_is_computed_not_hardcoded(tz):
    got = run_js("""
        out([t.zoneLabel(new Date('2026-09-15T17:30:00Z')),
             t.zoneLabel(new Date('2027-01-15T18:30:00Z'))])
    """, tz)
    assert got == ["GMT-04:00 America/New_York (EDT)", "GMT-05:00 America/New_York (EST)"]
    source = TIME_TS.read_text(encoding="utf-8")
    assert "-04:00" not in source.split("export function zoneLabel", 1)[1].split("\n}", 1)[0]


@node
def test_the_dst_edges_land_on_a_real_instant():
    got = run_js("""
        out({
          // 2:30 AM does not exist on 14 Mar 2027 — it lands at 3:30 AM EDT.
          gap: t.instantOf({year: 2027, month: 3, day: 14, hour: 2, minute: 30}).toISOString(),
          // 1:30 AM happens twice on 1 Nov 2026 — the first, EDT, reading.
          twice: t.instantOf({year: 2026, month: 11, day: 1, hour: 1, minute: 30}).toISOString(),
          // The day after the change is plain EST.
          after: t.instantOf({year: 2026, month: 11, day: 2, hour: 9, minute: 0}).toISOString(),
        })
    """, "Europe/Berlin")
    assert got == {"gap": "2027-03-14T07:30:00.000Z", "twice": "2026-11-01T05:30:00.000Z",
                   "after": "2026-11-02T14:00:00.000Z"}


@node
def test_the_time_list_reaches_5_am_and_11_pm():
    got = run_js("out(t.timeSlots().map((s) => s.label))", "Europe/Berlin")
    assert "5:00 AM" in got and "11:00 PM" in got
    assert got.index("5:00 AM") < got.index("11:00 PM")
    # The whole day, every 15 minutes: nothing between them is skipped.
    assert got[0] == "12:00 AM" and got[-1] == "11:45 PM" and len(got) == 96
    assert got[got.index("5:00 AM"):got.index("11:00 PM") + 1][:3] == [
        "5:00 AM", "5:15 AM", "5:30 AM"]


@node
def test_the_native_inputs_of_the_detail_panel_read_the_account_zone():
    got = run_js("""
        const at = new Date('2027-01-15T18:30:00Z')
        out({date: t.dateInput(at), time: t.timeInput(at),
             back: t.fromDateTimeInputs('2027-01-15', '13:30').toISOString(),
             empty: t.fromDateTimeInputs('', '13:30')})
    """, "Asia/Tokyo")
    assert got == {"date": "2027-01-15", "time": "13:30",
                   "back": "2027-01-15T18:30:00.000Z", "empty": None}


@node
def test_the_calendar_default_address_line_matches_the_server():
    from app.main import contact_address
    from app.models import Contact

    c = Contact(first_name="J", last_name="D", address_street=" 9811 SW 16th St ",
                address_city="Pembroke Pines", address_state="FL",
                address_postal_code="33025")
    got = run_js("""
        out([t.addressLine({address_street: ' 9811 SW 16th St ', address_city: 'Pembroke Pines',
                            address_state: 'FL', address_postal_code: '33025'}),
             t.addressLine({address_street: null, address_city: '', address_state: 'FL'}),
             t.addressLine({})])
    """, "Europe/Berlin")
    assert got[0] == contact_address(c) == "9811 SW 16th St, Pembroke Pines, FL 33025"
    assert got[1] == contact_address(Contact(address_state="FL")) == "FL"
    assert got[2] is None and contact_address(Contact()) is None
