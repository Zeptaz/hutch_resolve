# HUTCH Resolve: team source of truth

Updated: 2026-10-03. The mock sandbox, Resolve business backend, in-process conversation controller and combined frontend are implemented to the tested scope below. Browser Voice/model and final release qualification remain open. Historical baseline rows below record earlier findings; current status is in the task list and latest checkpoints. Checkboxes describe implementation and verification, not approval of a design.

## UI and conversation integration checkpoint

On 2026-10-02 `ResolveDev` fetched Jayith's `HutchChat` tip `9e7b8b1` and `VoiceFrontend` tip `4349eed`. The selected frontend was integrated into Resolve and pushed as `f2c9bab`. Source branches and the Voice runtime branch were not rewritten. Voice uses browser protocol `zeptaz-hutch-v2`. The conversation module is now mounted in Resolve using its existing scoped turn claims and consent gate. Do not mark a live Voice/browser journey complete until it has been exercised with the actual services.

Frontend integration checkpoint: the combined React app reuses one customer session and conversation across chat and call, adds explicit demo customer sign-in, v2 response-scoped playback and agent review controls. `VITE_API_MODE=live` is the default; scripted mock mode is visibly synthetic. A clean `npm ci`, pinned OpenAPI generation, TypeScript check and production build passed; lint exits 0 with warnings. No browser or live Voice call has been verified. Disposable PostgreSQL verifies text turn replay, guest upgrade, scoped reads and signed Voice proposal/confirmation through the mounted conversation service.

Dashboard branch intake on 2026-10-03: `origin/ResolveDashboard` tip `6fe6acb` was fetched and its agent dashboard selectively integrated into the same React app. A full branch merge would regress the newer Voice frontend and conversation backend, so those files were preserved. The imported dashboard uses the existing agent queue/detail/review APIs and adds responsive evidence/actions/conversation/receipt/history tabs, a versioned internal review panel and browser tests. The combined app typechecks and builds. The mock browser suite passes 15/15; five live browser checks pass against a disposable Resolve database. The integration commit is recorded in Git after push.

## Read first

Agents must read this file, the relevant [owner plan](docs/plans/harry.md), and the [shared contracts](docs/contracts.md) before working. After meaningful progress, update this file **and the relevant owner plan** with task IDs, changes, verification command/result, remaining work and blockers. Mark `[x]` only after implementation and verification. A proposed interface, mock test or passing unit suite is not proof of a working live integration. Label verification as static, unit, integration, browser or live. Record the implementation commit when available; never invent a hash for an uncommitted change.

Update canonical contracts before changing their implementations. Record compatibility changes here and in affected owner plans. Preserve unrelated changes. Never record credentials, transcripts from real customers, or production data. The Voice repository links here rather than maintaining another combined plan.

Authority: the reviewed master architecture (`HUTCH_Resolve_System_Architecture_Reviewed.pdf` / `.html` in the original competition workspace), final submission guidelines, and the user's updated three-person ownership/dashboard scope. The current architecture is retained. This execution plan adds a required minimal dashboard; older advice that the dashboard is optional is superseded. Master source documents are outside the Git repositories; the constraints required for implementation are restated here and in the contracts.

## Architecture and ownership

```mermaid
flowchart LR
  Customer[Customer chat/call UI - Jayith] -->|HTTP text| Chat[Conversation module - Tevin]
  Customer <-->|PCM WebSocket| Voice[External Zeptaz Voice Core]
  Voice <--> Adapter[Hutch Voice adapter - Harry]
  Adapter <-->|HMAC HTTP| Bridge[Resolve Voice bridge - Harry]
  Bridge --> Chat
  Chat -->|typed in-process facade| Resolve[Resolve services and permissions - Harry]
  Dashboard[Internal dashboard - Jayith] -->|Agent APIs| Resolve
  Resolve --> Providers[In-process mock providers - Harry]
  Providers --> DB[(PostgreSQL 18)]
  Resolve --> DB
```

One Python/FastAPI process, PostgreSQL, one React application with customer and agent routes, and external Voice. No separate chatbot deployment, Redis, event broker or extra telecom service containers. Production identity and real operator integration remain future work.

| Owner | Responsibilities | Boundary |
| --- | --- | --- |
| [Harry](docs/plans/harry.md) | Resolve backend, APIs, permissions, schemas, providers, evidence, actions, cases, receipts, dashboard data, Voice testing/integration | Owns all business decisions and account writes |
| [Jayith](docs/plans/jayith.md) | Customer chat/call frontend and internal dashboard frontend | Renders server results; never calculates authoritative findings or permissions |
| [Tevin](docs/plans/tevin.md) | Conversation backend, extraction, clarification, knowledge routing, templates and model telemetry | Calls Resolve services; no duplicate investigation, policy or execution logic |

Stack remains Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, psycopg 3, PostgreSQL 18; React 19, TypeScript and Vite. Pin tested dependency versions during implementation. Existing model identifiers are configuration candidates until access is verified; do not treat historical prices/model availability in the master as current measurements.

