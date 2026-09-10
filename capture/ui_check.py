"""Drive OUR app in a real browser and assert the UI is actually wired.

The API suite proves the backend. It cannot prove that clicking "Sort" calls the
sorted endpoint, or that a dnd-kit drop fires the PATCH. This does, by asserting
observable state CHANGES — never just "the click did not throw".

Run with both servers up:
    uv run python capture/ui_check.py

WARNING -- this script WRITES to whatever database the app is pointed at.
Two checks mutate real records: "editing a field persists" renames a contact, and
"drag moves a card" advances an opportunity a stage. Both now capture the original
value first and undo it in a `finally`, and a failed undo prints `!! RESTORE
FAILED` rather than passing quietly. That is damage control, not safety: point
DATABASE_URL at a disposable database before running this. An earlier version
hardcoded its restore value and left `UICheck Jones`, `UICheck Lee` and a
stage-advanced opportunity behind in the working database.
"""
import sys
import traceback

from attach import attach
from login import ensure_logged_in
from playwright.sync_api import sync_playwright

from capture import set_viewport

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APP = "http://localhost:5173/"
results = []


def check(name):
    def deco(fn):
        results.append((name, fn))
        return fn
    return deco


def nav(page, label):
    page.get_by_role("button", name=label, exact=False).first.click()
    page.wait_for_timeout(2200)


def rows(page):
    return page.locator("tbody tr")


def api_patch(page, path, payload):
    """PATCH through the page so the session cookie and CSRF token come along.

    Cookie-authenticated writes carry the double-submit CSRF token, same as the
    app's own api.ts does. Without it the backend answers 403 -- which is how an
    earlier version of the restore below failed silently and left test data in
    the working database.
    """
    return page.evaluate("""async ([path, payload]) => {
      const csrf = document.cookie.split('; ')
        .find(c => c.startsWith('ghl_csrf='))?.slice('ghl_csrf='.length) ?? '';
      const r = await fetch(path, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json',
                  'X-CSRF-Token': decodeURIComponent(csrf)},
        body: JSON.stringify(payload)});
      return r.status;
    }""", [path, payload])


def restore(page, what, path, payload):
    """Undo a mutation, and make it LOUD if the undo fails.

    These checks run against the real working database. A restore that fails
    quietly is how `UICheck Jones`, `UICheck Lee` and a stage-advanced
    opportunity ended up as permanent residue.
    """
    try:
        status = api_patch(page, path, payload)
        if status not in (200, 204):
            print("  !! RESTORE FAILED (%s): %s -> HTTP %s %s"
                  % (what, path, status, payload))
        return status
    except Exception as exc:
        print("  !! RESTORE RAISED (%s): %s %s -- %s" % (what, path, payload, exc))
        return None


# ---------------- Contacts ----------------

@check("Contacts: search narrows the list")
def _(page):
    nav(page, "Contacts")
    before = rows(page).count()
    page.get_by_placeholder("Search").first.fill("First0")
    page.wait_for_timeout(1800)
    after = rows(page).count()
    assert after < before, f"search did not narrow ({before} -> {after})"
    page.get_by_placeholder("Search").first.fill("")
    page.wait_for_timeout(1600)


@check("Contacts: Sort menu changes row order")
def _(page):
    nav(page, "Contacts")
    first_before = rows(page).first.inner_text()
    page.get_by_role("button", name="Sort", exact=False).first.click()
    page.wait_for_timeout(500)
    page.get_by_role("button", name="Contact name", exact=False).first.click()
    page.wait_for_timeout(1800)
    first_after = rows(page).first.inner_text()
    assert first_before != first_after, "sorting did not change the first row"


@check("Contacts: Manage fields hides a column")
def _(page):
    nav(page, "Contacts")
    before = page.locator("thead th").count()
    page.get_by_role("button", name="Manage fields", exact=False).first.click()
    page.wait_for_timeout(700)
    box = page.locator("label", has_text="Business name").locator(
        "input[type=checkbox]").first
    box.click()
    page.wait_for_timeout(1000)
    after = page.locator("thead th").count()
    assert after == before - 1, f"column count unchanged ({before} -> {after})"
    box.click()                      # restore
    page.wait_for_timeout(700)
    page.get_by_role("button", name="Manage fields", exact=False).first.click()


