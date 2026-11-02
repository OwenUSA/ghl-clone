# Quo (formerly OpenPhone) API reference for Owen AI

Built 2026-11-02. Sources: the official docs at https://www.quo.com/docs (index `https://www.quo.com/docs/llms.txt`),
the v1 OpenAPI spec (`https://openphone-public-api-prod.s3.us-west-2.amazonaws.com/public/openphone-public-api-v1-prod.json`),
and a READ-ONLY live probe of Dream Team Roofing's workspace on 2026-11-02 (~22 GETs, nothing written).
Owen AI's client is `backend/app/owen_ai/quo.py`: **GET only, only the paths in its `READS`**.

## Markers

| Marker | Meaning |
|---|---|
| **V** | Verified live against this account on 2026-11-02. Wins over the docs. |
| **D** | Official docs / OpenAPI only; not exercised live. |
| **N** | Not in `quo.py` `READS` — Owen AI's client will refuse it today. |
| **!** | A write. Owen AI must NEVER call it (the client refuses any non-GET anyway). |

---

## 0. Basics

| Item | Value | Mk |
|---|---|---|
| Base URL | `https://api.openphone.com/v1` (Quo rebranded; the API host is still openphone.com) | V |
| Auth | header `Authorization: <key>` — the raw key, **no `Bearer`**. A key has FULL access with its creator's privileges (owner/admin) — there are no read-only keys, so read-only is enforced only by our client | V / D |
| Key on servers | owen-main mirror: `OPENPHONE_API_KEY` in `/opt/santiagoproperties/owen-main/.env.prod`; Owen AI: `OWEN_AI_QUO_API_KEY` (same workspace key). Never print it | V |
| Rate limit | **10 requests/second per key**; over it → `429`. Live headers: `ratelimit: "per-second"; r=<remaining>; t=1` and `ratelimit-policy: "per-second"; q=10; w=1`. No `Retry-After` seen. `quo.py` backs off 2/4/8/16 s on 429 | V |
| Errors | JSON `{"message", "code", "errors":[{"path","message","schema"}]}`. E.g. missing participants → `400 {"message":"/participants: must have required property","code":"0100400"}`; bad phone → `400 "/participants/0: must match pattern \"^\\+[1-9]\\d{1,14}$\""` | V |
| Envelope | Single object: `{"data": {...}}`. Lists: `{"data": [...], "nextPageToken": str\|null, "totalItems": int}` (phone-numbers, call-recordings, contact-custom-fields: `{"data": [...]}` only) | V |
| Pagination | `maxResults` (1–100, default 10; **contacts max 50**; required on calls/messages/conversations/contacts) + `pageToken` = previous `nextPageToken`. Stop when `nextPageToken` is null | V |
| `totalItems` | **Unreliable** (docs warn). Live: conversations returned `totalItems: 5` with `maxResults=5` and a next page. Never use it as a count — page through | V |
| Timestamps | ISO 8601 UTC with ms (`2026-11-02T18:30:05.731Z`). Filters accept offsets (`2026-11-02T00:00:00-05:00` worked); URL-encode `+` / `:` | V |
| Phone format | E.164 everywhere (`+1XXXXXXXXXX`); the pattern is enforced on message participants | V |
| IDs | phone number `PN…`, user `US…`, call `AC…`, conversation `CN…`, message ids also look like `AC…` in docs | V/D |
| Plan gate | Call summaries / transcripts only on Business & Scale plans — **this account has them** (both returned `completed`) | V |
| API versions | Docs also describe a dated `2026-03-30` API (new paths: `/calls/{id}/recordings`, `/calls/{id}/transcripts`, contact notes, organization…). Owen AI uses **v1 only**; do not mix paths | D |

### Query-param encoding (array params)

- **`participants`** (calls, messages): the spec says repeat the key WITHOUT brackets: `participants=%2B15551234567`.
  **Live, both `participants=` and `participants[]=` were accepted** (calls: same 1 result either way; messages:
  `participants[]=` is parsed as an array — the validation error names `/participants/0`). Use the no-bracket
  form. httpx `params={"participants": ["+1…"]}` produces exactly that.
