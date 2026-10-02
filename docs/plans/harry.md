# Harry: Resolve backend and external Voice integration

Read [context.md](../../context.md) and [contracts](../contracts.md) first. Own all Resolve APIs and permissions, provider and business logic, persistence, dashboard data and Voice qualification. Tevin owns dialogue; Jayith owns UI. Update this plan and context after meaningful progress, including test evidence and remaining work. Unchecked means not implemented and verified.

## Starting point and implementation boundaries

Reuse PostgreSQL, schemas 001-003, seed/reset tooling, six synthetic scenarios, twelve knowledge cards, and the Voice adapter/client/grant store. There is no Resolve runtime. Voice has 15 passing tests but a reproduced transcript-finalization failure and one-model-turn session termination. Do not mark Voice complete from the existing suite.

Keep one application process. Suggested module ownership: `backend/resolve/{api,auth,contracts,services,providers,persistence,integrations,worker}` belongs to Harry; `backend/resolve/conversation` belongs to Tevin. Harry composes routers/dependencies and exports DTOs/facade. No HTTP call to the same process between dialogue and services. External Voice stays in its existing repository.

## H-01: database and fixture baseline

- [x] Establish Alembic lifecycle without replaying CREATE TABLE over data. Validate schemas 001-003 before baseline adoption; on fresh Compose PostgreSQL, upgrade through `0003_case_investigations`. Repeated upgrade is idempotent.
- [x] Introduce fixture version 2. Correct B's out-of-bundle posting to -8000 and closing to 2000; remove duplicate E opening; add a separate consistent 10 GB offer for C (preserving 9.7 GB remaining); link B's bucket to a valid 20 GB subscription; remove B's unsupported categories. Complete references used in explanations without inventing activation consent for F.
- [x] Add forward persistence for retired runs, GUEST principals/CSRF metadata, language and dialogue state, leased turn claims, one confirmation per operation, idempotency results, review history and escalation delivery. Verified same-case/run foreign keys and append-only application grants on an isolated PostgreSQL 18 database.
- [ ] Keep historical fixture runs immutable; seed corrected data into a new run. Add explicit linked reversal fixtures and metadata for fault tests. Never choose diagnoses by fixture name.
- [ ] Run seed/knowledge exactly once during bootstrap and mark readiness only after all required initialization. Add LF checkout rules for shell bootstrap.
- [ ] Reset creates a new explicit active run, expires prior active sessions and Voice bindings, and preserves historical evidence. Repeated requested run UUID fails clearly without partial data; no implicit newest-run lookup in authorization.

Forward schema additions (Harry owns all migrations):

| Persistence | Required additions/invariants |
| --- | --- |
| Sessions/principals | Stable server-configured demo principal key; GUEST role; CSRF hash; account/run for private sessions; revoked/expiry checks |
| Conversation/message | Persist language and structured dialogue state; immutable input hash/result for client-turn deduplication; one accepted turn at a time per conversation |
| Cases | `updated_at`; complaint/status validation; distinct review status NEW/IN_REVIEW/CLOSED; same-run scope; reported facts; originating client-turn hash and dedupe |
| Investigations | Append-only revisions with window, stable command key/hash, evidence snapshots, calculations, source completeness, missing/conflicts, action eligibility and review reasons |
| Confirmations | Append-only table for proposal/hash, actor/session, source channel, client turn, ACCEPT/DECLINE, recorded time; declines exist without operations |
| Operations | One operation per accepted proposal; confirmation FK; request fingerprint; lease/retry/recovery timestamps; provider operation reference |
| Review history | Append-only notes/dispositions with actor identity, case version, time and note visibility fixed to INTERNAL |
| Escalation | Case/operation, BILLING_REVIEW or TECHNICAL_SUPPORT, provider ticket ID nullable, delivery PENDING/DELIVERED/FAILED/REVIEW_REQUIRED |
| Idempotency | Subject+route+key scoped request hash and saved result; persist in-progress turn work before external/model calls; durable duplicate recovery |
| Run lifecycle | Explicit active/retired state and retirement time; existing sessions/bindings are revoked on operator reset |

