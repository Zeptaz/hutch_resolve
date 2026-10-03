# Core engine change log (Resolve engine — owner Harry)

Changes the chatbot work needs **outside** `backend/resolve/conversation/**`: Resolve services and
facade, providers, migrations, authorization, shared DTOs/contracts (`docs/contracts*`), the Voice bridge,
or the frontend. Each entry is a request/record for Harry (engine) or Jayith (frontend) and must say how
risky the change is. Do not merge an entry rated MEDIUM or HIGH without the owner's review.

Chatbot-only changes are in [chatbot-changes.md](chatbot-changes.md).

## Risk levels

| Level | Meaning |
| --- | --- |
| SMALL / LOW | Customer-facing wording or an additive optional field. No business rule, schema or permission change. Existing tests should pass unchanged. |
| MEDIUM | Additive contract/DTO change (new field or code), or a change other modules read. Needs contract update (`docs/contracts.md`, OpenAPI), regenerated frontend types and owner review. |
| BIG / HIGH | Changes business decisions, evidence sufficiency, permissions, action/consent flow, migrations or idempotency. Needs owner implementation or approval and PostgreSQL-gated tests. |

## Entry format

```
### CE-NNN — title
- Date / status: (PROPOSED | AGREED | DONE | REJECTED)
- Owner: Harry | Jayith
- Risk: SMALL | MEDIUM | BIG — why
- Files:
- What / why:
- Requested by: CB-NNN
- Verification:
- Commit:
```

---

### CE-001 — Engine-owned customer text is English-only and not localizable

- Date / status: 2026-10-03 — PROPOSED (nothing changed)
- Owner: Harry
- Risk: MEDIUM — additive DTO/contract change; no business-rule change
- Requested by: CB-001
- Where the English text is produced:
  - `backend/resolve/providers/sandbox.py:769` — "The observed closing balance matches opening plus posted entries."
  - `backend/resolve/providers/sandbox.py:1048` — "No matching recharge record was found…"
  - `backend/resolve/services/facade.py:517` — VAS activation-evidence / consent limitation text.
  - `backend/resolve/services/facade.py:697` — proposal `consequences` ("Create a human review request for …; no account change is made now.").
- What / why: findings, review reasons and proposal consequences reach the customer as finished English
  sentences. The chatbot cannot translate them safely by template because it only receives the sentence,
  not a stable meaning. Proposal: add an optional machine-readable `code` (+ `params`) next to each
  `finding.text`, `review_reason` and `consequences`, keeping the English text unchanged as the
  authoritative fallback. The chatbot would then render reviewed Sinhala/Singlish/Tamil templates per code.
- Verification: not started.
- Commit: none.

### CE-002 — Internal escalation reason and raw enum shown to the customer

- Date / status: 2026-10-03 — PROPOSED (nothing changed)
- Owner: Harry
- Risk: SMALL — wording only
- Requested by: CB-001
- Files: `backend/resolve/services/facade.py:639` (and its use in `consequences`).
- What / why: the offer the customer reads ends with "Reason: Human review offered for the reported
  vas dispute issue; see the case evidence." This is an agent-facing note, and `vas dispute` is the
  lower-cased `VAS_DISPUTE` enum. Either keep the reason out of the customer-facing `consequences`, or
  give it a customer-friendly label ("service charge"). The chatbot can hide it on its side only by string
  matching, which is fragile.
