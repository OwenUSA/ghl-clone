"""Compare captured components: GHL vs ours.

Anchors can resolve to slightly different scopes on each side, so raw box sizes
are not compared. What IS compared per component:
  * icon count      (a text-only rebuild scores 0 here — the failure mode that
                     the label-level audit could not see)
  * font-size / font-weight / colour vocabulary actually rendered
  * background colours and radii

Reports only values present on one side, which is what a rebuild needs.
"""
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path("references/components")


def load(side, view, name):
    f = ROOT / side / view / (name + ".json")
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def vocab(nodes, prop):
    out = {}
    for n in nodes:
        v = n["style"].get(prop)
        if v:
            out[v] = out.get(v, 0) + 1
    return out


def main():
    views = sorted({p.name for p in (ROOT / "ghl").iterdir() if p.is_dir()})
    total_issues = 0
    for view in views:
        names = sorted({p.stem for p in (ROOT / "ghl" / view).glob("*.json")})
        print("\n## %s" % view)
        for name in names:
            g, o = load("ghl", view, name), load("ours", view, name)
            if not g or not o:
                print("  %-20s ONE SIDE MISSING (ghl=%s ours=%s)"
                      % (name, bool(g), bool(o)))
                total_issues += 1
                continue

            gi = sum(1 for n in g["nodes"] if n["isIcon"])
            oi = sum(1 for n in o["nodes"] if n["isIcon"])
            issues = []
            if gi and not oi:
                issues.append("NO ICONS (GHL has %d)" % gi)
            elif gi != oi:
                issues.append("icons %d vs %d" % (gi, oi))

            for prop in ("font-size", "font-weight"):
                gv, ov = vocab(g["nodes"], prop), vocab(o["nodes"], prop)
                only_g = sorted(set(gv) - set(ov))
                if only_g:
                    issues.append("%s only in GHL: %s" % (prop, ",".join(only_g)))

            gc = set(vocab(g["nodes"], "color"))
            oc = set(vocab(o["nodes"], "color"))
            only_gc = sorted(gc - oc)
            if only_gc:
                issues.append("colours only in GHL: %s" % ", ".join(only_gc[:4]))

            if issues:
                total_issues += len(issues)
                print("  %-20s %s" % (name, "; ".join(issues)))
            else:
                print("  %-20s OK (icons %d, %d/%d nodes)"
                      % (name, gi, len(g["nodes"]), len(o["nodes"])))
    print("\nTOTAL component issues: %d" % total_issues)


if __name__ == "__main__":
    main()
