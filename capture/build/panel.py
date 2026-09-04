"""Helpers for driving GHL's own form controls ("hr-" design system).

Measured, not assumed - every selector here came out of a read-only probe against the
live Create-field panel (`captures/settings/*.json`, `cb_*.png`):

* The dropdowns are `input.hr-base-selection-input`: type-to-filter comboboxes, not
  `<select>`. Three things that do NOT work on them, all tried:
    - clicking the displayed text ("Single line") - no-op, it is not the control
    - clicking an option by text before filtering - matches the wrong node
    - pressing Enter after typing - leaves the filter text in the box UNCOMMITTED,
      which looks identical to success in a DOM dump but saves nothing
  What works: focus, type to filter, then click the option element *below* the input.
* Text inputs are `input.hr-input__input-el`, resolved via their label.

Nothing in this module submits anything. Committing a form is `guard.Guard.click`,
which is separately gated by ALLOW/DENY.
"""

# The panel is a right-hand drawer: div.hr-drawer[role=dialog]. EVERY lookup is
# scoped to it, and that is load-bearing, not tidiness.
#
# Measured failure: the fields grid *behind* the drawer has its own filter control
# also labelled "Field type". An unscoped search matched that one first (y=259 vs
# the drawer's y=185) and reported "combobox not found" - and had the background
# element happened to contain a matching input, it would instead have silently
# driven the wrong control while every log line looked correct.
_ROOT = """
() => {
  const els = [...document.querySelectorAll('[role=dialog]')].filter(e => {
    const r = e.getBoundingClientRect();
    return r.width > 200 && r.height > 200;
  });
  if (!els.length) return false;
  window.__root = els[els.length - 1];   // topmost drawer if several are stacked
  return true;
}
"""

# Find the control belonging to a labelled field, and stash it on window.__ctl.
# Walks up from the label text to the nearest container holding the input, so it
# does not depend on the drawer's exact nesting depth.
_FIND = """
(args) => {
  const [label, cls] = args;
  const root = window.__root;
  if (!root) return false;
  for (const lbl of root.querySelectorAll('*')) {
    if (lbl.children.length !== 0) continue;
    if ((lbl.innerText || '').trim() !== label) continue;
    let p = lbl.parentElement;
    for (let i = 0; i < 4 && p && root.contains(p); i++) {
      const c = p.querySelector('input.' + cls + ', textarea.' + cls);
      if (c) { window.__ctl = c; return true; }
      p = p.parentElement;
    }
  }
  return false;
}
"""

# Click a dropdown option by exact text.
#
# NOT scoped to the drawer: these dropdowns render into a portal on <body>, outside
# the drawer subtree. So instead the option must be geometrically attached to the
# combobox we just opened - below it, within 420px, and horizontally overlapping.
#
# That geometry is the guard here. "Checkbox" also appears as a value in the fields
# grid behind the drawer; an unrestricted text match clicks that row instead, which
# selects nothing and looks exactly like success.
_PICK = """
(args) => {
  const [want, box] = args;
  const hits = [];
  for (const e of document.querySelectorAll('*')) {
    if (e.children.length !== 0) continue;
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if ((e.innerText || '').trim() !== want) continue;
    if (r.y <= box.bottom - 2) continue;             // must be BELOW the input
    if (r.y > box.bottom + 420) continue;            // and attached to it
    if (r.right < box.left || r.left > box.right) continue;   // and overlapping
    hits.push({ e, y: r.y });
  }
  if (!hits.length) return { ok: false, reason: 'no option matched near the combobox' };
  hits.sort((a, b) => a.y - b.y);
  hits[0].e.click();
  return { ok: true, y: Math.round(hits[0].y), count: hits.length };
}
"""


def _handle(page):
    return page.evaluate_handle("() => window.__ctl").as_element()


