# Harry: Resolve backend and external Voice integration

Read [context.md](../../context.md) and [contracts](../contracts.md) first. Own all Resolve APIs and permissions, provider and business logic, persistence, dashboard data and Voice qualification. Tevin owns dialogue; Jayith owns UI. Update this plan and context after meaningful progress, including test evidence and remaining work. Unchecked means not implemented and verified.

## Starting point and implementation boundaries

Reuse PostgreSQL, schemas 001-003, seed/reset tooling, six synthetic scenarios, twelve knowledge cards, and the existing Voice adapter/client/grant store. Resolve business services, the conversation controller and the combined frontend are now present. Browser/live Voice and release qualification remain open. Historical baseline Voice findings below have since been repaired in the external Voice repository; this Resolve integration phase did not modify it.

Keep one application process. Suggested module ownership: `backend/resolve/{api,auth,contracts,services,providers,persistence,integrations,worker}` belongs to Harry; `backend/resolve/conversation` belongs to Tevin. Harry composes routers/dependencies and exports DTOs/facade. No HTTP call to the same process between dialogue and services. External Voice stays in its existing repository.

## Current full-system verification checkpoint — 2026-10-03

- [x] PostgreSQL-gated Resolve suite checks: **39/39 passed** across isolated PostgreSQL 18 projects (fresh migrations and synthetic fixtures; no shared DB touched). Suites cover turn recovery, action faults, operation restart, package activation, quota, review synchronization/recovery, audit worker, signed Voice bridge, domain and conversation/facade journeys, and seed/reset.
- [x] Added migration `0011_turn_recovery` and agent-only, CSRF-protected reconciliation route. It is fail-closed for active leases and related PENDING/RUNNING/UNKNOWN operations, writes an audit event, and makes the same turn terminal. A confirmed integration test verifies no Voice/text replay and permits a fresh turn.
- [x] Replaced the old expected failure for partial ledger evidence with a passing integration assertion; recharge evidence cannot promote an incomplete ledger to SUFFICIENT.
- [x] Fresh schema migration and upgrade from `0010_offer_readonly` to head both applied. App-role offer privileges verified read-only. Full Resolve suite: **443 passed, 39 PostgreSQL-gated skipped** in the no-DB run; those 39 gated checks passed separately. API contract/type generation, frontend typecheck/build and mock E2E **23/23** pass; Playwright exits 0.
- Resolve implementation commit `a4223df` is pushed and remote-confirmed on `origin/ResolveDev`.
- [ ] Real model/microphone qualification and production soak remain open. Zeptaz Voice was not modified in this phase; previously run Voice tests report **52 passed**.

## H-01: database and fixture baseline

- [x] Establish Alembic lifecycle without replaying CREATE TABLE over data. Validate schemas 001-003 before baseline adoption; on fresh Compose PostgreSQL, upgrade through `0003_case_investigations`. Repeated upgrade is idempotent.
- [x] Introduce fixture version 2. Correct B's out-of-bundle posting to -8000 and closing to 2000; remove duplicate E opening; add a separate consistent 10 GB offer for C (preserving 9.7 GB remaining); link B's bucket to a valid 20 GB subscription; remove B's unsupported categories. Complete references used in explanations without inventing activation consent for F.
- [x] Add forward persistence for retired runs, GUEST principals/CSRF metadata, language and dialogue state, leased turn claims, one confirmation per operation, idempotency results, review history and escalation delivery. Verified same-case/run foreign keys and append-only application grants on an isolated PostgreSQL 18 database.
- [ ] Keep historical fixture runs immutable; seed corrected data into a new run. Add explicit linked reversal fixtures and metadata for fault tests. Never choose diagnoses by fixture name.
- [ ] Run seed/knowledge exactly once during bootstrap and mark readiness only after all required initialization. Add LF checkout rules for shell bootstrap.
- [x] Reset creates a new explicit active run, expires prior active sessions and Voice bindings, and preserves historical evidence. A lifecycle migration preflight blocks unsafe reset; duplicate run UUID fails with retirement/revocation rolled back. No implicit newest-run lookup is used for authorization.

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

