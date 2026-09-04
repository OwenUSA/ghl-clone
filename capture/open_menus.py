"""Open ONLY allowlisted dropdowns on Conversations and record their contents.

Why this is paranoid: the thread header packs these into a 40px pitch —
  Filter messages | Call: +1786... | Add to Favorites | Mark as read | Delete Conversation
A coordinate click that drifts one slot places a real phone call or deletes a
conversation on a live production account.

So:
  * never click by coordinate - always resolve the element by its exact label
  * re-read the label at click time and abort if it matches the denylist
  * close with Escape only, never by clicking elsewhere
"""
import json
import pathlib
import re

from attach import attach
from ghl_account import LOC
from playwright.sync_api import sync_playwright
from settle import settle

URL = "https://app.gohighlevel.com/v2/location/" + LOC + "/conversations/conversations"

# Exact labels we are willing to open. Each opens a menu; none mutates.
ALLOW = [
    "Filter conversations",
    "Sort conversations",
    "Filter messages",
]

# Hard guard. If a resolved element's label matches any of these, abort.
DENY = re.compile(
    r"call|dial|delete|remove|archive|send|save|create|add\s|import|export|"
    r"merge|assign|convert|publish|duplicate|favorite|mark as|block|opt.?out",
    re.IGNORECASE)

RESOLVE = """
(label) => {
  const vis = e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const all = document.querySelectorAll('button,[role=button],[aria-haspopup],[class*=cursor-pointer]');
  for (const e of all) {
    if (!vis(e)) continue;
    const l = (e.getAttribute('aria-label') || e.getAttribute('title') || '').trim();
    if (l === label) {
      const r = e.getBoundingClientRect();
      window.__target = e;
      return { found: true, label: l, x: Math.round(r.x), y: Math.round(r.y),
               tag: e.tagName.toLowerCase() };
    }
  }
  return { found: false };
}
"""

SNAPSHOT = """
() => {
  const vis = e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const out = [];
  for (const e of document.querySelectorAll('*')) {
    if (!vis(e)) continue;
    const t = (e.innerText || '').trim();
    if (!t || t.length > 60) continue;
    if (e.children.length > 1) continue;
    const r = e.getBoundingClientRect();
    out.push(t + '@' + Math.round(r.x) + ',' + Math.round(r.y));
  }
  return out;
}
"""

with sync_playwright() as pw:
    b, ctx, page = attach(pw)
    page.bring_to_front()
    page.goto(URL, wait_until="domcontentloaded", timeout=90000)
    ok, reason = settle(page)
    print("settle:", ok, reason)
    if not ok:
        raise SystemExit("aborting")

    results = {}
    for label in ALLOW:
        if DENY.search(label):
            print("REFUSING (allowlist entry matches denylist): %r" % label)
            continue

        before = set(page.evaluate(SNAPSHOT))
        info = page.evaluate(RESOLVE, label)
        if not info.get("found"):
            print("NOT FOUND: %-24s - recorded as unmeasured, not as empty" % label)
            results[label] = {"status": "NOT_FOUND"}
            continue

        # Re-check the resolved element's own label before touching it.
        if DENY.search(info["label"]):
            print("REFUSING: resolved element %r matches denylist" % info["label"])
            results[label] = {"status": "REFUSED"}
            continue

        page.evaluate("() => window.__target.click()")
        page.wait_for_timeout(1400)
        after = page.evaluate(SNAPSHOT)
        new = [x for x in after if x not in before]
        page.keyboard.press("Escape")
        page.wait_for_timeout(700)

        items = []
        for n in new:
            txt, _, pos = n.rpartition("@")
            x, y = pos.split(",")
            items.append({"text": txt, "x": int(x), "y": int(y)})
        items.sort(key=lambda i: (i["y"], i["x"]))
        results[label] = {"status": "OPENED", "items": items}

        print("\n=== %s === %d new elements" % (label, len(items)))
        for i in items[:30]:
            print("   %-42s (%s,%s)" % (i["text"][:42], i["x"], i["y"]))

    out = pathlib.Path("captures/conversations")
    out.mkdir(parents=True, exist_ok=True)
    (out / "menus.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    print("\nwrote captures/conversations/menus.json")