Use relational constraints for ownership and uniqueness; typed JSONB for evidence/dialogue/results. Resolve's runtime role can read sandbox data but writes only its own mutable tables. Provider credentials write sandbox-owned state. Receipts, investigations, messages, confirmations and audit history are append-only. Verify grants on upgraded as well as fresh databases.

Acceptance: fresh initialization and existing-volume upgrade pass; A=42000, D actual35000/expected42000, B closing2000/quota0, C quota9700000000; E has one opening; six customers per new run; previous evidence remains readable by authorized agent history only.

## H-02: application, authentication and facade

- [ ] Create FastAPI composition, validated configuration, SQLAlchemy repositories, health/readiness and consistent error handling. Pin dependencies in an isolated environment.
- [x] Implement guest/demo customer/agent session endpoints with opaque hashed cookie tokens, configured credential hashes and fixed run/account scope, exact Origin checks, deterministic hashed CSRF tokens, atomic guest upgrade, 30-minute expiry, revocation and logout-driven Voice binding invalidation. Unit and isolated PostgreSQL integration pass.
- [x] Export the shared `AuthContext` and an in-process `ResolveFacade`; create scoped conversations/cases, get cases under session/run scope, and persist idempotent investigation results for Tevin without calling Resolve over HTTP.
- [ ] Add authenticated request context for domain routes, guest conversation transfer, route-level roles, throttling, and full nested-entity authorization before expanding the API surface.
- [x] Implement opaque hashed sessions, separate customer/agent cookies, Origin/CSRF checks, 30-minute expiry, logout/revocation, and synthetic login mapping. Caller-provided roles/account IDs never establish authorization.
- [ ] Add guest FAQ sessions; upgrading a guest rotates the token, scopes its existing conversation to the authenticated run/account, and preserves only public history. Changing an already private identity creates a new session/conversation.
- [ ] Authorize every entity read/write, including nested IDs. Same-run membership alone is insufficient for customer access: conversation must belong to the current session. Agent access is run-scoped. Distinguish forbidden role (403) from inaccessible entity (404).
- [ ] Publish shared Pydantic DTOs, scoped conversation/knowledge repositories and ResolveFacade per contracts; validate parity with documented OpenAPI. Tevin can use interface fakes before repositories are complete.

Facade methods: `get_account`, `create_case`, `get_case`, `investigate`, `propose_action`, `confirm_action`, `prepare_escalation`, `get_operation`, `get_receipt`. Every method receives authenticated context. Context carries request/session/principal/role/account/run and channel; no model-supplied permission fields.

## H-03: providers and primary investigation

- [x] Implement the first in-process synthetic account/balance/subscription read and customer-only `GET /account`; implement the balance statement adapter and deterministic A/D calculator. Verified on fresh PostgreSQL: A=42,000 minor LKR reconciles; D is CONFLICTING at -7,000; account endpoint returns the customer’s fixed account.
- [x] Persist case origin-turn deduplication and immutable investigation revisions with evidence/calculations/source status, stable command-key replay, audit events and REVIEW_REQUIRED conflicts. Isolated PostgreSQL verified A, D, stale-version, idempotent replay and cross-account 404.
- [x] Publish the contract-backed customer/agent case-detail and customer investigation routes. Require Idempotency-Key and validate persisted DTOs; verification includes replay, changed-body conflict, customer cross-account 404 and same-run agent read.
- [ ] Implement separate Customer/CRM, Charging, Recharge, Product/VAS, Usage/Quota and ServiceAssurance provider ports in-process. Investigation code depends on ports, not sandbox SQL.
- [ ] Implement bounded reads with source metadata, complete pagination, fixed simulation clock, event/posting times and explicit units. Source read timeout is two seconds with at most one safe retry; missing/stale/partial results remain visible.
- [ ] Persist cases and immutable investigation revisions. Support a maximum 30-day window; require clarification for critical missing dates/amounts. Evidence states are SUFFICIENT/PARTIAL/CONFLICTING, not percentages.
- [ ] Implement A/D from records: opening plus committed signed postings within account/wallet/currency/sequence boundaries; capture payment separately; count linked charges/reversals once. D's -7000 difference blocks account mutation.
- [ ] Snapshot evidence IDs, source ID/version, observation/fetch time, values/units and source payload. Produce deterministic finding codes/calculations, eligible actions and review reasons. Reinvestigation invalidates affected proposals.