- [x] Implement the first in-process synthetic account/balance/subscription read and customer-only `GET /account`; implement the balance statement adapter and deterministic A/D calculator. Verified on fresh PostgreSQL: A=42,000 minor LKR reconciles; D is CONFLICTING at -7,000; account endpoint returns the customerâ€™s fixed account.
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

Verification: 17 pytest tests pass; `compileall` and `git diff --check` pass. Fresh isolated PostgreSQL tests using separate runtime/provider credentials verified CRM unavailability -> UNKNOWN -> one ticket -> SUCCEEDED; VAS evidence -> proposal -> committed-response-lost -> same-key recovery -> exactly one subscription mutation/event; receipt retrieval and digest recomputation. H-04d/H-05c add coverage for rejected writes, failed operation lookup, fresh-runner recovery and concurrent review-sync lease exclusion. These results are synthetic only; broader soak and schema-upgrade qualification remain open.

## H-04: actions, receipts and handoff

- [ ] Implement only DEACTIVATE_VAS, SEND_SETTINGS_INSTRUCTIONS and CREATE_REVIEW_TICKET. Explicitly reject refund, credit, purchase, SIM-change and network-repair requests.
- [ ] Persist five-minute proposals bound to session/case/evidence revision/target/version/consequences/hash. Record accept/decline separately; check fresh presentation and exact proposal before accepting Voice decisions. Ambiguity requests clarification/text.
- [ ] Create confirmation and pending operation atomically. Unique proposal operation prevents two different confirmation keys from executing twice. Return 202 with persisted operation ID; no background-only acknowledgement.
- [ ] Use a single lifespan-managed poller in the hackathon app, PostgreSQL leases and short transactions. Never hold locks across model or provider calls. Provider mutation commits in its own transaction; simulate lost response after commit.
- [ ] Read back provider outcome. Recover UNKNOWN immediately, then at 2 and 10 seconds; unresolved becomes REVIEW_REQUIRED. Restart reclaims expired leases; never mint a new provider key for the same action.
- [x] H-04c Disposable PostgreSQL verifies committed-response-loss plus expired-lease recovery in a fresh runner; one VAS mutation/event and one receipt result. Two concurrent runners claim one CRM action once and produce one ticket/provider operation/receipt.
- [x] H-04d Disposable PostgreSQL action-fault qualification: CRM unavailable -> UNKNOWN -> recovered single ticket; rejected VAS write -> FAILED with unchanged subscription; committed VAS write/lost response -> one-shot lookup failure -> same-key recovery, one mutation/event/receipt. The operations/lookup profile is consumed only when a prior provider operation exists.
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
- [x] H-05c Disposable PostgreSQL verifies review-sync restart recovery after committed-response-loss and concurrent exclusion of a live lease; one provider operation and one ticket version/note update, with idempotent saved-result replay.

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
- [x] C: account/package/quota checks, supplied service checks and matching fresh incident; no invented ETA or healthy-service inference from an empty feed.
- [x] E: captured/pending fulfilment is not credited money; never suggest another recharge as recovery.
- [x] F: activation evidence missing; future deactivation and past dispute have separate outcomes.
- [x] Usage fault profiles: incomplete page/stale source stay PARTIAL; wrong unit is retained with explicit unit evidence and CONFLICTING, never converted. One-shot consumption is verified.
- [x] Execute and qualify action profiles: CRM outage, rejected mutation, lost response and failed operation lookup. Fault selection is private to fixtures/operator tooling and is not exposed to customers.

## H-07/H-08: Voice integration and qualification

