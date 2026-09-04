import json
import pathlib
import sys

p = pathlib.Path(sys.argv[1])
d = json.loads(p.read_text(encoding="utf-8"))

print("url:", d["url"])
print("viewport:", d["viewport"], "bp:", d["breakpoint"], "scroll:", d["scrollLabel"])
print("docHeight:", d["docHeight"], "elements:", d["count"])
print("scrolledVia:", d.get("scrolledVia"))
print("scrollers:", json.dumps(d.get("scrollers"), indent=1)[:700])
print("theme tokens:", len(d.get("theme", {})))
for k, v in list(d.get("theme", {}).items())[:15]:
    print("   ", k, "=", v)

print("\n--- text-bearing elements (first 45) ---")
n = 0
for e in d["elements"]:
    if e["text"]:
        print("  [%s] %-28s %s" % (e["tag"], e["text"][:40], e["box"]))
        n += 1
        if n >= 45:
            break