## Verified baseline

Inspection and checks on 2026-10-02, before documentation changes:

| Area | Evidence | Status |
| --- | --- | --- |
| Resolve Git | Clean `main`; local and remote `8df74d78ff7d47f152fb6835eec463b88ae1b749` | Verified |
| Voice Git | Clean `main`; local and remote `a7f723c663fe05090851eaaa8e97ff80c770d52e` | Verified |
| PostgreSQL | `docker compose ps`: healthy; read-only SQL: 7 runs, 42 customers, 12 knowledge cards, 13 Resolve tables | Existing running environment verified; fresh initialization/reset not rerun in this documentation task |
| Resolve runtime | `backend/resolve/.gitkeep`; no application handlers or frontend | Missing |
| Existing Voice tests | `python -m pytest -q`: 15 passed in 3.53 seconds | Unit/API helper coverage verified |
| Voice streaming | Ephemeral fake WebSocket/SDK sequence: transcript fragment, tool call, separate `turn_complete`; 0 Resolve turns, `no_finalized_caller_turn`, then disconnect | Reproduced defect; Voice is partial |
| Voice SDK behavior | Installed `AsyncSession.receive()` breaks at a model `turn_complete` | Current runtime needs repeated receive cycles |
| Fixture B | Read-only SQL returned `+8000` for `OUT_OF_BUNDLE_USAGE` | Must be debit `-8000`; closing must be 2000 |
| Fixture E | Read-only SQL found two opening snapshots at the same account/time/sequence | Duplicate must be corrected in new fixture version |

The ephemeral Voice reproduction patched external clients in memory and changed no source files. It is not a committed regression test; H-08 must add one. No live microphone/provider test was performed. Existing `.env` secrets were not read or copied. Tests passing do not establish complete Voice readiness.

Other static findings to address: C's offer advertises 20 GB but its grant is 10 GB; B's `video` categories contradict documented unknown categories; fault-profile rows are configuration, not executable failure behavior; reset retains runs but does not revoke sessions/bindings; readiness marker precedes entrypoint fixture completion; SQL bootstrap has no application migration lifecycle.

## Shared contracts and connections