- [x] H-07a Resolve generates UUID binding/session IDs, stores account/session/conversation/run/origin scope, signs Voice session provisioning with the existing HMAC format, returns the short-lived browser grant and revokes failed provisioning. No schema change was needed. Ten focused tests and a disposable PostgreSQL runtime-role integration passed.
- [x] H-07a Resolve verifies exact raw-body digest/signature, header/body event ID and active binding/session/run scope before deduplication. Same event/body replays its response; changed content conflicts. Conversation+turn claims are separate, leased, and reuse their stable downstream UUID after failure. Logout/reset/expiry revoke callback authorization.
- [x] H-07a audit repair: Voice consent requires a current scoped binding, a latest persisted matching proposal response and an unambiguous fresh affirmative transcript; accepted confirmations persist binding/turn/transcript digest/proposal response provenance. Revision 0006 fences recovered claims and bounds callback bodies. One unresolved turn per conversation prevents reordered decisions. Focused unit and disposable PostgreSQL checks pass.
- [x] H-07b Finalized Voice input uses the mounted `ConversationService`; the bridge passes trusted consent evidence after signed request and binding validation. Durable result projection, proposal selection and same-turn replay are verified on disposable PostgreSQL. Browser/live model qualification remains open.
- [ ] H-07c Qualify the complete Tevin + Voice path end-to-end, including proposal presentation and confirmed outcome. The Resolve bridge is pushed on `ResolveDev`; Voice runtime fixes and fake regressions are pushed on `hutch_zeptazvoice` branch `adapter_buildation` (`a6c3ea3`). Live microphone/model qualification still depends on credentials and Tevin's controller.
- [x] In Voice, add committed fake-runtime regression tests for fragmented transcripts and repeated model turns. Finalize only when SDK `input_transcription.finished` is true, independently of model `turn_complete`; defer tool calls until a finalized caller turn exists. Never substitute model-generated text for missing input. Full Voice suite passes (26 tests).
- [x] In Voice, handle repeated model receive cycles, emit interruption before later output and revoke proposal-presentation eligibility, require completed proposal audio before acknowledging presentation, honor Resolve `end_session` after final model completion or bounded timeout, cancel sibling tasks and exit the provider context. These behaviors have fake runtime coverage; actual browser audio-queue flushing remains J-03 work.
- [x] In Voice, implement v2 response-scoped audio/playback/proposal controls, sensitive speech matching with text fallback, bounded Resolve retry and stable pending turn identity. Fake runtime and adapter suites pass (34 tests). Jayith's browser must send `playback_complete` before `proposal_presented`; live microphone/model qualification remains open.
- [ ] Extend Voice qualification for forged/early/mismatched acknowledgements, ambiguous acceptance, provider unavailability and text continuation; retain same turn/event IDs on transport retries. Existing fake tests cover no-final-input, successful ack then interruption/rejection, socket/provider failure and timeouts, but not the full matrix.
- [ ] Live gate: real microphone, actual configured model, A investigation -> proposal -> clear confirmation -> persisted result/receipt; repeat decline/interruption and disconnect-to-text. Record model/profile, timestamp, release and result without logging raw audio/secrets.

Existing Voice read timeout is eight seconds; conversation processing must return within its budget. Persist slow work and return a pending response, never hold the call while polling a provider mutation. Do not extend timeouts casually to hide deadlocks or missing finalization.

Audit repair verification on 2026-10-02: Resolve default suite 74 passed/19 opt-in skipped, Voice 34 passed. A fresh disposable PostgreSQL 18 instance migrated from baseline through `0006_audit_hardening`; 22 focused consent, worker and signed-bridge integration checks passed. Action/review workers use fenced claims and ordered review sync. Resolve commit `a92bea5` and Voice commit `3f33b03` were pushed and remotely confirmed on their assigned branches. Pending: real Tevin controller, v2 browser integration, live Gemini/model call, broader release soak and metrics. These results do not complete H-07b/H-07c or the live H-08 gate.

Resolve integration checkpoint on 2026-10-02: revision `0007_conversation_runtime` adds guest turn claims, dialogue state and model telemetry columns. Text routes and the signed Voice bridge use one mounted conversation controller; guest login transfers public conversation history and completed turn claims. Conversation creation has atomic session-scoped idempotency. Full default Resolve suite: 420 passed, 34 opt-in skipped. Five disposable PostgreSQL conversation/Voice/guest tests pass. The complete browser microphone and real model path is still unverified. No changes were made to the external Voice repository in this phase.

Opt-in suite limitation: the legacy PostgreSQL modules mutate shared fixture rows and assume isolated runs. Running all modules against one reused container produced fixture lookup/pending-operation failures; one previously failing CRM fault test passed on a fresh migrated container. Isolate runs per module or reset fixtures before treating the aggregate PostgreSQL suite as a release gate.