def _tap(page):
    """Click+focus window.__ctl from inside the page.

    Deliberately NOT ElementHandle.click(). Playwright's actionability check waits
    for the element to be hit-testable, and a leftover dropdown overlay - e.g. from
    an interrupted run - covers the input and turns the call into a 30s timeout
    mid-form. `open_menus.py` uses a JS click for the same reason. The value is
    read back afterwards either way, so nothing is taken on trust.
    """
    page.evaluate("() => { window.__ctl.click(); window.__ctl.focus(); }")
    page.wait_for_timeout(180)


def _type(page, value, delay=25):
    page.keyboard.press("Control+A")
    page.keyboard.type(value, delay=delay)


def root(page):
    """Latch onto the open drawer. Call before any select()/fill()."""
    if not page.evaluate(_ROOT):
        raise RuntimeError("no open drawer - the panel is not showing")
    return True


def select(page, label, value, *, wait=2200):
    """Set a type-to-filter combobox. Raises if the value does not commit.

    Verified by reading the input back, because every failure mode observed on these
    controls is SILENT - the panel keeps looking plausible while holding nothing:
      * clicking the displayed text is a no-op
      * pressing Enter after typing leaves the filter text uncommitted
      * an unscoped option click hits the grid behind the drawer
    All three produce a screenshot that looks correct.
    """
    root(page)
    if not page.evaluate(_FIND, [label, "hr-base-selection-input"]):
        raise RuntimeError("combobox %r not found in the drawer" % label)
    _tap(page)
    page.wait_for_timeout(600)
    # Clear anything already in the filter, then type the target.
    _type(page, value, delay=60)
    page.wait_for_timeout(wait)

    box = page.evaluate("""() => {
      const r = window.__ctl.getBoundingClientRect();
      return { left: r.left, right: r.right, bottom: r.bottom };
    }""")
    res = page.evaluate(_PICK, [value, box])
    if not res.get("ok"):
        raise RuntimeError("no option %r under combobox %r (%s)"
                           % (value, label, res.get("reason")))
    page.wait_for_timeout(wait)

    # Read back from the RIGHT place. `input.value` is only the search box and is
    # cleared on commit, so checking it reports failure on a successful select.
    # The committed value renders in `.hr-base-selection-label`, and the wrapper
    # gains `hr-base-selection--selected`. Require both: the label alone could be
    # left-over filter text, the class alone does not prove which value landed.
    got = page.evaluate("""() => {
      let p = window.__ctl.parentElement;
      for (let i = 0; i < 4 && p; i++) {
        if ((p.className || '').toString().includes('hr-base-selection--selected')) {
          const lab = p.querySelector('.hr-base-selection-label');
          return lab ? (lab.innerText || '').trim() : (p.innerText || '').trim();
        }
        p = p.parentElement;
      }
      return null;
    }""")
    if got != value:
        raise RuntimeError("combobox %r did not commit: shows %r, wanted %r"
                           % (label, got, value))
    return True


def fill(page, label, value):
    """Set a plain text input by its label, and read it back."""
    root(page)
    if not page.evaluate(_FIND, [label, "hr-input__input-el"]):
        raise RuntimeError("input %r not found in the drawer" % label)
    _tap(page)
    _type(page, value)
    page.wait_for_timeout(420)
    got = page.evaluate("() => window.__ctl.value")
    if (got or "").strip() != value:
        raise RuntimeError("input %r did not take: shows %r" % (label, got))
    return True


_FIND_PH = """
(args) => {
  const [ph, cls] = args;
  const root = window.__root;
  if (!root) return false;
  for (const e of root.querySelectorAll('input.' + cls + ', textarea.' + cls)) {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if ((e.getAttribute('placeholder') || '') === ph) { window.__ctl = e; return true; }
  }
  return false;
}
"""

_FIND_ONLY = """
(cls) => {
  const root = window.__root;
  if (!root) return 0;
  const els = [...root.querySelectorAll('input.' + cls)].filter(e => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  });
  if (els.length === 1) { window.__ctl = els[0]; }
  return els.length;
}
"""


