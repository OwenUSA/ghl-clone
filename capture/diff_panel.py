"""Diff OUR Contact Details panel against GHL's, by measured landmark.

Both sides are located by rendered text within the right-hand panel region, then
a fixed property set is compared. Anything not found is reported as NOT FOUND —
never silently counted as matching.
"""
import glob
import json

# label -> (min_x on GHL side, min_x on our side)
# GHL's panel starts at x=1088; ours renders at the same place in the 3-pane layout.
PANEL_MIN_X = 1080

LANDMARKS = [
    ("Contact Details", ["font-size", "font-weight", "color"]),
    ("Owner", ["font-size", "font-weight", "color"]),
    ("Followers", ["font-size", "font-weight", "color"]),
    ("All fields", ["font-size", "font-weight", "color"]),
    ("DND", ["font-size", "font-weight", "color"]),
    ("Actions", ["font-size", "font-weight", "color"]),
    ("First name", ["font-size", "font-weight", "color"]),
    ("Last name", ["font-size", "font-weight", "color"]),
    ("Date of birth", ["font-size", "font-weight", "color"]),
    ("Contact source", ["font-size", "font-weight", "color"]),
    ("Contact type", ["font-size", "font-weight", "color"]),
    ("Search fields and folders", ["font-size", "color"]),
]


def load(pattern):
    files = sorted(glob.glob(pattern))
    return json.loads(open(files[-1], encoding="utf-8").read()) if files else None


def find(els, text):
    hits = [e for e in els
            if (e["text"] or "").strip() == text and e["box"]["x"] >= PANEL_MIN_X]
    if not hits:
        # Input hints are placeholders, not innerText.
        hits = [e for e in els
                if (e.get("placeholder") or e.get("aria") or "") == text
                and e["box"]["x"] >= PANEL_MIN_X]
    return hits[0] if hits else None


def main():
    ghl = load("captures/conversations/*__1440x900__top__*.json")
    ours = load("captures/ours/conversations/ours__1440x900__top__*.json")
    if not ghl or not ours:
        print("missing capture(s) - run capture_ours.py conversations Conversations")
        return

    rows, missing, match = [], [], 0
    for text, props in LANDMARKS:
        a, b = find(ghl["elements"], text), find(ours["elements"], text)
        if a is None or b is None:
            missing.append((text, a is not None, b is not None))
            continue
        for p in props:
            av = (a["style"].get(p) or "").strip()
            bv = (b["style"].get(p) or "").strip()
            if av != bv:
                rows.append((text, p, av, bv))
            else:
                match += 1

    print("| Landmark | Property | GHL | Ours |")
    print("|---|---|---|---|")
    for t, p, av, bv in rows:
        print("| %s | %s | `%s` | `%s` |" % (t, p, av, bv))
    print()
    print("%d properties match, %d differ" % (match, len(rows)))
    if missing:
        print("\nNOT FOUND (not counted as matching):")
        for t, in_g, in_o in missing:
            print("  %-28s in GHL=%-5s in ours=%s" % (t, in_g, in_o))


if __name__ == "__main__":
    main()