## H-09: integration, observability and release

- [ ] JSON logs with request/conversation/case/investigation/operation/Voice IDs, timings and error codes; redact credentials/transcripts/raw provider payloads. Capture model usage from Tevin and Voice duration/provider usage when available; never infer token counts.
- [ ] Readiness checks DB/migration head; Voice/model outage degrades channel capability and does not mark deterministic text unavailable. Protected diagnostic metrics only; no extra observability service required.
- [x] H-09a Safe HTTP request logs include request ID, method, route template, status, elapsed time and stable error code; tests confirm query/body/header values and exception messages are not logged.
- [x] H-09b Action/review-sync worker logs include case and operation/review-event IDs, action type, attempt count, terminal/current status, duration and bounded error code. They omit provider results, customer content and exception messages; three unit tests plus the existing HTTP observability tests pass.
- [x] Pytest covers reconciliation, permissions, provider faults, proposal/operation concurrency, restart and contracts. All 39 PostgreSQL-gated checks passed on isolated PostgreSQL 18; fresh migration and upgrade from 0010 both applied. Frontend mock E2E passed 23/23 with clean process exit. The real model/microphone gate remains separately open.
- [ ] Final README/configuration/dependency lock/demo access match the submitted commit; secrets shared separately. Confirm seven-day demo retention/cleanup and no audio recording.

## Verification log