- **calls: exactly ONE participant** (`maxItems: 1`, 1:1 only). **messages: up to 10** (several = a group thread).
- **`phoneNumbers`** (conversations): repeat the key; each value a `PN…` id or an E.164 number. `phoneNumbers=PNRxH5G3uI` worked.
- Always URL-encode `+` as `%2B` (a raw `+` decodes as a space and the filter fails).

---

## 1. The account (live, 2026-11-02)

**Phone numbers visible to the key — just ONE:**

| id | number | name | users on it |
|---|---|---|---|
| `PNRxH5G3uI` | `+19549147244` ((954) 914-7244) | Business development | Owen Buzaglo (owner) |

**Users — just ONE:** `US5MlHABW5` Owen Buzaglo, role `owner` (`totalItems: 1`).

So "across all our Quo numbers" is currently one number. Still list `/phone-numbers` first in any recipe
in case numbers are added. Not Quo: the CRM line +1 954-775-8492, the AI line +1 786-920-0331 (BulkVS via
owen-main) and Zuper Connect (954) 420-7373 — their history is NOT in this API.

Observed: today's 23 active conversations were all call activity; 4 older 1:1 conversations had **0 messages**
— texting on this number is rare. An empty `data: []` is a normal answer.

Contacts: ≥50 exist (paged), `source` null or `"other"`, no custom fields defined (`/contact-custom-fields` → `[]`).

---

## 2. Endpoints

### GET /phone-numbers — V
- Params: `userId` (optional, `US…`). No pagination.
- `data[]`: `id`, `number` (E.164), `formattedNumber`, `name`, `groupId`, `forward`, `symbol`, `portingStatus`,
  `restrictions.{calling,messaging}.{US,CA,Intl}` (`restricted|unrestricted`), `users[]` (`id, email, firstName,
  lastName, role, groupId`), `createdAt`, `updatedAt`.
- Gotcha: `users[].email` is staff PII — do not repeat it.

### GET /phone-numbers/{phoneNumberId} — D, **N**
Same object as one list item. Not in READS (harmless to add; the list covers it).

### GET /users, GET /users/{userId} — V (list) / D (by id)
- List params: `maxResults` (optional here), `pageToken`. Item: `id, firstName, lastName, email, role
  (owner|admin|member), pictureUrl, createdAt, updatedAt`.

### GET /contacts — V
- Params: `maxResults` (**required, 1–50**), `pageToken`, `externalIds` (array, repeat key), `sources` (array).
- **No search by phone or name.** To find a contact by number you must page all contacts and match
  `defaultFields.phoneNumbers[].value`. Prefer conversations/calls/messages (which take the number directly).
- Item: `id, externalId, source, sourceUrl, createdAt, updatedAt, createdByUserId,
  defaultFields{firstName,lastName,company,role,emails[{name,value,id}],phoneNumbers[{name,value,id}]},
  customFields[{name,key,id,(value)}]`.
- Gotcha: Quo contacts are Quo's address book only — a caller need not be a contact.

### GET /contacts/{id} — D
`{"data": <contact>}`.

### GET /contact-custom-fields — V
`data[]: {name, key, type}`, type ∈ `address|boolean|date|multi-select|number|string|url`. Live: **empty** (none defined).
Fields are created only in the Quo app; match a contact's `customFields[].key` to these.

### GET /conversations — V
- Params (all optional except maxResults): `phoneNumbers` (array: `PN…` or E.164 of OUR number), `userId`,
  `createdAfter/Before`, `updatedAfter/Before`, `excludeInactive` (bool), `maxResults` (1–100), `pageToken`.
  `phoneNumber` (singular) is deprecated.
- Sorted newest activity first. Item: `id (CN…)`, `phoneNumberId`, `participants[]` (the OTHER side, E.164,
  excludes our number), `name` (null in practice), `lastActivityAt`, `lastActivityId` (id of the last call or
  message), `assignedTo`, `createdAt`, `updatedAt`, `deletedAt`, `mutedUntil`, `snoozedUntil`.
