"""Audit our app against GHL, per view.

Three checks:
  1. MISSING UI  - text labels present in GHL's chrome but absent from ours.
                   Data values (names, phones, emails, money, dates) are excluded:
                   the two systems hold different records, so those are never
                   comparable. Element COUNTS are never compared either.
  2. STICKINESS  - elements whose viewport y moves between scroll top and bottom.
                   Anything that moves in ours but is pinned in GHL is a bug.
  3. VOCABULARY  - design values (font-size, weight, radius) present on one side
                   only. Full sets, no top-N truncation.
"""
import glob
import json
import re
import sys

# Windows stdout defaults to cp1252 and dies on characters like the caret glyph.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VIEWS = ["contacts", "conversations", "opportunities", "calendars",
         "dashboard", "launchpad"]

# Data, not chrome — never comparable between two different databases.
DATA = re.compile(
    r"^\(?\d{3}\)?[\s.-]?\d{3}[-.\s]?\d{4}$"      # phone
    r"|@"                                          # email
    r"|^\$"                                        # money
    r"|^\d+$"                                      # bare number
    r"|^\d{1,2}/\d{1,2}/\d{2,4}$"                  # date
    r"|^[A-Z]{2}$"                                 # avatar initials
    r"|\d{1,2}:\d{2}"                              # time
    r"|^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b",
    re.IGNORECASE)

# Third-party / out-of-scope chrome that differs by construction.
IGNORE = {
    "SV", "Voice Calling", "Notifications", "View Notifications",
    "Open Profile Menu", "What's new", "Contact updates", "Calendar updates",
    "ctrlK", "Collapse sidebar", "Alternative text for image not available",
    "loading", "close", "Calendar AI", "Icon only button", "Menu",
}


def load(pattern):
    files = sorted(glob.glob(pattern))
    return json.loads(open(files[-1], encoding="utf-8").read()) if files else None


def labels(cap):
    out = {}
    for e in cap["elements"]:
        t = (e["text"] or "").strip()
        if not t or len(t) > 40 or DATA.search(t) or t in IGNORE:
            continue
        out.setdefault(t, e)
    return out


def ys(cap):
    d = {}
    for e in cap["elements"]:
        t = (e["text"] or "").strip()
        if t and len(t) <= 40:
            d.setdefault(t, e["box"]["y"])
    return d


def vocab(cap, prop):
    vals = {}
    for e in cap["elements"]:
        v = (e["style"].get(prop) or "").strip()
        if v:
            vals[v] = vals.get(v, 0) + 1
    return vals


def main():
    total_missing = 0
    for view in VIEWS:
        ghl = load("captures/%s/*__1440x900__top__*.json" % view)
        ours = load("captures/ours/%s/ours__1440x900__top__*.json" % view)
        if not ghl or not ours:
            print("\n## %s — NO CAPTURE (not audited)" % view)
            continue

        g, o = labels(ghl), labels(ours)
        missing = sorted(set(g) - set(o))
        extra = sorted(set(o) - set(g))

        print("\n## %s" % view)
        print("GHL chrome labels: %d | ours: %d" % (len(g), len(o)))

        if missing:
            total_missing += len(missing)
            print("\n  MISSING FROM OURS (%d):" % len(missing))
            for m in missing:
                print("    - %s" % m)
        else:
            print("  no missing chrome labels")

        if extra:
            print("\n  ONLY IN OURS (%d): %s" % (len(extra), ", ".join(extra[:12])))

        # stickiness
        gt, gb = load("captures/%s/*__1440x900__top__*.json" % view), \
            load("captures/%s/*__1440x900__bottom__*.json" % view)
        ot, ob = ours, load("captures/ours/%s/ours__1440x900__bottom__*.json" % view)
        if gt and gb and ot and ob:
            gty, gby = ys(gt), ys(gb)
            oty, oby = ys(ot), ys(ob)
            pinned_ghl = {t for t in gty if t in gby and abs(gty[t] - gby[t]) < 2}
            moved_ours = {t for t in oty if t in oby and abs(oty[t] - oby[t]) >= 2}
            bugs = sorted((pinned_ghl & moved_ours) - IGNORE)
            if bugs:
                print("\n  STICKINESS — pinned in GHL but moves in ours (%d):" % len(bugs))
                for t in bugs[:15]:
                    print("    - %-30s GHL y=%s  ours %s -> %s"
                          % (t[:30], gty[t], oty[t], oby[t]))
            else:
                print("  stickiness: no elements pinned in GHL move in ours")

        for prop in ("font-size", "font-weight", "border-top-left-radius"):
            gv, ov = vocab(ghl, prop), vocab(ours, prop)
            only_g = sorted(set(gv) - set(ov))
            if only_g:
                print("  %s only in GHL: %s" % (prop, ", ".join(only_g)))

    print("\n\nTOTAL missing chrome labels across %d views: %d" % (len(VIEWS), total_missing))


if __name__ == "__main__":
    main()