| Date | Task | Evidence | Remaining |
| --- | --- | --- | --- |
| 2026-10-03 | Package action and Resolve integration | Added migration `0009_package_activation`, opt-in provider/action path, balance/offer revalidation, deterministic idempotent proposal and worker write; unified customer chat UI and typed v1.1 Voice proposal. Disposable PostgreSQL 18 verified fresh bootstrap, migration, run reset/offer allowlist, two concurrent confirmations yield one accepted operation, exactly one MAIN debit/subscription/provider operation, and Trust Receipt. Conversation 347 passed/16 DB-gated; package integration 1 passed; reset unit 2 passed/1 DB-gated; frontend build and mock E2E 19/19; Voice suite 49 passed. | Package crash/lost-response and package-specific injected-failure recovery remain unqualified. Broader PostgreSQL-gated suite not rerun. Feature remains off by default. |
| 2026-10-02 | Baseline | 15 existing Voice tests pass; ephemeral streaming reproduction fails; live DB healthy; seed defects confirmed read-only | H-01 through H-09 remain unchecked |
| 2026-10-02 | H-01 fixture corrections + H-01 migration phase + H-02 readiness starter | Fixture v2 SQL assertions pass on isolated bootstrap; initial Alembic validation reached `0002_domain_lifecycle`; app-role `/api/v1/readyz` returned 200; runtime role append-only review grant verified | H-01 reset/session and Voice-binding revocation, reversal fixture, readiness-after-seed marker; H-02 auth/facade/repositories and consistent errors; H-03 onward |
| 2026-10-02 | H-02 session authentication slice | 10 tests pass; isolated PostgreSQL login flow verified guest creation, atomic guest-to-customer token rotation, fixed customer/account and agent/run scopes, CSRF logout, and revoked-cookie rejection; migrated runtime role used successfully | AuthContext middleware for future routes, guest conversation transfer, throttling, all business APIs and authorization; route access policy tests against real domain entities |
| 2026-10-02 | H-03 account read + deterministic ledger core | 15 tests pass; isolated PostgreSQL verified customer-scoped account read and seeded statements; A=42,000/0, D=42,000 expected and 35,000 observed/-7,000 delta; incomplete page stays provisional; unsafe JSON money integer rejected | Freshness/page fault controls, broader provider ports and other complaint paths |
| 2026-10-02 | H-03 case/investigation facade | Isolated PostgreSQL revision 0003 verified session-owned conversation/case creation, immutable A investigation persistence, stable command replay, stale-version conflict, D review queue status and cross-account 404 | Public conversation routes, additional customer paths/providers/faults, action proposals/operations/receipts and dashboard APIs |
| 2026-10-02 | H-03 public case API slice | 16 tests pass; fresh PostgreSQL validates case detail/investigation wire models, origin-scoped customer/agent reads, idempotent success replay and changed-body 409 | Public conversation/controller routes, other complaint providers, proposals, confirmations, operation runner, receipts and review queue |
| 2026-10-02 | H-04a proposal/confirmation boundary | 17 tests pass; fresh isolated PostgreSQL migrated to revision 0004 and verified customer-scoped proposal/replay, accept/PENDING operation, confirmation replay, and duplicate accept rejection. HTTP test verifies CSRF and 202 response. | Provider operation runner/recovery, operation read, receipts, escalation delivery and action concurrency race test |
| 2026-10-02 | H-04b execution/recovery/receipt | Separate sandbox writer and Resolve roles; isolated CRM outage and VAS committed-response-lost integrations recover to one ticket / one VAS mutation; receipt hash verifies. | Worker restart/concurrency, unresolved CRM outage, Voice confirmation, and broader provider faults |
| 2026-10-02 | H-04c worker recovery/concurrent claim | Fresh disposable PostgreSQL migrated through `0005`; VAS provider commit followed by lost response was recovered after a fresh runner reclaimed an expired lease, yielding one mutation/event/receipt. Two concurrent runners claimed a CRM ticket action once; exactly one ticket, provider operation and receipt. Both integration tests passed; test volume removed. | Broader multi-process/restart stress, review-sync contention, remaining provider faults |
| 2026-10-02 | H-09a HTTP request observability | Four middleware tests and four Resolve app tests pass; logs correlate validated request IDs and use route templates, while excluding body/header/query data and exception messages. | Provider/action correlation IDs and protected diagnostics |
| 2026-10-02 | H-04d action-fault + H-05c review-sync qualification | Isolated PostgreSQL through `0005`; 3 action fault tests, 2 review-sync restart/concurrency tests and 6 quota/provider tests passed across separate synthetic fixture runs. Full default suite: 37 passed, 11 opt-in skipped; compileall and diff checks pass. | Broader soak/schema-upgrade qualification; conversation controller/Tevin integration; Voice bridge and frontend integration |
| 2026-10-02 | H-09b worker operation observability | Focused HTTP/worker observability checks 11 passed; full suite 40 passed, 14 opt-in PostgreSQL tests skipped; compileall and diff checks pass. | Protected diagnostics/metrics and production telemetry integration |
| 2026-10-02 | H-01e atomic fixture reset lifecycle | Two reset renderer tests and one isolated PostgreSQL integration pass. Verified previous run retirement, session and Voice binding revocation, new run activation, and full rollback on duplicate run UUID. Migration readiness preflight returned true on the migrated database. | Broader setup/upgrade qualification remains under H-09 |
| 2026-10-02 | H-07a Resolve-owned Voice bridge | Ten focused bridge tests pass. Disposable PostgreSQL 18 was freshly bootstrapped/migrated through `0005`; runtime role created a scoped Voice grant, persisted/replayed a signed turn exactly once, and rejected a new callback after logout revocation. Full suite: 51 passed, 15 opt-in PostgreSQL tests skipped; compileall and diff checks pass. | Tevin's actual ConversationService and integrated voice runtime |
| 2026-10-02 | H-08 Voice fake-runtime hardening on `adapter_buildation` | Commit `a6c3ea3` pushed and remote hash confirmed. Full Voice suite: 26 passed; `py_compile` and `git diff --check` pass. Regressions cover transcription `finished` independent of `turn_complete`, deferred tool routing, multiple turns, proposal ack/interruption eligibility, bounded end-session, session/audio limits, disconnect/provider failure and task/provider cleanup. No secrets detected in changed files. | Live Gemini/microphone/Resolve path, complete safety matrix, Jayith browser playback queue flush, and real Tevin controller integration |


Work order and time boxes are in context.md. Harry owns the critical path; publish interfaces early and integrate one vertical text slice before secondary features.


## Audit fix phase — 2026-10-03

The [audit and remediation status](../audits/2026-10-03-resolve.md) tracks findings from baseline `2b52b72` on `ResolveDev`. The implementation remains within the reviewed architecture and this phase changed only `hutch_resolve`; it did not access the Zeptaz Voice repository.