- **There is no filter by participant.** This endpoint is the index: it tells you which numbers had activity
  when; then call /calls or /messages with that participant.
- Gotcha: `updatedAfter=<start of today NY>` returned 23 conversations, i.e. it works as "had activity today".

### GET /calls — V
- **Required: `phoneNumberId` (PN…), `participants` (exactly 1, E.164), `maxResults` (1–100).**
  Optional: `userId`, `createdAfter`, `createdBefore`, `pageToken`. `since` is deprecated AND buggy (acts as createdBefore) — never use.
- Missing participants → 400. There is **no "all calls" listing** — go through conversations.
- Item: `id (AC…)`, `phoneNumberId`, `userId`, `direction` (`incoming|outgoing`), `status`
  (`queued|initiated|ringing|in-progress|completed|busy|failed|no-answer|canceled|missed|answered|forwarded|abandoned`),
  `duration` (s), `createdAt`, `answeredAt`, `answeredBy` (US… or null), `completedAt`, `initiatedBy`,
  `callRoute` (live: `"phone-number"`), `forwardedFrom`, `forwardedTo`, `aiHandled` (null, or set when Sona AI took it),
  `participants[]` (**includes our number** — 2 entries live), `updatedAt`.
- **Missed call** = `direction == "incoming"` and (`status` in `missed|no-answer|abandoned` or `answeredAt == null`).
  Check `answeredAt` too; a call forwarded/answered elsewhere can read differently.

### GET /calls/{callId} — V
`{"data": <call>}`, same fields as above.

### GET /call-summaries/{callId} — V
- `data: {callId, status (absent|in-progress|completed|failed), summary: string[]|null, nextSteps: string[]|null, jobs[]|null}`.
- Live: `completed`, `summary` = array of 1 sentence/paragraph, `nextSteps` = []. `jobs` = Sona AI job results.
- Gotcha: `absent` / `in-progress` right after the call — try again later; very short calls may never get one.

### GET /call-transcripts/{id} — V  (id = the call id `AC…`)
- `data: {callId, createdAt, duration (s, float), status, dialogue[]: {content, start, end, identifier, userId}}`.
- `identifier` = the speaker's phone number (E.164) — for our side compare to `+19549147244`; `userId` set when a
  Quo user spoke. Live: 9 segments for a 17 s call.
- Same `absent/in-progress` caveat as summaries.

### GET /call-recordings/{callId} — V (live), **N**
- `data[]: {id, url, type, status, startTime, duration}`, oldest segment first. Live `url` host `share.quo.com`.
- Not in READS. Add `^/call-recordings/[A-Za-z0-9_-]+$` if the assistant should give a recording link.
  The URL is a shareable link to customer audio — return it to staff only, never paste it to a customer.

### GET /call-voicemails/{callId} — D, **N**
- `data: {id, status (completed|in-progress), duration, transcript, recordingUrl}`; fields null while processing.
- Most useful read missing from READS for missed calls ("did they leave a voicemail, what did they say").

### GET /messages — V (shape of empty result + encoding) / D (item fields)
- **Required: `phoneNumberId`, `participants` (1–10, E.164, pattern-checked), `maxResults` (1–100).**
  Optional `userId`, `createdAfter`, `createdBefore`, `pageToken`; `since` deprecated.
- Item: `id`, `conversationId`, `phoneNumberId`, `direction (incoming|outgoing)`, `from` (E.164 usually),
  `to[]`, `text`, `media[]{url,type}`, `status (queued|sent|delivered|undelivered|received)`, `userId`, `createdAt`, `updatedAt`.
- Chronological. Several participants = the GROUP thread, not "any of these".
- Live: 5 different 1:1 participants all returned `200 {"data": []}` — the number barely texts. Not an error.

### GET /messages/{id} — D
`{"data": <message>}`.

