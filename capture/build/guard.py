"""The ONE place a mutating click on the live GHL account is allowed to happen.

Context: `DECISIONS.md` -> "Safety contract (binding)" is read-only on this account.
That contract was lifted on 2026-08-13 by the account owner, for ONE narrow scope:
creating opportunity custom fields and DRAFT workflows for the CompanyCam checklist
migration. Everything the contract forbids that is NOT on that list stays forbidden,
and this module is what enforces the difference at click time.

The paranoia is inherited from `capture/open_menus.py` and is not theoretical: GHL packs
"Call: +1786... | Delete Conversation" into a 40px pitch, so a coordinate click that
drifts one slot places a real phone call. Hence:

  * never click by coordinate - resolve by exact label
  * re-read the resolved element's own label and abort if it hits DENY
  * the label must ALSO hit ALLOW - an unrecognised verb is refused, not guessed
  * screenshot before and after every single click
  * append every attempt to an append-only JSONL audit trail, refusals included

`--dry-run` resolves and logs but never clicks. Every caller runs dry first.
"""
import argparse
import json
import pathlib
import re
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SHOTS = ROOT / "captures" / "checklists"
AUDIT = SHOTS / "audit.jsonl"

from ghl_account import LOC  # noqa: E402  (flat script layout)

BASE = "https://app.gohighlevel.com/v2/location/" + LOC

# Anything that could destroy data, SPEND MONEY, or reach a customer. Checked
# against the label we asked for AND against the label the DOM actually gave back.
#
# The money terms are not hypothetical. GHL bills per SMS/email/minute and meters
# its AI features separately, and the Workflows empty state on this very account
# advertises "Build workflows for free by chatting with AI - BETA" right next to
# the Create workflow button. Custom fields and draft workflows are free; the
# things sitting one slot away from them are not.
DENY = re.compile(
    r"delete|remove|archive|send|call|dial|publish|import|export|merge|"
    r"opt.?out|test|execute|deactivate|activate|disconnect|revoke|reset|"
    r"clear|wipe|drop|unsubscribe|block|"
    # money / metered
    r"buy|purchase|upgrade|subscribe|billing|payment|checkout|add.?on|"
    r"\bai\b|copilot|assistant|generate|enhance|rebill|wallet|credits?|"
    r"provision|a2p|register|verify.*number|premium|enable",
    re.IGNORECASE)

# The only write verbs this migration needs. Deliberately small and CLOSED: if a
# future step needs a new verb, that is a decision someone makes on purpose, in a diff.
#
# These are MEASURED from the live panels (captures/settings/create_field_panel.json),
# not assumed. The first draft guessed "Add Field"/"Add Folder"; GHL actually says
# "Create field" / "Create folder", and the submit button is "Create custom field" -
# so every one of those guesses would have failed to resolve.
ALLOW = re.compile(
    r"^(save|update|cancel|continue|next|done|confirm|create|"
    r"create field|create folder|create custom field|create workflow|"
    r"save action|save trigger)$",
    re.IGNORECASE)


class Refused(Exception):
    """A click was blocked by the guard. Never caught-and-continued silently."""


# Lifted from open_menus.py:RESOLVE. Extended to also match on visible innerText,
# because GHL's settings buttons ("Add Field") carry no aria-label - the menus this
# pattern was written for did. Exact match only; no substring, no fuzzy.
RESOLVE = """
(label) => {
  const vis = e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const sel = 'button,[role=button],[role=menuitem],[role=tab],a,'
            + '[aria-haspopup],[class*=cursor-pointer]';
  const hits = [];
  for (const e of document.querySelectorAll(sel)) {
    if (!vis(e)) continue;
    const aria = (e.getAttribute('aria-label') || e.getAttribute('title') || '').trim();
    const text = (e.innerText || '').trim();
    const isButton = e.tagName === 'BUTTON' || e.getAttribute('role') === 'button';
    // Prefer aria. Fall back to innerText for a real <button> at any depth - GHL
    // wraps its labels several nodes deep (the folder drawer's "Create" button has
    // 5 descendants), and a leaf-only rule silently fails to find the submit
    // control. For non-buttons keep the leaf restriction, so we never match a
    // whole panel whose concatenated text happens to equal the label.
    const cand = aria || ((isButton || e.querySelectorAll('*').length <= 3) ? text : '');
    if (cand === label) {
      const r = e.getBoundingClientRect();
      hits.push({ el: e, label: cand, x: Math.round(r.x), y: Math.round(r.y),
                  tag: e.tagName.toLowerCase(),
                  disabled: !!(e.disabled || e.getAttribute('aria-disabled') === 'true') });
    }
  }
  if (hits.length === 0) return { found: false, count: 0 };
  // Ambiguity is an error, never a guess - same rule as the ghl CLI's exit 5.
  if (hits.length > 1) {
    return { found: false, count: hits.length, ambiguous: true,
             at: hits.map(h => [h.x, h.y]) };
  }
  window.__target = hits[0].el;
  return { found: true, count: 1, label: hits[0].label, disabled: hits[0].disabled,
           x: hits[0].x, y: hits[0].y, tag: hits[0].tag };
}
"""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def audit(record):
    """Append-only. Written before the click, and again after, so a crash mid-click
    still leaves evidence that the click was attempted."""
    SHOTS.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def shot(page, step, when):
    SHOTS.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-z0-9]+", "-", step.lower()).strip("-")[:60]
    path = SHOTS / ("%s__%s.png" % (safe, when))
    try:
        # Short timeout: the default 30s was spent stalled on "waiting for fonts
        # to load" and then threw anyway. Evidence is worth having, but never at
        # the cost of the run that produces it.
        page.screenshot(path=str(path), timeout=12000)
    except Exception as exc:                      # a failed screenshot must not
        return "SCREENSHOT_FAILED: %s" % str(exc)[:80]   # abort a half-done step
    try:
        return str(path.relative_to(ROOT))
    except ValueError:                            # SHOTS redirected outside the
        return str(path)                          # repo (tests) - absolute is fine