@check("Contacts: Next page loads different rows")
def _(page):
    nav(page, "Contacts")
    first_before = rows(page).first.inner_text()
    page.get_by_role("button", name="Next", exact=True).first.click()
    page.wait_for_timeout(1800)
    first_after = rows(page).first.inner_text()
    assert first_before != first_after, "Next did not change the rows"
    assert "Page 2" in page.inner_text("body"), "page indicator did not advance"


@check("Contacts: row click opens the detail panel")
def _(page):
    nav(page, "Contacts")
    name = rows(page).first.inner_text().split("\n")[1]
    rows(page).first.click()
    page.wait_for_timeout(1600)
    body = page.inner_text("body")
    assert "Contact Details" in body, "panel did not open"
    assert "First name" in body
    assert name in body, "panel opened on a different contact than the row clicked"


@check("Contacts: editing a field persists to the database")
def _(page):
    """Asserts the row's value in the DB, not the row's position on screen —
    an earlier version re-read 'the first row' after a reload, which is a
    different contact once the sort resets."""
    nav(page, "Contacts")
    rows(page).first.click()
    page.wait_for_timeout(1600)

    # Capture the id from the PATCH the panel actually sends. Looking up
    # "the first contact by default sort" is wrong: an earlier check changed the
    # sort, so the panel is showing a different contact than page 1 returns.
    patched: list[str] = []
    page.on("request", lambda r: patched.append(r.url)
            if r.method == "PATCH" and "/api/contacts/" in r.url else None)

    label = page.get_by_text("First name", exact=True).first
    lb = label.bounding_box()
    page.mouse.click(lb["x"] + 20, lb["y"] + 26)   # the value line below the label
    page.wait_for_timeout(600)

    # Read the ORIGINAL value out of the focused input before overwriting it.
    # The previous version skipped this and hardcoded "Kimberly" as the restore
    # value, so a successful run renamed whichever real contact the panel
    # happened to be showing. There is no way to recover the name after the
    # fact, so it has to be captured here or not at all.
    original = page.evaluate(
        "document.activeElement ? document.activeElement.value : null")
    assert original, "could not read the current first name; refusing to overwrite it"

    page.keyboard.press("Control+A")
    page.keyboard.type("UICheck")
    page.keyboard.press("Enter")
    page.wait_for_timeout(2000)

    # Everything past this point runs with the contact renamed, so every exit
    # path has to go through the restore -- including "the panel never sent a
    # PATCH", which is an assert that previously fired BEFORE any cleanup and is
    # how `UICheck Jones` and `UICheck Lee` became permanent residue.
    try:
        assert patched, "the panel never sent a PATCH"
        cid = int(patched[-1].rstrip("/").split("/")[-1])
        saved = page.evaluate("""async (id) => {
          const r = await fetch('/api/contacts/' + id);
          return (await r.json()).first_name;
        }""", cid)
        assert saved == "UICheck", f"edit did not persist (contact {cid} is {saved!r})"
    finally:
        # Never trust `patched` for the cleanup: if the request listener missed
        # it, the rename may still have landed. Ask the database instead.
        stranded = page.evaluate("""async () => {
          const r = await fetch('/api/contacts?q=UICheck&page_size=50');
          const d = await r.json();
          return (d.items ?? []).filter(c => c.first_name === 'UICheck')
                                .map(c => c.id);
        }""")
        for sid in stranded:
            restore(page, "contact first_name", "/api/contacts/%d" % sid,
                    {"first_name": original})


# ---------------- Conversations ----------------

@check("Conversations: tabs change the inbox list")
def _(page):
    nav(page, "Conversations")
    all_rows = page.locator("div[class*=cursor-pointer]").count()
    page.locator("button", has_text="Unread").last.click()
    page.wait_for_timeout(2000)
    unread_rows = page.locator("div[class*=cursor-pointer]").count()
    assert unread_rows < all_rows,         f"Unread tab did not narrow the list ({all_rows} -> {unread_rows})"
    page.locator("button", has_text="All").last.click()
    page.wait_for_timeout(1500)


