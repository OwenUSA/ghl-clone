"""Diff our app against GHL on named landmarks.

Element-by-element path diffing is meaningless here: the DOM structures differ by
construction (their Vue micro-frontends vs our React), and element counts are
never comparable anyway because the data differs. What IS comparable is the
measured value of a specific, named thing on each side.

Each landmark is located by its rendered text (or class), then a fixed set of
properties is compared. Anything not found is reported as NOT FOUND, never as
"matches".
"""
import glob
import json
import sys

LANDMARKS = [
    # (label, side-agnostic matcher, properties to compare)
    ("body", {"tag": "body"},
     ["background-color", "color", "font-size", "font-weight", "line-height"]),
    ("sidebar", {"tag": "aside"},
     ["background-color", "width", "height"]),
    ("nav row: Contacts", {"text": "Contacts", "max_x": 224},
     ["font-size", "font-weight", "line-height", "color"]),
    ("location name", {"text": "Dream Team Roofing"},
     ["font-size", "font-weight", "line-height", "color"]),
    ("location city", {"text": "Bradenton, FL"},
     ["font-size", "font-weight", "line-height", "color"]),
    ("page title", {"text": "Contacts", "min_x": 225, "min_y": 95, "max_y": 130},
     ["font-size", "font-weight", "line-height", "color"]),
    ("column header: Phone", {"text": "Phone"},
     ["font-size", "font-weight", "color"]),
    ("column header: Email", {"text": "Email"},
     ["font-size", "font-weight", "color"]),
    ("column header: Business name", {"text": "Business name"},
     ["font-size", "font-weight", "color"]),
]


def load(pattern):
    files = sorted(glob.glob(pattern))
    if not files:
        return None
    return json.loads(open(files[-1], encoding="utf-8").read())


def find(els, m):
    for e in els:
        if "tag" in m and e["tag"] != m["tag"]:
            continue
        if "text" in m and (e["text"] or "").strip() != m["text"]:
            continue
        b = e["box"]
        if "max_x" in m and b["x"] > m["max_x"]:
            continue
        if "min_x" in m and b["x"] < m["min_x"]:
            continue
        if "min_y" in m and b["y"] < m["min_y"]:
            continue
        if "max_y" in m and b["y"] > m["max_y"]:
            continue
        return e
    return None


def main():
    ghl = load("captures/contacts/*__1440x900__top__*.json")
    ours = load("captures/ours/contacts/ours__1440x900__top__*.json")
    if not ghl or not ours:
        print("missing capture(s)")
        sys.exit(1)

    rows, missing, matches = [], [], 0
    for label, matcher, props in LANDMARKS:
        a = find(ghl["elements"], matcher)
        b = find(ours["elements"], matcher)
        if a is None or b is None:
            missing.append((label, a is not None, b is not None))
            continue
        for p in props:
            av = (a["style"].get(p) or "").strip()
            bv = (b["style"].get(p) or "").strip()
            if av != bv:
                rows.append((label, p, av, bv))
            else:
                matches += 1
        if label == "sidebar":
            for k in ("x", "y", "w", "h"):
                if a["box"][k] != b["box"][k]:
                    rows.append((label, "box." + k, a["box"][k], b["box"][k]))
                else:
                    matches += 1

    print("| Landmark | Property | GHL | Ours |")
    print("|---|---|---|---|")
    for label, p, av, bv in rows:
        print("| %s | %s | `%s` | `%s` |" % (label, p, av, bv))

    print()
    print("%d properties match, %d differ" % (matches, len(rows)))
    if missing:
        print("\nNOT FOUND (not counted as matching):")
        for label, in_ghl, in_ours in missing:
            print("  %-28s in GHL=%s  in ours=%s" % (label, in_ghl, in_ours))


if __name__ == "__main__":
    main()
