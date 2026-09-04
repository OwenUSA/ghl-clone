"""Reduce the raw stage sweep to a clean, ordered stage list.

The collector matches both a stage's header container and the inner line inside
it, so raw output double-counts. Only entries shaped
"<name> | <n> opportunities | $<value>" are real stage headers.
"""
import glob
import json
import re

RE = re.compile(r"^(?P<name>.+?)\s*\|\s*(?P<n>\d+)\s+opportunit\w*\s*\|\s*\$(?P<v>[\d,]+\.\d{2})$")

f = max(glob.glob("captures/opportunities/*__stages__*.json"))
raw = json.loads(open(f, encoding="utf-8").read())

stages, seen = [], set()
for s in raw["stages"]:
    m = RE.match(s["head"])
    if not m:
        continue
    name = m.group("name").strip()
    cents = round(float(m.group("v").replace(",", "")) * 100)
    key = (name, int(m.group("n")), cents)
    if key in seen:
        continue
    seen.add(key)
    stages.append({"name": name, "count": int(m.group("n")),
                   "value_cents": cents, "absX": s.get("absX", s["x"])})

stages.sort(key=lambda s: s["absX"])
total = sum(s["count"] for s in stages)

print("%d stages" % len(stages))
for i, s in enumerate(stages):
    print("  %2d. %-30s %2d  $%s" % (i, s["name"], s["count"],
                                     format(s["value_cents"] / 100, ",.2f")))
print("\nsum = %d   header = %s   %s"
      % (total, raw["header"],
         "RECONCILED" if str(total) == str(raw["header"]) else "MISMATCH"))

out = "captures/opportunities/stages_clean.json"
json.dump({"stages": stages, "sum": total, "header": raw["header"]},
          open(out, "w", encoding="utf-8"), indent=1)
print("wrote", out)
