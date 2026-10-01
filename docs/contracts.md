# Planned APIs — contracts only

Nothing in this document is served by this repository. These are the reviewed master plan interfaces for later Resolve application work; the current repo prepares their data and database only.

## Sandbox provider boundaries

The simulator is intended to run in process against `sandbox` tables behind typed provider interfaces. The following internal route shapes can be used by a later simulator/admin harness, but they must never be exposed as customer API routes.

| Method and route | Owner | Contract |
| --- | --- | --- |
| `GET /sandbox/v1/accounts/{id}` | Customer/CRM | Account/customer synthetic profile, region and target version. |
| `GET /sandbox/v1/accounts/{id}/statement?from=&to=&wallet=` | Charging | Opening and closing balance snapshots plus committed signed postings ordered by sequence. |
| `GET /sandbox/v1/accounts/{id}/recharges?from=&to=` | Recharge gateway | Payment and fulfilment separately, with optional credited posting ID. |
| `GET /sandbox/v1/offers`; `GET /sandbox/v1/accounts/{id}/subscriptions` | Product/VAS | Synthetic catalogue, subscription state, recurrence and activation evidence reference. |
| `GET /sandbox/v1/accounts/{id}/usage`; `GET /sandbox/v1/accounts/{id}/quota-statement` | Usage/quota | Usage windows, bucket snapshots and ordered grant/consume/expire/reversal movements. |
| `GET /sandbox/v1/accounts/{id}/service-status` | Service assurance | Supplied checks and matching regional incidents with observation/expiry times. Empty incidents do not assert healthy service. |
| `POST /sandbox/v1/subscriptions/{id}/deactivations` | Product/VAS | Confirmed eligible future renewal stop only; expected version and idempotency key required. |
| `POST /sandbox/v1/accounts/{id}/settings-requests` | Service assurance | Simulated delivery of settings instructions; never a network repair. |
| `POST /sandbox/v1/tickets`; `GET/PATCH /sandbox/v1/tickets/{id}` | Customer/CRM | Create/read mock review ticket; agent-only status/notes update is versioned. |
| `GET /sandbox/v1/operations/{id}`; `GET /sandbox/v1/operations/by-key/{key}` | Provider operation ledger | Actual simulator result and unknown-response recovery. |

Collection reads use cursor pagination, default 100 and maximum 500. Results should be wrapped as `{source, fetched_at, as_of, complete_through, source_version, complete, next_cursor, warnings, data}`. A partial page, stale source, unknown unit, missing opening snapshot or outage is incomplete evidence. Only an explicitly complete empty response means “no records.”

Writes require `Idempotency-Key` and `expected_version`. Same key and identical normalized body returns the saved result. Same key and different body returns `409`. A committed write with a lost response remains unknown until provider-operation lookup/readback resolves it; never mint a new key to retry. The customer cannot call these internal paths.

## Resolve customer and agent API (future)

All public paths use `/api/v1` and a session-derived subject; never accept an account ID from free text as authorization. Public FAQ needs no private account scope. Private records and all mutations authorize the current session for the case/account on every read and write.

| Method and route | Purpose |
| --- | --- |
| `POST /demo/sessions` | Controlled synthetic customer session. |
| `POST /conversations`; `POST /conversations/{id}/messages` | Start or continue text; message includes stable `client_turn_id`, text, language and expected conversation version. |
| `GET /conversations/{id}` | Resume messages, cases and pending operations. |
| `GET /cases/{id}`; `POST /cases/{id}/investigations` | Authorized case and a new immutable evidence revision after clarification/refresh. |
| `POST /cases/{id}/action-proposals`; `POST /action-proposals/{id}/confirmations` | Consequences proposal and record of fresh accept/decline. |
| `GET /operations/{id}` | Actual action state. |
| `POST /cases/{id}/escalations`; `GET /cases/{id}/receipt` | Persist review handoff and retrieve stored Trust Receipt JSON. |
| `POST /conversations/{id}/voice-sessions` | Create Voice binding via authenticated server-to-server request. |
| `POST /integrations/voice/turns`; `POST /integrations/voice/events` | HMAC-authenticated, event-ID-deduplicated Voice exchange and lifecycle callbacks. |
| `GET /agent/cases`; `PATCH /agent/cases/{id}/review` | Protected mock agent review queue and disposition; charging evidence is never edited. |

Message results carry IDs/version, optional case, text, typed cards, citations, pending question and operation IDs. Investigation evidence records source ID/version, observed time, value/unit and immutable source payload. Partial evidence is a valid result, not a diagnosis. Errors: 401 unauthenticated, 404 inaccessible, 409 stale version/key conflict, 422 invalid input and 503 dependency outage. Async writes return 202 plus operation ID.

Only `DEACTIVATE_VAS`, `SEND_SETTINGS_INSTRUCTIONS` and `CREATE_REVIEW_TICKET` may be proposed. No refund, credit, recharge purchase, SIM change or network repair. A proposal binds subject, case, investigation revision, exact target/version, consequences, expiry and hash. A fresh affirmative after presentation is mandatory. Receipt revisions are append-only and their SHA-256 digest is neither a signature nor proof of source truth.

## Voice-specific contract

The external Voice session/request/response models and HMAC headers are specified in [the Voice contract document](../../hutch_zeptazvoice/docs/hutch-resolve-contract.md). Resolve owns `voice_bindings`, event replay records, conversation scope and action policy. The new Voice adapter calls future Resolve endpoints only; no Resolve API implementation exists here.
