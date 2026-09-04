from attach import attach
from playwright.sync_api import sync_playwright

JS = """
() => {
  const vis = e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const byClass = [], byAnim = [];
  const sel = '[class*=spinner],[class*=loader],[class*=loading],[class*=skeleton],'
            + '[role=progressbar],.animate-spin';
  for (const e of document.querySelectorAll(sel)) {
    if (!vis(e)) continue;
    const r = e.getBoundingClientRect();
    byClass.push({tag: e.tagName, cls: (e.getAttribute('class')||'').slice(0,90),
                  w: Math.round(r.width), h: Math.round(r.height)});
  }
  for (const e of document.querySelectorAll('svg,div')) {
    if (!vis(e)) continue;
    const cs = getComputedStyle(e);
    if (cs.animationName && cs.animationName !== 'none'
        && cs.animationIterationCount === 'infinite') {
      const r = e.getBoundingClientRect();
      byAnim.push({tag: e.tagName, anim: cs.animationName,
                   cls: (e.getAttribute('class')||'').slice(0,90),
                   w: Math.round(r.width), h: Math.round(r.height)});
    }
  }
  return {byClass: byClass.slice(0,15), byAnim: byAnim.slice(0,15)};
}
"""

with sync_playwright() as pw:
    b, ctx, page = attach(pw)
    s = page.evaluate(JS)
    print("MATCHED BY CLASS/ROLE (%d):" % len(s["byClass"]))
    for x in s["byClass"]:
        print("  ", x)
    print("\nMATCHED BY INFINITE ANIMATION (%d):" % len(s["byAnim"]))
    for x in s["byAnim"]:
        print("  ", x)
