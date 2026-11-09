---
name: proposal-draft
description: Draft a Good/Better/Best roof leak repair proposal for a job from its inspection photos. The scope comes from the photos; the prices are Owen's, or an estimate from past proposals with the basis shown. It is stored as a PENDING draft for Owen to approve on the Proposals tab, and never sent. Use for "draft the proposal for job 728", "build the G/B/B for #741", "make the proposal", "prepare the options", "haz la propuesta del trabajo 728", "prepara el presupuesto", "borrador de propuesta", "arma las opciones Good/Better/Best", "cotiza el trabajo".
---

# Proposal draft

Read `knowledge/proposal-drafting-guide.md` fully first. It covers templates, tiers, wording, the price block, the
estimator, photo → scope and the checklist. Also apply the 10 rules in `knowledge/inspection-report-guide.md` §1.
Diagnosis help: `knowledge/roof-leak-playbook.md`.

**Prices Owen gives in the chat always win.** If he gave none, estimate them (guide §4) and show the basis.

1. **Job facts:** `mcp__owen__job_dossier`. Note:
   - board and stage, AHS or retail, and the **AHS authorized credit** (note "AHS approved #… $…" / AUTHO, field
     `AHS authorized ($)`, `AHS-LEAK-REPAIR` line). If the credit is unknown on an AHS job, ask Owen; never assume
     $1,125.
   - room names and leak areas from the notes and chats;
   - **existing proposals**: an auto-created template DRAFT (the tool fills it), or one already sent or approved
     (stop and ask before drafting another; `allow_second: true` only when Owen asks for a reissue).
2. **Photos:** `mcp__owen__job_photos` (`which="inspection"`), then **Read each `path`** you rely on. Log for each
   photo: number, time, what it shows. Drop duplicates, checks, other houses and blurry frames. You need an
   overview, a leak close-up and the interior stain. If there is no roof photo, stop and tell Owen
   "needs inspection photos" (guide §5.1).
3. **Template:** always the **Short Proposal** template (`short`; the tool forces it). Still identify the roof
   material at the leak: write the scope for that material, and if it is not tile, warn Owen in `note` that the
   BEST product is named "Tile Leak Repair".
4. **Findings:** list the distinct defects with photo refs, the number of leak areas, the stories, and rot seen or
   not seen. Use the inspector's own marks.
5. **Scopes per tier** (guide §2, §5.2): GOOD = targeted fix of each leak area. BETTER = everything in GOOD plus the
   expanded area, plywood and self-adhered underlayment, plus the BETTER findings. BEST = everything in BETTER plus
   the 2x area, double underlayment, tune-up and photo documentation. Use 5-8 steps in Luis's canonical wording. Put
   the warranty (1 month on every option by default, unless Owen says otherwise) and the payment terms in each option. Every finding goes either into a tier or
   into `notes_for_owen` as "seen, not priced".
6. **Prices:** use Owen's prices, or the estimate from guide §4: base row + adders → round ($25 / $50) → check the
   ladder. Then build the price block: P = price, M = P + C, R = the tier anchor, S = R − P. Deposit = 50%.
7. **Spec:** `{job_uid, template, title, options: [{name, option_description, is_recommended, price, deposit,
   warranty, scope_html}], allow_second?, notes_for_owen}`. Title = `Active Leak Repair - <street address>`.
   `notes_for_owen` must contain the price basis (Owen's, or the estimate row + adders + comparable DTR-N G/B/B),
   the source of the AHS credit, damage that is seen but not priced, missing photos, and template conflicts. If the
   tool's schema differs from this, follow the tool.
8. Run the guide's **§9 checklist**, then call `mcp__owen__propose_proposal_draft(spec)`. If it returns validation
   errors, fix them and call it again.
9. **Reply to Owen:**
   - one line: "Draft for #N is **waiting for your approval on the Proposals tab**";
   - a table, one row per tier: Option | What it covers (≤ 12 words) | Customer pays | Warranty;
   - **Price basis**: "your prices", or "estimate: <row> + <adders>; comparables DTR-129 $600/$1,400/$3,000, …";
     plus the AHS credit and where it came from;
   - warnings: damage not priced, missing photos, an auto-draft template mismatch, odd credit, any assumption;
   - offer edits ("change a price, drop a tier, move hip mortar to BEST").

**Never say the proposal was created, sent, or is in Zuper.** It is a pending draft. Only Owen's approval creates it
in Zuper, and then only as a Zuper DRAFT that nobody sends. Edits later: change the spec and call the tool again.

## Step pictures (optional, only on a created proposal)

`mcp__owen__proposal_step_pictures {proposal: <draft id or DTR-N>, action: generate | attach | discard}` draws one
step-by-step banner per option from the proposal's own scope and, when Owen says so, puts them on the Zuper draft.

- **Generate only when Owen asks** ("add the step pictures to DTR-160"). Never offer to do it on your own.
- **Only on a proposal that is created** (approved and in Zuper as a draft). A pending draft is refused — tell Owen
  to approve it first.
- **Mention the cost** before or with the result: about $0.17 per option, ~$0.51 for Good/Better/Best (OpenAI).
- After `generate`, **show Owen the preview links exactly as the tool returned them** (they render as pictures in
  the chat) and ask whether to put them on.
- **Attach only when he says so** — after he has seen them, or when he explicitly asked to generate AND put them on
  in one go ("make the step pictures and put them on DTR-160"). `discard` if he does not want them.
- Attach changes only the option pictures; prices and wording stay, it stays a DRAFT, nothing is sent. Never say it
  was sent. If a step fails, repeat the tool's sentence; do not retry on your own.
