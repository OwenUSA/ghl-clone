"""The job's address in the browser (2026-09-14): executed where it can be.

**Executed under node.** `lib/opportunityAddress.ts` (the card line, the modal's
fallback and its PATCH body) and `lib/accountTime.ts`'s `meetingDefaultAddress` are
import-free, so the shipped code itself runs here.

**Asserted against source.** The modal and the card are React, which this project has
no runner for; each assertion names the failure it would catch.
"""
import json

from tests.test_opportunity_card_ui import _read, node, run_js

PALM = {"address_street": "12 Palm Ave", "address_city": "Bradenton",
        "address_state": "FL", "address_postal_code": "34205"}
OFFICE = {"address_street": "900 Office Park Dr", "address_city": "Tampa",
          "address_state": "FL", "address_postal_code": "33602"}
NONE = {"address_street": None, "address_city": None, "address_state": None,
        "address_postal_code": None}
js = json.dumps


# ---------------- executed: the card line ----------------

@node
def test_the_card_line_is_street_and_city_and_only_with_an_address():
    got = run_js("opportunityAddress.ts", """
        out([
          m.cardAddressLine(%s),
          m.cardAddressLine({ address_street: '  ', address_city: 'Tampa' }),
          m.cardAddressLine({ address_state: 'FL', address_postal_code: '34205' }),
          m.cardAddressLine(%s),
          m.cardAddressLine({}),
          m.cardAddressLine(null),
        ])
    """ % (js(PALM), js(NONE)))
    assert got == ["12 Palm Ave, Bradenton", "Tampa", None, None, None, None]


# ---------------- executed: the modal's fallback ----------------

@node
def test_the_contact_address_is_only_offered_while_the_card_has_none():
    got = run_js("opportunityAddress.ts", """
        const empty = m.addressForm(%s)
        out({
          empty,
          offered: m.contactFallback(empty, %s),
          cardHasOne: m.contactFallback(m.addressForm(%s), %s),
          typing: m.contactFallback({ ...empty, address_city: 'X' }, %s),
          contactHasNone: m.contactFallback(empty, %s),
        })
    """ % (js(NONE), js(OFFICE), js(PALM), js(OFFICE), js(OFFICE), js(NONE)))
    assert got["empty"] == dict.fromkeys(PALM, "")
    assert got["offered"] == OFFICE
    assert got["cardHasOne"] is None
    assert got["typing"] is None
    assert got["contactHasNone"] is None


@node
def test_the_contact_address_is_copied_only_when_use_is_clicked():
    """Seeding the form from a card with no address sends NOTHING — merely showing the
    greyed fallback must never put the contact's address on the card. The click's
    function is what copies it, and then Update sends all four."""
    got = run_js("opportunityAddress.ts", """
        const seeded = m.addressForm(%s)
        const clicked = m.withContactAddress(seeded, %s)
        out({
          untouched: m.addressChanges(%s, seeded),
          afterClick: m.addressChanges(%s, clicked),
        })
    """ % (js(NONE), js(OFFICE), js(NONE), js(NONE)))
    assert got["untouched"] == {}
    assert got["afterClick"] == OFFICE


@node
def test_the_patch_body_sends_only_changed_fields_and_blank_as_null():
    got = run_js("opportunityAddress.ts", """
        const form = { ...m.addressForm(%s), address_street: ' 14 Palm Ave ',
                       address_postal_code: '   ' }
        out(m.addressChanges(%s, form))
    """ % (js(PALM), js(PALM)))
    assert got == {"address_street": "14 Palm Ave", "address_postal_code": None}


def test_the_modal_seeds_from_the_card_and_copies_only_on_click():
    source = _read("components", "OpportunityDetail.tsx")
    # The form is seeded from the OPPORTUNITY, never from the contact.
    assert "address: addressForm(o)," in source
    # Exactly one caller of the copy, and it is a button's onClick labelled for it.
    assert source.count("withContactAddress(") == 1
    click = source.index("withContactAddress(")
    assert "onClick={() => set('address', withContactAddress(" in source
    assert source.index("Use contact address", click) - click < 200
    # Update sends what changed in the address along with everything else.
    assert "Object.assign(body, addressChanges(o, f.address))" in source


def test_the_address_group_honours_hide_empty_fields():
    source = _read("components", "OpportunityDetail.tsx")
    assert "(!hideEmpty || ADDRESS_FIELDS.some(([k]) => form.address[k].trim()))" in source
    assert "(!hideEmpty || form.address[key].trim())" in source
    # The greyed contact value is a placeholder, never the input's value.
    assert "placeholder={fallback?.[key] || placeholder}" in source
    assert "value={form.address[key]}" in source


def test_the_card_draws_one_grey_line_only_with_an_address():
    source = _read("components", "OpportunityCard.tsx")
    assert "const address = cardAddressLine(o)" in source
    assert "{layout !== 'Unlabeled' && address && (" in source
    line = source[source.index("data-card-address") - 200:source.index("data-card-address") + 200]
    assert 'className="truncate"' in line, "a long street must be cut, not wrap the card"
    # Drawn directly under the Value row.
    assert source.index("{money(o.value_cents)}") < source.index("data-card-address")
    assert source.index("data-card-address") < source.index("o.business_name && (")


# ---------------- executed: Calendar default ----------------

@node
def test_the_booking_default_location_prefers_the_cards_address():
    got = run_js("accountTime.ts", """
        out([
          m.meetingDefaultAddress(%s, %s),
          m.meetingDefaultAddress(%s, %s),
          m.meetingDefaultAddress(undefined, %s),
          m.meetingDefaultAddress(%s, null),
        ])
    """ % (js(PALM), js(OFFICE), js(NONE), js(OFFICE), js(OFFICE), js(NONE)))
    assert got == ["12 Palm Ave, Bradenton, FL 34205", "900 Office Park Dr, Tampa, FL 33602",
                   "900 Office Park Dr, Tampa, FL 33602", None]


def test_the_booking_dialog_shows_what_the_server_will_store():
    dialog = _read("components", "NewAppointmentDialog.tsx")
    assert "meetingDefaultAddress(lockedOpportunity, picked.data)" in dialog
    modal = _read("components", "OpportunityDetail.tsx")
    locked = modal[modal.index("lockedOpportunity={{"):]
    assert "address_street: o.address_street" in locked[:300]