- Fixed and unit/API-verified: AUD-01 deterministic response/rewrite safeguards; AUD-02 investigation Origin/CSRF; AUD-04 late microphone cleanup; AUD-06 explicit auth realm; AUD-11 redacted catch-all error envelope and request ID. AUD-05 escalation API/reason, AUD-07 Voice operation projection, AUD-08 review-event backfill, AUD-09 aggregate case status, AUD-10 encrypted idempotent Voice grant provisioning, AUD-12 shared throttling and proxy policy are implemented. Their PostgreSQL checks passed as recorded in the current checkpoint above.
- AUD-03 now has agent-authorized durable reconciliation for abandoned claims. It refuses ambiguous operations, audits the terminal decision, blocks replay, and permits a fresh turn after reconciliation; the isolated PostgreSQL test verifies these transitions.
- Readiness now advertises text/actions/Voice/model capabilities. Both the HTTP confirmation endpoint and shared facade reject ACCEPT before persistence when action execution is unavailable. Canonical contract/OpenAPI and generated frontend types include readiness capabilities and retryable `503 ACTION_EXECUTION_UNAVAILABLE`.

Historical checkpoint before the isolated DB harness was made available: backend suite **438 passed, 35 PostgreSQL-gated skips** and frontend mock e2e **18/18**. The current results supersede this snapshot above.

### Follow-up remediation phase — 2026-10-03

- [x] Fixed durable text/voice turn claim lookup to return the locked conversation row mapping, not its first scalar field. Isolated PostgreSQL regression passed.
- [x] Scoped escalation investigation reads through the owning case/account; preserve the reason in the proposal; store a typed pending-proposal reference in the conversation so the case-panel handoff reaches the normal confirmation card/endpoint and survives a refresh. Isolated PostgreSQL claim/escalation persistence passed.
- [x] Avoid eager evaluation of package-only consequence fields on VAS/settings/review proposals. Project strict package evidence, proposal terms and operation outcome fields through the existing API shapes; provider-only activation details remain internal to the receipt/outcome store.
- [x] Tevin answer grounding rejects unsupported customer outcome claims; focused answer tests pass. The guard is deliberately conservative lexical validation and does not establish arbitrary semantic entailment.
- [x] Jayith browser voice handling preserves the same idempotency key for unknown grant outcomes, rotates after expired grants and ignores stale call startup failures; frontend typecheck/build/lint and mock e2e pass.
- [x] Voice adapter safety fix buffers and verifies generated audio/transcript against Resolve speech text, falls back to canonical text and reports tool failures as typed errors without fabricated replies. Voice suite **52 passed**; pushed as `hutch_zeptazvoice/adapter_buildation` commit `3bc6a27`.
- [x] PostgreSQL-gated turn-claim/escalation persistence, package API projections, review delivery ordering, worker recovery and abandoned-turn reconciliation were verified on isolated disposable databases; see the current checkpoint above.

### Security and reproducibility phase — 2026-10-03

