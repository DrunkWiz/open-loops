# Evaluation results

- **Model:** `nvidia/nemotron-3-super-120b-a12b` on Nebius Token Factory (fast mode)
- **Run:** 2026-09-24, 0.7 min per run
- **Score:** **29.3/34 checks passed on average (86%)** over 3 runs (range 27–31; runs: 30, 31, 27)
- **Source grounding:** 103/110 (94%) extracted items had a quote found verbatim in the document

Checks are hand-written expectations for the files in `samples/` (see `evals/cases.json`). The details below are from the last run.

## Commitment extraction

**conflicting_updates_email.txt** (9 items)

- ✅ Hamid: onboarding due 2026-10-06 — *uses the revised date, not the old Oct 9*
- ✅ Jess: dark mode due 2026-10-13
- ✅ Tomás: screenshot due 2026-10-15
- ✅ Grace: customer success due 2026-09-25 — *'by Friday' resolved from a Tuesday*
- ✅ no open item about offline sync — *offline sync was cut from v3 (telling customers about it is fine)*

**family_group_chat.txt** (7 items)

- ✅ Dad: cake due 2026-10-10
- ❌ Dad: linda due 2026-10-23 (got 2026-09-25)
- ❌ Jake: slideshow (not found)
- ✅ Grace: taco due 2026-09-22 — *'tomorrow' from Monday*
- ✅ Mom: guest list

**founder_voice_memo.txt** (6 items)

- ✅ Omar: security due 2026-10-10
- ❌ Wei: sso (not found)
- ✅ Alex: lisa due 2026-09-23 — *first-person memo = the user*
- ✅ Alex: crm

**slack_incident_thread.txt** (8 items)

- ✅ Mike: lint
- ✅ Kevin: alert
- ✅ Sofia: postmortem

**lecture_notes_messy.md** (3 items)

- ✅ someone: ps4 due 2026-10-08
- ✅ someone: topic due 2026-10-05

**acme_followup_email.txt** (3 items)

- ✅ Priyanka: questionnaire due 2026-10-07 — *reassigned from Omar, deadline moved up*
- ✅ Lisa: order form due 2026-09-30
- ✅ no open item about sso — *SSO scoping no longer needed this week*

## Cross-document change detection

**mobile_v3_kickoff_email.txt → conflicting_updates_email.txt** (4 proposals)

- ✅ update: onboarding — *deadline moved Oct 9 → Oct 6*
- ✅ cancelled: offline sync — *offline sync cut*
- ✅ cancelled: watch — *Apple Watch moved to v3.1*
- ✅ update: screenshot — *Tomás moved Oct 13 → Oct 15*

**founder_voice_memo.txt → acme_followup_email.txt** (0 proposals)

- ❌ update: questionnaire — *questionnaire moved Oct 10 → Oct 7*
- ❌ update: questionnaire — *questionnaire reassigned Omar → Priyanka*
- ❌ cancelled: sso — *Wei's SSO scoping cancelled*
- ❌ update: case study — *case study and thank-you note sent*

## Grounded Q&A

- ✅ *When is the midterm?* → Oct 14
- ✅ *Is offline sync in v3?* → No, offline sync is cut from v3 and will move to v3.1. This is stated in Grace Liu's email dated September 22, 2026, which says: "Offline sync is ALSO cut from 
- ✅ *Which logo did Hannah approve?* → Hannah hasn't approved any logo yet; she was leaning toward option C according to Speaker 1 (the auto-transcript).
- ✅ *What was the root cause?* → The primary root cause was Redis memory exhaustion due to missing TTLs on cache keys written by the new promo-banner feature, which caused high latency and cont
