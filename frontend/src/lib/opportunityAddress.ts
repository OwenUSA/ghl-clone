/**
 * The job's address on an opportunity (2026-09-14) — the pure rules the modal and the
 * board card draw from. Import-free, so `test_opportunity_address_ui.py` executes this
 * very file under node.
 *
 * The card's four columns mirror the contact's (`address_street`, `address_city`,
 * `address_state`, `address_postal_code`). Nothing here ever writes the contact's
 * address onto a card by itself: `withContactAddress` is called from one click handler
 * and nowhere else.
 */

export const ADDRESS_FIELDS = [
  // key, label, placeholder, max length (the columns, as the API enforces them)
  ['address_street', 'Street address', 'Enter street address', 255],
  ['address_city', 'City', 'Enter city', 120],
  ['address_state', 'State', 'Enter state', 80],
  ['address_postal_code', 'Zip code', 'Enter zip code', 20],
] as const

export type AddressKey = (typeof ADDRESS_FIELDS)[number][0]

/** What the API sends: any of the four, each possibly null or absent. */
export type AddressSource = Partial<Record<AddressKey, string | null>> | null | undefined

/** The modal's editable copy: always four strings, '' for none. */
export type AddressForm = Record<AddressKey, string>

const KEYS = ADDRESS_FIELDS.map(([k]) => k)
const t = (v?: string | null) => (v ?? '').trim()

export function addressForm(src: AddressSource): AddressForm {
  const out = {} as AddressForm
  for (const k of KEYS) out[k] = src?.[k] ?? ''
  return out
}

export function hasAddress(src: AddressSource): boolean {
  return KEYS.some((k) => t(src?.[k]) !== '')
}

/** The board card's one grey line: "street, city". Null when neither is on the card. */
export function cardAddressLine(src: AddressSource): string | null {
  return [t(src?.address_street), t(src?.address_city)].filter(Boolean).join(', ') || null
}

/** Only the fields that changed, blank as null — the PATCH body's address part. */
export function addressChanges(saved: AddressSource,
                               form: AddressForm): Partial<Record<AddressKey, string | null>> {
  const body: Partial<Record<AddressKey, string | null>> = {}
  for (const k of KEYS) {
    if (t(form[k]) !== t(saved?.[k])) body[k] = t(form[k]) || null
  }
  return body
}

/**
 * The contact's address to show greyed while the card has none — or null when the
 * card has one (typed or saved), or the contact has none to offer.
 */
export function contactFallback(form: AddressForm, contact: AddressSource): AddressForm | null {
  if (hasAddress(form) || !hasAddress(contact)) return null
  return addressForm(contact)
}

/** "Use contact address": the form with the contact's four fields copied in. */
export function withContactAddress(form: AddressForm, contact: AddressSource): AddressForm {
  return { ...form, ...addressForm(contact) }
}
