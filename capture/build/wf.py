"""Helpers for GHL's Workflow builder.

Separate from `panel.py` because the builder is a different application: a cross-origin
micro-frontend (`client-app-automation-workflows.leadconnectorhq.com`) inside an iframe.
`page.evaluate` on the top frame cannot see into it, so everything here runs against the
frame handle.

Measured quirks so far:
  * "Create workflow" is a DROPDOWN, not a button - options are
    `div.hr-dropdown-option` (Start from Scratch / Select from Template / Company based
    workflow). Clicking the inner `.hr-dropdown-option-body__label` registers as a click
    and does nothing, which reads as success.
  * The empty state puts an **AI workflow builder** ("Build workflows for free by
    chatting with AI", BETA) directly beside the create control. It is metered. Nothing
    here goes near it, and guard.py's DENY covers `\\bai\\b`, `generate` and `assistant`.
"""

HOST = "client-app-automation-workflows"


def frame(page, *, required=True):
    for f in page.frames:
        if HOST in f.url:
            try:
                f.evaluate("() => 1")
                return f
            except Exception:
                break
    if required:
        raise RuntimeError("workflow iframe not reachable")
    return None


def texts(fr):
    return fr.evaluate("""() => {
      const o = [];
      for (const e of document.querySelectorAll('*')) {
        const r = e.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        if (e.children.length !== 0) continue;
        const t = (e.innerText || '').trim();
        if (t && t.length <= 70) o.push(t);
      }
      return [...new Set(o)];
    }""")


def buttons(fr):
    return fr.evaluate("""() => {
      const o = [];
      for (const e of document.querySelectorAll('button,[role=button]')) {
        const r = e.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        const t = (e.innerText || '').trim();
        if (t) o.push(t.slice(0, 50).replace(/\\n/g, ' / '));
      }
      return [...new Set(o)];
    }""")


def click_button(fr, prefix):
    """Click a <button> whose text starts with `prefix`."""
    return fr.evaluate("""(p) => {
      for (const e of document.querySelectorAll('button')) {
        const r = e.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        if ((e.innerText || '').trim().startsWith(p)) { e.click(); return true; }
      }
      return false;
    }""", prefix)


def click_dropdown_option(fr, label):
    """Click a `.hr-dropdown-option` by its label.

    Targets the option container, not the label node. The label is a leaf with no
    handler - clicking it succeeds silently and nothing happens.
    """
    return fr.evaluate("""(want) => {
      for (const e of document.querySelectorAll('.hr-dropdown-option')) {
        const r = e.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        if ((e.innerText || '').trim() === want) { e.click(); return true; }
      }
      return false;
    }""", label)


def click_text(fr, want, *, exact=True):
    """Click the nearest clickable ancestor of a leaf whose text matches."""
    return fr.evaluate("""(args) => {
      const [want, exact] = args;
      const hit = t => exact ? t === want : t.includes(want);
      for (const e of document.querySelectorAll('*')) {
        if (e.children.length !== 0) continue;
        const r = e.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        if (!hit((e.innerText || '').trim())) continue;
        let p = e;
        for (let i = 0; i < 5 && p; i++) {
          const tag = p.tagName;
          const cls = (p.className || '').toString();
          if (tag === 'BUTTON' || p.getAttribute('role') === 'button' ||
              /option|item|card|cursor-pointer/i.test(cls)) {
            p.click();
            return true;
          }
          p = p.parentElement;
        }
        e.click();
        return true;
      }
      return false;
    }""", [want, exact])


def fill_placeholder(fr, ph, value, page):
    """Type into an input by placeholder, inside the frame."""
    ok = fr.evaluate("""(ph) => {
      for (const e of document.querySelectorAll('input,textarea')) {
        const r = e.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        if ((e.getAttribute('placeholder') || '').trim() !== ph) continue;
        e.click(); e.focus();
        window.__wf = e;
        return true;
      }
      return false;
    }""", ph)
    if not ok:
        raise RuntimeError("no input with placeholder %r" % ph)
    page.keyboard.press("Control+A")
    page.keyboard.type(value, delay=25)
    got = fr.evaluate("() => window.__wf.value")
    if (got or "").strip() != value:
        raise RuntimeError("input %r did not take: %r" % (ph, got))
    return True


def diff(fr, before, label, limit=60):
    now = texts(fr)
    new = [t for t in now if t not in set(before)]
    print("\n--- %s --- %d new" % (label, len(new)))
    for t in new[:limit]:
        print("     %s" % t)
    return now