def fill_placeholder(page, placeholder, value):
    """Set a text input by placeholder. The Create-folder drawer has no adjacent
    label text for its controls, only placeholders."""
    root(page)
    if not page.evaluate(_FIND_PH, [placeholder, "hr-input__input-el"]):
        raise RuntimeError("input with placeholder %r not found" % placeholder)
    _tap(page)
    _type(page, value)
    page.wait_for_timeout(420)
    got = page.evaluate("() => window.__ctl.value")
    if (got or "").strip() != value:
        raise RuntimeError("input %r did not take: shows %r" % (placeholder, got))
    return True


def select_only(page, value, *, wait=2200):
    """Set the drawer's ONLY combobox. Refuses if there is more than one, rather
    than picking the first - guessing which control to drive is how the wrong thing
    gets set while the log still reads correct."""
    root(page)
    n = page.evaluate(_FIND_ONLY, "hr-base-selection-input")
    if n != 1:
        raise RuntimeError("expected exactly 1 combobox in the drawer, found %d" % n)
    _tap(page)
    page.wait_for_timeout(600)
    _type(page, value, delay=60)
    page.wait_for_timeout(wait)
    box = page.evaluate("""() => {
      const r = window.__ctl.getBoundingClientRect();
      return { left: r.left, right: r.right, bottom: r.bottom };
    }""")
    res = page.evaluate(_PICK, [value, box])
    if not res.get("ok"):
        raise RuntimeError("no option %r (%s)" % (value, res.get("reason")))
    page.wait_for_timeout(wait)
    got = page.evaluate("""() => {
      let p = window.__ctl.parentElement;
      for (let i = 0; i < 4 && p; i++) {
        if ((p.className || '').toString().includes('hr-base-selection--selected')) {
          const lab = p.querySelector('.hr-base-selection-label');
          return lab ? (lab.innerText || '').trim() : (p.innerText || '').trim();
        }
        p = p.parentElement;
      }
      return null;
    }""")
    if got != value:
        raise RuntimeError("combobox did not commit: shows %r, wanted %r" % (got, value))
    return True


def open_by_text(page, text):
    return page.evaluate("""(want) => {
      for (const e of document.querySelectorAll('button')) {
        const r = e.getBoundingClientRect();
        if (r.width > 0 && (e.innerText || '').trim() === want) { e.click(); return true; }
      }
      return false;
    }""", text)


# The options editor is a TABLE of click-to-edit cells, not a list of inputs.
# Measured: before you click a row, "Enter option" is a <p> inside a <td> and there
# is NO input in the DOM at all - so counting inputs reports 0 rows and the build
# aborts with "only 0 option rows for 14 options". Clicking the cell swaps in an
# `input.hr-input__input-el[placeholder="Enter option"]`.
_OPT_TBODY = """
() => {
  const root = window.__root;
  if (!root) return null;
  for (const tb of root.querySelectorAll('tbody')) {
    const tbl = tb.closest('table') || tb.parentElement;
    const head = (tbl ? tbl.innerText : '') || '';
    if (head.includes('Action')) { window.__opt = tb; return tb.querySelectorAll('tr').length; }
  }
  // Fall back to any tbody inside the drawer if the header text moves.
  const tb = root.querySelector('tbody');
  if (tb) { window.__opt = tb; return tb.querySelectorAll('tr').length; }
  return null;
}
"""


def option_rows(page):
    """Number of rows currently in the Checkbox/Dropdown options table."""
    root(page)
    n = page.evaluate(_OPT_TBODY)
    if n is None:
        raise RuntimeError("options table not found - is the field type set?")
    return n