### Webhooks — D, not for Owen AI
`GET /webhooks`, `GET /webhooks/{id}` exist (and `POST /webhooks/{messages|calls|call-summaries|call-transcripts}`,
`DELETE /webhooks/{id}` **!**). owen-main's mirror receives Quo events; Owen AI neither reads nor registers webhooks.

### Writes that exist — **! never call**
`POST /messages` (**sends a real SMS from our Quo number**), `POST/PATCH/DELETE /contacts…`,
`POST /conversations/{id}/mark-as-read|done|open`, all `/tasks` writes, webhook create/delete.
Owen AI is read-only on Quo; `quo.py` refuses every non-GET before a connection exists. If someone asks
Owen AI to text a customer from Quo: refuse and point them to the CRM composer.

---

## 3. Recipes

All examples: `PN = PNRxH5G3uI`, `X = customer number in E.164` (normalize first: strip non-digits, 10 digits → `+1` prefix).

### A. Every call and text with a customer number, across all our Quo numbers
1. `GET /phone-numbers` → list of `PN` ids (today: one).
2. For each PN: `GET /calls?phoneNumberId=PN&participants=%2B1…&maxResults=100` and follow `nextPageToken`.
3. For each PN: `GET /messages?phoneNumberId=PN&participants=%2B1…&maxResults=100`, paginate.
4. Merge by `createdAt`. Optional window: `createdAfter=…`. Cost: 2 requests per number per page — well under 10/s.
(Optional sanity: `GET /conversations?phoneNumbers=PN&maxResults=100` and look for `participants == [X]`, but there
is no participant filter so this means paging everything — skip unless needed.)

### B. What was said on a call
1. Have the call id (`AC…`) from recipe A or a conversation's `lastActivityId`.
2. `GET /call-summaries/{AC}` → `summary[]`, `nextSteps[]` (short answer).
3. `GET /call-transcripts/{AC}` → `dialogue[]`; label each segment "us" if `identifier == +19549147244` or `userId` set, else "customer".
4. Status `absent|in-progress` → say it is not ready yet; `failed` → none exists. (Recording/voicemail: needs READS additions, §2.)

### C. Today's missed calls
1. `start = today 00:00 America/New_York` as ISO with offset (e.g. `2026-11-02T00:00:00-05:00`, `-04:00` in summer), URL-encoded.
2. `GET /conversations?phoneNumbers=PN&updatedAfter=<start>&maxResults=100` (page) → participants active today.
3. For each 1:1 conversation: `GET /calls?phoneNumberId=PN&participants=<p>&createdAfter=<start>&maxResults=100`.
4. Keep `direction=="incoming"` and (`status ∈ {missed,no-answer,abandoned}` or `answeredAt==null`).
5. Then check whether we called/texted back later (outgoing call or message with a later `createdAt`) — report only the unanswered ones.
Budget: 1 + N requests (23 conversations today → ~24 requests); throttle to ≤8/s.

### D. Find a message thread
- Know the number: `GET /messages?phoneNumberId=PN&participants=<X>&maxResults=50`, paginate backwards with `pageToken`
  (or narrow with `createdAfter/Before`). Group thread: repeat `participants` for each member.
- Know only a name: Quo has no contact search — page `GET /contacts?maxResults=50` and match
  `defaultFields.firstName/lastName`, take `phoneNumbers[].value`, then as above. (The CRM is usually the better place to
  resolve a name to a number.)
- Know a conversation id (`CN…`): there is no GET-by-id; use its `participants` + `phoneNumberId` from the list.

---

## 4. Gotchas summary
- No participant filter on conversations; calls/messages REQUIRE one participant set → always two-step.
- `totalItems` lies; `since` is buggy; contacts `maxResults` caps at 50 (others 100).
- Call `participants` includes our number; conversation `participants` excludes it.
- Summaries/transcripts lag the call; `absent` is common for very short calls.
- One key = full workspace access, no read-only keys — the GET-only guard in `quo.py` is the only protection.
- Quo history covers only Quo numbers (today +19549147244), not the CRM/BulkVS or Zuper Connect lines.
