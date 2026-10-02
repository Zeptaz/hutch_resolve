# Tevin: conversation backend

Read [context.md](../../context.md) and [shared contracts](../contracts.md). Update both this plan and context after meaningful progress; record tests and unresolved work. No chatbot backend currently exists. Run as a module inside Harry's Resolve application, not a separate service.

## Boundary and interfaces

Own `backend/resolve/conversation`: text/conversation routers, dialogue controller, extraction, clarification, knowledge routing, templates and model telemetry. Export `ConversationService.handle_turn(context, turn) -> TurnResult`; both text and Harry's authenticated Voice bridge use this entrypoint. Harry supplies authenticated context, shared DTOs, scoped repositories, knowledge lookup and ResolveFacade. No private dependency on Harry's ORM models or sandbox tables.

ResolveFacade owns enquiry, cases, investigation, proposals, confirmations, operations, escalation and receipts. Conversation code cannot calculate ledger/quota results, assign evidence sufficiency, authorize account scope, decide action eligibility, construct success from a user's intent, or write provider records. Use fakes returning contract fixtures until Harry's facade is available.

## T-01: contracts, state and independent development

- [ ] Implement interface fakes and tests using published examples. Freeze input/output with Harry/Jayith before routing changes.
- [ ] Define persistent dialogue state: active_case_id, pending_question, candidate complaint/entities requiring clarification, language and pending proposal reference. Store through Harry's scoped repository interface; do not create a second conversation database.
- [ ] Handle text and structured input, maximum 4000 text characters. The structured variants are category_selection, complaint_details, action_decision and case_selection; they run the same business paths without a model.
- [ ] Use stable conversation+client_turn_id identity. Save input fingerprint and completed result. Identical replay returns the same result; changed payload conflicts. Coordinate with Harry's persisted turn claim so retries/restart cannot repeat a business action. Serialize accepted turns per conversation without holding a DB lock across model calls.

## T-02: intake and primary text flow

- [ ] Public FAQ works under GUEST; account enquiry/complaint requires authenticated customer scope. Ask for demo login rather than extracting an account from speech/text.
- [ ] Route FAQ, account enquiry, BALANCE_RECHARGE, DATA_DEPLETION, CONNECTIVITY, VAS_DISPUTE, action decision, receipt/status and human request. One clarification at a time for critical uncertain dates, amounts, target or negation.
- [ ] Define model structured extraction schema: allowed intent, language, complaint type, candidate entities and ambiguity list. Validate all output. Entity candidates are customer-reported, never authoritative evidence.
- [ ] Create a case through ResolveFacade and invoke investigation after required clarification. Default explicit demo windows use the simulation clock; real timestamps are used for authentication/confirmation expiry. Maximum investigation window is 30 days.
- [ ] Compose reply/card DTOs from persisted findings. Preserve monetary amounts, units, evidence references and state codes. Follow-up answers use the saved investigation; corrections request a new revision through Resolve.
- [ ] Persist multiple issues as separate cases in one conversation. Explicit case selection updates active state after scoped authorization. Do not silently attach new complaints to an unrelated active case.

## T-03: safe decisions, review, knowledge and fallback

- [ ] Render eligible proposal returned by Resolve; never create permissions from an LLM recommendation. ACCEPT/DECLINE calls Resolve confirmation with exact proposal/hash and fresh turn. Plain ambiguous speech asks again; a generic yes without current presentation context cannot execute.
- [ ] For Voice, preserve the original final transcript and provided presentation ID/hash. Pass decision evidence to Resolve, which owns final acceptance validation. Text controls use explicit action_decision fields. Declines are persisted, including when no operation exists.
- [ ] Explain PENDING/RUNNING/UNKNOWN/FAILED/REVIEW_REQUIRED/SUCCEEDED faithfully. Polling is the UI's responsibility; return operation references without waiting for execution. Never infer success from a successful HTTP request.
- [ ] Human request calls prepare_escalation, shows the review-ticket proposal, then uses normal confirmation. Conflicting evidence still allows review. CRM outage returns a Resolve reference and pending delivery, not a fabricated external ticket.
- [ ] Retrieve twelve reviewed knowledge cards through the shared repository; use card text/citations and reviewed version. Distinguish PUBLIC facts from SYNTHETIC policies. No live crawl/vector database or invented HUTCH vendor details.
- [ ] Apply six-second model budget; permit one schema repair only within that total budget. Otherwise return category/details forms and deterministic templates. Source/model text is untrusted; never execute SQL, arbitrary URLs or tool names from it.

## T-04: secondary coverage, language and observability

- [ ] Route B quota depletion, C connectivity, E pending recharge and F activation dispute through Resolve; templates reflect missing/conflicting evidence and separate future deactivation from past consent.
- [ ] Validate English/Sinhala/Tamil critical phrases with human review: amount/date, target, negation, accept/decline, pending/success and missing evidence. Romanized aliases alone do not prove native-language coverage. Record only demonstrated support.
- [ ] Record request/conversation/case IDs, actual provider/model, prompt/schema version, purpose, tokens when reported, latency, retry/status and failure category through the shared telemetry interface. Do not log raw transcripts or credentials.
- [ ] Supply contract and dialogue tests covering multiple issues, corrections, stale versions, replay, injection attempts, expired proposal, ambiguous consent, unavailable model, FAQ citations and all six scenarios. No duplicate business-rule tests masquerading as a second implementation.
- [ ] Prepare technical/AI disclosure and limitations from measured runs. Cite usage sample counts; label estimates and missing provider usage. Harry reviews technical claims; Jayith uses demonstrated behavior for the recording.

## Implementation order and acceptance

Hours 0-4 interface fakes/schema; 4-14 primary routing and persistence; 14-24 decisions/FAQ/fallback; 24-32 secondary paths/language; 32-40 dialogue/contract regressions and usage; 40-48 technical documentation.

T-01 is independent; T-02 integrates H-02/H-03; T-03 integrates H-04; T-04 integrates H-06. Ask Harry for a contract correction via canonical documentation rather than reaching into his implementation.

Acceptance: a full A journey produces the same case/action/receipt through text and Voice; D remains conflict; no action occurs from ambiguous or expired consent; retries produce one stored turn/action; FAQ cannot leak another conversation; all workflows remain reachable via structured controls with no model; correction invalidates affected proposals; native-language claims match human checks.

## Verification log

| Date | Task | Evidence | Remaining |
| --- | --- | --- | --- |
| 2026-10-02 | Baseline | No conversation implementation found; master and frozen contract define intended behavior | T-01 through T-04 pending |