Acceptance: modifying seed values changes calculated outcomes; missing opening or incomplete pages cannot produce a conclusive ledger tie-out. A reconciles; D conflicts. Balance explanation alone never establishes consent/refund eligibility.

### H-04a completed: proposal and confirmation boundary

- [x] Add revision `0004_action_proposals`; bind proposals to sandbox/session/case/evidence revision/case version and stable request hash/key. Confirmation turns and operation confirmation IDs are unique. Runtime confirmation records remain append-only.
- [x] Add customer-only proposal and confirmation routes with exact configured Origin and CSRF checks. Proposals expire after five minutes, require latest eligible evidence and re-read target version/status.
- [x] Persist proposal/decline/accept audit. Acceptance atomically inserts the append-only confirmation and a unique durable `PENDING` operation. Same-turn replay returns the saved operation; a second acceptance is rejected.
- [x] Offer CREATE_REVIEW_TICKET with every persisted investigation; offer DEACTIVATE_VAS only for a sufficient VAS dispute with an active recurring renewal-enabled target. Other action eligibility remains evidence-dependent.

Verification: 17 pytest tests pass. Fresh isolated PostgreSQL bootstrap/Alembic through `0004_action_proposals` and a runtime-role facade integration verified scoped investigation, proposal replay, accepted PENDING operation, confirmation replay, and duplicate-accept rejection. `compileall` and `git diff --check` pass. H-04a alone stopped at PENDING; see H-04b for verified mock execution and receipt outcomes.

### H-04b completed: mock execution, recovery and Trust Receipts

- [x] Add a separate `SANDBOX_DATABASE_URL` for the mock-provider write role. The application role remains read-only on sandbox data. A missing writer URL leaves confirmed operations safely pending and logs the degraded capability.
- [x] Add one lifespan-managed in-process worker with short PostgreSQL leases, `SKIP LOCKED` claims, restartable expired RUNNING work, and stable operation-derived provider idempotency keys. Provider commits and Resolve result updates use separate transactions.
- [x] Implement mock VAS renewal cancellation, settings-instruction preparation, and synthetic review ticket creation. Persist provider operation outcome with the mutation; consume seeded provider-unavailable, write-rejection and committed-response-lost fault profiles.
- [x] Recover UNKNOWN at 2/10-second intervals up to three attempts, then require review. The CRM outage path keeps ticket ID null until a real synthetic ticket row exists.
- [x] Add customer-session/agent-run scoped operation polling and receipt reads. Append terminal Trust Receipts with findings, calculations, evidence references, action outcome, handoff state and canonical SHA-256 integrity digest.

Verification: 17 pytest tests pass; `compileall` and `git diff --check` pass. Fresh isolated PostgreSQL tests using separate runtime/provider credentials verified CRM unavailability -> UNKNOWN -> one ticket -> SUCCEEDED; VAS evidence -> proposal -> committed-response-lost -> same-key recovery -> exactly one subscription mutation/event; receipt retrieval and digest recomputation. These results are synthetic only. Restart, simultaneous workers, terminal CRM failure and broader fault profiles remain open.

## H-04: actions, receipts and handoff

