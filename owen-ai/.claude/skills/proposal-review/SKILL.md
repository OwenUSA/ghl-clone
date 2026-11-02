---
name: proposal-review
description: Read and compare Zuper proposals — what was quoted to a customer, which option they took, proposals waiting for an answer, and whether a proposal follows the DTR conventions. Use for "what did we quote", "proposals pending", "check this proposal".
---

# Proposal review

Read knowledge/zuper/live-account.md §1–2 first (templates, the description convention).

- **What was quoted:** `GET /estimate/{uid}` — per option: name, price, deposit, line items
  (product code + description). Say which option the customer approved, if any.
- **Waiting:** proposals in "waiting on customer" status, oldest first, with age in days, job #,
  amount range (Good–Best).
- **Convention check** (for a draft or recent proposal): template used matches the job type?
  each option = one DTR package line? description has price block (retail, AHS member price, AHS
  credit, customer pays, savings), 5–8 scope steps, warranty by tier, 50/50 terms? deposit = 50%?
  title in a known format? List each deviation.

Never send, approve or change a proposal. Phase 1 is read-only.
