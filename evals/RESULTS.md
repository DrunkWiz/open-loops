# Evaluation results

- **Model:** `nvidia/nemotron-3-super-120b-a12b` on Nebius Token Factory (reasoning on)
- **Run:** 2026-09-23, 3.0 min
- **Score:** **27/27 checks passed (100%)**
- **Source grounding:** 38/38 (100%) extracted items had a quote found verbatim in the document

Checks are hand-written expectations for the files in `samples/` (see `evals/cases.json`).

## Commitment extraction

**conflicting_updates_email.txt** (9 items)

- ✅ Hamid: onboarding due 2026-10-06 — *uses the revised date, not the old Oct 9*
- ✅ Jess: dark mode due 2026-10-13
- ✅ Tomás: screenshot due 2026-10-15
- ✅ Grace: customer success due 2026-09-25 — *'by Friday' resolved from a Tuesday*
- ✅ no open item about offline sync — *offline sync was cut from v3 (telling customers about it is fine)*

**family_group_chat.txt** (8 items)

- ✅ Dad: cake due 2026-10-10
- ✅ Dad: linda due 2026-10-23
- ✅ Jake: slideshow
- ✅ Grace: taco due 2026-09-22 — *'tomorrow' from Monday*
- ✅ Mom: guest list

**founder_voice_memo.txt** (7 items)

- ✅ Omar: security due 2026-10-10
- ✅ Wei: sso
- ✅ Alex: lisa due 2026-09-23 — *first-person memo = the user*
- ✅ Alex: crm

**slack_incident_thread.txt** (8 items)

- ✅ Mike: lint
- ✅ Kevin: alert
- ✅ Sofia: postmortem

**lecture_notes_messy.md** (6 items)

- ✅ someone: ps4 due 2026-10-08
- ✅ someone: topic due 2026-10-05

## Cross-document change detection

**mobile_v3_kickoff_email.txt → conflicting_updates_email.txt** (4 proposals)

- ✅ update: onboarding — *deadline moved Oct 9 → Oct 6*
- ✅ cancelled: offline sync — *offline sync cut*
- ✅ cancelled: watch — *Apple Watch moved to v3.1*
- ✅ update: screenshot — *Tomás moved Oct 13 → Oct 15*

## Grounded Q&A

- ✅ *When is the midterm?* → The midterm is now scheduled for **October 14** (it was moved from October 12).
- ✅ *Is offline sync in v3?* → No—offline sync has been removed from v3 and will be part of v3.1 instead (“Offline sync is ALSO cut from v3…It’ll go in v3.1 too.” – conflicting_updates_email.
- ✅ *Which logo did Hannah approve?* → I couldn't find that in your documents.
- ✅ *What was the root cause?* → The root cause was the new promo‑banner feature writing a cache key for every user session without setting a TTL, which filled Redis node 3’s memory and caused 