- [ ] Implement only DEACTIVATE_VAS, SEND_SETTINGS_INSTRUCTIONS and CREATE_REVIEW_TICKET. Explicitly reject refund, credit, purchase, SIM-change and network-repair requests.
- [ ] Persist five-minute proposals bound to session/case/evidence revision/target/version/consequences/hash. Record accept/decline separately; check fresh presentation and exact proposal before accepting Voice decisions. Ambiguity requests clarification/text.
- [ ] Create confirmation and pending operation atomically. Unique proposal operation prevents two different confirmation keys from executing twice. Return 202 with persisted operation ID; no background-only acknowledgement.
- [ ] Use a single lifespan-managed poller in the hackathon app, PostgreSQL leases and short transactions. Never hold locks across model or provider calls. Provider mutation commits in its own transaction; simulate lost response after commit.
- [ ] Read back provider outcome. Recover UNKNOWN immediately, then at 2 and 10 seconds; unresolved becomes REVIEW_REQUIRED. Restart reclaims expired leases; never mint a new provider key for the same action.
- [ ] Persist append-only receipt revisions and deterministic SHA-256 digest of canonical JSON excluding digest itself. Digest is integrity metadata, not a signature or proof of source truth.
- [ ] Build context-rich review packet, durable pending delivery and mock provider ticket reference. Request-for-human creates a CREATE_REVIEW_TICKET proposal; exact details are confirmed through the same confirmation endpoint. Review actions remain eligible when evidence conflicts.

Receipt fields: case/revision/time/simulation, issue/window, findings/calculations and evidence references, missing/conflicting facts, requested/confirmed/completed actions, actual operation state, handoff delivery/ticket reference and next step. Customer projections omit internal agent notes. Stopping future renewal never closes a historic dispute automatically.

Acceptance: decline/expiry/stale target/stale investigation write nothing; concurrent acceptance yields one provider operation; lost response/restart recovers accurately; CRM outage remains pending without a fabricated ticket ID; receipt never turns UNKNOWN into success.

## H-05: dashboard backend

- [x] H-05a Queue `GET /agent/cases` with review-status, complaint-type, evidence-state, delivery-state filters; case-ID or exact synthetic-line search; HMAC-signed opaque cursor; newest update then ID ordering. Scope is fixed to the authenticated sandbox run.
- [x] H-05a Agent case detail returns synthetic account identity, case and conversation, evidence/source status, investigations, action proposals/confirmations/operations, receipts, ticket-delivery state and internal review notes/audit history.
- [x] H-05a Versioned PATCH appends a note and optionally changes review status. NEW -> IN_REVIEW -> CLOSED; CLOSED -> IN_REVIEW requires reopening reason. Closing requires a disposition and explanatory note. Notes-only patches increment case version. Stale writes fail; request replay is idempotent.
- [x] H-05a Keep customer case status, investigation state, operation state, provider ticket delivery state and agent review disposition distinct. Closing a review does not alter evidence or account state.
- [x] H-05a Persist local review history and audit atomically. Agent updates cannot trigger charging/product mutations.
- [x] H-05b If a delivered mock ticket exists, queue its review-status/note synchronization using a stable provider key derived from review event ID; display pending/unknown until confirmed. Do not alter provider ticket status. Isolated PostgreSQL test simulates committed-response-loss, retries same provider key, verifies a single ticket mutation/note, and confirms idempotent review replay reads SYNCED.

Acceptance: customer/guest denied; wrong run denied; stale PATCH returns 409; simultaneous agents do not overwrite notes; review history survives restart; dashboard shows a pending handoff without needing CRM availability. No analytics/admin portal beyond this scope.

Verification for H-05: `python -m pytest -q` (32 passed; opt-in DB tests skipped without env), `python -m compileall -q backend`, `git diff --check`, and runtime OpenAPI generation show all three `/api/v1/agent/cases` routes. Isolated PostgreSQL checks cover queue/detail/review behavior and review-sync committed-response-loss recovery with one mock ticket update and idempotent replay. Broader multi-worker/restart stress qualification remains part of H-09.

## H-06: remaining complaint and fault paths

