"""Query a capture for the measured values of specific elements.

Usage:
  python capture/measure.py contacts "Contacts"        # by text
  python capture/measure.py contacts --box 0 0 300 900 # by region
"""
import json
import pathlib
import sys

KEYS = ["display", "position", "width", "height", "background-color", "color",
        "font-size", "font-weight", "line-height", "padding-top", "padding-left",
        "padding-right", "padding-bottom", "gap", "border-top-left-radius",
        "border-top-width", "border-top-color", "box-shadow", "overflow-y"]


def latest(view):
    files = sorted((pathlib.Path("captures") / view).glob("*__1440x900__top__*.json"))
    return files[-1]


def show(e, keys=KEYS):
    b = e["box"]
    print("  <%s> %s" % (e["tag"], (e["text"] or e["aria"] or "")[:44]))
    print("    cls: %s" % e["cls"][:100])
    print("    box: x=%s y=%s w=%s h=%s" % (b["x"], b["y"], b["w"], b["h"]))
    st = e["style"]
    shown = {k: st[k] for k in keys if st.get(k) and st[k] not in
             ("none", "normal", "auto", "0px", "rgba(0, 0, 0, 0)")}
    for k, v in shown.items():
        print("      %-24s %s" % (k, v))
    print()


def main():
    view = sys.argv[1]
    d = json.loads(latest(view).read_text(encoding="utf-8"))
    els = d["elements"]

    if "--box" in sys.argv:
        i = sys.argv.index("--box")
        x0, y0, x1, y1 = (float(v) for v in sys.argv[i + 1:i + 5])
        sel = [e for e in els
               if x0 <= e["box"]["x"] <= x1 and y0 <= e["box"]["y"] <= y1]
        sel.sort(key=lambda e: (e["box"]["y"], e["box"]["x"]))
        print("%d elements in region" % len(sel))
        for e in sel[:40]:
            show(e)
        return

    needle = sys.argv[2].lower()
    hits = [e for e in els
            if needle in (e["text"] or "").lower()
            or needle in (e["cls"] or "").lower()
            or needle in (e["aria"] or "").lower()]
    print("%d matches for %r in %s" % (len(hits), sys.argv[2], view))
    for e in hits[:12]:
        show(e)


if __name__ == "__main__":
    main()