- [Canonical contract and authorization rules](docs/contracts.md).
- [OpenAPI 3.1 document](docs/contracts/openapi.json): proposed Resolve HTTP API; current Voice-facing models are preserved.
- [Concrete examples](docs/contracts/examples.json): synthetic contract fixtures, not actual API results.
- [Voice wire contract](https://github.com/Zeptaz/hutch_zeptazvoice/blob/main/docs/hutch-resolve-contract.md): existing external interface and known runtime limitations.

Freeze contract version `1.0.0` before parallel feature work. Harry owns shared DTOs, migrations and facade; Tevin owns the conversation module; Jayith can build against examples immediately. `openapi.json` is design documentation until runtime parity is tested. Generate frontend types from it during implementation. Runtime OpenAPI must match the frozen contract or include an explicit reviewed revision.

Development: frontend `http://localhost:5173` proxies `/api` to Resolve `http://localhost:8080`; PostgreSQL uses existing `localhost:55432`; Voice is separately configured, default `http://localhost:8088`. Browser audio connects directly to the returned Voice URL. Tevin's module needs no network service credentials. See contracts for secrets, cookies, CSRF, IDs and errors.

## Tasks and dependencies

### Existing foundation

- [x] DOC-01 Publishable combined/owner plans, agent instructions and versioned API/event specifications prepared.
- [x] DOC-02 Documentation validated: 96 schemas, 340 references, 43 examples, 5 invalid-input rejections, 26 HTTP operations, receipt digest, 4 exact existing Voice schemas plus nested proposal, 3 actual Voice model parses and 34 local links.
- [x] BASE-01 Existing repositories and correct remote `main` histories inspected.
- [x] BASE-02 Existing PostgreSQL running state and seed counts checked read-only.
- [x] BASE-03 Existing 15-test Voice suite run; runtime defects investigated separately.
- [ ] H-01 Correct/version fixtures, add migration lifecycle and safe readiness/reset. Owner Harry.
- [x] H-01a Fixture version 2 corrects B/C/E inputs and has SQL drift assertions. Owner Harry; verified on an isolated fresh PostgreSQL volume.
- [x] H-01b Alembic non-destructively adopts the legacy SQL schemas 001-003 as revision 0001. Verified current-head lookup and readiness with the app role against the isolated volume. Later schema revisions are tracked separately.
- [x] H-01c Alembic revision `0002_domain_lifecycle` adds scoped turn claims, confirmation and idempotency records, review history, escalation delivery, case review state and explicit run retirement. Isolated PostgreSQL 18 fresh upgrade/repeat/readiness and append-only grant check passed.
- [x] H-01d Alembic revision `0003_case_investigations` adds reported case facts and originating-turn dedupe plus immutable windowed investigation evidence/calculations/source metadata and stable-command replay. Fresh upgrade and persisted A/D integration verified with the runtime role.
- [x] H-01e Operator reset creates a new active run while atomically retiring earlier runs and revoking sessions/Voice bindings. A lifecycle-column preflight blocks unsafe reset before migration; disposable PostgreSQL verifies success and duplicate-run rollback.
- [ ] H-02 Compose application, authentication, scoped repositories and shared facade. Owner Harry; depends H-01.
- [x] H-02a FastAPI liveness/readiness starter uses synchronous SQLAlchemy/Psycopg and checks the Alembic revision. Verified fake-probe tests and actual PostgreSQL integration. Auth, APIs and business services remain open.
- [x] H-02b Guest/demo customer/agent session endpoints use opaque hashed cookie credentials, fixed server-configured identity scopes, exact Origin, CSRF, atomic guest credential rotation, expiry, and logout revocation including Voice bindings. Five auth tests plus real PostgreSQL end-to-end session flow passed.
- [x] H-02c Shared `AuthContext` and `ResolveFacade` are available in-process; Tevin can create scoped conversations/cases, replay originating case turns, get scoped cases, and run idempotent investigations without loopback HTTP. Isolated PostgreSQL integration verified persisted results and cross-account 404.
- [ ] H-03 Implement provider reads/faults, cases and A/D deterministic investigation. Owner Harry; depends H-02.
- [x] H-03a Customer-only account endpoint and synthetic account/balance/subscription read; bounded charging statement reader and deterministic A/D reconciliation core. Verified on isolated seeded PostgreSQL: A exactly reconciles, D reports -7,000 conflict, incomplete evidence stays PARTIAL.
- [x] H-03b Case-origin-turn deduplication and immutable persisted investigation revisions, including saved calculations/evidence/source status, command-key replay, audit events and review-required conflict state. Verified A/D results, replay, stale-version rejection and cross-account denial against isolated PostgreSQL.
- [x] H-03c Public case detail and investigation endpoints match shared request/response DTOs, use customer/agent session scope and require stable `Idempotency-Key`; customer reports remain separate from evidence. Verified response models, replay/conflict and account/run access on isolated PostgreSQL.
- [ ] H-04 Implement all permitted proposals, confirmations, durable operations/recovery and receipts. Owner Harry; depends H-03. Remaining: Voice confirmation/presentation gate and broader soak qualification.
- [x] H-04c Worker restart/concurrent-claim integration. Disposable PostgreSQL verifies committed-response-loss recovery after an expired RUNNING lease with one VAS mutation/event/receipt, and two concurrent runners claiming one pending CRM action once.
- [x] H-04d Action fault qualification. Disposable PostgreSQL verifies CRM unavailability/recovery, terminal VAS write rejection without mutation, and committed-write/response-loss followed by failed operation lookup and same-key recovery. Exactly one subscription mutation/event/receipt; the lookup fault is consumed only when a prior provider operation exists.
- [x] H-04a Customer proposal and explicit confirmation boundary. Five-minute session/case/evidence/target-version-bound proposals, stable proposal replay, CSRF/origin-protected action routes, append-only confirmation and atomic accepted PENDING operation. Fresh isolated PostgreSQL integration plus 17-test suite verified.
- [x] H-04b Single-process leased mock operation runner, sandbox-role provider writes, UNKNOWN recovery, operation polling and append-only Trust Receipts. Fresh PostgreSQL verified CRM outage recovery and one-ticket idempotency; VAS evidence, committed-response-loss recovery, one subscription mutation/event and receipt digest. `SANDBOX_DATABASE_URL` is required to execute writes; absent configuration leaves operations PENDING.
- [x] H-05 Implement review queue/detail/update APIs, audited permissions and mock-ticket sync. Owner Harry; depends H-02/H-03.
- [x] H-05a Agent queue, scoped detail and versioned review APIs; append-only notes/audit and idempotent updates. 19 tests, compileall, diff check, runtime OpenAPI and prior isolated PostgreSQL service verification pass.
- [x] H-05b Durable review-ticket outbox and leased mock CRM writer. Isolated PostgreSQL test injects committed-response-loss, retries with the event-derived provider key, verifies one ticket version/note update, and checks idempotent replay returns SYNCED. Fault selection now maps generated fixture line aliases rather than UUID suffixes.
- [x] H-05c Review sync restart and claim qualification. Disposable PostgreSQL verifies a fresh runner settles committed-response-loss with one ticket version/note update and matching replay; a concurrent worker cannot claim a live lease and causes no duplicate update.
- [ ] H-06 Implement B/C/E/F investigations and remaining failure profiles. Owner Harry; depends H-03.
- [x] H-06a Pure deterministic quota reconciliation core and tests. Unit coverage verifies expected and conflicting arithmetic, consume-to-usage links, reversal integrity and incomplete sources.
- [x] H-06b Seeded DATA_DEPLETION provider and persisted investigation. Opt-in isolated PostgreSQL end-to-end test verifies quota bucket reaches zero, usage-to-consume links, separate -8,000 minor-unit balance posting and public investigation response validation.
- [x] H-06c Seeded CONNECTIVITY provider and persisted investigation. Matching fresh account check and regional incident give C a grounded finding; absent ETA stays absent. Empty/stale incident feeds do not infer healthy service. Opt-in isolated PostgreSQL test passes.
- [x] H-06d Seeded E payment/fulfilment evidence and BALANCE_RECHARGE investigation. Captured-but-pending fulfilment is not credited or treated as complete; response explicitly says not to submit a duplicate payment. Same-sequence snapshots with the same amount are valid repeat observations; changed amount/currency stays conflicting.
- [x] H-06e Seeded F VAS_DISPUTE activation evidence handling. Missing activation record is explicit and not consent; future VAS renewal stop is separately eligible and remains unaccepted until customer confirmation. PostgreSQL integration verifies the proposal consequence leaves historic charges under investigation.
- [x] H-06f Money ledger checks linked reversals and duplicate external posting references without double-counting related original rows. Unit checks and fresh B/C/E/F PostgreSQL matrix pass.
- [x] H-06g Charging/usage read fault profiles are consumed once through the optional sandbox writer: late, missing opening, incomplete page and stale source become PARTIAL; duplicate reference, bad reversal and wrong unit become CONFLICTING with unit evidence. Disposable PostgreSQL tests verify outcomes and one-shot consumption; readiness requires migration `0006_audit_hardening`.
- [ ] H-07 Implement Resolve Voice bridge using the conversation service. Owner Harry; depends H-02/T-02.
- [x] H-07a Resolve-owned Voice session provisioning, callback authentication, active binding checks, durable event/turn deduplication, retry with stable downstream key, and the injected conversation-service boundary. No schema migration was needed. Ten focused tests and a disposable PostgreSQL test verified grants, replay, revocation and runtime-role writes.
- [x] H-07a audit hardening: revision `0006_audit_hardening` fences event/turn claims, stores Voice consent provenance, serializes conversation turns, bounds signed callback bodies at 16 KiB, and moves bridge database calls off the async route. Disposable PostgreSQL 18 migrated through 0006; 22 focused domain/worker/Voice integration checks passed. These checks use a stub conversation service, so H-07b remains open. Resolve repair commit `a92bea5` is remotely confirmed on `ResolveDev`.
- [x] H-07b Mount Tevin's real `ConversationService` on text and Voice routes, sharing persisted turn claims and Resolve's consent gate. Disposable PostgreSQL verifies text replay and a signed Voice proposal/presentation/accept/replay path; HTTP voice projection uses the persisted result. Browser microphone and actual model qualification remain H-08/J-03 work.
- [ ] H-08 Fix and qualify Voice streaming, then run a live integrated call. Owner Harry; fake-runtime fixes are verified and pushed; live gate depends H-04/H-07/J-03 and real provider credentials.
- [x] H-08a Voice fake-runtime hardening on `hutch_zeptazvoice/adapter_buildation`: transcript finalizes on SDK `input_transcription.finished` independently of model completion; repeated turns, proposal acknowledgement/interruption invalidation, bounded `end_session`, and task/provider cleanup are covered. 26 Voice tests pass; commit `a6c3ea3` is remotely confirmed.
- [x] H-08b Fake-runtime safety and retry hardening: protocol `zeptaz-hutch-v2` correlates audio/playback/proposal acknowledgements by `response_id`, buffers and checks sensitive speech, rejects malformed/early acknowledgements, retains stable turn IDs for retryable pending results, and bounds Resolve retries. Full Voice suite: 34 passed. Voice repair commit `3f33b03` is remotely confirmed on `adapter_buildation`. Jayith must implement the v2 browser controls; a live Gemini/browser call is still open.
- [ ] H-09 Verify access, concurrency, restart, setup and observability. Owner Harry; depends H-01 through H-08.
- [x] H-09a JSON HTTP request observability logs request ID, method, route template, status, elapsed time and stable error code. No bodies, query values, credentials, transcripts, raw payloads or exception text; focused app/middleware tests pass.
- [x] H-09b Action and review-sync worker logs correlate case/operation or review-event IDs, action type, attempt, status, elapsed time and bounded error code. Tests confirm safe fields only; provider/customer payloads are excluded.
- [ ] H-09c Isolate opt-in PostgreSQL tests by fixture run/database. A shared-container broad run first failed because older modules defaulted to a different run ID; after setting it, earlier tests left pending operations and removed accounts consumed by later modules. A fresh database passed the previously failing CRM action fault test. Do not count the shared-container broad run as a product regression or as a passing suite.
- [x] T-01 Conversation DTOs, ports, fakes and contract parity tests are imported and pass; revision 0007 persists dialogue state and model-call metadata.
- [x] T-02 Scoped text routes, persistent turn replay, guest FAQ and customer case journey run against Resolve's facade. Disposable PostgreSQL checks the route, replay and guest upgrade.
- [ ] T-03 Confirmations, handoff, FAQ and structured/model fallback are implemented in the imported controller and unit tested. Full six-scenario browser and actual-model qualification remains open.
- [ ] T-04 Complete secondary paths, language checks, telemetry and dialogue regressions. Owner Tevin; depends H-06/T-03.
- [x] J-01 Customer/agent shells, separate session handling, typed API client and contract-backed mocks are implemented; 15 mock browser checks pass across desktop/phone. Live backend journeys remain J-02/J-04.
- [ ] J-02 Integrate chat, evidence, action state, receipts and review workflow. Owner Jayith; depends H-04/H-05/T-02.
- [ ] J-03 Integrate browser audio, playback acknowledgement and text continuation. Owner Jayith; depends H-07/H-08.
- [ ] J-04 Verify browser journeys, accessibility and failures. Owner Jayith; depends J-02/J-03.
- [ ] INT-01 Pass complete A text/Voice journey and D-to-dashboard review. All; Harry coordinates.
- [ ] INT-02 Pass B/C/E/F and safety/recovery/authorization acceptance matrix. All.
- [ ] REL-01 Verify fresh setup, demo access and release source; record actual model/profile and usage. Harry, supported by Tevin.
- [ ] REL-02 Final technical PDF, architecture/Gantt, AI declaration, README and known limits. Tevin; Harry reviews technical claims.
- [ ] REL-03 Presentation and actual demo recording, accessible links. Jayith; all review.
- [ ] REL-04 Record final submission commits and complete team release review. All.

Task details and acceptance live in owner plans. Do not mark INT/REL tasks complete because planning files were published.

## Two-day sequence

Elapsed windows are coordination targets, not 48 hours of continuous work per person. Protect rest and the final integration/submission window.

| Hours | Harry | Tevin | Jayith | Gate |
| --- | --- | --- | --- | --- |
| 0-4 | Contracts, fixture/schema fixes, auth skeleton, Voice failing tests | Facade fakes and dialogue schemas | Shells and examples | Stable shared contract |
| 4-14 | Providers and A/D evidence/case APIs | Primary text routing and persistence | Chat/evidence and dashboard detail | A computes 42000; D exposes -7000 mismatch |
| 14-24 | Confirmations, operations, receipts, review APIs | Actions, handoff, FAQ/fallback | Confirmation, polling, receipts, review | One complete text action and human review |
| 24-32 | B/C/E/F and Voice integration | Secondary dialogue and language checks | Call UI and text continuation | Full primary Voice path |
| 32-40 | Recovery, access, concurrency, setup | Regressions and actual usage | Browser/failure/accessibility checks | Integration acceptance |
| 40-48 | Release fixes and access/setup | Technical documents and AI disclosure | Video and presentation | Verified source matches demo |

Harry is the critical path. Tevin and Jayith work against frozen contracts immediately and own their contract tests. Cut cosmetic polish, analytics and unverified language claims before cutting evidence, authorization or confirmation safeguards. If a release gate is missed, record the exact unsupported capability; do not silently redefine completion.

## Acceptance and blockers

Release acceptance: A exact money reconciliation and one confirmed VAS deactivation; D conflict blocks account mutation; B exact quota and negative out-of-bundle charge; C supplied incident without invented ETA; E captured payment is not credited; F missing activation evidence is not consent. Include reversals, late posting, partial pages, stale evidence, lost write response, duplicate confirmation, restart and CRM delivery outage.

Cross-account/run access must fail for cases, evidence, operations, receipts and Voice bindings. Review updates must be scoped, versioned and audited. Text remains usable without Voice/model access. Voice must pass multi-turn automated tests and a real microphone journey, including interruption and continuation by text. Display simulated integration everywhere relevant.

Current blockers/risks: The conversation controller, Resolve Voice bridge and combined customer/dashboard frontend are now integrated; earlier progress-log blockers describe their historical state. Actual model credentials/quota, physical microphone/Voice runtime qualification, six-scenario customer browser checks, native-language review and broader worker concurrency/upgrade qualification remain open. The 2026-10-03 audit found 12 unresolved defects; see [the detailed report](docs/audits/2026-10-03-resolve.md). No external Voice repository changes or verification were performed in this audit. No HUTCH integration access will be supplied; this is expected for the simulated environment. Final submission still requires the verified prototype/source/README, technical PDF, deck/demo, architecture, limitations, AI disclosure/token assumptions, lifecycle/Gantt and safe evaluator access.

## Progress log

| Date | Work | Verification | Remaining |
| --- | --- | --- | --- |
| 2026-10-02 | Audited both repositories, master architecture and submission materials; confirmed two-day/one-process choices | Clean Git/remote checks, existing Voice suite, ephemeral streaming reproduction, read-only database queries | All unchecked implementation tasks above |
| 2026-10-02 | Prepared owner plans, AGENTS instructions, API specification and proposed browser interruption event | Python/jsonschema checks: 96 schemas, 340 refs, 43 examples, 5 rejection cases, 26 operations; receipt digest; Voice schema/model parity; 34 local links all pass | Application tasks remain unchecked; publication commits are discoverable in each repository's Git history |
| 2026-10-02 | Harry started on local `ResolveDev` from `cba91f0`: fixture v2 corrections/checks, Alembic adoption plus domain lifecycle revision, FastAPI health/readiness | Fresh isolated PostgreSQL 18 bootstrap; fixture invariant SQL passes; Alembic fresh/repeated upgrade reaches `0002_domain_lifecycle`; five new tables exist; runtime role can append but not rewrite review history; app-role `/api/v1/readyz` returns 200; Resolve suite 5 passed; compileall passed | Reset/session and Voice-binding revocation; reversal/fault fixtures; readiness-after-seed marker; authentication, scoped APIs/business providers/actions/receipts/dashboard; Voice defects; UI and conversation modules |
| 2026-10-02 | H-02 session security endpoints implemented on `ResolveDev` | 10 tests passed; fresh isolated PostgreSQL verified real guest creation, hash-only token storage, customer login with fixed account scope, guest CSRF upgrade/rotation, agent scope, CSRF logout/revocation and post-logout denial | AuthContext for business APIs, guest conversation transfer, throttling and complete role/nested-object authorization |
| 2026-10-02 | H-03a account and ledger provider slice | 15 tests passed; isolated seeded PostgreSQL customer endpoint returned scoped account; provider calculation reports A `SUFFICIENT` at 42,000 minor LKR and D `CONFLICTING` with -7,000 delta; partial source stays `PARTIAL`; unsafe JSON money integer is rejected; compileall/diff checks pass | Provider fault controls, recharge/VAS/usage/service providers and remaining business APIs |
| 2026-10-02 | H-03b persisted case and investigation facade | Fresh isolated DB upgraded to `0003_case_investigations`; runtime role created a scoped conversation/case, persisted A evidence/calculations/audit, replayed a stable command key without a duplicate, rejected a stale version, returned D as `REVIEW_REQUIRED` at -7,000, and hid A from account D with 404 | Public Tevin conversation routes, source-fault execution, proposal/confirmation/receipt APIs and full provider set |
| 2026-10-02 | H-03c case API surface | 16 tests pass; isolated fresh DB tested `GET /cases/{id}` and `POST /cases/{id}/investigations` response validation, customer reports, stable request replay, changed-body conflict, and customer cross-account 404/agent same-run access | Conversation routes, broader provider faults/paths, proposals/confirmed operations/receipts and review APIs |
| 2026-10-02 | H-05a agent dashboard queue/detail/review APIs on `ResolveDev` | 19 tests passed; compileall and diff checks passed; runtime OpenAPI exposes all three dashboard routes. Prior isolated PostgreSQL service checks verified cursor integrity/pagination, review replay/conflict, transitions and audit persistence. | H-05b ticket review synchronization; broader concurrency/restart qualification; customer and agent frontend; conversation routes and remaining complaint/provider-fault paths |
| 2026-10-02 | H-05b review-ticket sync on `ResolveDev` | Fresh migration `0005_review_ticket_sync`; isolated app/sandbox PostgreSQL roles; committed-response-loss fault produced UNKNOWN, retry with same event key produced SYNCED, one ticket version/note update, and idempotent review replay returned SYNCED. Generated run exposed UUID-suffix fault selection; fixed to use stable SIM line alias. | Multi-worker/restart stress, explicit unavailable/rejected sync outcome browser handling; customer/agent frontend and conversation routes |
| 2026-10-02 | H-06a quota reconciliation core on `ResolveDev` | 22 tests passed; compileall and diff checks passed. Pure deterministic tests verify byte snapshots against ordered bucket postings, consume-to-usage correspondence, safe separation of out-of-bundle usage and partial/conflicting outcomes. | Provider query, persisted DATA_DEPLETION investigation/API integration, recharge fulfilment (E), connectivity (C), activation (F), ticket sync and remaining fault profiles |
| 2026-10-02 | H-06b DATA_DEPLETION provider/API path on `ResolveDev` | Fresh isolated PostgreSQL 18 migration and synthetic run; provider reads selected quota snapshots/entries/usage; complete facade investigation persisted as SUFFICIENT; API response model validated; asserted 20 GB quota reaches zero and separate -8,000 minor LKR charge reconciles to 2,000; fixture invariant SQL passes; opt-in PostgreSQL test added | H-05b ticket status synchronization; B fault profiles; E recharge fulfilment; C service/incident checks; F activation evidence; broader restart/concurrency verification |
| 2026-10-02 | H-06c CONNECTIVITY investigation on `ResolveDev` | Fresh isolated PostgreSQL seeded C integration verifies active package, fresh account provisioning check and matching regional degraded incident; persisted investigation response validates; no ETA fabricated. Unit tests verify empty/stale feeds and failing checks. | H-05b ticket status synchronization; B fault profiles; E recharge fulfilment; F activation evidence; broader restart/concurrency verification |
| 2026-10-02 | H-06d E recharge investigation on `ResolveDev` | Isolated PostgreSQL B/C/E integration tests pass; seeded E's captured payment and pending fulfilment are explicit evidence with no linked money entry; API response validates and duplicate payment advice is suppressed. Snapshot duplicate handling now flags only same-sequence value/currency conflicts. Full suite 30 passed, 3 opt-in integration tests skipped by default. | H-05b ticket status synchronization; B fault profiles; F activation evidence; broader restart/concurrency verification |
| 2026-10-02 | H-06e F VAS activation evidence on `ResolveDev` | Four seeded PostgreSQL integration tests pass; F returns PARTIAL with explicit absent activation evidence and no consent claim; current recurring VAS still permits proposal for a future stop, without confirmation/mutation, and carries the historic-dispute boundary | H-05b ticket status synchronization; B fault profiles; remaining activation/recharge provider failures; broader restart/concurrency verification |
| 2026-10-02 | H-06f Money ledger integrity on `ResolveDev` | Unit checks enforce duplicate reference and opposite amount/currency reversal links; fresh isolated PostgreSQL migration and all four B/C/E/F persisted investigation integration tests pass after provider shape update | Seeded late/duplicate/reversal fault execution and operation recovery/restart/concurrency qualification; H-05b ticket sync; broader remaining provider failures |
| 2026-10-02 | H-06g charging/usage fault execution on `ResolveDev` | Disposable PostgreSQL migrated through `0005`; one-shot late/missing-opening charging and incomplete-page/stale/wrong-unit usage profiles were consumed. Incomplete/stale evidence returned PARTIAL; duplicate reference, reversal mismatch and wrong unit returned CONFLICTING; temporary volumes removed. Provider fault tests passed. | CRM outage/rejection/lookup profiles and broader action/review-worker qualification |
| 2026-10-02 | H-04c worker recovery/concurrent claim on `ResolveDev` | Fresh disposable PostgreSQL migrated through `0005`; VAS provider commit followed by lost response was recovered after a fresh runner reclaimed an expired lease, yielding one mutation/event/receipt. Two concurrent runners claimed a CRM ticket action once; exactly one ticket, provider operation and receipt. Both integration tests passed and the temporary volume was removed. | Broader multi-process/restart stress, review-sync contention, remaining provider faults and frontend/conversation work |
| 2026-10-02 | H-09a HTTP request observability on `ResolveDev` | Four middleware tests and four app health/logging tests pass; verifies route-template logging, request ID response correlation, stable error code and omission of request data/exception text. Compileall and diff checks pass. | Provider/action operation correlation fields and protected diagnostics |
| 2026-10-02 | H-04d action fault and H-05c review-sync qualification | Disposable PostgreSQL migrated through `0005`; 3 action-fault tests pass (CRM outage recovery, VAS rejection/no mutation, lost response + unavailable lookup + same-key success with one mutation/event); 2 review-sync tests pass (fresh-runner recovery and live-lease exclusion, one ticket update); 6 quota/provider integration tests pass. Full unit suite: 37 passed, 11 opt-in skipped. | Resolve conversation controller/Tevin integration, Voice bridge/qualification, broader soak and schema-upgrade test, both frontends |
| 2026-10-02 | H-09b worker operation observability | Focused HTTP/worker observability checks 11 passed; full unit suite 40 passed, 14 opt-in PostgreSQL skipped; compileall and diff check pass. Logs include scoped worker correlation IDs, attempts, outcomes and duration without customer/provider payloads. | Broader protected diagnostics/metrics and production telemetry integration |
| 2026-10-02 | H-07a Resolve-owned Voice bridge | Ten focused bridge tests pass; disposable PostgreSQL 18 fresh volume upgraded through `0005`; runtime role provisioned a session-bound binding, processed and replayed one signed turn without a duplicate, persisted integration/turn records, and denied a new callback after logout revocation. Full suite: 51 passed, 15 opt-in PostgreSQL tests skipped; compileall and diff checks pass. | Tevin's real ConversationService and live dialog/result integration; Voice fake-runtime work is underway on the verified `adapter_buildation` branch. |
| 2026-10-02 | H-08a Voice fake-runtime hardening | On `hutch_zeptazvoice/adapter_buildation`, commit `a6c3ea3` is pushed and remote hash confirmed. Full Voice suite 26 passed; compile and diff checks pass. Synthetic runtime coverage includes finished transcription independent of model completion, deferred tool routing, repeated turns, proposal acknowledgement/interruption invalidation, bounded end-session, disconnect/provider failure, and cleanup. | Live Gemini/microphone/Resolve path, remaining voice safety matrix, browser playback queue flush, and real Tevin controller integration |
| 2026-10-03 | Combined frontend intake on ResolveDev | Selective UI integration from `HutchChat` and `VoiceFrontend` was committed and remotely confirmed as `f2c9bab`. `npm ci`, OpenAPI generation, typecheck and build pass; lint exits 0 with warnings. | Actual browser/service qualification and UI acceptance matrix |
| 2026-10-03 | Conversation and Resolve Voice integration | Revision 0007, text routes, mounted dialogue controller, session-scoped conversation creation replay, guest-to-customer public history transfer and durable signed Voice projection are implemented. Full default suite 420 passed/34 opt-in skipped; five disposable PostgreSQL tests pass, including Voice proposal/presentation/accept/replay. Contract example parity has no expected failures. | Real model and browser microphone call, six-scenario UI journey, native-language human review and release matrix. External Voice repo was not changed in this phase. |
| 2026-10-03 | ResolveDashboard frontend intake | Selective import from `origin/ResolveDashboard` `6fe6acb` preserved current chat/Voice and backend. New agent queue, case packet tabs and versioned review UI use existing agent APIs. Found and fixed stale review acknowledgement and nested-route keyboard navigation through Playwright. Typecheck/build pass; lint exits 0 with warnings; 15 mock browser checks pass. Unique disposable PostgreSQL project migrated through 0007 and seeded six cases; five live dashboard browser checks pass, including review conflict, CSRF and role isolation. Test project and volume removed. Backend suite remains 420 passed/34 opt-in skipped. | Actual microphone/model call, six-scenario customer browser release checks and accessibility review. |


## Current audit follow-up ? 2026-10-03

Source: [Resolve integration audit](docs/audits/2026-10-03-resolve.md), baseline `2b52b722e1a9bb6f0149d767a2ecaf28831b0298` on ResolveDev. Audit only; no fixes implemented. The report distinguishes API/unit reproductions from static findings.

- [x] AUD-01 Tevin: deterministic case/action/financial replies bypass rewriting; signed decimal, outcome polarity and supported-link regressions added. Unit/conversation verification passes; real-model qualification remains open.
- [x] AUD-02 Harry: investigation writes now use customer Origin/CSRF guard; API regression tests pass.
- [ ] AUD-03 Harry/Tevin/Jayith: atomic expected-version claim and persisted text resume are implemented. Abandoned Voice and legacy text claims fail closed, but still require an authorized durable reconciliation policy; later turns can remain blocked. PostgreSQL race/restart checks are unavailable.
- [x] AUD-04 Jayith: late microphone permission and repeated cleanup are guarded; focused fake-media tests pass. Real browser microphone qualification remains open.
- [ ] AUD-05 Harry/Jayith: idempotent escalation route and reason persistence are implemented with route tests; database persistence and CRM delivery/replay remain PostgreSQL-gated.
- [x] AUD-06 Harry: explicit `X-Resolve-Realm` selection and no cross-cookie fallback; auth tests pass.
- [ ] AUD-07 Harry/Tevin/Jayith: Voice operation IDs/status projection and multi-operation client tracking are implemented; PostgreSQL/browser integration remains outstanding.
- [ ] AUD-08 Harry: review-event backfill on CRM ticket delivery is implemented; lost-response/order/worker recovery checks remain PostgreSQL-gated.
- [ ] AUD-09 Harry: aggregate case status derives from evidence, active operations and review state; concurrency/closure ordering remains PostgreSQL-gated.
- [ ] AUD-10 Harry: scoped idempotent Voice grant provisioning and encrypted grant replay are implemented; concurrent/lost-response integration remains PostgreSQL-gated.
- [x] AUD-11 Harry: safe 500 envelope, request correlation and redacted exception logging; regression test passes.
- [ ] AUD-12 Harry: shared PostgreSQL fixed-window throttles, trusted-proxy policy and migration are implemented; persistence, multi-worker isolation and migration upgrade remain PostgreSQL-gated.

Audit-fix checkpoint 2026-10-03: full default backend suite **438 passed, 35 skipped**; all skips require disposable PostgreSQL. Guard-specific suite **56 passed, 1 skipped**. Python compileall and `git diff --check` pass. Frontend typecheck/build pass, and the final mock e2e suite passed **18/18** after request-timeout hardening. `/readyz` now reports text/action/Voice/model capability, and ACCEPT is rejected before persistence when action execution is unavailable. Contract/OpenAPI docs updated and generated types refreshed. Docker engine access is denied in this environment, and no disposable database URL is configured; do not treat migration, transaction, locking, worker-recovery, or outbox checks as complete. The conversation recovery blocker and live model/microphone/browser gates remain open. No commit/push has been made for this checkpoint yet.
