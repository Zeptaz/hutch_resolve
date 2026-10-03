# Shared implementation contracts v1.2.0

**Mixed implementation status.** Resolve implements health/readiness, session lifecycle, scoped case APIs, deterministic investigation and action services, receipts, agent review, text conversation routes and the signed Voice bridge. The conversation controller is mounted in the same process and uses Resolve's persisted turn claims and facade. The combined customer chat/call and agent frontend builds. PostgreSQL verifies text replay, guest upgrade, action/worker recovery, dashboard review, package/quota behavior and signed Voice consent. Agent-authorized stalled-turn reconciliation is defined below and covered on disposable PostgreSQL. Real browser microphone/model qualification and release soak remain open. See [OpenAPI 3.1](contracts/openapi.json). [Examples](contracts/examples.json) are synthetic design fixtures. Harry owns shared contracts; revise these documents before implementations diverge.

## Ownership and connections

One Resolve process hosts Tevin's conversation controller and Harry's business services/providers. Tevin invokes a typed in-process facade. Jayith owns one React app with customer and agent routes. Voice remains external.

| Connection | Development address | Authentication |
| --- | --- | --- |
| Browser -> Resolve | `/api/v1`; Vite localhost:5173 proxies to localhost:8080 | Customer/agent session cookie and CSRF |
| Conversation -> Resolve facade | In-process async methods | AuthContext from middleware or validated Voice binding |
| Resolve -> PostgreSQL | localhost:55432/hutch_resolve | Resolve role; separate sandbox-provider role |
| Resolve -> Voice | VOICE_BASE_URL default http://localhost:8088 + /api/hutch/sessions | HMAC-SHA256, same event ID/body on one retry |
| Voice -> Resolve | HUTCH_RESOLVE_BASE_URL includes /api/v1 | HMAC plus stored binding scope |
| Browser -> Voice | Returned absolute WebSocket URL | Exact Origin, single-use grant subprotocol |

Use localhost consistently for the UI origin. Deployment uses same-origin HTTPS routing and external WSS. One Resolve worker/poller and one Voice worker suffice for the hackathon. No separate chatbot service, internal loopback HTTP, Redis or event broker.

## Authentication and identifiers

Opaque random session credentials are stored hashed. Cookies: `resolve_customer_session` and `resolve_agent_session`, HttpOnly, SameSite=Lax, Secure under HTTPS. Separate cookies allow agent/customer testing in one browser. Session GET returns a separate CSRF token; cookie mutations require `X-CSRF-Token` and an exact allowed Origin. No permanent browser token in localStorage or URLs.

Shared customer/agent reads use `X-Resolve-Realm: customer|agent`. If exactly one session cookie exists, the server may infer that realm; if both exist and the header is absent, it returns `400 AUTH_REALM_REQUIRED`. Invalid values return `400 AUTH_REALM_INVALID`; a selected invalid or expired session never falls back to the other cookie. The React transport sends the realm on every request.

Anonymous creation and login require exact Origin, JSON content type and throttling. They are exempt from CSRF only when not upgrading an existing session; guest upgrade also requires its CSRF token. Environment-configured demo identities map to credential hashes and permitted synthetic account/run or AGENT role. Login never accepts arbitrary role/account IDs. Sessions expire after 30 minutes.

Roles: GUEST (public FAQ), CUSTOMER (own session/account), AGENT (review data in assigned run), SIMULATOR (operator CLI only). AuthContext is `{session_id, principal_id, role, sandbox_id?, account_id?, request_id, channel}`; callers/models cannot supply trusted context. Guest upgrade rotates credentials and atomically scopes its public conversation. Changing an already private identity creates a new session/conversation. Logout/reset/expiry invalidates Voice callbacks. Retained private history is not automatically attached to a new login.

Check ownership inside every facade/API read and write. A customer conversation belongs to the current session; cases and nested evidence/operation/receipt IDs must match its account/run. AGENT permission is run-scoped, not global. Route-role denial is403; inaccessible entity404. HMAC authentication alone cannot authorize an unrelated binding.