- Update after CB-001: still visible in the text message and the confirmation card ("What this means").
  With the Singlish rewrite it now appears translated too ("Reason: Reported balance recharge issue eka gana
  human review eka offer karala thiyenawa…"), so it reads even more like a message to the customer.
- Verification: not started.
- Commit: none.

### CE-003 — Optional: let the customer see the English original of a rewritten reply

- Date / status: 2026-10-03 — PROPOSED (optional; nothing changed)
- Owner: Harry (API) + Jayith (frontend)
- Risk: MEDIUM — additive response field and UI control; no business-rule change.
- Requested by: CB-001
- What / why: rewritten replies already persist Resolve's English wording as `messages.source_reply_text`,
  but `TurnResult` does not expose it, so the chat UI cannot offer "Show original (English)". Exposing it as
  an optional field (contract + OpenAPI + generated types) would let a customer or demo viewer check the
  machine-written Singlish against the authoritative text.
- Verification: not started.
- Commit: none.

### CE-004 — Account view has no price/charge per subscription

- Date / status: 2026-10-03 — PROPOSED (nothing changed)
- Owner: Harry
- Risk: MEDIUM — additive optional fields on `SubscriptionSummary` (contract + OpenAPI + generated types);
  no business-rule change.
- Requested by: CB-003
- What / why: for "what are my VAS charges?" the chatbot can list services from `AccountView.subscriptions`
  but cannot say what each costs, because the view has no `price_minor`/renewal period. Today it offers a
  VAS investigation to read the actual posted charges from the ledger. Adding e.g. `price_minor`,
  `currency` and `renewal_period` (from `sandbox.offers`) would let it answer in one turn without opening a
  case. The chatbot would only display the values, never compute charges.
- Verification: not started.
- Commit: none.


---

## CRM integration — branch `tevin/crm-integration` (2026-10-03)

Branch `tevin/crm-integration` = `origin/tevin/chatbot-fixes` `4b305ff` + merge of `origin/main` `d7dc9da`
(Jayith's dashboard fixes, merge `3281d02`) + the HubSpot CRM work ported from `tevin/hubspot-crm`
`61d8506` (fork point `46d41ed`). The CRM branch was ported change by change, not merged, so newer main
code was kept wherever main had already fixed the same defect. Nothing was pushed; nothing touches `main`.

### CE-005 — HubSpot CRM adapter for review tickets and review sync

- Date / status: 2026-10-03 — DONE on branch, verified; **needs Harry's review before merge**
- Owner: Harry (adapter area and merge reviewer); prototype by Tevin
- Risk: **BIG** — changes the action-execution path for `CREATE_REVIEW_TICKET` and review sync: the worker
  makes external HTTP calls (up to ~5 per review sync, 3 s connect / 5 s read each) and stores non-UUID
  ticket IDs. Mitigation: off by default (`CRM_PROVIDER=mock` keeps today's behaviour), idempotency records,
  fault profiles, retries and delivery states unchanged and still in Resolve.
- Files: `backend/resolve/providers/crm.py` (new port), `backend/resolve/providers/hubspot.py` (new),
  `backend/resolve/services/operations.py`, `backend/resolve/app/config.py`, `backend/resolve/app/main.py`,
  `.env.example`, `docs/contracts.md` (CRM section, marked proposed), `README.md`, `docs/plans/hubspot-crm.md`.
- What / why: accepted human-review handoffs create a HubSpot ticket (unique `resolve_operation_id`, a retry
  after a lost response finds the same ticket); agent review updates add one tagged note per review event and
  move the pipeline stage. Mock path unchanged. Conflict resolved against main's retired-run fence
  (`a4223df`/`07823b2`): the fence is checked **before** the HubSpot call too; if the run is retired after
  HubSpot already created the ticket, the ticket is recorded instead of claiming "no change was made".
- Requested by: HubSpot CRM plan (not a CB entry).
- Verification:
  - Unit: `tests/test_hubspot_crm.py` 24 passed (fake HubSpot via `httpx.MockTransport`).
  - PostgreSQL (disposable, `sh scripts/run_db_tests.sh`, own container on 55435): all 14 opt-in files pass,
    0 skips, incl. `test_hubspot_writer_postgres_integration.py` 6 passed (fake HubSpot + real sandbox role).
  - Browser, mock CRM, local stack on a disposable DB: D balance complaint → review offered → accepted → seeded
    one-shot CRM outage showed UNKNOWN then SUCCEEDED with a mock ticket → dashboard Delivered, start review
    → "Synced to ticket".
  - Live, real HubSpot (team account, synthetic data): A complaint → customer asked for a person → accepted →
    HubSpot ticket `338730627792` created; dashboard shows "HubSpot ticket ID" + working "Open in HubSpot"
    link; start review → Synced; API read-back: stage IN_REVIEW, tagged review note present, case/queue/
    evidence properties correct.
  - Live, second run (2026-10-04): Singlish D complaint answered in Singlish → review accepted → HubSpot ticket
    `338552685274` → agent note synced → review closed as NEEDS_OPERATOR_FOLLOWUP → synced; both sync jobs
    SYNCED on attempt 1; API read-back: stage CLOSED, review version 6, both tagged notes present.
  - Not verified here: multi-worker lease overrun (known limit), HubSpot outage on the live account.
- Commit: `54a4b58`.

### CE-006 — Review sync in progress shown as PENDING instead of a 500

- Date / status: 2026-10-03 — DONE (Jayith's `eff54ce` from `tevin/hubspot-crm`, cherry-picked as `b4c0dc3`)
- Owner: Jayith (author); file owner Harry (`services/review.py`)
- Risk: **SMALL** — maps the job-table status RUNNING to the API's PENDING; no schema or rule change.
- Files: `backend/resolve/services/review.py`, `tests/test_review_models.py`.
- What / why: HubSpot calls take 1–2 s and overlap the dashboard's 5 s refresh; RUNNING failed response
  validation (500). Additive conflict with main's `REVIEW_STATUS_TEXT` resolved by keeping both.
- Verification: unit 3 passed; browser live HubSpot run showed "Sync pending" then "Synced to ticket" with
  no 500 while the sync was in flight.
- Commit: `b4c0dc3`.

### CE-007 — `facade.propose_escalation` breaks a chat review request (defect, engine unchanged)

- Date / status: 2026-10-03 — PROPOSED (worked around in the chatbot by CB-005; engine not changed)
- Owner: Harry
- Risk of the proposed fix: **MEDIUM** — facade signature/behaviour of the escalation route.
- Files: `backend/resolve/services/facade.py` `propose_escalation` (from `7bcc769`).
- What / why: it calls `UUID(request_key)` and writes the conversation's dialogue state itself. That suits the
  case-panel route (`POST /cases/{id}/escalations`, UUID `Idempotency-Key`), but the chat passes its
  deterministic non-UUID command key and owns the dialogue state during a turn. On `main`, "I want a real
  person" after a case returns **500** (`ValueError: badly formed hexadecimal UUID string`); reproduced in the
  browser on this branch before CB-005. Proposal: keep the dialogue-state write on the HTTP route only (or
  accept non-UUID keys and skip the write when called by the conversation).
- Verification: real-facade PostgreSQL regression `test_customer_review_request_reaches_real_facade_with_reason`
  fails against main's adapter and passes with CB-005.
- Commit: none (engine).

### CE-008 — Agent dashboard "Open in HubSpot" link and CRM labels

- Date / status: 2026-10-03 — DONE on branch; **for Jayith's review**
- Owner: Jayith
- Risk: **SMALL** — display only; optional `VITE_CRM_NAME` / `VITE_CRM_TICKET_URL` (HTTPS template with
  `{id}`); without them the card is unchanged.
- Files: `frontend/src/routes/agent/CaseTabs.tsx`, `frontend/src/vite-env.d.ts`, `frontend/.env.example`.
- Verification: `npm run typecheck` pass; lint exit 0 with the 4 existing warnings; browser: link text
  "Open in HubSpot", hubspot.com host, URL ends with the ticket ID; mock run shows the unchanged "Ticket ID".
- Commit: `54a4b58`.

### CE-009 — Chat input race and 24 Sinhala/Tamil labels

- Date / status: 2026-10-03 — DONE on branch; **for Jayith's review**
- Owner: Jayith
- Risk: **SMALL** — the message box is disabled until the conversation exists; labels are additive
  machine drafts marked for fluent review (no duplicate keys with main's sign-in string).
- Files: `frontend/src/routes/customer/ChatShell.tsx`, `frontend/src/i18n/messages.ts`.
- Verification: typecheck pass; key-count check 208/208/208, no duplicates; browser chat used normally.
  Mock Playwright suite not rerun on this branch.
- Commit: `2d0c8ce`.

### CE-010 — Local database and HubSpot tooling scripts

- Date / status: 2026-10-03 — DONE on branch
- Owner: Harry (ops scripts); Tevin wrote them
- Risk: **SMALL** — developer scripts only; no runtime code.
- Files: `scripts/dev_db.sh`, `scripts/run_db_tests.sh`, `scripts/demo_faults.py`, `scripts/hubspot_setup.py`.
- What / why: disposable PostgreSQL per test file (own container/port, never the shared 55432 DB or the app's
  DB), fault control for demos, HubSpot setup/spike. Fixed for main: every `database/*.sh` init script is made
  executable in the temporary copy and readiness waits for main's `99-ready.sh` marker (the old script
  failed on main with "bad interpreter"); `run_db_tests.sh` now also runs `TURN_RECOVERY_DATABASE_URL` tests.
- Verification: `run_db_tests.sh` runs all 14 opt-in files, each on a fresh database (results in CE-005); `dev_db.sh reset` reaches
  `0011_turn_recovery (head)`.
- Commit: `54a4b58`.

### CE-011 — CRM-branch engine changes NOT ported (Harry to decide)

- Date / status: 2026-10-03 — PROPOSED (not on this branch)
- Owner: Harry
- Risk: **BIG** — business policy and fixture data.
- What: from `tevin/hubspot-crm` 3120d4e/61d8506, kept main's version instead:
  - Scenario A offers `DEACTIVATE_VAS` before the review on a SUFFICIENT balance ledger with a VAS charge, and
    a stable sort puts the VAS stop first (facade). Not needed for the CRM path; changes eligibility.
  - Fixture opening snapshots moved from 2 Oct 08:00 to 1 Oct 00:00 (`database/seed.sql`). In this run the
    default 00:00–12:00 window for A still reported the starting balance as not available, consistent with
    the CRM branch finding (A's armed late/duplicate-posting faults were also active, so not isolated).
  - Voice binding check comparing conversation IDs by string value (facade). Main's disposable-DB Voice tests
    pass without it.
  - Main already fixed differently (kept main's): proposal `KeyError: 'price_minor'`, provisional-ledger
    SUFFICIENT upgrade, `propose_escalation` investigation filter, `turn_claims` mapping, confirmation
    `simulation` flag, `escalation_reason` column, and a default reason for Resolve-offered reviews.
- Verification: n/a. Commit: none.
