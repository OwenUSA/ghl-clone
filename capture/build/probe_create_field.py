"""READ-ONLY: open the "Create field" panel, record what it offers, close with Escape.

Opening a creation form is a read. Submitting it is the mutation, and this script has no
Save path at all - it never resolves a Save/Create control, and it closes with Escape
only (the `capture/open_menus.py` rule).

What it must answer before `fields.py` can be written:

  * the exact object/type names offered for an Opportunity field
  * whether CHECKBOX is a single boolean or a multi-select group needing options
    (measured elsewhere: `task.completed` is CHECKBOX, so the type exists - but the
    live account's only example is a system field, which proves nothing about the
    create form)
  * whether a folder can be chosen inline, and the input selectors to fill

Nothing here is billable. The Workflows empty state on this account advertises an AI
builder beside "Create workflow"; the Custom Fields page has no such control, but the
denylist in guard.py covers it regardless.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from attach import attach
from playwright.sync_api import sync_playwright
from settle import settle

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "captures" / "settings"
from ghl_account import LOC  # noqa: E402  (flat script layout)

URL = ("https://app.gohighlevel.com/v2/location/" + LOC
       + "/settings/fields?tab=field")

# Resolve "Create field" by exact text. This is the ONE control we open, and opening
# a form mutates nothing.
OPEN = """
() => {
  for (const e of document.querySelectorAll('button,[role=button],a')) {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const t = (e.innerText || '').trim();
    if (t === 'Create field') { e.click(); return true; }
  }
  return false;
}
"""

PANEL = """
() => {
  const vis = e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const texts = [];
  for (const e of document.querySelectorAll('*')) {
    if (!vis(e)) continue;
    if (e.children.length > 1) continue;
    const t = (e.innerText || '').trim();
    if (t && t.length <= 60) texts.push(t);
  }
  const inputs = [];
  for (const e of document.querySelectorAll('input,select,textarea')) {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    inputs.push({ tag: e.tagName.toLowerCase(), type: e.getAttribute('type') || '',
                  name: e.getAttribute('name') || '', id: e.getAttribute('id') || '',
                  placeholder: e.getAttribute('placeholder') || '',
                  aria: (e.getAttribute('aria-label') || '').trim() });
  }
  const buttons = [];
  for (const e of document.querySelectorAll('button,[role=button]')) {
    if (!vis(e)) continue;
    const t = (e.innerText || '').trim();
    if (t) buttons.push(t.slice(0, 40));
  }
  return { texts: [...new Set(texts)], inputs, buttons: [...new Set(buttons)] };
}
"""


def main():
    with sync_playwright() as pw:
        b, ctx, page = attach(pw)
        page.bring_to_front()
        page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        ok, reason = settle(page, timeout_ms=90000)
        print("settle:", ok, reason)
        page.wait_for_timeout(6000)

        before = set(page.evaluate(PANEL)["texts"])

        if not page.evaluate(OPEN):
            raise SystemExit("'Create field' not found - recorded as unmeasured")
        print("opened 'Create field'")
        page.wait_for_timeout(5000)

        panel = page.evaluate(PANEL)
        OUT.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(OUT / "create_field_panel.png"))

        new = [t for t in panel["texts"] if t not in before]
        print("\n--- panel-only text (%d) ---" % len(new))
        for t in new:
            print("   ", t)
        print("\n--- inputs (%d) ---" % len(panel["inputs"]))
        for i in panel["inputs"]:
            print("    %-9s type=%-10s ph=%-28s name=%s"
                  % (i["tag"], i["type"], i["placeholder"][:28], i["name"]))
        print("\n--- buttons ---")
        for x in panel["buttons"]:
            print("   ", x)

        (OUT / "create_field_panel.json").write_text(
            json.dumps({"new_text": new, "inputs": panel["inputs"],
                        "buttons": panel["buttons"]}, indent=1), encoding="utf-8")

        # Close with Escape ONLY. Never click elsewhere to dismiss - a stray click
        # in a settings panel is exactly the failure this harness exists to avoid.
        page.keyboard.press("Escape")
        page.wait_for_timeout(1500)
        page.keyboard.press("Escape")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "create_field_closed.png"))
        print("\nclosed with Escape. Nothing was saved.")


if __name__ == "__main__":
    main()