Resolve generates session/conversation/case/investigation/proposal/operation/receipt/binding IDs as UUID strings. Clients generate stable UUID client_turn_id and Idempotency-Key. Existing Voice models accept string IDs; Resolve generates UUID-form values and validates persisted mappings. Text/speech never establishes identity.

## Common data, errors and concurrency

- RFC3339 UTC timestamps; UI displays Asia/Colombo. Existing Voice session expiry stays Unix seconds. Simulation clock applies to business records, real time to auth/confirmation expiry.
- Money is signed integer minor LKR (100 = LKR1); quota integer bytes. Public JSON numbers must fit JavaScript safe integers; reject overflow. Display decimal GB.
- Languages en/si/ta; text max4000; investigation window max30days; agent note max2000. Synthetic results have simulation=true.
- Versions begin at1. Mutable writes include expected_version. Lists use `{items,next_cursor}`, default limit25/max100; opaque cursor binds filters/order. Provider pagination is separately100/max500.
- DTOs reject unknown fields except immutable source payload/telemetry maps explicitly allowing them.
- Mutations require Idempotency-Key except session lifecycle, messages (client_turn_id) and Voice callbacks (event/turn IDs). Browser Idempotency-Key values are UUIDs. Persist subject+route+key, canonical request fingerprint and result. Matching retry replays response; changed body409. Authenticate scope before replay; check replay before stale-version rejection. Voice grant replay returns the same encrypted-at-rest, short-lived grant; expired or uncertain grants cannot be minted again under the same key.
- Persist one active turn claim per conversation before remote/model calls, without holding locks during calls. Identical in-progress turn returns409 TURN_IN_PROGRESS/retryable=true. Other concurrent turns return CONVERSATION_BUSY or STALE_VERSION. An expired text claim can be retried with its original downstream key; a Voice claim is never automatically replayed without fresh signed evidence. An agent may explicitly reconcile a stalled claim only after confirming no related operation is PENDING, RUNNING or UNKNOWN. Reconciliation is audited and terminal: the same turn returns409 TURN_ABANDONED and cannot be replayed; the customer starts a new turn.

Error envelope: `{error:{code,message,retryable,request_id,details}}`; return X-Request-Id. Details contain safe validation/current-version information, never another account's data. Unhandled exceptions return `500 INTERNAL_ERROR` with this envelope; exception messages and customer/provider payloads are not exposed. Investigation writes require Origin and CSRF. Anonymous creation/login use shared PostgreSQL fixed-window buckets; defaults are 30 anonymous creations/client/10 minutes, 30 login attempts/client/10 minutes and 10/client+identity/10 minutes. `TRUSTED_PROXY_IPS` is empty by default; a single sanitized X-Forwarded-For address is honored only from an exact listed proxy IP. Rate-limit failures return `429 RATE_LIMITED` with `Retry-After: 600`.

| HTTP | Codes | Handling |
| --- | --- | --- |
| 401 | UNAUTHENTICATED, SESSION_EXPIRED, INVALID_SERVICE_SIGNATURE | Reauthenticate |
| 403 | ROLE_FORBIDDEN, CSRF_FAILED, ORIGIN_FORBIDDEN | Correct permission/context |
| 404 | RESOURCE_NOT_FOUND | Hide inaccessible entity details |
| 409 | STALE_VERSION, IDEMPOTENCY_CONFLICT, PROPOSAL_INVALIDATED, TURN_IN_PROGRESS, CONVERSATION_BUSY, TURN_ABANDONED, TURN_ALREADY_SETTLED, TURN_OUTCOME_UNRESOLVED | Refresh/clarify; same-ID retry only when retryable; reconcile unresolved operations before settling a stalled turn |
| 422 | VALIDATION_ERROR, ACTION_NOT_ALLOWED, PROPOSAL_EXPIRED, CONFIRMATION_REQUIRED | Fix input/request new proposal |
| 429 | RATE_LIMITED | Honor Retry-After |
| 503 | DEPENDENCY_UNAVAILABLE, ACTION_EXECUTION_UNAVAILABLE | Safe fallback; bounded idempotent retry. An accepted action is never persisted when its worker/writer is unavailable. |

