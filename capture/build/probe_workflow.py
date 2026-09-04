"""READ-ONLY: map the Workflow builder before building anything in it.

The builder is a separate cross-origin micro-frontend
(`client-app-automation-workflows.leadconnectorhq.com`) embedded in an iframe, so none
of the Custom-Fields work transfers: different DOM, different components, and
`page.evaluate` on the top frame cannot see inside it.

What this must establish before `workflows.py` can exist:
  1. Can we reach into the iframe at all (cross-origin frames can be opaque).
  2. Does "Start from scratch" exist, and what does an empty canvas look like.
  3. Is there a **Task Completed** trigger, and are there **Create Task** and
     **Update Opportunity** actions - the whole design depends on those three.
  4. Where the Draft/Publish control sits, so it can be added to the DENY list by
     its real label rather than a guess.

Safety: this opens pickers and reads them. It never saves a workflow, and it must never
touch Publish. The account currently has ZERO workflows, so anything found here is
GHL's own furniture, not ours.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from attach import attach
from playwright.sync_api import sync_playwright
from settle import settle

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "captures" / "workflows"
from ghl_account import LOC  # noqa: E402  (flat script layout)

BASE = "https://app.gohighlevel.com/v2/location/" + LOC
URL = BASE + "/automation/workflows"

WF_HOST = "client-app-automation-workflows"

TEXTS = """
() => {
  const vis = e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const out = [];
  for (const e of document.querySelectorAll('*')) {
    if (!vis(e) || e.children.length !== 0) continue;
    const t = (e.innerText || '').trim();
    if (t && t.length <= 60) out.push(t);
  }
  return [...new Set(out)];
}
"""

BUTTONS = """
() => {
  const vis = e => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const out = [];
  for (const e of document.querySelectorAll('button,[role=button],a')) {
    if (!vis(e)) continue;
    const t = (e.innerText || '').trim();
    if (t) out.push(t.slice(0, 46).replace(/\\n/g, ' / '));
  }
  return [...new Set(out)];
}
"""

CLICK = """
(want) => {
  const vis = e => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  for (const e of document.querySelectorAll('button,[role=button],a,div,span,p,li')) {
    if (!vis(e)) continue;
    if (e.children.length > 2) continue;
    if ((e.innerText || '').trim() === want) { e.click(); return true; }
  }
  return false;
}
"""


def wf_frame(page):
    """The builder's iframe. Returns None if it is not present or not readable."""
    for f in page.frames:
        if WF_HOST in f.url:
            try:
                f.evaluate("() => 1")
                return f
            except Exception as exc:
                print("   frame present but NOT readable: %s" % str(exc)[:70])
                return None
    return None


def dump(frame, label, before=None):
    texts = frame.evaluate(TEXTS)
    btns = frame.evaluate(BUTTONS)
    new = [t for t in texts if not before or t not in before]
    print("\n--- %s --- (%d texts, %d new, %d buttons)"
          % (label, len(texts), len(new), len(btns)))
    print("  buttons:")
    for x in btns[:40]:
        print("     %s" % x)
    if before:
        print("  new text:")
        for x in new[:60]:
            print("     %s" % x)
    return texts


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    record = {}

    with sync_playwright() as pw:
        b, ctx, page = attach(pw)
        page.bring_to_front()
        page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        settle(page, timeout_ms=90000)
        page.wait_for_timeout(12000)

        fr = wf_frame(page)
        if fr is None:
            raise SystemExit("workflow iframe not reachable - recorded as unmeasured")
        print("iframe:", fr.url[:110])

        base = dump(fr, "workflows list")
        record["list"] = base
        page.screenshot(path=str(OUT / "1_list.png"))

        # Open the create flow. "Create workflow" is a permitted verb; this only
        # opens a chooser, it does not save anything.
        for label in ["Create workflow", "+ Create workflow", "Create Workflow"]:
            if fr.evaluate(CLICK, label):
                print("\nclicked %r" % label)
                break
        else:
            raise SystemExit("no 'Create workflow' control found")
        page.wait_for_timeout(5000)
        after = dump(fr, "create chooser", base)
        record["chooser"] = after
        page.screenshot(path=str(OUT / "2_chooser.png"))

        # Start from scratch -> empty canvas.
        for label in ["Start from scratch", "Start from Scratch", "Blank Workflow"]:
            if fr.evaluate(CLICK, label):
                print("\nclicked %r" % label)
                break
        else:
            print("\n'Start from scratch' not found in the chooser")
        page.wait_for_timeout(8000)

        fr = wf_frame(page) or fr
        canvas = dump(fr, "empty canvas", after)
        record["canvas"] = canvas
        page.screenshot(path=str(OUT / "3_canvas.png"))
        print("\n  url now:", fr.url[:120])

        (OUT / "probe.json").write_text(json.dumps(record, indent=1),
                                        encoding="utf-8")
        print("\nwrote captures/workflows/probe.json")
        print("NOTE: nothing was saved. This workflow was never named or stored.")


if __name__ == "__main__":
    main()
