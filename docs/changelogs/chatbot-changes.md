# Chatbot change log (Tevin)

Changes to the conversation module only: `backend/resolve/conversation/**` and its tests.
Anything that touches Resolve services, providers, migrations, shared DTOs/contracts, the Voice
bridge or the frontend goes in [core-engine-changes.md](core-engine-changes.md) instead.

Branch: `tevin/chatbot-fixes` (from `main` @ `4b581b2`) for CB-001..003, merged into `main` on 2026-10-04 as `0375c4b`; `tevin/crm-integration` for CB-004..005.

## Risk levels

| Level | Meaning |
| --- | --- |
| LOW | Wording, templates, prompts, logging. No change to routing, state, consent or what is sent to Resolve. |
| MEDIUM | Changes routing, dialogue state, extraction schema or what text the customer sees for findings/offers. Needs dialogue tests and a manual chat run. |
| HIGH | Touches consent/confirmation, turn replay/idempotency, Voice decision handling, or what is sent to ResolveFacade. Needs Harry's review before merge. |

## Entry format

```
### CB-NNN — title
- Date / status: (INVESTIGATED | IN PROGRESS | DONE | REVERTED)
- Risk: LOW | MEDIUM | HIGH — why
- Files:
- What changed / why:
- Verification: (static / unit / mock / browser / live — say which)
- Commit:
- Core dependency: (CE-NNN or none)
```

---

### CB-001 — Replies stay English even when the customer writes Singlish/Sinhala

- Date / status: 2026-10-03 — DONE on branch, verified, committed `73677a4`
- Risk: MEDIUM — partly reverses the AUD-01 "never rewrite case replies" rule for **text chat only**.
  Harry should review before merge (see "What Harry should check").
- Reported symptom: customer writes Singlish ("Mata VAS charges gana poddak check karanna puluwan da?",
  "reload ekak damma eka watila nah..."); the bot understands but answers with long, fixed English text.

**Root cause (verified against the running stack and its database):**

1. Understanding worked: 32 `EXTRACTION` calls, all `OK` (`gemini-3.5-flash-lite`, `extract-v5`); the
   conversation's saved state was `language=si`, `script=LATIN`.
2. The reply rewrite never ran (0 `REPLY_REWRITE` rows ever). `ConversationService._localize` skipped it
   for any reply with a case, proposal, cards, operation IDs or a "critical" word, i.e. every case reply.
3. `locales/si.json`/`ta.json` are `UNREVIEWED_DRAFT` and in native script, so they never applied and would
   not have matched a Latin-script (Singlish) customer anyway.
4. Most of the reply is engine-owned English (`finding.text`, `review_reasons`, `proposal.consequences`),
   pasted as one paragraph (see CE-001/CE-002).

**What changed (options C + A + B):**

- **A — two-tier localization** (`service.py` `_localize`, `ports.py` `TurnDraft`, `rewrite.py`):
  - Tier 1 (unchanged rule): operation outcomes/status, consent prompts, declines, proposal errors and any
    other case reply are never freeform-rewritten. They use reviewed locale templates or English.
  - Tier 2 (new, text channel only): drafts explicitly marked `rewrite_allowed` (investigation findings +
    offer, follow-up findings, human-review/package/alternative offers, account balance) are rewritten into
    the customer's style. The English original is persisted as `source_reply_text`; cards and the
    confirmation buttons stay English and authoritative; text consent is still buttons only.
  - Stronger fact check for every rewrite: numbers/signs/decimals/IDs/links (as before) **plus** every
    `keep_exact` label (offer target, alternative targets) verbatim, and a question must stay a question.
  - Prompt `rewrite-v3`: keep negations/limits ("does not prove", "not confirmed"), never turn pending into
    done, keep paragraph breaks, Singlish style notes with two worked examples.
  - Voice: unchanged — never rewritten, one continuous reply.