def set_option(page, index, value):
    """Fill the index-th (0-based) option row. Clicks the cell to reveal its input."""
    root(page)
    if page.evaluate(_OPT_TBODY) is None:
        raise RuntimeError("options table not found")
    # A row is FIVE cells: [drag] [icon] [Checkbox label] [Value] [Action].
    # `row.querySelector('td')` returns the drag handle, whose click does nothing -
    # so the input never appears and the row looks permanently uneditable. Target
    # the label cell by its content instead of by position.
    ok = page.evaluate("""(i) => {
      const rows = [...window.__opt.querySelectorAll('tr')];
      if (i >= rows.length) return false;
      const tds = [...rows[i].querySelectorAll('td')];
      let cell = tds.find(td => td.querySelector('input'));
      if (!cell) cell = tds.find(td => (td.innerText || '').trim() === 'Enter option');
      // Otherwise the label cell is the first one carrying any text that is not
      // the Value column's hint.
      if (!cell) {
        cell = tds.find(td => {
          const t = (td.innerText || '').trim();
          return t && !t.startsWith('If not edited');
        });
      }
      if (!cell) return false;
      (cell.querySelector('input') || cell.querySelector('p') || cell).click();
      window.__optcell = cell;
      return true;
    }""", index)
    if not ok:
        raise RuntimeError("option row %d has no editable label cell" % index)
    page.wait_for_timeout(300)

    found = page.evaluate("""() => {
      const inp = window.__optcell.querySelector('input');
      if (!inp) return false;
      window.__ctl = inp;
      return true;
    }""")
    if not found:
        raise RuntimeError("option row %d did not become editable" % index)

    _tap(page)
    _type(page, value, delay=14)
    page.wait_for_timeout(240)
    got = page.evaluate("() => window.__ctl.value")
    if (got or "").strip() != value:
        raise RuntimeError("option %d did not take: shows %r" % (index, got))
    return True


def add_option_row(page):
    """Click '+ Add option'. Scoped to the drawer.

    Must prefer the real <button>. The label renders as button > span > p, so a
    matcher that skips elements with children lands on the inert inner <span>:
    the click succeeds, returns true, and adds no row - the run then fails much
    later with "only 1 option rows for 14 options".
    """
    root(page)
    return page.evaluate("""() => {
      const root = window.__root;
      const want = t => t === '+ Add option' || t === 'Add option';
      for (const e of root.querySelectorAll('button,[role=button]')) {
        const r = e.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        if (want((e.innerText || '').trim())) { e.click(); return true; }
      }
      // Fall back to a clickable ancestor of the label text.
      for (const e of root.querySelectorAll('span,p,div,a')) {
        if (e.children.length > 0) continue;
        const r = e.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        if (!want((e.innerText || '').trim())) continue;
        let p = e;
        for (let i = 0; i < 4 && p; i++) {
          if (p.tagName === 'BUTTON' || p.getAttribute('role') === 'button') {
            p.click(); return true;
          }
          p = p.parentElement;
        }
        e.click();
        return true;
      }
      return false;
    }""")


def snapshot(page):
    """Visible leaf text + inputs. Used to diff the panel before/after a change."""
    return page.evaluate("""() => {
      const vis = e => {
        const r = e.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return false;
        const cs = getComputedStyle(e);
        return cs.visibility !== 'hidden' && cs.display !== 'none';
      };
      const texts = [];
      for (const e of document.querySelectorAll('*')) {
        if (!vis(e) || e.children.length !== 0) continue;
        const t = (e.innerText || '').trim();
        if (t && t.length <= 60) texts.push(t);
      }
      const inputs = [...document.querySelectorAll('input,textarea')]
        .filter(vis)
        .map(e => ({ ph: e.getAttribute('placeholder') || '',
                     cls: (e.className || '').toString().slice(0, 44),
                     val: (e.value || '').slice(0, 40),
                     y: Math.round(e.getBoundingClientRect().y) }));
      const buttons = [...document.querySelectorAll('button')]
        .filter(vis).map(e => (e.innerText || '').trim()).filter(Boolean);
      return { texts: [...new Set(texts)], inputs, buttons: [...new Set(buttons)] };
    }""")


def open_create_field(page):
    return page.evaluate("""() => {
      for (const e of document.querySelectorAll('button')) {
        const r = e.getBoundingClientRect();
        if (r.width > 0 && (e.innerText || '').trim() === 'Create field') {
          e.click(); return true;
        }
      }
      return false;
    }""")


def escape(page, times=3):
    """Close panels with Escape ONLY. Never click elsewhere to dismiss - a stray
    click in a settings panel is the exact failure this harness exists to avoid."""
    for _ in range(times):
        page.keyboard.press("Escape")
        page.wait_for_timeout(900)