- [x] H-06a Pure quota bucket reconciliation core with sequenced grant/consume/expire/reversal entry model, usage-to-consumption cross-check, snapshot continuity, safe numeric bounds, and a strict split from OUT_OF_BUNDLE charging. Unit cases cover exact depletion, snapshot/usage mismatches, reversal integrity and incomplete sources.
- [x] H-06b DATA_DEPLETION provider and persisted investigation path. Reads account-scoped bucket ledgers, selected snapshots and usage, cross-checks in-bundle consumption, and includes the separate LKR balance ledger calculation. The synthetic fixture now has an opening bucket snapshot. Opt-in isolated PostgreSQL test verifies 20 GB depletion and the independent -LKR 80 posting with 2,000 minor units closing balance; runtime investigation response validates.
- [x] H-06c CONNECTIVITY provider and persisted investigation path. Requires active synthetic data package context, checks account-scoped provisioning results and same-region MOBILE_DATA incidents. Freshness windows are simulation policy (5 minutes for checks, 30 minutes for incidents). Empty/stale incident feeds and unexplained ENABLED checks stay PARTIAL; no recovery ETA is inferred. Seeded C integration verifies a current degraded incident with no ETA.
- [x] H-06d BALANCE_RECHARGE includes account-scoped payment/fulfilment/credit records. A captured-but-pending seeded E payment is surfaced without attributing it as an account credit or advising a duplicate payment. Credit links are checked against amount and posting kind. Repeated snapshots at the same sequence are only conflicting when their amount/currency differ.
- [x] H-06e VAS_DISPUTE now includes activation-evidence presence. Missing activation evidence is recorded as missing and never treated as consent; a future renewal-stop proposal can remain eligible from a fresh active recurring VAS target, with consequences stating past charges remain unresolved. Seeded F PostgreSQL test validates partial investigation plus proposal creation without confirmation or mutation.
- [x] H-06f Money ledger validates each in-window reversal against its referenced original amount/currency and flags duplicate external posting references. Related original postings are read for verification but are not double-counted in the balance calculation. Unit tests and fresh B/C/E/F PostgreSQL matrix pass.
- [x] B extension: the provider consumes the seeded one-shot late, duplicate and reversal-mismatch charging profiles only through the configured sandbox writer. Late visibility makes a statement incomplete, duplicate references conflict, and invalid reversal amounts conflict. Isolated PostgreSQL test verifies all three and profile consumption; missing opening evidence remains partial.
- [ ] C: account/package/quota checks, supplied service checks and matching fresh incident; no invented ETA or healthy-service inference from an empty feed.
- [ ] E: captured/pending fulfilment is not credited money; never suggest another recharge as recovery.
- [ ] F: activation evidence missing; future deactivation and past dispute have separate outcomes.
- [ ] Execute remaining fault-profile configuration: missing opening, partial page, stale source, wrong unit, CRM outage, rejected mutation, lost response and failed operation lookup. Fault selection is private to fixtures/operator tooling and is not exposed to customers.

## H-07/H-08: Voice integration and qualification

- [ ] Resolve generates scoped binding/session IDs, signs session creation and returns only the short-lived browser grant. Preserve the current strict Voice models and HMAC format; document any version change before editing either repository.
- [ ] Resolve verifies signature/body/event IDs and scope before persisting/deduplicating callbacks. Matching event or turn replay returns the saved response, including across process restart; changed content conflicts. Turn deduplication is conversation+turn, distinct from event-envelope deduplication.
- [ ] Map finalized Voice input to Tevin's ConversationService and project the result into existing VoiceTurnResponse. Browser/account identifiers in speech are never trusted. Rebind from persisted state; revoke callback authorization on logout/reset/expiry.
- [ ] In Voice, add committed fake-SDK/WebSocket regression tests for fragmented transcripts and repeated model turns before fixing them. Define finalization using observed SDK input-transcription completion, separate from assistant turn completion; hold a pending tool call until a complete input turn or bounded safe failure. Never substitute model-generated text for missing input.
- [ ] Handle multiple model receive cycles, interruption/audio queue flush, grounded output, exact proposal playback acknowledgement, duplicate tool calls, end_session, all-task cancellation and provider close. Emit the proposed `interrupted` event before further output and clear presentation eligibility; close after final grounded output when Resolve requests end_session. Expose a generic runtime/adapter boundary; keep Hutch routing out of core.
- [ ] Test grants/origin/replay, no-final-input, early/forged/mismatched ack, ambiguous acceptance, timeout, disconnect, provider unavailability and continuation by text. Use the same turn/event ID for transport retries.
- [ ] Live gate: real microphone, actual configured model, A investigation -> proposal -> clear confirmation -> persisted result/receipt; repeat decline/interruption and disconnect-to-text. Record model/profile, timestamp, release and result without logging raw audio/secrets.