- **C — conversational composition** (`service.py` `_investigation_draft`, `_paragraphs`): text replies are
  split into paragraphs (what I found / what limits the answer / what I can do next); duplicate review
  reasons that repeat a finding are dropped. Every finding and Resolve's consequences stay in the text
  (the connectivity scenario requires "does not repair the network" to be visible), so nothing was cut.
  Voice keeps a single spoken paragraph.
- **B — Singlish templates** (`templates.py`, new `locales/si-Latn.json`): locale now follows how the
  customer writes: `si` + Latin/mixed script → `si-Latn`; `ta` + Latin → `ta-Latn` (no file yet → English).
  A missing/unreviewed romanized file falls back to **English**, never to the native script the customer did
  not use. Voice always uses the native-script locale. `si-Latn.json` is a machine draft with status
  `UNREVIEWED_DRAFT`: **it is not used until a fluent reviewer corrects it and sets `REVIEWED`**, per T-04.
  When reviewed, Tier-1 replies (outcomes, declines, prompts) become deterministic Singlish and are not
  re-paraphrased by the model.

**Verification:**

- Unit (fake model): `.venv/bin/python -m pytest -q` → **461 passed, 40 skipped** (baseline 443 passed;
  18 new tests). New tests cover: Singlish case reply rewritten with English source saved; changed amount,
  dropped target label and dropped question each rejected (`FACT_CHECK`); `keep_exact` sent to the model;
  Voice never rewritten and not paragraphed; text paragraphs; decline after a rewritten offer stays
  deterministic; locale selection matrix; romanized fallback to English; reviewed Singlish templates used
  and not re-rewritten; Voice ignores romanized templates; `si-Latn` keys/placeholders match English.
- Live model, fake Resolve (contract fixtures): real Gemini extraction + rewrite on the reported messages.
  First prompt version drifted ("separate from future renewal" → wrong meaning); after `rewrite-v3` examples
  the meaning, negations, amounts, dates and labels were preserved in all three samples (1.1–1.7 s per rewrite).
- Browser + live stack (local Resolve on 8080 restarted on this branch, Vite 5173, real Gemini, local
  PostgreSQL): both reported messages answered in Singlish; `resolve.model_calls` shows `REPLY_REWRITE`
  `rewrite-v3` `OK` (1150 ms, 1675 ms). Cards/buttons English.
- Not verified: fluent human review of Singlish quality (sample slip: "SIM-LK-0001 wenata" should be
  "walata"); Tamil/Tanglish live samples; Voice (unchanged by design).

**What Harry should check:** that Tier 2 (text-only rewrite of findings/offers, card authoritative,
English persisted) is acceptable against AUD-01. Residual risk: a lexical check cannot prove a rewritten
sentence keeps every nuance (e.g. "separate from"); mitigations are the prompt rules, strict fact check,
English card + buttons, English source in storage, and Voice/outcomes excluded.

- Files: `backend/resolve/conversation/{service.py,rewrite.py,templates.py,ports.py,locales/si-Latn.json}`,
  `tests/conversation/{test_rewrite.py,test_locales.py}`.
- Commit: `73677a4` (branch `tevin/chatbot-fixes`, pushed and remote-confirmed).
- Core dependency: CE-001 (clean localization of engine text), CE-002 (internal "Reason:" note still shown,
  now also rewritten into Singlish), CE-003 (optional "show original" toggle).

### CB-002 — "Check my VAS charges" sometimes classified as a balance enquiry