PARTIAL/CONFLICTING investigations are valid200 results. Accepted durable operations return202; acceptance never claims completion.

## HTTP surface

All paths use `/api/v1`; OpenAPI defines exact fields and response models. Session responses are no-store. Session/auth/integration/domain/dashboard handlers belong to Harry; conversation handlers belong to Tevin, using Harry's scoped repositories.

| Method/path | Request -> result |
| --- | --- |
| POST /sessions/anonymous | Empty JSON ->201 SessionView |
| POST /demo/sessions | demo_identity, credential ->200 SessionView |
| GET /session; DELETE /session | Customer cookie ->SessionView /204 |
| POST /agent/sessions | demo_identity, credential ->200 AgentSessionView |
| GET /agent/session; DELETE /agent/session | Agent cookie ->AgentSessionView /204 |
| POST /conversations | Customer/guest cookie, Origin, CSRF, UUID Idempotency-Key; language ->201 ConversationView, identical replay returns same ID, changed body409 |
| GET /conversations/{id} | Scoped ID ->conversation, messages, cases and pending state |
| POST /conversations/{id}/messages | client_turn_id, expected_version, language, input ->200 TurnResult |
| POST /conversations/{id}/turns/{turn_id}/resume | Customer/guest cookie, Origin, CSRF; no body ->200 canonical TurnResult. Resumes only an expired, persisted text turn using original normalized input and command keys. A live claim returns retryable 409; Voice consent is never reconstructed by the browser. |
| GET /account | No account selector ->scoped AccountView |
| GET /cases/{id} | Scoped ID ->CaseView/current investigation |
| POST /cases/{id}/investigations | expected_version, complaint_type, window_start/end, reported_facts ->200 InvestigationResult |
| POST /cases/{id}/action-proposals | Customer cookie, Origin, CSRF, Idempotency-Key; expected_version, investigation_id, action_type, target_id ->201 ProposalView |
| POST /action-proposals/{id}/confirmations | Customer cookie, Origin, CSRF; proposal_hash, ACCEPT/DECLINE, client_turn_id ->202 persisted PENDING operation or200 decline |
| GET /operations/{id} | Customer session or agent run scope ->OperationView |
| POST /cases/{id}/escalations | expected_version, investigation_id, reason ->201 CREATE_REVIEW_TICKET ProposalView |
| GET /cases/{id}/receipt | Customer session or agent run scope, optional revision ->stored ReceiptView |
| POST /conversations/{id}/voice-sessions | Implemented: customer session, Origin+CSRF, session/account/run-scoped binding, outbound Voice grant ->201 VoiceSessionGrant |
| POST /integrations/voice/turns; /events | Implemented: strict signed body, active binding scope and durable event/turn replay -> Voice result/ack through the mounted conversation service |
| GET /agent/cases | Implemented: filters, exact case/line alias search, signed cursor -> scoped queue |
| GET /agent/cases/{id} | Implemented: sandbox-scoped AgentCaseDetail |
| PATCH /agent/cases/{id}/review | Implemented: versioned/idempotent review and internal note; delivered mock ticket gets a durable sync job |
| POST /agent/conversations/{conversation_id}/turns/{turn_id}/reconcile | Implemented: AGENT cookie, exact Origin and CSRF required; note required; expired claim only; refuses unresolved related operations; audited terminal state; never replays the customer/Voice turn |
| GET /healthz; /readyz | Process/DB-migration readiness; `/readyz` reports text/actions/voice/model capability flags and returns 503 when PostgreSQL is unavailable. Text remains usable when optional Voice/model integrations are down; actions require the sandbox writer and worker. |