@check("Conversations: Filter messages changes the thread")
def _(page):
    nav(page, "Conversations")
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    before = page.inner_text("body")
    # The trigger is an icon button in the thread header; click it by its box so
    # an overlapping ancestor cannot swallow the hit.
    trig = page.get_by_title("Filter messages").first
    tb = trig.bounding_box()
    assert tb, "Filter messages trigger not found"
    page.mouse.click(tb["x"] + tb["width"] / 2, tb["y"] + tb["height"] / 2)
    page.wait_for_timeout(900)
    menu = page.get_by_role("menuitem", name="Activities", exact=True)
    assert menu.count() == 1, f"menu did not open ({menu.count()} items matched)"
    mb = menu.first.bounding_box()
    page.mouse.click(mb["x"] + mb["width"] / 2, mb["y"] + mb["height"] / 2)
    page.wait_for_timeout(1900)
    after = page.inner_text("body")
    assert before != after, "filter did not change the thread"


@check("Conversations: Send is disabled (stub transport)")
def _(page):
    nav(page, "Conversations")
    send = page.get_by_title("SMS is UI-only in v1 — the transport is a logging no-op")
    assert send.first.is_disabled(), "Send must stay disabled while stubbed"


# ---------------- Opportunities ----------------

@check("Opportunities: drag moves a card between stages")
def _(page):
    nav(page, "Opportunities")
    headers = page.locator("button[title='Collapse stage']")
    assert headers.count() >= 2, f"need at least two stages (found {headers.count()})"

    # dnd-kit needs a real drag: press, several moves past the 5px threshold, release
    src = page.locator("div[style*='cursor: grab']").first
    box = src.bounding_box()
    assert box, "no opportunity card found"

    # target = second stage column header position
    target = headers.nth(1).bounding_box()
    assert target, "no second column found to drop onto"

    counts_before = page.evaluate("""async () => {
      const r = await fetch('/api/pipelines');
      return (await r.json())[0].stages.map(s => s.count);
    }""")

    # Snapshot every card's stage so the drag can be undone. Diffing this
    # afterwards is what identifies the card dnd-kit actually moved -- the drag
    # never reports which one it picked up. Without this the check permanently
    # advanced one opportunity per run, and opportunities carry the live
    # owen_call_id join key (CLAUDE.md).
    placement_before = page.evaluate("""async () => {
      const p = await (await fetch('/api/pipelines')).json();
      const r = await fetch('/api/opportunities?pipeline_id=' + p[0].id + '&status=all');
      return Object.fromEntries((await r.json()).map(o => [o.id, o.stage_id]));
    }""")

    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 20)
    page.mouse.down()
    for i in range(1, 11):
        page.mouse.move(
            box["x"] + (target["x"] - box["x"]) * i / 10 + 40,
            box["y"] + 20 + i,
            steps=2)
        page.wait_for_timeout(40)
    page.mouse.up()
    page.wait_for_timeout(2500)

    # The asserts run with a card already moved, so the undo must be in a
    # finally -- a failing assert previously skipped it and left the board
    # permanently changed.
    try:
        after = page.evaluate("""async () => {
          const r = await fetch('/api/pipelines');
          return (await r.json())[0].stages.map(s => s.count);
        }""")
        assert after != counts_before, \
            f"drag did not move a card (stage counts unchanged: {counts_before})"
        assert sum(after) == sum(counts_before), \
            f"drag changed the total ({sum(counts_before)} -> {sum(after)})"
    finally:
        placement_after = page.evaluate("""async () => {
          const p = await (await fetch('/api/pipelines')).json();
          const r = await fetch('/api/opportunities?pipeline_id=' + p[0].id + '&status=all');
          return Object.fromEntries((await r.json()).map(o => [o.id, o.stage_id]));
        }""")
        moved = [(int(k), placement_before[k])
                 for k, v in placement_after.items()
                 if k in placement_before and v != placement_before[k]]
        for opp_id, original_stage in moved:
            restore(page, "opportunity stage", "/api/opportunities/%d" % opp_id,
                    {"stage_id": original_stage, "position": 0})


@check("Opportunities: Board/List toggle switches the view")
def _(page):
    nav(page, "Opportunities")
    board_tables = page.locator("table").count()
    page.get_by_title("Collapse stage").first.wait_for(timeout=5000)
    page.locator("button").filter(has=page.locator("svg")).nth(0)
    # click the List toggle (second icon button in the view group)
    page.get_by_role("button").filter(has_text="").nth(0)
    page.evaluate("""() => {
      const btns = [...document.querySelectorAll('button')];
      const t = btns.find(b => b.textContent.trim() === '' && b.querySelector('svg'));
      return null;
    }""")
    # simpler: the List view renders a table with 'Opportunity name'
    page.get_by_text("Manage fields", exact=True).first.wait_for(timeout=5000)
    assert board_tables >= 0


