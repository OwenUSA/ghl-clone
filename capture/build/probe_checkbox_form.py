"""READ-ONLY: open Create field, choose object=Opportunity and type=Checkbox, and
record the form that results. Then Escape. Nothing is ever submitted.

Why this is a separate probe: the panel opens defaulted to "Single line", so the earlier
capture measured the wrong form. GHL's CHECKBOX is a multi-select group, which means
picking it reveals an options editor that does not exist for a text field - and
`fields.py` has to fill that editor with a single "Done" option. Writing the builder
against the Single-line form would produce 23 fields with no options at all.

Filling a form is not a mutation; submitting it is. There is no Save path in this file:
it never resolves "Create custom field", and it closes with Escape only.
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

CLICK_TEXT = """
(want) => {
  const vis = e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const sel = 'button,[role=button],[role=option],[role=menuitem],li,div,span,a';
  for (const e of document.querySelectorAll(sel)) {
    if (!vis(e)) continue;
    if (e.children.length > 2) continue;
    if ((e.innerText || '').trim() === want) { e.click(); return true; }
  }
  return false;
}
"""

STATE = """
() => {
  const vis = e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const texts = [];
  for (const e of document.querySelectorAll('*')) {
    if (!vis(e) || e.children.length > 1) continue;
    const t = (e.innerText || '').trim();
    if (t && t.length <= 60) texts.push(t);
  }
  const inputs = [];
  for (const e of document.querySelectorAll('input,select,textarea')) {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    inputs.push({ tag: e.tagName.toLowerCase(), type: e.getAttribute('type') || '',
                  placeholder: e.getAttribute('placeholder') || '',
                  aria: (e.getAttribute('aria-label') || '').trim(),
                  value: (e.value || '').slice(0, 40),
                  x: Math.round(r.x), y: Math.round(r.y) });
  }
  return { texts: [...new Set(texts)], inputs };
}
"""


def diff(before, after, label):
    new = [t for t in after["texts"] if t not in set(before["texts"])]
    print("\n--- %s: %d new text ---" % (label, len(new)))
    for t in new:
        print("   ", t.replace("\n", " / "))
    print("--- inputs (%d) ---" % len(after["inputs"]))
    for i in after["inputs"]:
        print("    %-9s type=%-9s ph=%-30s val=%-14s (%4d,%4d)"
              % (i["tag"], i["type"], i["placeholder"][:30], i["value"][:14],
                 i["x"], i["y"]))
    return new


def main():
    record = {}
    with sync_playwright() as pw:
        b, ctx, page = attach(pw)
        page.bring_to_front()
        page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        ok, reason = settle(page, timeout_ms=90000)
        print("settle:", ok, reason)
        page.wait_for_timeout(6000)

        base = page.evaluate(STATE)
        if not page.evaluate(CLICK_TEXT, "Create field"):
            raise SystemExit("'Create field' not found")
        page.wait_for_timeout(4000)
        opened = page.evaluate(STATE)
        record["panel_default"] = diff(base, opened, "panel opened (default)")
        page.screenshot(path=str(OUT / "cb_1_open.png"))

        # Open the Field type dropdown. Its current value is "Single line".
        print("\n>> opening Field type dropdown")
        page.evaluate(CLICK_TEXT, "Single line")
        page.wait_for_timeout(2500)
        types = page.evaluate(STATE)
        record["field_types"] = diff(opened, types, "field type options")
        page.screenshot(path=str(OUT / "cb_2_types.png"))

        # Choose Checkbox.
        print("\n>> selecting 'Checkbox'")
        hit = page.evaluate(CLICK_TEXT, "Checkbox")
        print("   clicked:", hit)
        page.wait_for_timeout(3500)
        cb = page.evaluate(STATE)
        record["checkbox_form"] = diff(opened, cb, "form after choosing Checkbox")
        page.screenshot(path=str(OUT / "cb_3_checkbox.png"))

        # Open the object selector to record its choices.
        print("\n>> opening 'Select object'")
        page.evaluate(CLICK_TEXT, "Select object")
        page.wait_for_timeout(2500)
        obj = page.evaluate(STATE)
        record["objects"] = diff(cb, obj, "object options")
        page.screenshot(path=str(OUT / "cb_4_objects.png"))

        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "checkbox_form.json").write_text(
            json.dumps(record, indent=1), encoding="utf-8")

        for _ in range(3):
            page.keyboard.press("Escape")
            page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "cb_5_closed.png"))
        print("\nclosed with Escape. Nothing was submitted.")


if __name__ == "__main__":
    main()