- Date / status: 2026-10-03 — DONE with CB-003 (extraction prompt `extract-v6` examples: "VAS charges … check
  karanna" → NEW_COMPLAINT/VAS_DISPUTE; "VAS charges monadwada/mokadda" → ACCOUNT_ENQUIRY/SERVICES).
- Risk: MEDIUM — extraction prompt. Verification: see CB-003.

### CB-003 — "Mata VAS charges monadwada kiyanna puluwanda?" got "I don't have reviewed information"

- Date / status: 2026-10-03 — DONE on branch, verified, committed `73677a4`
- Risk: MEDIUM — changes extraction schema/prompt, routing and knowledge lookup; no change to consent,
  proposals or what is sent to Resolve beyond an existing VAS_DISPUTE investigation.
- Root causes (three, all in the chatbot module):
  1. **Knowledge search never matched non-English customers.** `PostgresKnowledgeRepository._search` filtered
     `WHERE language=:language`, but all 12 reviewed cards are `en`. Every FAQ from a Sinhala/Tamil
     (incl. Singlish) customer ended at `faq_none`. Unit tests missed it because the fake repo ignores language.
  2. **No answer path for "which services/charges do I have".** Gemini labelled the question `FAQ`; even as
     `ACCOUNT_ENQUIRY` the bot only ever replied with the balance.
  3. **Simple prompts stayed English once a case existed** (`_localize` blocked anything while
     `active_case_id` was set), so `faq_none`, menus and clarifications were English after the first case.
- What changed:
  - `storage.py`: search the customer's language **and English**, customer-language cards ranked first; the
    grounded answerer already writes the answer in the customer's style. Verified on the real DB: `si`
    searches now return the same cards as `en` (previously none).
  - `extraction.py`: new optional `account_topic` (BALANCE | SERVICES | PACKAGES), prompt `extract-v6` with
    rules + Singlish examples ("my services/charges" is never FAQ). 5 eval cases added (`vas-01..05`).
  - `service.py`: `_account` answers SERVICES (lists each VAS with status/renewal from Resolve's account view,
    no invented price, then asks "shall I check your charge records?") and PACKAGES (name, status, data left).
    A yes starts the normal VAS_DISPUTE investigation through ResolveFacade; no clears it; unclear re-asks.
    The yes routes to the latest question even when an older offer is still open — it never accepts that
    offer (`confirm_action` not called; regression test).
  - VAS investigation replies now say the charge in words by quoting each posted `VAS_CHARGE` ledger line
    from Resolve's calculation verbatim (no sum, no sign change), e.g. "…service charges posted in these
    records: -LKR 60.00."
  - `_ask` prompts and `faq_none` are now rewrite-allowed in text chat (no case facts); the consent prompt is
    explicitly excluded, and nothing is re-rewritten once a reviewed locale wrote it.
  - New templates (`vas_charge_lines`, `account_services`, `account_no_services`, `service_renews`,
    `service_no_renewal`, `offer_charge_check`, `account_packages`, `account_no_packages`,
    `package_item_data`) with unreviewed drafts in `si`, `ta`, `si-Latn`.
- Verification:
  - Unit: `.venv/bin/python -m pytest -q` → **473 passed, 40 skipped** (12 new tests in
    `tests/conversation/test_account_enquiry.py`).
  - Live Gemini extraction (`try_extract`): the reported message and 6 variants all classified as intended
    (SERVICES ×3, PACKAGES, BALANCE, VAS_DISPUTE ×2). Full eval `try_extract --eval --rpm 12`, `extract-v6`,
    gemini-3.5-flash-lite, 2026-10-03T16:26Z: **62/62** (english 18, singlish 21, sinhala_script 7, tanglish 7,
    tamil_script 4, mixed 2, adversarial 3), 0 fallbacks, median 1622 ms, max 3049 ms. Machine-scored
    classification only; not a native-language quality review.
  - Browser + local live stack: signed-in customer asks the reported question → Singlish list of
    "Synthetic video alerts (active, automatic renew wenawa)" + charge-check question → "ow" → Singlish
    investigation reply stating "-LKR 60.00", Balance check card, older offer untouched.
  - DB check of the knowledge query for `si` (read-only).
- Not verified: Tamil live samples; fluent review of the new draft strings; Voice (unchanged path except that
  a spoken yes to the charge check now starts the look-up instead of being treated as offer consent — safer).
- Commit: `73677a4` (branch `tevin/chatbot-fixes`, pushed and remote-confirmed).
- Core dependency: CE-004 (price per subscription).

---

## CRM integration — branch `tevin/crm-integration` (2026-10-03)

From `tevin/chatbot-fixes` `4b305ff` + `main` `d7dc9da`; conversation fixes ported from `tevin/hubspot-crm`.
Engine/frontend/tooling parts of the same work are CE-005..CE-011 in [core-engine-changes.md](core-engine-changes.md).

### CB-004 — Offer re-try, no review loop, no unprompted review on reconciled answers, Voice replay

- Date / status: 2026-10-03 — DONE on branch, verified, committed `ddf619d` (ported from `tevin/hubspot-crm`
  `3120d4e`/`61d8506`, audit findings 5, 6, 7, 16, 17, 19)
- Risk: **HIGH** — touches the confirmation flow (a fresh proposal after PROPOSAL_INVALIDATED) and turn
  idempotency (Voice fingerprint). Needs Harry's review before merge. Small code size (~70 lines).
- Files: `backend/resolve/conversation/{service.py,identity.py,locales/si.json,locales/ta.json}`,
  `tests/conversation/{test_decisions_and_handoff.py,test_resolve_integration.py}`.
- What changed:
  - Accepting an offer that the case invalidated meanwhile (an earlier action finished) re-offers the same
    action on a fresh proposal; the customer confirms again; nothing runs on the stale proposal.
  - If Resolve refuses the first offer (STALE_VERSION / ACTION_NOT_ALLOWED / PROPOSAL_INVALIDATED), the next
    eligible action is offered instead of failing the turn.
  - No unprompted human review when every finding is `LEDGER_RECONCILED`/`QUOTA_RECONCILED`; still available
    on request; a pending payment (E) still offers it.
  - The action just accepted or declined is never re-offered as the "next" option (decline-review loop).
  - Voice turn fingerprint ignores `presentation_response_id`, so a retried Voice turn that produced a new
    offer is no longer `IDEMPOTENCY_CONFLICT`.
  - SI/TA package hint names the translated button.
- Not ported: the CRM branch's adapter change (Resolve-offered reviews via `propose_escalation` with a fixed
  reason) — main's facade now supplies a default reason; `test_postgres_runtime` expectation tied to the
  un-ported scenario A VAS offer (CE-011).
