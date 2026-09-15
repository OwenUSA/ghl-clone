"""GoHighLevel's Book appointment modal and the calendar grid, at the source.

The behaviour is asserted at the API (test_book_appointment.py) and the time
arithmetic is executed under node (test_account_time.py). What is pinned here is
what the owner decided the SCREEN must and must not draw (screenshot 29,
2026-09-13), so a later edit has to argue with a test to put a removed control back.
"""
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def _jsx(source: str) -> str:
    """The component body, without the header comment that describes the layout."""
    return source.split("export function NewAppointmentDialog(", 1)[1]


def test_the_modal_is_book_appointment_with_its_two_tabs():
    body = _jsx(_read("components", "NewAppointmentDialog.tsx"))
    assert "'Book appointment'" in body
    assert "['appointment', 'Appointment'], ['blocked', 'Blocked off time']" in body
    assert 'role="tablist"' in body and "aria-selected={tab === key}" in body


def test_the_left_column_is_what_screenshot_29_draws():
    body = _jsx(_read("components", "NewAppointmentDialog.tsx"))
    order = ["<Label>Calendar</Label>", "<Label>Appointment title</Label>",
             "Add description", "{dateAndTime}", "<Label>Meeting location</Label>"]
    at = [body.index(x) for x in order]
    assert at == sorted(at), "the left column is out of GoHighLevel's order"
    assert "initialTitle = '{{contact.name}}'" in body, (
        "the title is not prefilled with the contact.name template")
    assert "Showing slots in this timezone: (Account timezone)" in body
    assert "zoneLabel(starts, z)" in body, "the timezone label is not computed"
    assert "GMT-04:00" not in body and "GMT-05:00" not in body, "an offset is hardcoded"
    assert '<DateTimeField label="Start time"' in body
    assert '<DateTimeField label="End time"' in body
    assert "['calendar_default', 'Calendar default'], ['custom', 'Custom']" in body


def test_the_owners_removals_are_not_drawn():
    """No Default/Custom toggle, no Recurring event, no Opportunity and no
    Assigned to field — the owner's "match GoHighLevel exactly"."""
    body = _jsx(_read("components", "NewAppointmentDialog.tsx"))
    for gone in ("Recurring", ">Default<", "'Default'", "Assigned to", "listUsers",
                 "Not linked to a deal", "listOpenOpportunities", 'label="Opportunity"'):
        assert gone not in body, "%r is drawn in the Book appointment modal" % gone
    # The deal is still SENT when the modal is opened from one.
    assert "opportunity_id: lockedOpportunity?.id ?? null" in body


def test_the_right_column_selects_a_contact_and_takes_internal_notes():
    body = _jsx(_read("components", "NewAppointmentDialog.tsx"))
    assert "<Label required>Select Contact</Label>" in body
    assert 'variant="search" placeholder="Search by name, email, or phone"' in body
    assert "if (!contact) return 'Select a contact.'" in body, "the contact is not required"
    assert "<Label>Internal notes</Label>" in body and "Add internal note" in body


def test_the_footer_has_status_cancel_and_book():
    body = _jsx(_read("components", "NewAppointmentDialog.tsx"))
    footer = body.split("Status :", 1)[1]
    assert "Cancel" in footer and "'Book appointment'" in footer


def test_the_time_picker_is_the_account_zone_and_the_whole_day():
    picker = _read("components", "DateTimeField.tsx")
    assert "const SLOTS = timeSlots()" in picker and "SLOTS.map" in picker
    assert "instantOf(withMinutes(wall, s.minutes), timeZone)" in picker
    assert "pickerLabel(value, timeZone)" in picker
    # No browser-local Date arithmetic leaks in.
    for local in (".getHours()", ".setHours(", "toLocaleTimeString", "toLocaleString"):
        assert local not in picker, local


def test_booking_over_blocked_time_is_confirmed_in_place():
    body = _jsx(_read("components", "NewAppointmentDialog.tsx"))
    assert "e.status === 409) setOverlap(e.message)" in body
    anyway = body.rsplit("Book anyway", 1)[0].rsplit("<button", 1)[1]
    assert "create.mutate(true)" in anyway, "Book anyway does not confirm the booking"
    assert "allow_blocked_time: allow" in body
    panel = _read("components", "AppointmentDetailDialog.tsx")
    assert "allow_blocked_time: true" in panel and "Save anyway" in panel


def test_the_blocked_off_time_tab_creates_edits_and_deletes():
    body = _jsx(_read("components", "NewAppointmentDialog.tsx"))
    for call in ("createBlockedTime(body)", "patchBlockedTime(blockedTimeId!, body)",
                 "deleteBlockedTime(blockedTimeId!)"):
        assert call in body, call
    assert "removeBlock.mutate()" in body and "setConfirmDelete(true)" in body, (
        "a block is deleted without being asked")


def test_the_calendar_grid_covers_the_whole_day_and_opens_at_7_am():
    """The owner, 2026-09-15: open on 7 AM to 7 PM like Workiz (it was 5 AM). The hour
    height that makes that fit is executed in test_calendar_layout.py."""
    page = _read("pages", "CalendarsPage.tsx")
    assert "const HOURS = Array.from({ length: 24 }, (_, i) => i)" in page, (
        "the hour grid no longer runs to 11 PM")
    grid = _read("lib", "calendarGrid.ts")
    assert "export const FIRST_VISIBLE_HOUR = 7" in grid
    assert "hourPane.current.scrollTop = FIRST_VISIBLE_HOUR * hourPx" in page
    assert "setHourPx(hourPxFor(el.clientHeight))" in page
    assert "<div ref={hourPane}" in page


def test_blocked_time_is_drawn_on_the_grid_and_distinct_from_appointments():
    page = _read("pages", "CalendarsPage.tsx")
    assert "listBlockedTimes(" in page
    week = page.split("{layouts[i].map((p) => {", 1)[1].split("})}", 1)[0]
    assert "<BlockedBlock" in week
    block = page.split("function BlockedBlock(", 1)[1].split("\n}\n", 1)[0]
    month = page.split("{blocked[i].map((b) => (", 1)[1].split("\n                ))}", 1)[0]
    for drawn, where in ((block, "Day/Week"), (month, "Month")):
        assert "BLOCKED_FILL" in drawn, "%s draws a block like a booking" % where
        assert "a.color" not in drawn, "%s paints a block in a calendar colour" % where
        assert "onDoubleClick={(e" in drawn or "onDoubleClick={(ev" in drawn, (
            "double-clicking a block in %s books the slot under it" % where)
    # "View by type: Appointments" hides them.
    assert "enabled: kind !== 'appointments'" in page
    assert "blockedTimeId={openBlocked}" in page


def test_the_deals_tab_still_books_through_this_dialog_and_shows_the_location():
    detail = _read("components", "OpportunityDetail.tsx")
    assert "<NewAppointmentDialog" in detail and "lockedOpportunity={{ id: o.id" in detail
    tab = _read("components", "opportunity", "AppointmentTab.tsx")
    assert "a.location" in tab


def test_the_detail_panel_hides_internal_notes_from_a_tech_and_reads_eastern():
    panel = _read("components", "AppointmentDetailDialog.tsx")
    assert "{a.notes_visible && (" in panel
    assert "from '../lib/accountTime'" in panel
    assert "from '../lib/calendarGrid'" not in panel, (
        "the panel still reads times in the browser's zone")