Case creation is an internal facade operation invoked by conversation intake. Public FAQ cannot create account cases. Health routes are under `/api/v1` in Resolve; Voice retains existing `/healthz`.

Turn input is a discriminated union: text(text); category_selection(complaint_type); complaint_details(complaint_type,window_start,window_end,reported_facts); action_decision(proposal_id,proposal_hash,decision); case_selection(case_id); package_query; package_selection(offer_id). Complaint types: BALANCE_RECHARGE, DATA_DEPLETION, CONNECTIVITY, VAS_DISPUTE, PACKAGE_ACTIVATION. Reported facts may include amount_minor, recharge_reference, subscription_id and description; they are customer reports, never source evidence.

TurnResult fields: message_id, conversation_id, conversation_version, nullable case_id, reply_text, cards[], citations[], nullable pending_question, operation_ids[], simulation. PendingQuestion includes code/text/allowed_input_types. Fixed cards: account, timeline, calculation, finding, confirmation, ticket, receipt, package_catalogue. No model HTML. ConversationView persists language, version, messages, cases, active_case_id, pending_question/proposal, operation_ids and expiry.

### Synthetic one-shot package action

`package_query` returns a fixed `package_catalogue` card with `offers[]`: `id`, `name`, integer `price_minor`, `currency=LKR`, integer `data_bytes`, `validity_seconds`, `recurring=false`, `recommended`, Resolve-computed `can_purchase`, and nullable `recommendation_reason`. Values come only from the synthetic sandbox catalogue and account. Deterministic ranking may personalize recommendations only from a complete 30-day usage window with explicit source coverage. The current seed schema has no usage coverage marker, so Resolve conservatively returns an unranked catalogue without a best-fit claim.

`package_selection(offer_id)` creates a `PACKAGE_ACTIVATION` case, evidence snapshot, and normal five-minute `ACTIVATE_PACKAGE` proposal. `ProposalView.package_terms` is required only for that action and contains the exact offer name, price_minor, currency, data_bytes, validity_seconds and recurring=false. Consequences must state the same offer terms, one MAIN debit, existing packages are retained, and auto-renewal is off. The user must explicitly accept or decline. Voice acceptance additionally requires playback and exact proposal presentation acknowledgements plus a fresh affirmative transcript.

The idempotent synthetic provider rechecks offer version, eligibility, available MAIN balance and duplicate active offer, then atomically writes one money debit, a new subscription, activation event, quota bucket/grant and provider operation record. It never replaces packages or auto-renews. Operations use the standard worker/readback/recovery path; terminal states create a Trust Receipt. `RESOLVE_PACKAGE_ACTIVATION_ENABLED=false` by default and requires the sandbox writer. `/readyz` reports `capabilities.package_activation`.

## Domain data and safe execution

SourceEnvelope: source, fetched_at, as_of, complete_through, source_version, complete, next_cursor, warnings[], data. Only a complete empty response means no records. Freshness assumptions: account/balance/subscription60s; service checks5m. Use simulation clock for synthetic observed records, actual time for fetch/auth metadata.

InvestigationResult: id, case_id, revision, complaint_type, window_start/end, evidence_state, findings, calculations, evidence, source_status, missing, conflicts, eligible_actions, review_reasons, created_at, simulation. EvidenceItem snapshots source/record/version, observed/fetched time, value/unit and immutable payload. Findings reference evidence IDs; calculations enumerate contributing IDs and units. Evidence state is SUFFICIENT/PARTIAL/CONFLICTING.

CaseView: id, conversation_id, account_id, complaint_type, status, review_status, version, timestamps, latest investigation, operation IDs and receipt reference. Case.status OPEN/AWAITING_CUSTOMER/ACTION_PENDING/REVIEW_REQUIRED/RESOLVED is independent of review.status NEW/IN_REVIEW/CLOSED. Resolve determines these values.