- [x] Enforced Secure cookies for non-local configured origins; corrected trusted proxy address extraction and added direct-spoof/trusted-chain tests. Focused settings/auth tests: **17 passed**.
- [x] Added a streaming request-body cap (1 MiB, stable `REQUEST_TOO_LARGE` envelope) and readiness now requires the Voice grant encryption key and HMAC/base URL configuration. Focused body/readiness tests pass.
- [x] Review ticket completion now takes the case lock before delivery backfill, serializing with agent review writes. Sandbox provider writes verify the run remains ACTIVE under a row share-lock before mutation.
- [x] Fixture package allowlisting now shares the seed/reset transaction. Docker `.hutch_initialized` readiness marker moved to a final `99-ready.sh` step after all root-level seeds.
- [x] Revision `0010_least_privilege_package_catalogue` revokes unnecessary sandbox offer UPDATE from the Resolve role. Disposable PostgreSQL confirms read=true/update=false; upgrade from this revision through 0011 succeeds.
- [x] Fixed package contract generator working-directory dependence and made enum/required-list extension idempotent. Two consecutive runs produce byte-identical OpenAPI; OpenAPI generated TypeScript and contract tests were refreshed.
- [x] Removed unused `shadcn` CLI from frontend dependency graph while retaining its MIT stylesheet and license. Full public npm audit: **0 vulnerabilities**.
- [x] Logout and review retry state are recoverable across failure/reload. Review retry retains the same persisted key/body for unknown network outcomes. Definitive 409/422 responses now clear the key; this prevents stale conflict requests from bypassing the “I've checked it” guard. Focused conflict and committed-response-lost Playwright tests both pass.
- Verification: full default backend suite **439 passed, 36 PostgreSQL-gated skips**; `test_seed_run.py` in project venv **2 passed, 1 PostgreSQL-gated skip**; frontend typecheck/build pass, lint passes with six existing warnings. Expanded mock browser suite: **22 passed, 1 failed** before the definitive-conflict key fix; after the fix both affected scenarios passed (Playwright printed both green results, but Windows teardown hung and was interrupted). Rerun the full suite before release.
- [x] PostgreSQL integration for migration grants, reset atomicity, case-lock races, retired-run fencing and operation recovery passed on disposable databases. Full mock browser suite now exits cleanly after 23/23 passing tests. Live Voice/model release qualification remains open.

Historical mock browser rerun after the conflict-key fix reported **22/22 passed** but the Playwright process hung during Windows teardown. The current 23-test run exits cleanly; see the verification checkpoint above.

### Main branch merge verification — 2026-10-03

Merged `ResolveDev` into `main` with a normal merge commit after updating local `main` to `origin/main`. The histories diverged because the remote contained seven frontend commits; conflicts were limited to the integrated frontend and shared progress notes. The later integrated ResolveDev frontend was selected, and both histories remain reachable. Backend suite: **443 passed, 39 PostgreSQL-gated skipped**. Frontend typecheck and production build pass; mock browser suite **23/23 passed**. The merge-phase default run did not rerun the PostgreSQL-gated checks; see the earlier disposable-database qualification entries. Actual Gemini/model/microphone qualification remains open.

### Local full-stack manual run — 2026-10-03

Started fresh isolated PostgreSQL, Resolve, live Vite and external Voice on the merged `main` tree. Migrated to `0011_turn_recovery`. Fixed LF shell bootstrap on Windows, stale readiness revision, Voice grant expiry constraint, and strict package catalogue projection. Verified live synthetic A/B/C/D/E/F investigations, A receipt, F VAS deactivation/recovery, package activation/receipt, agent review, customer isolation, and unavailable-provider Voice text fallback. Backend default suite **443 passed, 40 skipped**; focused disposable PostgreSQL **10 passed** plus **1 package facade integration**; Voice suite **52 passed**. Real Gemini/microphone and native-language release qualification are still open. See root `context.md` for the full verification boundary and demo database state.

### Voice-only repair — 2026-10-03

On the user's `voice_test`/`voice_test2` branches, live synthetic speech isolated the missing reply to Voice's Gemini event handling: authoritative `input_transcription` lacked `finished`, and no tool call arrived. Updated only the external Voice runtime and customer call frontend/wire contract. No shared Resolve backend engine or dashboard code changed. Voice unit suite **54 passed**; frontend typecheck/build and mock desktop/phone browser suite **24/24 passed**; live synthetic WAV reached a Resolve-backed reply with browser speech-synthesis invocation. Physical microphone/playback and multilingual human review remain open under H-08/J-03.

Follow-up verification on the same branches: Gemini Live produced 378 KB of PCM with an output transcript exactly matching Resolve's 134-character approved reply. The browser received and drained the frames and sent `playback_complete`; Voice returned `playback_ack`. The original Live turn was discarded as ungrounded, and the second turn was bounded at 45 seconds to accommodate real provider latency. Voice unit suite remains **54/54 passed**, frontend build/typecheck and mock E2E **24/24 passed**. H-08 remains open for a human microphone/speaker call, spoken proposal consent and native-language review. Shared Resolve backend files remain untouched.

Implementation commits remotely confirmed: Voice `voice_test2` **47e8c92**; Resolve `voice_test` **8911392**.