class Guard:
    """Wraps a page. `dry_run=True` means nothing is ever clicked."""

    def __init__(self, page, *, dry_run=True, step="unnamed"):
        self.page = page
        self.dry_run = dry_run
        self.step = step
        self.clicks = 0

    def _refuse(self, label, why, extra=None):
        rec = {"ts": _now(), "step": self.step, "label": label,
               "url": self.page.url, "result": "REFUSED", "why": why}
        if extra:
            rec.update(extra)
        audit(rec)
        raise Refused("%s -- label=%r" % (why, label))

    def click(self, label, *, wait_ms=1200):
        """Resolve `label` exactly, verify it against DENY and ALLOW, then click.

        Raises Refused on anything unexpected. Returns the resolved info dict, or
        None in dry-run.
        """
        # Gate 1: the label we were ASKED for.
        if DENY.search(label):
            self._refuse(label, "requested label matches DENY")
        if not ALLOW.match(label.strip()):
            self._refuse(label, "requested label is not in ALLOW")

        info = self.page.evaluate(RESOLVE, label)
        if info.get("ambiguous"):
            self._refuse(label, "ambiguous: %d elements match" % info["count"],
                         {"at": info.get("at")})
        if not info.get("found"):
            self._refuse(label, "not found")

        # Gate 2: the label the DOM actually handed back. Guards against a
        # resolver bug pointing __target at something other than what we named.
        if DENY.search(info["label"]):
            self._refuse(label, "resolved element matches DENY",
                         {"resolved": info["label"]})

        # A disabled submit means the form is incomplete. Clicking it does nothing
        # and the run would report success having saved no record, so fail loudly
        # here instead of producing a confident, wrong summary.
        if info.get("disabled"):
            self._refuse(label, "resolved element is DISABLED - form incomplete")

        before = shot(self.page, "%s__%s" % (self.step, label), "before")
        rec = {"ts": _now(), "step": self.step, "label": label,
               "resolved": info["label"], "at": [info["x"], info["y"]],
               "tag": info["tag"], "url": self.page.url, "before": before}

        if self.dry_run:
            rec["result"] = "DRY_RUN"
            audit(rec)
            print("   [dry] would click %-24r at (%s,%s) <%s>"
                  % (label, info["x"], info["y"], info["tag"]))
            return None

        audit(dict(rec, result="CLICKING"))
        self.page.evaluate("() => window.__target.click()")
        self.page.wait_for_timeout(wait_ms)
        self.clicks += 1

        rec["result"] = "CLICKED"
        rec["after"] = shot(self.page, "%s__%s" % (self.step, label), "after")
        rec["url_after"] = self.page.url
        audit(rec)
        print("   [ok]  clicked %-24r -> %s" % (label, rec["after"]))
        return info

    def fill(self, selector, value, *, label=""):
        """Type into an input. Typing is not a mutation on its own - only the
        subsequent Save is - but it is audited so the trail is complete."""
        rec = {"ts": _now(), "step": self.step, "action": "fill",
               "selector": selector, "value": value, "label": label,
               "url": self.page.url}
        if self.dry_run:
            audit(dict(rec, result="DRY_RUN"))
            print("   [dry] would fill %-28s = %r" % (selector, value))
            return
        self.page.fill(selector, value)
        audit(dict(rec, result="FILLED"))
        print("   [ok]  filled %-28s = %r" % (selector, value))


def common_args(desc):
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--apply", action="store_true",
                    help="actually click. Without it, everything is a dry run.")
    return ap