ProposalView: id, case_id, investigation_id, action_type, target_id/version, target_label, consequences, hash, expiry and simulation. `package_terms` is present for ACTIVATE_PACKAGE only. Five-minute hash-bound proposal also binds authenticated session and evidence revision; current target status/version is re-read before confirmation. Target is subscription for DEACTIVATE_VAS, account for SEND_SETTINGS_INSTRUCTIONS/CREATE_REVIEW_TICKET, and a purchasable synthetic offer for ACTIVATE_PACKAGE. Existing Voice gets its narrow proposal projection including typed package terms when applicable. Reinvestigation/changed target invalidates affected proposals.

Public confirmation uses an explicit authenticated decision. Internal Voice confirmation additionally includes original final transcript, fresh turn and presented ID/hash; Resolve owns the decision gate. Ambiguous yes or no valid presentation context cannot accept. Decline persists a confirmation record without an operation. Accept atomically persists confirmation and operation; the response reports `operation_status=PENDING`, never completion. Database uniqueness permits one operation per proposal. Both actions require customer Origin and CSRF validation.

Operation states: PENDING -> RUNNING -> SUCCEEDED/FAILED/UNKNOWN; UNKNOWN -> SUCCEEDED/FAILED/REVIEW_REQUIRED. Reconcile unknown at0/2/10seconds via provider lookup/readback. Persist lease/retry state, recover after restart, never mint a new provider key. No lock spans external calls; provider writes use a separate transaction.

Forward revisions `0002_domain_lifecycle` through `0009_package_activation` add scoped lifecycle, investigation, proposal/confirmation persistence, review sync, Voice consent fencing and dialogue state after baseline adoption. Revision 0004 binds proposals to sandbox, actor session, case version, evidence revision, request key/hash and target label; confirmation client turns and accepted operations are uniquely scoped. Revision 0007 allows public guest turn claims and adds dialogue state and model-call metadata. Confirmation and review history rows remain append-only. The runtime PostgreSQL role may append confirmations/review history/investigations but cannot update or delete their records. Accepted operations are durably `PENDING`. A lifespan-managed in-process worker claims action and review-sync jobs with leases, uses a separate sandbox database role and stable provider keys, records actual mock readback, retries UNKNOWN after 2/10 seconds and appends digest-protected receipts for terminal actions. CRM review sync writes a separate review object/note and does not change the CRM ticket's own status. Receipt, operation and agent review projections are scoped.

ReceiptView: id, case_id, revision, issued_at, issue, window, findings, calculations, evidence_references, missing, conflicts, actions, handoff, next_step, simulation, digest_sha256. Store append-only. Digest is SHA256 over UTF-8 JSON with sorted keys/no whitespace/integer numbers, excluding digest_sha256 itself. It is neither a signature nor proof of source truth. Internal notes are excluded from customer receipts.

Human-review request creates a CREATE_REVIEW_TICKET proposal; normal confirmation delivers it. Handoff queues BILLING_REVIEW/TECHNICAL_SUPPORT, with Resolve reference and nullable actual provider ticket ID. Delivery state PENDING/DELIVERED/FAILED/REVIEW_REQUIRED is independent of local review. Conflict still permits review/guidance. VAS deactivation does not close a historic charge dispute.

## Dashboard contract

Agent cookie/role/run required. GET queue filters: review_status, complaint_type, evidence_state, delivery_state, search(case UUID or exact synthetic line), cursor, limit. Queue membership is a case with status REVIEW_REQUIRED, an accepted escalation/delivery record, or an existing agent review event; a merely proposed/unconfirmed handoff does not add a case. Sort updated_at DESC then ID DESC. Rows contain case_id,line_alias,complaint_type,evidence_state,review_status,delivery_state,updated_at,version. No analytics/admin scope.

