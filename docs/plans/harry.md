# Harry: Resolve backend and external Voice integration

Read [context.md](../../context.md) and [contracts](../contracts.md) first. Own all Resolve APIs and permissions, provider and business logic, persistence, dashboard data and Voice qualification. Tevin owns dialogue; Jayith owns UI. Update this plan and context after meaningful progress, including test evidence and remaining work. Unchecked means not implemented and verified.

## Starting point and implementation boundaries

Reuse PostgreSQL, schemas 001-003, seed/reset tooling, six synthetic scenarios, twelve knowledge cards, and the Voice adapter/client/grant store. There is no Resolve runtime. Voice has 15 passing tests but a reproduced transcript-finalization failure and one-model-turn session termination. Do not mark Voice complete from the existing suite.

Keep one application process. Suggested module ownership: `backend/resolve/{api,auth,contracts,services,providers,persistence,integrations,worker}` belongs to Harry; `backend/resolve/conversation` belongs to Tevin. Harry composes routers/dependencies and exports DTOs/facade. No HTTP call to the same process between dialogue and services. External Voice stays in its existing repository.

## H-01: database and fixture baseline

- [x] Establish Alembic lifecycle without replaying CREATE TABLE over data. Validate schemas 001-003 before baseline adoption; on a fresh Compose database, upgrade through the same migration chain to `0002_domain_lifecycle`. Repeated upgrade is idempotent.
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
| Cases | `updated_at`; complaint/status validation; distinct review status NEW/IN_REVIEW/CLOSED; composite scope/ownership constraints |
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
- [ ] Implement opaque hashed sessions, separate customer/agent cookies, Origin/CSRF checks, 30-minute expiry, logout/revocation, and synthetic login mapping. Caller-provided roles/account IDs never establish authorization.
- [ ] Add guest FAQ sessions; upgrading a guest rotates the token, scopes its existing conversation to the authenticated run/account, and preserves only public history. Changing an already private identity creates a new session/conversation.
- [ ] Authorize every entity read/write, including nested IDs. Same-run membership alone is insufficient for customer access: conversation must belong to the current session. Agent access is run-scoped. Distinguish forbidden role (403) from inaccessible entity (404).
- [ ] Publish shared Pydantic DTOs, scoped conversation/knowledge repositories and ResolveFacade per contracts; validate parity with documented OpenAPI. Tevin can use interface fakes before repositories are complete.

Facade methods: `get_account`, `create_case`, `get_case`, `investigate`, `propose_action`, `confirm_action`, `prepare_escalation`, `get_operation`, `get_receipt`. Every method receives authenticated context. Context carries request/session/principal/role/account/run and channel; no model-supplied permission fields.

## H-03: providers and primary investigation

- [ ] Implement separate Customer/CRM, Charging, Recharge, Product/VAS, Usage/Quota and ServiceAssurance provider ports in-process. Investigation code depends on ports, not sandbox SQL.
- [ ] Implement bounded reads with source metadata, complete pagination, fixed simulation clock, event/posting times and explicit units. Source read timeout is two seconds with at most one safe retry; missing/stale/partial results remain visible.
- [ ] Persist cases and immutable investigation revisions. Support a maximum 30-day window; require clarification for critical missing dates/amounts. Evidence states are SUFFICIENT/PARTIAL/CONFLICTING, not percentages.
- [ ] Implement A/D from records: opening plus committed signed postings within account/wallet/currency/sequence boundaries; capture payment separately; count linked charges/reversals once. D's -7000 difference blocks account mutation.
- [ ] Snapshot evidence IDs, source ID/version, observation/fetch time, values/units and source payload. Produce deterministic finding codes/calculations, eligible actions and review reasons. Reinvestigation invalidates affected proposals.

Acceptance: modifying seed values changes calculated outcomes; missing opening or incomplete pages cannot produce a conclusive ledger tie-out. A reconciles; D conflicts. Balance explanation alone never establishes consent/refund eligibility.

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

- [ ] Queue `GET /agent/cases` with review-status, complaint-type, evidence-state, delivery-state filters; case-ID or exact synthetic-line search; opaque cursor; newest update then ID ordering.
- [ ] Detail endpoint returns synthetic identity, issue/window, relevant conversation, evidence revisions/source freshness, calculations/conflicts, proposal/confirmation/operation history, receipt revisions, ticket delivery and internal review notes.
- [ ] Versioned PATCH appends a note and optionally changes review status. NEW -> IN_REVIEW -> CLOSED; CLOSED -> IN_REVIEW requires reopening reason. Closing requires a disposition and explanatory note. Notes-only patches increment case version. Return updated review record/version.
- [ ] Keep customer case status, investigation state, operation state, provider ticket status and agent review disposition distinct. Closing a review does not alter evidence or account state.
- [ ] Persist local review history and audit atomically. If a mock ticket exists, queue its status/note synchronization using a stable provider key derived from review event ID; show pending sync rather than claiming CRM success. This agent-only administrative update cannot trigger charging/product mutations.

Acceptance: customer/guest denied; wrong run denied; stale PATCH returns 409; simultaneous agents do not overwrite notes; review history survives restart; dashboard shows a pending handoff without needing CRM availability. No analytics/admin portal beyond this scope.

## H-06: remaining complaint and fault paths

- [ ] B: quota grant/consume/expire/reverse accounting per bucket; usage explanatory only; out-of-bundle charge is separate and negative.
- [ ] C: account/package/quota checks, supplied service checks and matching fresh incident; no invented ETA or healthy-service inference from an empty feed.
- [ ] E: captured/pending fulfilment is not credited money; never suggest another recharge as recovery.
- [ ] F: activation evidence missing; future deactivation and past dispute have separate outcomes.
- [ ] Execute existing fault-profile configuration: late/duplicate posting, reversal, missing opening, partial page, stale source, wrong unit, CRM outage, rejected mutation, lost response and failed operation lookup. Expose control only through operator tooling/test fixtures.

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
| 2026-10-02 | H-01 fixture corrections + H-01 migration phase + H-02 readiness starter | Fixture v2 SQL assertions pass on isolated bootstrap; Alembic fresh upgrade and repeated upgrade both pass at `0002_domain_lifecycle`; app-role `/api/v1/readyz` returns 200; PostgreSQL confirms all five new tables and denies UPDATE on review history while allowing INSERT; tests 5 passed, compileall and diff check pass | H-01 reset/session and Voice-binding revocation, reversal fixture, readiness-after-seed marker; H-02 authentication/facade/repositories and consistent errors; H-03 onward |

Work order and time boxes are in context.md. Harry owns the critical path; publish interfaces early and integrate one vertical text slice before secondary features.
