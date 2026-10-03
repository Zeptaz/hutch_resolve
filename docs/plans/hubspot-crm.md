# HubSpot CRM adapter: plan and checklist

Prototype owner: Tevin. Adapter area owner and merge reviewer: Harry. Branch `tevin/hubspot-crm` (local worktree `../hutch_resolve-hubspot`), based on `origin/ResolveDev` `7023924`. Shared plan document: https://claude.ai/code/artifact/2bf20fea-4040-4c28-a3df-631eeddfd049

Goal: send accepted human-review handoffs and agent review updates to a real HubSpot account through Resolve's existing CRM ticket path, with `CRM_PROVIDER=mock` restoring today's behaviour. Target: merged and demo-ready by the 21:00 feature freeze on 2026-10-03; submission deadline 12:00 on 2026-10-04. Unchecked means not implemented and verified.

## Ground rules

- All work happens in this worktree. The main checkout and its uncommitted files are not touched.
- Throwaway PostgreSQL container `hutch-tevin-hubspot` on `127.0.0.1:55434`. Ports 55432 (shared) and 55433 (Tevin's hybrid database) are not used.
- The HubSpot key lives only in this worktree's git-ignored `.env` as `HUBSPOT_ACCESS_TOKEN`. Never in chat, commits, logs or on screen.
- Synthetic data only. Every HubSpot ticket is marked as synthetic demo data.
- The contract changes before the code. Harry reviews before anything merges into `ResolveDev`. No push until the team's hold-push rule is cleared with Jayith.

## What the code already gives us

| Finding | Where | Consequence |
| --- | --- | --- |
| Ticket IDs are stored as text | `escalation_deliveries`, `review_sync_jobs` | HubSpot numeric IDs fit; no migration needed |
| Writer is fixed to the mock | `OperationRunner.__init__` constructs `MockSandboxWriter` | Add a CRM ticket port; config picks the adapter |
| Review sync parses the ticket ID as a UUID | `operations.py`, `UUID(job["provider_ticket_id"])` | Fails on HubSpot IDs; must change |
| 3 attempts, about 2 s then 10 s apart | `OperationRunner.run_once` | An outage over about 12 s ends REVIEW_REQUIRED; demo uses the single-use fault |
| Single-use CRM outage fault seeded | `seed.sql`, `crm/create_ticket PROVIDER_UNAVAILABLE` | First escalation after reset shows PENDING, then delivers |
| Delivery wording already honest | `conversation/templates.py` | No customer-side change expected |
| Ticket ID already shown | `CaseDetail.tsx`, `ChatCards.tsx` | Only an "Open in HubSpot" link and source label to add |
| `httpx` already pinned | `requirements.txt` | No new dependency |
| New HubSpot accounts cannot create private apps since 2026-09-28 | HubSpot changelog | Use a Service Key (Settings, Integrations, Service Keys) |
| Service Keys have no webhooks | HubSpot docs | Sync is one-way, Resolve to HubSpot |

## Phase 0: setup and access (11:45-12:30)

- [ ] Tevin: tell Harry the adapter is being prototyped behind his CRM port, for his review before merge.
- [x] Tevin: create a team HubSpot account (free CRM). Hub ID <hub-id>, data hosting `na2` (North America), web UI `app-na2.hubspot.com`.
- [x] Tevin: create Service Key `hutch-resolve-demo` (HubSpot shows it as the actor on tickets and notes).
- [x] Tevin: put the key in this worktree's `.env` as `HUBSPOT_ACCESS_TOKEN`; never printed by tooling.
- [x] Hub ID read through the API by `scripts/hubspot_setup.py check --write-env`, which wrote portal, web base, pipeline and stage IDs to `.env`.
- [x] Worktree `../hutch_resolve-hubspot` on new local branch `tevin/hubspot-crm` from `origin/ResolveDev` `7023924`. Main checkout verified unchanged.
- [x] Isolated venv `~/.venvs/hutch-hubspot` (Python 3.14.4) installed from `requirements-dev.txt` with exact branch pins (`google-genai==2.19.0`, `cryptography` 46.0.7). The shared `~/.venvs/hutch` venv was not changed; it has newer versions than this branch pins.
- [x] Worktree `.env` (git-ignored, mode 600) points at the throwaway database; `CRM_PROVIDER=mock`; HubSpot values blank.
- [x] Throwaway PostgreSQL 18 started from an executable copy of `database/` (no tracked file mode change), seeded (1 run, 6 accounts, 1 CRM fault profile) and migrated to `0008_audit_remediation`.
- [x] Baseline, unit/API level: `~/.venvs/hutch-hubspot/bin/python -m pytest -q` gives **438 passed, 35 skipped** (all skips need disposable PostgreSQL). Matches Harry's recorded audit-fix checkpoint.
- [x] Baseline, PostgreSQL integration: after the stopgaps below, the CRM-related opt-in files plus the HubSpot writer tests give **15 passed** on a fresh database. They consume fixture state (active VAS, single-use faults), so a second run on the same database fails until it is rebuilt (H-09c). `test_review_sync_postgres_integration` still needs a reset-generated fixture run.

### `ResolveDev` defects found and patched on this branch only (Harry to replace)

All were reproduced on a fresh database or in the browser; each patch is minimal and listed so Harry can take or replace it.

| # | Defect on `ResolveDev` `7023924` | Effect | Stopgap on this branch |
| --- | --- | --- | --- |
| 1 | `case_status.py` reads `resolve.operations.action_type` (column does not exist) | Every investigation fails | Join through `action_proposals` |
| 2 | No migration creates `action_proposals.escalation_reason` | Proposals fail; the action runner's poll query fails every loop | Migration `0009_escalation_reason` (`ADD COLUMN IF NOT EXISTS`) |
| 3 | `database.py` expects revision `0007_conversation_runtime` while head is `0008` | Fully migrated database reports not ready | Expect `0009_escalation_reason` |
| 4 | `turn_claims.py` uses `.scalar_one_or_none()` then reads `scoped["version"]` | Every chat message with a version fails with 500 | `.mappings().one_or_none()` |
| 5 | `facade.propose_escalation` filters `resolve.investigations` by nonexistent `sandbox_id` | `POST /cases/{id}/escalations` and chat review requests fail | Drop the filter; the case is already scoped |
| 6 | Agent case detail omits `simulation` on confirmations | Dashboard cannot open any confirmed case (500) | Add `simulation: True` |
| 7 | Tevin's `resolve_adapter.py` proposed review tickets without the reason the facade now requires | Review offers fail with 422 | Route review proposals through `propose_escalation` with the customer's reason or a fixed Resolve-offer reason |

Two opt-in tests were updated to pass a review reason. Harry's audit checkpoint ran only the default suite, which skips every database test, so none of these surfaced there.

#### Original Phase 0 finding

Verified on a freshly migrated database at `0008_audit_remediation`; static plus PostgreSQL integration evidence.

1. `services/case_status.py` (from `aa6af0e`) queries `resolve.operations.action_type`, which does not exist; `action_type` is on `resolve.action_proposals`. `refresh_case_status` runs from `ResolveFacade.investigate`, `OperationRunner._complete` and `AgentReviewService.update_review`, so every investigation fails with `UndefinedColumn` on a fresh database.
2. `action_proposals.escalation_reason` is read and written by `facade.py` and `operations.py` (from `02d986b`/`aa6af0e`), but no migration creates it. Proposal creation fails, and the runner's polling query fails on every loop, so no action executes.

Evidence: the five CRM-related PostgreSQL test files fail on the fresh database. With a temporary one-line `case_status.py` join through `action_proposals` and the column added only in the throwaway database, `test_review_sync_recovery` 2/2 and `test_audit_worker` 2/2 pass, and the remaining 3 failures are test drift: two tests escalate without the now-required reason, and `test_review_sync_postgres_integration` uses an account ID absent from this fixture run (`sessions_sandbox_account_fk`). The temporary edit was reverted and the database rebuilt clean; no fix is committed here. Harry's audit checkpoint ran only the default suite, which skips all database tests.

Proposed fix for Harry: in `case_status.py`, replace `o.action_type='CREATE_REVIEW_TICKET'` with `EXISTS(SELECT 1 FROM resolve.action_proposals p WHERE p.id=o.proposal_id AND p.case_id=o.case_id AND p.action_type='CREATE_REVIEW_TICKET')`; add a migration `0009` creating `action_proposals.escalation_reason text`; update the two tests to pass a reason; then run the opt-in PostgreSQL suites on a fresh database.

## Phase 1: API spike (12:30-13:15)

Ready to run once the key is in `.env`: `python scripts/hubspot_setup.py check --write-env`, then `properties`, then `spike --pause`. The spike runs the real adapter, prints timings and archives its tickets; it never prints the key.


- [x] Read the ticket pipeline; record the pipeline ID and stage IDs for New, Waiting on us and Closed.
- [x] Create custom ticket properties `resolve_operation_id` (unique value), `resolve_case_id`, `resolve_queue`, `resolve_evidence_state`.
- [x] Create a test ticket twice with the same `resolve_operation_id`; confirm the duplicate is rejected and an `idProperty` lookup returns the original.
- [x] Create a note on the ticket; record the association type ID; read it back through the ticket's associations, not search.
- [x] Change `hs_pipeline_stage`; confirm the move in the HubSpot UI.
- [x] Confirm the working API path (v3 or the 2026-03 dated path) and the record URL format.
- [x] Time five ticket creates; they must fit well inside the 15-second worker lease.
- [x] Archive the spike tickets.

**Live results (2026-10-03, real HubSpot, synthetic data):** Support Pipeline `0`, stages New `1`, Waiting on us `3`, Closed `4`. All five `resolve_*` properties created; the free account accepts `resolve_operation_id` as unique. A second create for the same operation returned the same ticket. **A raw duplicate create returns HTTP 400, not 409**: the adapter now treats 400/409 from create as a possible duplicate and looks the ticket up before rejecting (unit test added). Review sync wrote one note found through the v4 associations read with association type 228, the replay added no note, and the stage moved to Closed (confirmed in the HubSpot UI). v3 paths work. Five creates: median about 1.2-1.4 s, max 1.7 s. Record URL: `https://app-na2.hubspot.com/contacts/<hub-id>/record/0-5/<ticket>` (account-specific domain, stored as `HUBSPOT_APP_BASE`). UI review led to two fixes: subject label "VAS dispute" and HTML-escaped note lines joined with `<br>`. Spike tickets archived; ticket 338575564501 kept for viewing (`python scripts/hubspot_setup.py archive 338575564501`).

Gate 1 (13:15): **passed**. All of the above work with a Service Key on a free account. Otherwise decide within 10 minutes: static-auth private app through the HubSpot CLI, or keep the mock and show HubSpot as proposed.

## Phase 2: contract and design (13:15-13:45)

- [x] `docs/contracts.md`: provider ticket ID is an opaque string; CRM provider chosen by configuration; fixed list of fields that leave Resolve.
- [x] Fields sent: subject, synthetic finding summary, Resolve reference, case ID, queue, evidence state, synthetic-data marker. Never transcripts, names or phone numbers.
- [x] Stage mapping: NEW to New, IN_REVIEW to Waiting on us, CLOSED to Closed; disposition and note in a HubSpot note.
- [x] Error mapping: timeout, connection, 5xx, 429 are provider-unavailable (retry, no success claimed); 401/403 FAILED `CRM_AUTH_REJECTED`, no retry; 400/422 FAILED `PROVIDER_REJECTED`; duplicate operation ID is looked up and returns the original ticket.
- [x] Keep the 3-attempt retry window; changing it for the CRM is Harry's call.
- [ ] Harry approves the contract change. Draft is in `docs/contracts.md` under "External CRM for review tickets (proposed)".

## Phase 3: build the adapter (13:45-16:30)

- [x] Settings `CRM_PROVIDER` (`mock` default or `hubspot`), `HUBSPOT_ACCESS_TOKEN`, `HUBSPOT_PIPELINE_ID`, `HUBSPOT_STAGE_NEW`, `HUBSPOT_STAGE_IN_REVIEW`, `HUBSPOT_STAGE_CLOSED`, `HUBSPOT_PORTAL_ID`, `HUBSPOT_API_BASE`; validated at startup; placeholders only in `.env.example`.
- [x] CRM ticket port (`create_ticket`, `sync_review`); today's mock behaviour unchanged behind it; HubSpot as the second implementation. VAS and settings actions stay on the sandbox writer.
- [x] Fault profiles `crm/create_ticket` and `crm/update_ticket` checked before any HubSpot call.
- [x] Keep the local `provider_operations` record for every CRM call.
- [x] Review sync accepts non-UUID ticket IDs; notes tagged `resolve-review:<event_id>` and checked before writing; then stage update.
- [x] `httpx` with 3 s connect and 5 s read timeouts, no adapter-level retries, bearer header; never log bodies, responses or the token.
- [ ] Worker logs add only `provider` and HTTP status code. Not done: existing error codes (`CRM_TIMEOUT`, `CRM_RATE_LIMITED`, `CRM_AUTH_REJECTED`) already identify the cause; optional.
- [ ] Known limit: a review sync makes up to five HubSpot calls (3 s connect + 5 s read each), which can outlast the 15 s worker lease. Harmless with today's single in-process worker; a multi-worker deployment needs a longer lease or fewer calls.

## Phase 4: automated tests (15:30-17:30)

- [x] Unit (fake HubSpot via `httpx.MockTransport`), `tests/test_hubspot_crm.py`, 20 passed: ticket created with synthetic marker, pipeline and unique operation ID; retry finds the same ticket; create conflict resolves to the existing ticket; timeout/connect/503/429 raise unavailable with nothing created; 401/403/400 terminal; review note tagged and stage moved; replay adds no note; older review refused; other case's ticket refused; missing ticket terminal; token never in repr or errors; config defaults to mock, validates token, API base and provider name.
- [x] Writer integration (fake HubSpot plus real PostgreSQL sandbox role), `tests/test_hubspot_writer_postgres_integration.py`, opt-in `HUBSPOT_IT_SANDBOX_DATABASE_URL`, 6 passed on the throwaway database: ticket recorded locally and replay makes no HubSpot call, no `sandbox.tickets` row; armed outage raises before any HubSpot call; committed-response-lost recovers one ticket; rejected key recorded FAILED and not retried; review sync with numeric ticket ID recorded under a derived UUID and idempotent; review outage leaves no record. Seeded fault profiles restored afterwards.
- [ ] Runner-level retry to REVIEW_REQUIRED with no ticket number: covered by existing runner logic; end-to-end check waits for Harry's schema fix.
- [ ] Integration (disposable PostgreSQL plus fake HubSpot): D handoff to DELIVERED with receipt ticket ID; outage and recovery; worker restart mid-operation; review update reaches SYNCED. Partly covered: writer integration (6) plus browser runs on `tevin/crm-integration` (mock outage then delivery; live HubSpot delivery and sync); no automated end-to-end runner test with fake HubSpot yet.
- [x] Regression: full suite with `CRM_PROVIDER=mock` on `tevin/crm-integration`: 505 passed, 48 skipped (main + chatbot baseline 473/40; the additions are CRM and chatbot tests). All 14 opt-in PostgreSQL files pass on fresh databases.
- [ ] Live (opt-in `HUBSPOT_LIVE_TEST=1`): create, duplicate, note, stage, archive on the team account.

## Phase 5: dashboard and wording (16:30-17:30)

- [x] "Open in HubSpot" link beside the provider ticket, only when DELIVERED (done by Tevin on this branch for Jayith to review): `frontend/src/routes/agent/CaseTabs.tsx`, optional `VITE_CRM_NAME` and `VITE_CRM_TICKET_URL` (HTTPS template with `{id}`). Without them the card is unchanged. Typecheck and lint pass; browser-verified link to the right record.
- [x] Source label: "HubSpot ticket ID", and the card text says notes are added and the review status moves the HubSpot pipeline, with HubSpot edits not copied back.
- [x] Tevin: customer wording checked with a real HubSpot ID in the browser: queued message promised no ticket number until issued, then "Ticket number 338536334028". Sinhala and Tamil strings were not changed (not re-read in the browser).

## Phase 6: end-to-end runs and rehearsal (17:30-19:15)

- [x] Browser (2026-10-03, no model key, form fallback): SIM-LK-0004 balance complaint, conflicting evidence, review offered, accepted; HubSpot ticket 338536334028 "Resolve review: Balance or recharge (SIM-LK-0004)" created in New; dashboard shows Delivered, Synced and a working link.
- [x] Browser: agent started the review (HubSpot moved to Waiting on us, note added), then closed it as NEEDS_OPERATOR_FOLLOWUP with a note (HubSpot moved to Closed, note added). Verified in HubSpot's activity timeline.
- [x] Outage (fresh database, seeded single-use `crm/create_ticket` fault): customer saw "queued, no ticket number until issued"; attempt 1 UNKNOWN `PROVIDER_UNAVAILABLE`, attempt 2 about 2 s later SUCCEEDED; ticket 338597309140 carries the customer's reason, PARTIAL evidence and BILLING_REVIEW queue.
- [x] Natural language with the Gemini key (`gemini-3.5-flash-lite`): free-text complaint extracted, review offered for missing records; decline then "I want a real person" produced a review with reason "Customer asked for a person to review this case." Demo note: say "this morning" or "2 October"; "yesterday" is read as 1 October against the 2 October fixture.
- [x] Fixed: chat progress polling failed with `AUTH_REALM_REQUIRED` when one browser holds both customer and agent sessions (the demo setup). `frontend/src/api/client.ts` now always sends `X-Resolve-Realm`; browser-verified. For Jayith's review.
- [x] Adapter regression test `tests/test_conversation_review_reason.py` (3 passed): Resolve-offered and customer-requested reviews both reach `propose_escalation` with a reason; other actions unchanged.
- [x] Fallback drill on `tevin/crm-integration`: mock journey, then restart with `CRM_PROVIDER=hubspot` on the same database; both delivered. Switch is a restart (seconds); time not formally recorded.
- [ ] Two clean runs; backup clip recorded.

Gate 2 (19:30): two clean runs, or ship with the mock and keep HubSpot as proposed.

## Phase 7: submission docs (18:30-20:30)

- [ ] README: HubSpot setup, variables, `CRM_PROVIDER` switch, running without HubSpot.
- [ ] Architecture: HubSpot as built adapter; HUTCH CRM, Salesforce, Zendesk as proposed.
- [ ] Security and privacy: fields sent, scopes, key storage and rotation, hosting region, synthetic-only rule.
- [ ] Known limitations: one-way sync; HubSpot edits not read back; no webhooks with Service Keys; demo outage is simulated.
- [ ] Third-party components: HubSpot CRM (free tier, SaaS).
- [ ] Deck: integration slide and demo beat.

## Phase 8: merge and release check (19:30-21:00)

- [ ] Harry reviews; rebase onto latest `ResolveDev`; merge without force push after checking with Jayith.
- [ ] Fresh setup from the README with `CRM_PROVIDER=mock` and with `hubspot`.
- [ ] `context.md` and this plan updated with verification and commit hash.
- [ ] Feature freeze 21:00; rotate or delete the Service Key after judging.

## Full audit, 2026-10-03 (after merging `ResolveDev` `46d41ed`)

Method: every opt-in PostgreSQL test file on its own fresh database (`sh scripts/run_db_tests.sh`), the default suite, frontend typecheck/lint/build, and browser runs of scenario A and the HubSpot handoff with the real model and real HubSpot. Fixes are on this branch; items marked "Harry" or "Jayith" need their review before merge.

| # | Severity | Owner | Finding | Fix on this branch | Verified |
| --- | --- | --- | --- | --- | --- |
| 1 | Critical | Harry (`d5c881a`) | `propose_action` built a dict literal that always evaluated the package terms, so every non-package proposal (VAS stop, settings, review ticket) raised `KeyError: 'price_minor'` | Package text built only for `ACTIVATE_PACKAGE` | DB suites; browser scenario A |
| 2 | Critical | Harry (fixture) | Opening balance snapshots were at 2 Oct 08:00, so "this morning"/"today" windows (00:00 start) never reconciled: the headline line could not work | Five opening snapshots moved to 1 Oct 00:00; amounts and sequences unchanged; `check-sandbox.sql` passes | New scenario DB test; browser A reconciles to LKR 420 |
| 3 | High | Harry (policy) | Scenario A never offered the VAS stop (only `VAS_DISPUTE` could), contrary to release acceptance, the product spec and the conversation tests | Complete + SUFFICIENT balance ledger with a `VAS_CHARGE` lists `DEACTIVATE_VAS` before the review | New scenario DB test; browser A: renewal stop SUCCEEDED |
| 4 | High | Demo operations | Every reset arms 13 one-shot faults: A's first reads come back partial and the VAS stop fails up to four times. No tool existed to control them | `scripts/demo_faults.py` (list/clear/arm) and `scripts/dev_db.sh reset --demo` | Used for every browser run |
| 5 | High | Tevin | A follow-up offer made while the previous action ran was invalidated when it finished, so "yes" hit a dead end | `_confirm` re-offers the same action on a fresh proposal | Unit test; browser A |
| 6 | High | Tevin (Voice) | A retried Voice turn that had produced a new offer returned `IDEMPOTENCY_CONFLICT` (the fingerprint included the bridge-derived presentation ID) | Fingerprint excludes `presentation_response_id` | `test_postgres_runtime` |
| 7 | Medium | Tevin | If Resolve refused the first offer (target changed), the whole turn failed | Skip to the next eligible action | Conversation DB fault test |
| 8 | Medium | Harry | Voice binding check compared a UUID with a string ID, answering `NOT_FOUND` | Compare IDs by value | `test_audit_domain_repairs` |
| 9 | Medium | Tevin (mine) | HubSpot duplicate-note check read only the first 100 notes; no startup check | Paging (20 pages max), startup verification, dead code removed | Unit tests; live check "ready" |
| 10 | Low | Tevin (mine) | Resolve-offered review reason claimed the evidence needed checking even when it reconciled | Neutral, always-true reason | Browser A |
| 11 | Process | All | The default suite skips every database test, which is how items 1-8 and the earlier six bugs reached `ResolveDev` | `scripts/run_db_tests.sh` | 14 files, all pass |
| 13 | Medium | Process | Running the database tests against the app's own database let the app's worker execute test operations; in HubSpot mode it sent three test tickets to HubSpot | `run_db_tests.sh` uses its own container and port (55435) and removes it afterwards | Test tickets archived; rerun on 55435 |
| 14 | High | Harry (finding 12) | A proven recharge upgraded a provisional ledger (e.g. late posting) to SUFFICIENT | Conservative merge: CONFLICTING > PARTIAL > SUFFICIENT | Conversation fault test: xfail turned into a hard assertion, fails without the fix |
| 15 | Medium | Harry (policy) | Scenario F listed the review before the safe VAS stop | Stable sort puts `DEACTIVATE_VAS` first | Scenario DB test; browser F in Tamil |
| 16 | Medium | Tevin | A fully reconciled answer (B) still pushed an unprompted review, reading as doubt | No unprompted review when every finding is `LEDGER_RECONCILED`/`QUOTA_RECONCILED`; still available on request; E (payment pending) still offers it | Real-facade conversation test; browser B |
| 17 | Medium | Tevin | Declining a requested review re-offered the same review as the "next" option (loop) | The decided action is never re-offered | Unit test (fails without the fix); browser B |
| 18 | Medium | Jayith | Chat input enabled before the conversation opened, so an early send could fail; caused the one failing Playwright test | Input disabled until the conversation exists | Playwright mock suite 19/19 |
| 19 | Low | Tevin/Jayith | Sinhala/Tamil drafts told customers to press 'Yes, go ahead' (the button is translated); 24 UI labels missing in SI/TA (demo sign-in, packages) | Button names corrected; 24 labels drafted per language, marked for fluent review | Locale tests; browser SI/TA |
| 12 | Docs | All | README readiness revision stale (`0007`) and no macOS reset path | README updated; macOS scripts | Reviewed |

Merge resolution: upstream already fixed `case_status`, the `escalation_reason` column (in `0009_package_activation`; our `0009_escalation_reason` was dropped), the readiness revision and the realm header. Still needed from this branch: `turn_claims`, `propose_escalation` filter, confirmation `simulation`, the conversation adapter reason, and items 1-10 above.

Remaining checks (same day): Playwright mock suite 19/19 with installed Chrome; browser runs of B (English), C (Sinhala), F (Tamil) and a mock-CRM fallback drill on D (mock ticket UUID; zero HubSpot tickets created). E's review offer is covered by the real-facade conversation test.

Not fixed (reported):
- **Sinhala/Tamil case replies are English.** By design, case replies (money, findings, offers, consent) are never machine-translated; they switch to Sinhala/Tamil only after a fluent person reviews `locales/si.json`/`ta.json` and sets `"status": "REVIEWED"` (see `conversation/LANGUAGE_REVIEW.md`). Resolve finding sentences stay English even then (needs per-finding-code templates with Harry). UI chrome is localized.
- Live Voice untested here (needs the Zeptaz Voice service, Gemini Live credentials and a microphone); Resolve's bridge and Voice conversation paths pass on PostgreSQL.
- Four frontend lint warnings and two icon buttons without accessible names (header cases button, sign-in dialog close) (Jayith).
- HubSpot call chain can outlast the 15 s lease with more than one worker.

## Verification log

| Date | Task | Verification | Result |
| --- | --- | --- | --- |
| 2026-10-03 | Phase 0 worktree and environment | `git worktree list`; main checkout `git status` unchanged; branch pins installed in isolated venv | Done |
| 2026-10-03 | Phase 0 database | Fresh PostgreSQL 18 on 55434, seed counts, `alembic current` | `0008_audit_remediation (head)` |
| 2026-10-03 | Phase 0 unit baseline | `python -m pytest -q` | 438 passed, 35 skipped |
| 2026-10-03 | Phases 2-4 without a key | `pytest -q` (default) and opt-in `tests/test_hubspot_writer_postgres_integration.py` on the throwaway database; `git diff --check` | 458 passed, 41 skipped (438 baseline + 20 new; 6 new opt-in skips); writer integration 6 passed |
| 2026-10-03 | Phase 1 live spike | `scripts/hubspot_setup.py check/properties/spike --keep` against the team HubSpot account; UI check in browser | Gate 1 passed; duplicate create is HTTP 400 (handled); default suite 459 passed, 41 skipped; writer integration 6 passed |
| 2026-10-03 | Phases 5-6 browser run | Worktree app (`CRM_PROVIDER=hubspot`) on 8080, frontend on 5173, real HubSpot | Customer D to HubSpot ticket, agent start and close mirrored in HubSpot, dashboard link verified; 7 defects patched (table above); default suite 459 passed, 41 skipped; DB suites 15 passed on a fresh database; frontend typecheck/lint pass |
| 2026-10-03 | Phase 6 natural language and outage | Gemini key loaded (`readyz` model true); browser run on fresh database; realm-header fix; adapter test | Outage then delivery verified; default suite 462 passed, 41 skipped; frontend typecheck/lint pass |
| 2026-10-03 | Remaining checks | Playwright mock suite; browser B/C(si)/F(ta); mock fallback drill; locale checks; finding 12 | Playwright 19/19; default suite 458 passed, 46 skipped; all 14 DB files pass; items 14-19 fixed |
| 2026-10-03 | Merge `46d41ed` and full audit | `sh scripts/run_db_tests.sh` (14 files), default suite, frontend typecheck/lint/build, browser A with Gemini + HubSpot | All DB files pass; default 457 passed, 44 skipped; 12 findings fixed (audit table) |
| 2026-10-03 | Phase 0 integration baseline | CRM-related opt-in PostgreSQL files on a fresh database | Blocked by `ResolveDev` schema drift; to be reported to Harry |

## Integration onto main — branch `tevin/crm-integration` (2026-10-03)

Base: `origin/tevin/chatbot-fixes` `4b305ff` (main `4b581b2` + CB-001..003), then `origin/main` `d7dc9da` merged (`3281d02`). This branch's work was ported change by change (`git apply -3` of the net `46d41ed..61d8506` diff for selected files, conflicts resolved by hand), not merged, so main's later fixes are not regressed. Change records: CE-005..CE-011 and CB-004..CB-005.

| CRM-branch change | Decision | Why |
| --- | --- | --- |
| `providers/crm.py`, `providers/hubspot.py`, runner/config/main wiring, `.env.example`, contract CRM section, README, scripts, HubSpot tests | Ported (`54a4b58`) | Core of this work. Conflicts: `operations.py` (kept main's retired-run fence, also checked before the HubSpot call), `main.py` imports, README readiness line (kept main's `0011`). |
| Dashboard "Open in HubSpot" link | Ported (`54a4b58`) | For Jayith's review (CE-008). |
| Conversation: re-offer, skip refused offer, no unprompted review when reconciled, no decided-action loop, Voice fingerprint, SI/TA button text | Ported (`ddf619d`) | Not on main (CB-004). |
| Chat input race, 24 SI/TA labels | Ported (`2d0c8ce`) | Not on main; kept alongside main's new sign-in string (CE-009). |
| `eff54ce` (later on the remote CRM branch): sync RUNNING shown as PENDING | Cherry-picked (`b4c0dc3`) | HubSpot-only 500 on the dashboard (CE-006). |
| `resolve_adapter.py` review reason via `propose_escalation` | Not ported | Main's facade gives Resolve-offered reviews a default reason. Main's own customer-request path then failed (CE-007), fixed in the chatbot by CB-005 (`2d34967`). |
| Facade: proposal KeyError, provisional ledger, escalation investigation filter; `turn_claims`; confirmation `simulation`; migration `0009_escalation_reason` | Not ported | Already fixed on main (differently where noted). |
| Facade scenario A VAS offer + VAS-first sort; `seed.sql` snapshots at 1 Oct 00:00; Voice binding string compare | Not ported | Harry's policy/fixture decisions, not needed for the CRM path (CE-011). |
| Test tweaks passing an explicit escalation reason; `test_postgres_runtime` A expectation; `test_scenario_actions_postgres_integration.py` | Not ported | Reason is optional on main; the others depend on the un-ported scenario A change. |
| Remote `c82d78c` (voice call screen), `1550d79` (dropdowns) | Not ported | Unrelated to CRM (Jayith). |

`scripts/dev_db.sh` needed two fixes for main: make every `database/*.sh` executable in the temporary copy (main added `99-ready.sh`) and wait for its `.hutch_initialized` marker. `run_db_tests.sh` now also runs the turn-reconciliation test.

Verification on this branch: see the CE-005 entry and `context.md` (unit, PostgreSQL, browser mock CRM, live HubSpot ticket `338730627792`). Second live run (2026-10-04): ticket `338552685274` — Singlish complaint, agent note and close (NEEDS_OPERATOR_FOLLOWUP) synced; HubSpot read-back stage CLOSED with both tagged notes. Test tickets on the team HubSpot account: `338730627792`, `338552685274` (kept; archive with `python scripts/hubspot_setup.py archive <id>` when done).