Review updates append local review history and audit first. If a delivered mock ticket is linked, Resolve enqueues one sync job per review event. Sync state is `PENDING`, `UNKNOWN`, `SYNCED`, `FAILED`, or `REVIEW_REQUIRED`; it is separate from the provider ticket's own status. The worker uses the review event ID as its stable provider idempotency key. No linked ticket yields `NOT_APPLICABLE`. The response is never reported as synchronized until the mock provider write is confirmed.

Detail returns case, account, conversation, investigations, proposals, confirmations, operations, receipts, handoff, review_notes and audit_events. Include source freshness and missing evidence. Agent-only source payload detail is not automatically exposed to customer responses.

PATCH review fields: expected_version, optional review_status/disposition/note/reopen_reason; at least note or status required. NEW -> IN_REVIEW -> CLOSED; CLOSED -> IN_REVIEW requires reopen_reason. Closing requires note and disposition REVIEW_COMPLETE/NEEDS_OPERATOR_FOLLOWUP/CUSTOMER_WITHDREW. Notes-only writes preserve status and increment case.version. Append actor/time/old-new version/status to immutable history. No customer action or financial adjustment is triggered by review status.

Commit local review/audit atomically. If provider ticket exists, queue its status/note update with a stable key from review event ID; expose pending sync rather than claiming CRM success. No ticket means no invented external update. All review notes are INTERNAL.

## In-process facade and events

Tevin exports `ConversationService.handle_turn(AuthContext, NormalizedTurn) -> TurnResult`. Text supplies expected conversation version; Voice bridge obtains conversation/account/session scope from the validated binding. For Voice, it passes `channel=VOICE`, stable turn/downstream UUIDs, transcript/language, and trusted VoiceConsentEvidence constructed only after HMAC and binding validation. The service's Voice result projection is `response_id`, optional `case_id`, `reply_text`, `speech_text`, optional `pending_question`, `proposal`, `operation_status`, and `end_session`. Browser body cannot set trusted channel/presentation fields.

Harry exports ResolveFacade methods create_conversation/get_account/create_case/get_case/investigate/propose_action/confirm_action/prepare_escalation/get_operation/get_receipt. The mounted conversation service uses scoped conversation storage, a bounded knowledge repository and the same persisted turn claims for text and Voice. `POST /conversations` requires a UUID `Idempotency-Key`; identical retry returns the same conversation, while a changed body conflicts. Guest login upgrade transfers public conversation history and completed turns to the new customer session. The single in-process worker claims leased operations and records sandbox results using separate provider credentials. Tevin owns dialogue logic; Harry owns storage/migrations.

| Internal method | Input after AuthContext | Result |
| --- | --- | --- |
| create_conversation | language | ConversationView; session scope comes only from AuthContext |
| get_account | No account selector | AccountView |
| create_case | conversation_id, expected conversation version, stable client_turn_id, complaint_type/window/reported_facts | CaseView; replay scoped originating turn rather than duplicate case |
| get_case | case_id | CaseView |
| investigate | case_id, expected case version, complaint/window, stable command key | InvestigationResult; replay returns stored revision |
| propose_action | case_id, ProposalRequest, stable command key | ProposalView |
| confirm_action | proposal_id, ConfirmationRequest, stable command key, optional trusted VoiceConsentEvidence | ConfirmationResult |
| prepare_escalation | case_id, EscalationRequest, stable command key | ProposalView |
| get_operation | operation_id | OperationView |
| get_receipt | case_id, optional revision | ReceiptView |

Stable command keys derive from conversation_id + originating turn_id + command name + target, not random values generated on each replay. VoiceConsentEvidence contains binding_id, voice_session_id, final transcript, presented proposal ID/hash and turn_id; only the authenticated Voice bridge constructs it. Conversation state stores pending_question, candidate complaint/reported facts, language, active_case_id and pending proposal reference; it never stores an independent business decision.

Internal events persist in PostgreSQL, not a broker. Envelope: event_id, event_type, schema_version=1, occurred_at, request_id, nullable conversation/case/investigation/operation IDs and typed payload. Types: investigation.completed, proposal.created, confirmation.recorded, operation.changed, escalation.delivery_changed, review.updated. Payload is the relevant entity/revision/status transition, not full private transcripts. Persist audit event with mutation. Browser uses HTTP polling, not SSE.

