"""Emit a build spec for a view: geometry + styles, INCLUDING icons.

The label-based audit could not see icons, spacing or component shape — an icon
has no text, so "no missing labels" was true and meaningless. This dumps every
visible box in a region, marks svg/icon nodes, and reports the measured pitch,
so a component can be rebuilt from numbers instead of from a screenshot.
"""
import glob
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REGIONS = {
    "conversations": [
        ("icon rail", 224, 284),
        ("inbox list", 284, 560),
        ("thread", 560, 1088),
        ("contact panel", 1088, 1440),
    ],
    "contacts": [("toolbar + header", 224, 1440, 0, 260),
                 ("grid", 224, 1440, 260, 900)],
    "opportunities": [("header + toolbar", 224, 1440, 0, 320),
                      ("cards", 224, 1440, 320, 900)],
    "calendars": [("toolbar", 224, 1440, 0, 200),
                  ("grid + panel", 224, 1440, 200, 900)],
}

KEYS = ["font-size", "font-weight", "color", "background-color",
        "border-top-left-radius", "border-top-width", "border-top-color"]


def main():
    view = sys.argv[1] if len(sys.argv) > 1 else "conversations"
    f = max(glob.glob("captures/%s/*__1440x900__top__*.json" % view))
    d = json.loads(open(f, encoding="utf-8").read())
    els = d["elements"]

    for region in REGIONS[view]:
        name, x0, x1 = region[0], region[1], region[2]
        y0 = region[3] if len(region) > 3 else 0
        y1 = region[4] if len(region) > 4 else 100000
        sel = [e for e in els
               if x0 <= e["box"]["x"] < x1 and y0 <= e["box"]["y"] < y1]
        # Only leaf-ish nodes: text carriers and icons.
        rows = []
        for e in sel:
            t = (e["text"] or "").strip()
            is_icon = e["tag"] in ("svg", "path", "i")
            if not t and not is_icon:
                continue
            if e["tag"] == "path":
                continue
            rows.append((e, t, is_icon))
        rows.sort(key=lambda r: (r[0]["box"]["y"], r[0]["box"]["x"]))

        print("\n### %s  (x %s..%s) — %d nodes" % (name, x0, x1, len(rows)))
        seen = set()
        for e, t, is_icon in rows:
            b = e["box"]
            key = (t, round(b["x"]), round(b["y"]))
            if key in seen:
                continue
            seen.add(key)
            s = e["style"]
            label = ("[icon %s]" % e["tag"]) if is_icon else t[:34]
            bits = []
            for k in KEYS:
                v = (s.get(k) or "").strip()
                if v and v not in ("none", "normal", "0px", "rgba(0, 0, 0, 0)"):
                    bits.append("%s=%s" % (k.replace("border-top-", "b-"), v))
            print("  y=%-7s x=%-7s %-6s %-36s %s"
                  % (b["y"], b["x"], "%gx%g" % (b["w"], b["h"]), label,
                     " ".join(bits[:5])))


if __name__ == "__main__":
    main()
