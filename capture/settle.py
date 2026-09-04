"""Wait for a GHL view to actually finish hydrating.

Text markers alone are NOT enough: GHL's content region shows a bare SVG
spinner with no text, which a text-only check reports as 'settled' and would
record a loading state as ground truth. This checks three things:
  1. no loading text
  2. no visible spinner element
  3. DOM signature stable for `quiet_ms`
"""

LOADING_MARKERS = (
    "Loading fresh data",
    "Loading new sub account",
    "Initializing",
    "Loading...",
)

PROBE = """
() => {
  const txt = document.body ? (document.body.innerText || '') : '';
  const vis = e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  // Only a LARGE spinner blocks a capture. GHL ships a permanent 14x14
  // 'hr-base-loading' placeholder inside loaded components (measured on
  // Conversations), which a naive check reports as 'still loading' forever.
  // The real blocking spinner on Contacts is ~64px and centred.
  // An infinite-animation heuristic was tried and dropped: too many false
  // positives in a chat UI.
  const MIN_BLOCKING_PX = 24;
  let spinner = false;
  let spinnerCls = '';
  const sel = '[class*=spinner],[class*=loader],[class*=loading],[class*=skeleton],'
            + '[role=progressbar],.animate-spin';
  for (const e of document.querySelectorAll(sel)) {
    if (!vis(e)) continue;
    const r = e.getBoundingClientRect();
    if (r.width >= MIN_BLOCKING_PX && r.height >= MIN_BLOCKING_PX) {
      spinner = true;
      spinnerCls = (e.getAttribute('class') || '').slice(0, 60);
      break;
    }
  }
  return {
    nodes: document.querySelectorAll('*').length,
    textlen: txt.length,
    text: txt.slice(0, 200),
    spinner: spinner,
    spinnerCls: spinnerCls,
  };
}
"""


# A spinner/skeleton only means "not ready" when there is no real content yet.
# Measured: Contacts stuck-loading = 339 chars of text; loaded views = 934
# (Conversations) to 2921 (Contacts). Opportunities keeps permanent
# 'crm-opportunities-card-skeleton' placeholders for off-screen cards in its
# virtualised kanban, so "any skeleton = still loading" is wrong.
TEXT_OK = 600


def settle(page, timeout_ms=90000, quiet_ms=2500, poll_ms=750, min_nodes=500, verbose=False):
    """Return (ok, reason). ok=False means the capture is NOT trustworthy."""
    waited = 0
    stable_for = 0
    last = None
    last_state = {}
    while waited < timeout_ms:
        try:
            s = page.evaluate(PROBE)
        except Exception:
            page.wait_for_timeout(poll_ms)
            waited += poll_ms
            continue
        last_state = s
        loading_text = any(m in s["text"] for m in LOADING_MARKERS)
        sig = (s["nodes"], s["textlen"])
        blocked = (loading_text
                   or s["nodes"] < min_nodes
                   or (s["spinner"] and s["textlen"] < TEXT_OK))

        if not blocked and sig == last:
            stable_for += poll_ms
            if stable_for >= quiet_ms:
                return True, "settled nodes=%d textlen=%d" % sig
        else:
            stable_for = 0
        last = sig
        if verbose:
            print("   .. t=%ds nodes=%s text=%s spinner=%s"
                  % (waited // 1000, s["nodes"], s["textlen"], s["spinner"]))
        page.wait_for_timeout(poll_ms)
        waited += poll_ms

    return False, "TIMEOUT after %ds (nodes=%s spinner=%s) - capture NOT trustworthy" % (
        timeout_ms // 1000, last_state.get("nodes"), last_state.get("spinner"))