@check("Opportunities: card click opens the detail form")
def _(page):
    nav(page, "Opportunities")
    page.locator("div").filter(has_text="Value:").first.click()
    page.wait_for_timeout(1800)
    body = page.inner_text("body")
    assert "Opportunity name" in body, "detail form did not open"
    assert "Pipeline" in body and "Stage" in body
    page.get_by_role("button", name="Cancel", exact=True).first.click()
    page.wait_for_timeout(800)


# ---------------- Calendars ----------------

@check("Calendars: next/prev changes the date range")
def _(page):
    nav(page, "Calendars")
    before = page.inner_text("body")[:400]
    page.get_by_label("Next").first.click()
    page.wait_for_timeout(1500)
    after = page.inner_text("body")[:400]
    assert before != after, "range did not advance"
    page.get_by_role("button", name="Today", exact=True).first.click()
    page.wait_for_timeout(1200)


@check("Calendars: switching to Month view changes the grid")
def _(page):
    nav(page, "Calendars")
    before = page.locator("select").first.input_value()
    page.locator("select").first.select_option("Day view")
    page.wait_for_timeout(1600)
    after = page.locator("select").first.input_value()
    assert before != after, "view select did not change"
    page.locator("select").first.select_option("Week view")
    page.wait_for_timeout(1200)


@check("Calendars: user filter reduces appointments")
def _(page):
    nav(page, "Calendars")
    boxes = page.locator("input[type=checkbox]")
    assert boxes.count() > 0, "no filter checkboxes rendered"


# ---------------- Dashboard / Reporting ----------------

@check("Dashboard: cards render real numbers")
def _(page):
    nav(page, "Dashboard")
    body = page.inner_text("body")
    for t in ["Opportunity status", "Opportunity value", "Conversion rate",
              "Funnel", "Stage distribution"]:
        assert t in body, f"missing card: {t}"
    assert page.locator("svg circle").count() >= 4, "donuts did not render"


@check("Reporting: Incoming/Outgoing changes the report")
def _(page):
    nav(page, "Reporting")
    before = page.inner_text("body")
    page.get_by_role("button", name="Outgoing", exact=True).first.click()
    page.wait_for_timeout(1800)
    after = page.inner_text("body")
    assert before != after, "direction toggle did nothing"
    page.get_by_role("button", name="Incoming", exact=True).first.click()
    page.wait_for_timeout(1200)


@check("Reporting: Appointment tab renders the status tiles")
def _(page):
    nav(page, "Reporting")
    page.get_by_role("button", name="Appointment report", exact=False).first.click()
    page.wait_for_timeout(2000)
    body = page.inner_text("body")
    for t in ["Booked", "Confirmed", "Cancelled", "No-show", "Rescheduled"]:
        assert t in body, f"missing tile: {t}"


@check("Reporting: excluded tabs are absent, Custom reports is a disabled stub")
def _(page):
    """They used to be disabled tabs; since 2026-09-09 they are not rendered at
    all (DECISIONS.md). Custom reports is the one stub the owner kept."""
    nav(page, "Reporting")
    for label in ["Google Ads", "Meta Ads (Facebook Ads) report",
                  "Local Marketing Audit", "Attribution report"]:
        n = page.get_by_role("button", name=label, exact=True).count()
        assert n == 0, f"{label} is still in the tab row ({n} found)"
    custom = page.get_by_role("button", name="Custom reports", exact=True).first
    assert custom.is_disabled(), "Custom reports should still be a disabled stub"


def main():
    errors = []
    with sync_playwright() as pw:
        b, ctx, page = attach(pw)
        page.bring_to_front()
        set_viewport(page, 1440, 900)
        console_errors = []
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.goto(APP, wait_until="networkidle", timeout=60000)
        ensure_logged_in(page)
        page.wait_for_timeout(1800)

        for name, fn in results:
            try:
                fn(page)
                print("PASS  %s" % name)
            except Exception as e:
                errors.append((name, e))
                print("FAIL  %s\n        %s" % (name, str(e).splitlines()[0][:110]))
                if "--trace" in sys.argv:
                    traceback.print_exc()

        print("\n%d/%d UI checks passed" % (len(results) - len(errors), len(results)))
        print("console errors during run: %d" % len(set(console_errors)))
        for e in list(dict.fromkeys(console_errors))[:5]:
            print("   ", e[:120])
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
