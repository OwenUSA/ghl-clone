"""READ-ONLY: capture the finished result for the hand-off document.

Three shots:
  1. Settings > Custom Fields, Folders tab  - the three new folders
  2. Settings > Custom Fields, filtered to Opportunity - the 25 new fields
  3. A real opportunity - the checklist as staff will actually see it

Shot 3 is the one that matters. The settings pages prove the fields were created;
only the opportunity proves they are usable on a job.

Navigating and opening a record is read-only. Nothing is clicked that saves, and the
opportunity is left exactly as found - no field is filled in, not even to demonstrate.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from attach import attach
from playwright.sync_api import sync_playwright
from settle import settle

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "captures" / "checklists"
from ghl_account import LOC  # noqa: E402  (flat script layout)

BASE = "https://app.gohighlevel.com/v2/location/" + LOC


def shoot(page, url, name, *, after_ms=6000, then=None):
    print("\n-> %s" % name)
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    ok, reason = settle(page, timeout_ms=90000)
    print("   settle:", ok, reason[:60])
    page.wait_for_timeout(after_ms)
    if then:
        then(page)
        page.wait_for_timeout(5000)
    path = OUT / ("result__%s.png" % name)
    try:
        page.screenshot(path=str(path), timeout=20000)
        print("   saved", path.name)
    except Exception as exc:
        print("   screenshot failed:", str(exc)[:70])


def open_card(page):
    """Click the first kanban card's title to open the job detail.

    Deliberately targets the card TITLE, not the card. Cards carry a row of action
    icons (call, message, tasks, appointment) along the bottom - clicking the card
    body risks landing on one of those, and the leftmost is a phone call.
    """
    hit = page.evaluate("""() => {
      const bad = /call|dial|message|sms|email|delete|remove/i;
      for (const e of document.querySelectorAll('*')) {
        if (e.children.length !== 0) continue;
        const r = e.getBoundingClientRect();
        if (r.width < 80 || r.height < 10 || r.y < 380) continue;
        const t = (e.innerText || '').trim();
        // Card titles look like "74223369 ROOF - Gla..." or "AIDA CASTANEDA - A..."
        if (t.length < 8 || t.length > 60) continue;
        if (bad.test(t)) continue;
        if (!/ROOF|-/.test(t)) continue;
        if ((e.getAttribute('aria-label') || '')) continue;
        // The title must not be inside a link. The card's contact name IS an
        // anchor to the contact record - clicking it navigated to Contacts
        // instead of opening the job.
        if (e.closest('a')) continue;
        e.click();
        return t;
      }
      return null;
    }""")
    print("   clicked card:", hit)


def first_opportunity_id(page):
    """Get a real opportunity id from the search API.

    Matching links by href was too loose - `/opportunities/forecast` satisfied a
    "looks like an id" regex and the shot captured the Forecast page instead of a
    job. Ids come from the API, where they are unambiguous.
    """
    found = []

    def on_response(resp):
        if "opportunities/search" not in resp.url:
            return
        try:
            if "json" not in (resp.headers.get("content-type") or ""):
                return
            body = resp.json()
        except Exception:
            return

        def walk(node):
            if isinstance(node, list):
                for x in node:
                    walk(x)
            elif isinstance(node, dict):
                if node.get("id") and node.get("pipelineId"):
                    found.append({"id": node["id"], "name": node.get("name")})
                for v in node.values():
                    walk(v)
        walk(body)

    page.on("response", on_response)
    page.goto(BASE + "/dashboard", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(2500)
    page.goto(BASE + "/opportunities/list", wait_until="domcontentloaded",
              timeout=90000)
    settle(page, timeout_ms=90000)
    page.wait_for_timeout(9000)
    page.remove_listener("response", on_response)
    return found[0] if found else None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        b, ctx, page = attach(pw)
        page.bring_to_front()
        shoot(page, BASE + "/settings/fields?tab=folder", "folders")
        shoot(page, BASE + "/settings/fields?tab=field", "fields")

        # Open the detail from a card. Navigating straight to /opportunities/<id>
        # just redirects back to the board in this build, so the card click is the
        # only route in. Clicking a card opens a record; it does not modify it.
        shoot(page, BASE + "/opportunities/list", "opportunity",
              after_ms=9000, then=open_card)
    print("\ndone - read-only, nothing was modified.")


if __name__ == "__main__":
    main()