Existing Voice read timeout is eight seconds; conversation processing must return within its budget. Persist slow work and return a pending response, never hold the call while polling a provider mutation. Do not extend timeouts casually to hide deadlocks or missing finalization.

## H-09: integration, observability and release

- [ ] JSON logs with request/conversation/case/investigation/operation/Voice IDs, timings and error codes; redact credentials/transcripts/raw provider payloads. Capture model usage from Tevin and Voice duration/provider usage when available; never infer token counts.
- [ ] Readiness checks DB/migration head; Voice/model outage degrades channel capability and does not mark deterministic text unavailable. Protected diagnostic metrics only; no extra observability service required.
- [ ] Pytest covers reconciliation, permissions, provider faults, proposal/operation concurrency, restart and contracts. Coordinate Playwright with Jayith and dialogue tests with Tevin. Test both fresh setup and upgrade of the existing database.
- [ ] Final README/configuration/dependency lock/demo access match the submitted commit; secrets shared separately. Confirm seven-day demo retention/cleanup and no audio recording.

## Verification log

| Date | Task | Evidence | Remaining |
| --- | --- | --- | --- |
| 2026-10-02 | Baseline | 15 existing Voice tests pass; ephemeral streaming reproduction fails; live DB healthy; seed defects confirmed read-only | H-01 through H-09 remain unchecked |
| 2026-10-02 | H-01 fixture corrections + H-01 migration phase + H-02 readiness starter | Fixture v2 SQL assertions pass on isolated bootstrap; initial Alembic validation reached `0002_domain_lifecycle`; app-role `/api/v1/readyz` returned 200; runtime role append-only review grant verified | H-01 reset/session and Voice-binding revocation, reversal fixture, readiness-after-seed marker; H-02 auth/facade/repositories and consistent errors; H-03 onward |
| 2026-10-02 | H-02 session authentication slice | 10 tests pass; isolated PostgreSQL login flow verified guest creation, atomic guest-to-customer token rotation, fixed customer/account and agent/run scopes, CSRF logout, and revoked-cookie rejection; migrated runtime role used successfully | AuthContext middleware for future routes, guest conversation transfer, throttling, all business APIs and authorization; route access policy tests against real domain entities |
| 2026-10-02 | H-03 account read + deterministic ledger core | 15 tests pass; isolated PostgreSQL verified customer-scoped account read and seeded statements; A=42,000/0, D=42,000 expected and 35,000 observed/-7,000 delta; incomplete page stays provisional; unsafe JSON money integer rejected | Freshness/page fault controls, broader provider ports and other complaint paths |
| 2026-10-02 | H-03 case/investigation facade | Isolated PostgreSQL revision 0003 verified session-owned conversation/case creation, immutable A investigation persistence, stable command replay, stale-version conflict, D review queue status and cross-account 404 | Public conversation routes, additional customer paths/providers/faults, action proposals/operations/receipts and dashboard APIs |
| 2026-10-02 | H-03 public case API slice | 16 tests pass; fresh PostgreSQL validates case detail/investigation wire models, origin-scoped customer/agent reads, idempotent success replay and changed-body 409 | Public conversation/controller routes, other complaint providers, proposals, confirmations, operation runner, receipts and review queue |
| 2026-10-02 | H-04a proposal/confirmation boundary | 17 tests pass; fresh isolated PostgreSQL migrated to revision 0004 and verified customer-scoped proposal/replay, accept/PENDING operation, confirmation replay, and duplicate accept rejection. HTTP test verifies CSRF and 202 response. | Provider operation runner/recovery, operation read, receipts, escalation delivery and action concurrency race test |
| 2026-10-02 | H-04b execution/recovery/receipt | Separate sandbox writer and Resolve roles; isolated CRM outage and VAS committed-response-lost integrations recover to one ticket / one VAS mutation; receipt hash verifies. | Worker restart/concurrency, unresolved CRM outage, Voice confirmation, and broader provider faults |

Work order and time boxes are in context.md. Harry owns the critical path; publish interfaces early and integrate one vertical text slice before secondary features.