- Verification: unit (default suite) **505 passed, 48 skipped**; PostgreSQL disposable: conversation runtime
  6 passed, real-facade conversation 22 passed incl. the new reconciled-vs-pending test; browser: decline of
  the offered review on A did not loop.
- Core dependency: none.

### CB-005 — "I want a real person" after a case returned a 500

- Date / status: 2026-10-03 — DONE on branch, verified, committed `2d34967`
- Risk: **HIGH** by the table above — it changes which facade method the chat calls for a customer-requested
  review (`propose_action` with `escalation_reason` instead of `propose_escalation`). The payload is the same
  case, latest investigation, Resolve-listed review target and the customer's reason; Resolve still validates
  eligibility/version and stores the reason. Needs Harry's review. Small code size (one adapter method).
- Files: `backend/resolve/conversation/resolve_adapter.py`, `tests/test_conversation_review_reason.py`,
  `tests/conversation/test_resolve_integration.py`.
- Root cause: CE-007 — main's `propose_escalation` parses the request key as a UUID and writes the dialogue
  state; the chat's command key is deterministic and not a UUID. Found in the browser during the CRM test.
- Verification: unit 4 passed (reason passed through; no proposal when Resolve listed no review); PostgreSQL
  real-facade regression fails on main's adapter and passes with the fix; browser on the live HubSpot stack:
  decline → "Actually I want a real person…" → review offered with "Reason: Customer asked for a person to
  review this case." → accepted → HubSpot ticket created and synced.
- Core dependency: CE-007 (proper engine fix).
