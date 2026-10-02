# Mock HUTCH environment

## Current qualification note (2026-10-02)

The running database is healthy, but this document's original fixture/readiness descriptions are intended outcomes, not a complete validation record. The [team context](../context.md) records confirmed B debit-sign and E duplicate-opening defects, other fixture inconsistencies, and readiness/reset lifecycle work under H-01. Fault profiles configure future behavior; no simulator runtime currently executes them. Use the [current shared contracts](contracts.md) for implementation.

## Deployment

```mermaid
flowchart LR
  Scripts[PowerShell start / reset] --> Compose[Docker Compose]
  Compose --> PG[(PostgreSQL 18)]
  PG --> S[sandbox schema: synthetic provider-owned records]
  PG --> R[resolve schema: Resolve-owned persistence]
  R --> API[FastAPI: sessions, account read, health]
  API --> P[In-process sandbox provider and ledger calculator]
  P --> S
```

One local database models provider ownership by schema and table contract. The container health check waits until migrations and initial fixtures finish, rather than only waiting for PostgreSQL's temporary bootstrap server. There are no separately deployed CRM, charging, or network services; the starter Resolve app reads the synthetic data through in-process provider adapters.

## Sources and assumptions

The public evidence and limits below follow the reviewed architecture research. HUTCH publicly describes customer self-care, usage history, plan activation, bill payments and complaints ([HUTCH self-care](https://hutch.lk/hutch-self-care/)); its online recharge portal describes recharge, package activation and transaction results ([online recharge FAQ](https://hutch.lk/faq-online-web-recharge/)). Public evidence does not disclose the internal APIs or current schemas. Historical vendor references in the master plan are not grounds for treating any vendor as current. Every row here is generated synthetic data and vendor neutral.

Telecom-style boundaries for CRM/tickets, charging, recharge fulfilment, catalogue/VAS, quota and service assurance are **industry-based assumptions**. The prepaid-only scope, tax-inclusive synthetic prices, regional incident mapping, timeouts, retention and fault types are simulation choices, not statements about HUTCH policy.

## Tables and relationships

`sandbox.sandbox_runs` owns each isolated fixture set. Customers own prepaid accounts; accounts relate to balance snapshots and ordered money postings, recharge/payment and credit references, offers and subscriptions, subscription events, quota buckets/snapshots/entries, usage records, checks, incidents by region, and CRM-style tickets. Provider operations store idempotency outcome and fault profiles select simulator failure behavior. Composite sandbox foreign keys prevent cross-run references.

`resolve` contains persistence for scoped conversations/messages, versioned cases, immutable investigation evidence/calculation revisions, action proposals, confirmations and operations, append-only receipts, Voice bindings/events, audits, model usage and reviewed knowledge cards. Alembic revision `0002_domain_lifecycle` adds explicit sandbox retirement, GUEST/session metadata, language/dialogue state, leased turn claims, one-operation-per-proposal, persisted idempotency outcomes, internal review history and escalation delivery. Revision `0003_case_investigations` stores originating-turn hashes, reported facts separately from evidence, investigation windows, provider completeness and immutable result data. Revision `0004_action_proposals` binds proposals and confirmations to sessions, evidence revisions, target versions and stable client request keys. New relations carry same-run ownership constraints, and the runtime role can append but not rewrite confirmation/review/investigation history. The backend implements health/readiness, session lifecycle, scoped account/case reads, public balance investigations, proposals and confirmations, a leased mock-operation worker, operation/receipt reads, and in-process conversation/case creation. Provider writes use `SANDBOX_DATABASE_URL` and the sandbox role; omit this setting only to keep confirmed work safely pending. Twelve English knowledge cards cite public HUTCH pages and keep limits explicit; English search terms include curated Sinhala/Tamil transliterations.

Amounts are signed `bigint` minor LKR units (100 = LKR 1.00); usage and quota use integer bytes. Persist event occurrence separately from posting/recording time. IDs are UUIDs and mutable targets carry versions. The fixture clock is 2 October 2026, 12:00 Asia/Colombo; security grants, session and action expiries use real time.

## Fixture index

| Case | Seed evidence | Expected safe interpretation |
| --- | --- | --- |
| A | LKR 0 opening; +1,000 recharge, −499 package, −60 VAS, −21 rated use; LKR 420 close | Exact ledger tie-out. A future VAS deactivation can be proposed; historic dispute remains review work. |
| B | 20 GB grant, 11.4 + 5.2 + 3.4 GB consumed, zero remaining; LKR 100 opening balance minus LKR 80 out-of-bundle charge gives LKR 20 | Quota movements explain exhaustion; don't count usage or its linked money posting twice. Usage category is unspecified. |
| C | Synthetic 10 GB offer/grant, 0.3 GB consumed and 9.7 GB remaining, fresh data-enabled check, South region mobile-data incident | Report only supplied evidence; no repair claim or invented ETA. |
| D | Same postings as A, closing snapshot LKR 350 | LKR 70 conflict blocks a conclusive diagnosis and account mutation; route for review. |
| E | LKR 500 payment captured, fulfilment pending, no credit entry; balance LKR 100 | Payment is not an account credit. Do not request another payment/recharge. |
| F | Active recurring VAS, posted charge, no activation evidence | Report missing evidence without asserting consent; confirmed future deactivation does not decide a past dispute. |

Fixtures use non-dialable `SIM-LK-*` aliases, synthetic identities, UTC-aware timestamps and versioned resources. Reset generates a new active run and new deterministic row IDs in one transaction, retires prior active runs, and revokes all current sessions/Voice bindings. Historical rows remain stored; a failed or duplicate run insert rolls back the retirement/revocation. Keep the volume out of source control.

## Reconciliation rules for future Resolve code

- Money closing = opening snapshot + each committed signed posting for the same account, wallet, currency and sequence interval. Pending/failed recharge is not a posting. A reversal is a separately linked opposite posting.
- Quota remaining = opening bucket bytes + grant, consumption, expiry and reversal entries. Usage explains consumption but is not an additional quota movement. Out-of-bundle usage does not debit the exhausted bundle.
- Preserve posting sequence and occurred/recorded times so a late posting is visible. A future provider result should carry source, fetched time, source version, completeness, watermark and cursor. Empty data means none only after a complete read.
- Freshness assumptions: account, balance and subscription 60 seconds; service checks five minutes. Missing incidents do not prove service health.

## Provider contract surface

The provider exposes bounded account/balance/subscription reads, balance statements, quota-bucket statements, recharge fulfilment, account service checks and regional incident reads. Seeded A/D balance, B DATA_DEPLETION, C CONNECTIVITY, E captured-payment/pending-fulfilment and F VAS_DISPUTE investigations are implemented. E is not reported as a credit, and Resolve does not recommend a duplicate payment. F explicitly records missing activation evidence without treating it as consent; a confirmed future renewal stop remains distinct from the unresolved historic charge. C uses five-minute service-check and 30-minute incident freshness assumptions, and no current incident is not treated as healthy. Mutations are restricted to versioned/idempotent VAS deactivation, delivery of settings instructions and ticket creation/update. No refund, credit, recharge purchase or network repair is supported. Investigation results include source metadata/completeness; each mutation returns durable operation ID and actual readback status. Simulator/admin endpoints must not be customer-facing. Reads are capped at 500 records per source. Same key/body returns the saved result; same key with a different body conflicts.

Fault profiles cover late/duplicate posts, linked reversal, missing opening snapshot, pending payment, incomplete usage page, stale source, wrong unit, CRM outage, rejected writes, committed writes with lost responses, duplicate/stale confirmation and operation lookup failure. The action worker executes one-shot CRM unavailability, VAS write rejection and committed-response-loss profiles. On recovery, an existing stable provider-operation key is looked up; a one-shot `operations/lookup:LOOKUP_UNAVAILABLE` fault leaves the Resolve operation UNKNOWN for another same-key retry without repeating the provider mutation. Disposable PostgreSQL tests verify the failure and recovery outcomes. Fault-profile selection remains private to fixture/operator tooling and is not customer-facing. Other profiles listed above may still configure future paths and are not yet consumed.

## Fixture version 2

The checked-in `database/seed.sql` is now fixture version 2. It corrects B's signed debit and closing snapshot, links B's 20 GB quota bucket to a package subscription, removes unsupported app/category labels, gives C a separate matching 10 GB offer, and removes E's duplicate same-time opening snapshot. These are synthetic simulation choices. Existing initialized volumes retain their version 1 rows; generate version 2 in a new fixture run. `scripts/check-sandbox.sql` now raises an SQL exception when these selected invariants drift. Alembic revision 0001 validates and adopts schemas 001-003 on an already bootstrapped database; revisions 0002-0004 extend the Resolve lifecycle, investigation and confirmation schema; it does not replay DDL or change existing rows.

The B fixture now has an explicit opening quota snapshot at its valid-from time and sequence 1, followed by sequenced consumption to a closing zero-byte snapshot at sequence 4. This additional synthetic opening evidence is required for the two-snapshot reconciliation; it is not an assertion about a HUTCH production schema. To run the seeded B database integration tests, migrate and load the disposable sandbox, then set `QUOTA_IT_DATABASE_URL` (and optionally `QUOTA_IT_RUN_ID`) before running `python -m pytest -q tests/test_quota_postgres_integration.py`.

Charging and usage read fault profiles are consumed once when the provider is configured with `SANDBOX_DATABASE_URL`. A late posting is hidden from the current statement and makes the source incomplete; a duplicate posting is surfaced as a duplicate reference conflict; a mismatched reversal is conflicting evidence. Usage `INCOMPLETE_PAGE` and `STALE_SOURCE` faults mark the quota statement incomplete, so reconciliation returns `PARTIAL` even when the visible rows balance. In this mock contract `STALE_SOURCE.age_seconds` is represented as an explicit source-freshness failure, not by altering the fixture clock or pretending that the current rows are a complete current read. `WRONG_UNIT` retains the reported numeric values without conversion, labels usage evidence with the simulated source unit (the seed uses `KB` while the contract expects `BYTES`), and returns `CONFLICTING` with `USAGE_UNIT_MISMATCH`; arithmetic over those values must not be treated as a byte reconciliation. Source-version annotations record the simulated page cursor, stale age, or unit mismatch for diagnosis. An absent writer URL leaves all fault profiles untouched. Test fixtures may reset selected `remaining_uses` values to replay a scenario; customer-facing APIs do not expose fault controls.

For C, a fresh account-scoped provisioning check and a matching same-region `MOBILE_DATA` incident are retained as separate evidence. Freshness thresholds are simulation policy. When no current cause is supported, the case remains partial and does not assert healthy service or invent an ETA.

## Local commands

Fresh volume initialization runs SQL migrations 001, 002 and 003, then baseline fixture version 2 and knowledge cards. Run `python -m alembic upgrade head` after the DB is ready to adopt the existing schemas and apply application revisions 0002+. `scripts/reset.ps1` requires run-lifecycle columns, then inserts a new fixture run while retiring earlier active runs and revoking sessions/Voice bindings in the same SQL transaction. `database/seed.sql` is the baseline; `scripts/seed_run.py` re-keys it for a requested run UUID using only Python's standard library. Run `Get-Content scripts/check-sandbox.sql | docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U hutch_admin -d hutch_resolve` in PowerShell to assert the selected fixture invariants. To discard all history, explicitly remove the Compose volume with `docker compose down -v`.