## Voice compatibility

The [Voice wire contract](https://github.com/Zeptaz/hutch_zeptazvoice/blob/voice_test2/docs/hutch-resolve-contract.md) defines the current voice repair branch. OpenAPI mirrors strict VoiceTurnRequest/Response/EventRequest. Do not append HTTP fields without coordinating strict-model compatibility.

Resolve session creation body: binding_id, conversation_id, voice_session_id, account_id, origin, expires_at(Unix seconds). Resolve stores the scoped binding before calling Voice and revokes it if grant provisioning fails. Binding max180s covers the 60-second browser grant window plus a full 120-second call; the browser grant itself is single use and origin/session bound. Voice returns browser_grant, websocket_path, websocket_url and expires_at. The current browser offers `zeptaz-hutch-v3` and `hutch-grant.{token}`; Voice still accepts v2 for old clients. Binary mono PCM16 in16kHz/out24kHz; existing16KiB frame/120s call/3.84MB audio limits.

Voice turn: binding_id, voice_session_id, event_id, turn_id, transcript, language, is_final=true, nullable presented_proposal_id/hash. Response: response_id, nullable case_id, reply_text, speech_text, nullable pending_question/proposal/operation_status, end_session. Detailed cards/receipts are fetched by browser from Resolve. Events connected/disconnected/error/usage carry binding/session/event IDs and details; Resolve returns accepted/event_id. Delivery is currently best effort; missing events do not prove call state. Signed callback bodies are capped at 16 KiB (413 otherwise).

HMAC headers: X-Voice-Timestamp, X-Voice-Event-Id, X-Voice-Body-Sha256, X-Voice-Signature. HMAC-SHA256 over UTF-8 `timestamp.event_id.body_sha256`; hash exact transmitted bytes; max60s skew. Header/body event IDs match. Resolve verifies signature and active binding scope before deduplication; same event/body replays result, changed body409. It deduplicates `turn_id` separately under conversation, leases a turn before the Tevin call, and reuses its persisted downstream key on recovery.

Voice uses a 2s connect/8s response timeout and an 18s total retry budget. It retries retryable `TURN_IN_PROGRESS`/`CONVERSATION_BUSY` and transient transport/5xx with the same body/event/turn IDs; a pending result retains those IDs for the next identical caller retry. Resolve returns within budget and does not wait for account execution. Model extraction uses a total6s budget including at most one repair; structured fallback handles exhaustion.

Browser protocol `zeptaz-hutch-v3` uses `{"type":"input_activity_start","segment_id":N}` after two 100 ms speech frames and `{"type":"input_activity_end","segment_id":N}` after seven quiet frames. Segment IDs increase for the call. The browser continues forwarding microphone PCM while a reply is pending or playing so a caller can interrupt. Voice immediately emits `interrupted` for the affected response and drops old PCM. `input_audio_end` is sent when the microphone is muted or paused; v2 retains its legacy behavior. Gemini automatic activity detection uses 700 ms silence by default. A finalized Gemini input transcription invokes Resolve once through the external adapter; a bounded queue preserves up to two later turns. A call arriving before or after the Resolve result receives that same result, including errors, unless cancelled. Voice waits 300 ms for a Gemini tool call before sending the result snapshot as a separate speech request. After a tool response, it requests snapshot speech only when the previous model turn completes. A per-turn snapshot supplies Resolve's reply, case, operation status, proposal and standing rules. Model PCM streams from its first valid frame, bracketed by `audio_start`/`audio_end` with `response_id`; the browser sends `playback_complete` after draining and receives `playback_ack`. `resolve_result` carries canonical `reply_text` for display. The Resolve HTTP Voice response still includes `speech_text` for compatibility but the browser ignores it. Generated speech may summarize the display text. Offers are accepted or declined only through visible buttons; Voice rejects `proposal_presented` and spoken yes/no alone carries no presentation evidence. Resolve remains the authority for consent and action execution.

The `greeting` message remains text. In the live customer UI, its fixed English text plays from a checked-in recording generated with the same Gemini Live `Kore` voice used for replies; it never uses browser speech synthesis or grants proposal consent. If the recording cannot load, the caption remains visible and the call continues. When text intent extraction times out, Resolve may answer a narrowly matched direct account-balance enquiry through its existing customer-scoped account read; this does not infer a balance complaint or create a case.

Voice emits `{"type":"interrupted","response_id":null}` (the affected response ID when known) before later output and clears proposal eligibility. The browser discards queued audio and suppresses pending acknowledgements. The v3 browser control change requires a coordinated frontend deployment; it changes no HTTP Voice turn/event schema.

For explicit end_session=true, Voice delivers the final grounded output, emits ended with reason resolve_requested after playback/turn completion or bounded timeout, then closes. Normal disconnect/text continuation never discards Resolve state. Fake runtime tests pass; live browser/model qualification remains open.

## Mock provider boundary

Typed in-process ports: get_account, get_statement, list_recharges, list_offers/subscriptions, list_usage, get_quota_statement, get_service_status, deactivate_vas, send_settings_instructions, create/get/update_ticket, get_operation and get_operation_by_key. No public `/sandbox` routes are needed. Fault/reset controls are operator CLI/test-only. Provider writes require stable idempotency key and expected target version; changed body/key conflicts; committed lost response remains unknown until lookup/readback. Vendor-neutral mocks are not HUTCH deployment/schema facts.

## Configuration and verification

| Setting | Location | Purpose/default |
| --- | --- | --- |
| DATABASE_URL / SANDBOX_DATABASE_URL | Resolve server | Separate Resolve/provider DB role DSNs |
| APP_ORIGIN | Resolve server | http://localhost:5173; exact HTTPS origin on deployment |
| DEMO_IDENTITIES_JSON | Resolve secret config | JSON map: identity -> `{credential_sha256, role, principal_id, sandbox_id, account_id?}`; credential hashes and fixed synthetic scope are server-side |
| APP_SECRET_KEY | Resolve secret config | Random secret >=32 bytes for CSRF derivation; replace the example value |
| VOICE_BASE_URL | Resolve server | http://localhost:8088 |
| VOICE_HMAC_SECRET | Resolve server | Random secret >=32 bytes; set the same value as Voice's `HUTCH_RESOLVE_HMAC_SECRET` |
| HUTCH_RESOLVE_HMAC_SECRET | Voice server | Same random secret as Resolve `VOICE_HMAC_SECRET`, minimum32 bytes |
| HUTCH_RESOLVE_BASE_URL | Voice server | http://localhost:8080/api/v1 |
| ZEPTAZ_PUBLIC_BASE_URL | Voice server | Reachable HTTP(S) Voice base for returned WS URL |
| HUTCH_VOICE_ALLOWED_ORIGINS | Voice server | Exact UI origins, no wildcard |
| HUTCH_VOICE_GRANT_SECRET | Voice server | Separate random grant secret |
| GEMINI_API_KEY / GEMINI_TEXT_MODEL | Resolve server | Access-verified text model configuration |
| GEMINI_API_KEY / GEMINI_LIVE_MODEL | Voice server | Existing live model configuration |
| LOG_LEVEL / OTEL_SERVICE_NAME | Both servers | Redacted diagnostics; no required collector deployment |
| VITE_API_BASE_URL | Browser build | /api/v1; no secret |

New settings are planned; implementation will update `.env.example`. This documentation task provisions no credentials. Contract gates: schema/example validation; runtime OpenAPI parity; shared facade tests against fakes/real services; strict Voice parsing; role/replay/stale-version tests; browser partial/pending states. Breaking changes require explicit contract version/update note and coordinated Voice deployment.
