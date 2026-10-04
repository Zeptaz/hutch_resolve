# Tevin: conversation backend

Read [context.md](../../context.md) and [shared contracts](../contracts.md). Update both this plan and context after meaningful progress; record tests and unresolved work. The conversation module is now mounted inside Harry's Resolve application. Do not deploy a separate chatbot service.

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
| 2026-10-03 | CRM integration onto main (branch `tevin/crm-integration`): CB-004 offer re-try / no review loop / no unprompted review on reconciled answers / Voice replay fingerprint; CB-005 customer-requested review via `propose_action` (main returned 500) | Unit 505 passed/48 skipped; PostgreSQL disposable: conversation runtime 6, real-facade conversation 22 (new regression fails without CB-005); browser mock CRM and live HubSpot journeys (see context.md). Commits `ddf619d`, `2d34967`. | Harry review (both HIGH); CE-007 engine fix; scenario A policy (CE-011). |
| 2026-10-03 | CB-002/CB-003 VAS questions + knowledge language | Knowledge search falls back to English cards for si/ta customers; `account_topic` SERVICES/PACKAGES answers from the account view with an opt-in charge-records check; VAS charge lines quoted verbatim; prompts translatable after a case. Unit 473 passed/40 skipped; live extraction samples; browser end to end. | Fluent review of new draft strings; Tamil live samples; CE-004 subscription price. Commit `73677a4`. |
| 2026-10-03 | CB-001 Singlish replies (T-04, branch `tevin/chatbot-fixes`) | Two-tier `_localize`: text-chat findings/offers/balance rewritten with strict fact check (`rewrite-v3`), outcomes/consent/Voice deterministic; paragraphed text replies; script-aware locales (`si-Latn` draft, unreviewed). Unit 461 passed/40 skipped; live Gemini + fake Resolve; browser on local live stack with `REPLY_REWRITE` OK rows. See `docs/changelogs/chatbot-changes.md`. | Fluent review of `si-Latn.json` and rewritten samples; Harry AUD-01 review; CB-002 VAS "check charges" routing; CE-001/002/003 engine requests. Commit `73677a4`. |
| 2026-10-03 | Package query/selection integration | Added typed package-query/selection turns; conversation gets catalogue/usage and asks ResolveFacade to create case/evidence/proposal. No duplicate package policy. Tests: 347 passed, 16 database-gated skipped. Resolve-backed package selection/action was separately verified through disposable PostgreSQL, including concurrent confirmation. | Usage lacks explicit coverage so no personalized best-fit claim is exposed; full customer Voice/browser journey remains open. |
| 2026-10-02 | Baseline | No conversation implementation found; master and frozen contract define intended behavior | T-01 through T-04 pending |
| 2026-10-02 | Resolve integration | Imported the conversation module and contract tests from `HutchChat`; text and signed Voice turns use one mounted service with persisted scoped turn claims. Revision 0007 stores dialogue state and model telemetry. Contract examples validate. Full default suite 420 passed/34 opt-in skipped; five disposable PostgreSQL conversation/Voice/guest tests passed. | Browser and real-model qualification, native-language human review, full six-scenario end-to-end matrix and release disclosure remain open. Package activation is now in the v1.1 contract but remains disabled pending PostgreSQL qualification. |


## Audit remediation checkpoint — 2026-10-03

[Audit findings and verification limits](../audits/2026-10-03-resolve.md). AUD-01 deterministic replies for actions/cases/financial outcomes now bypass freeform rewriting; number/sign, outcome polarity and supplied-link regression tests are added. Unit/conversation tests pass, but no real-model semantic qualification has run. For AUD-03, normalized TEXT input is saved and same-ID recovery uses atomic expected-version claims; trusted Voice consent is never reconstructed. Abandoned Voice and legacy claims with no saved payload fail closed and can still block later conversation turns until an authorized reconciliation policy exists. See current aggregate results and PostgreSQL blockers in [`context.md`](../../context.md).

Update this plan and root context.md after each implemented and verified correction, recording commands/results and remaining work.

### 2026-10-03 grounded response follow-up

Resolve conversation answers now reject outcome claims unsupported by the available grounded facts, preventing the freeform response path from inventing a customer outcome. Focused conversation answer tests passed (**40 passed**); the aggregate Resolve suite is **439 passed, 36 PostgreSQL-gated skipped**. No real-model semantic qualification has run, so model answer quality and native-language phrasing remain open.

### Local full-stack conversation verification — 2026-10-03

The real Resolve-backed package query initially failed strict presentation DTO validation because Resolve-only usage/offer metadata leaked into the chatbot adapter. The adapter now projects only declared presentation fields; a real facade/disposable PostgreSQL regression passes. Live structured browser/API paths exercised A/B/C/D/E/F, package selection, action status and receipts. The model capability was unavailable because no live model key was configured; free-text fallback requested structured complaint entry. Real-model semantic, native-language and physical Voice qualifications remain open. See root `context.md` for aggregate checks.

### Direct balance enquiry recovery — 2026-10-04

Two real Voice turns with “Can I know my account balance?” were transcribed correctly, but `EXTRACTION/TIMEOUT` at about six seconds sent them to the generic category prompt. The shared conversation service on `voice_test` now recognizes only clear account-balance read requests when extraction fails and invokes its existing customer-scoped `_account` path. It does not classify balance complaints or how-to questions as account reads, and guest access still requires sign-in. Regression tests cover timeout, complaint/how-to exclusion and guest denial; the full Resolve default suite passed **446/446** with 40 opt-in PostgreSQL skips. A live synthetic browser call returned the current scoped balance and verified PCM speech. Tevin's broader model and native-language intent qualification remains open.

Implementation commit **7c5ebd9** is remotely confirmed on `hutch_resolve/voice_test`.

### Model-failure routing follow-up — 2026-10-04

The shared conversation service now recovers a narrow set of direct balance questions and complete English complaint starters after failed intent extraction, without bypassing the Resolve facade or consent gates. The local ignored demo setting was switched to `gemini-3.5-flash-lite` after a successful structured extraction. Full default Resolve suite: **449 passed, 40 opt-in PostgreSQL skipped**; two live API/PostgreSQL chat turns returned the expected scoped balance and investigated reload case, with `EXTRACTION/OK` telemetry. Physical Voice and native-language intent review remain open. Verified backend implementation commit **d5d1214** is on `voice_test`.


### Voice confirmation and interruption repair - 2026-10-04

- [x] Initial offers, alternatives and pending-offer follow-ups in Voice direct callers to the on-screen **Yes, go ahead / No, leave it** buttons. Security requires a button decision; spoken yes/no never authorizes an action. Text behavior and canonical pending-offer ownership remain unchanged. New English Voice strings are explicitly unreviewed for Sinhala/Tamil.
- [x] Browser VAD requires three consecutive 100 ms speech frames and 700 ms quiet; it rejects playback echo more strongly until local playback actually drains, freezes room-noise learning during playback, and keeps the speaking indicator stable between PCM chunks. Normal sensitivity resumes immediately after drain.
- [x] External Voice v3 uses browser activity boundaries as its sole VAD. Bounded 300 ms preroll preserves initial speech; mute discards it. V2 retains provider VAD. Live verification caught and fixed invalid automatic silence settings when provider detection is disabled.
- [x] Verification: Resolve default suite **452 passed, 40 disposable-PostgreSQL checks skipped**; Voice **71 passed**; frontend TypeScript and final mock browser suite **30/30 passed**. Earlier failures exposed quiet-caller thresholds and an undersized test audio budget; corrected and rerun. One chat browser journey failed in an earlier run and passed in the final suite. A real Gemini connection produced **16 and 24 PCM frames across two synthetic turns**, with two accepted playback acknowledgements and exactly two Resolve-stub calls. This checks provider/manual-VAD runtime interoperability, not signed Resolve integration or physical acoustics.
- [ ] H-08/J-03/J-04 remain open for physical microphone/speaker echo and repeated interruption, native-language review, and full signed browser/model qualification of this revision. Historical logs cannot identify the exact acoustic source of the reported loop.

Changes belong to `hutch_resolve/voice_test` and `hutch_zeptazvoice/voice_test2`. Shared conversation changes are restricted to Voice wording/channel propagation. Commit and local service restart evidence follows after verification.

### Voice + CRM release candidate - 2026-10-04

- [x] Integrated `main` + `tevin/crm-integration` + Harry's `voice_test` on `integration/voice-crm` (Voice repo: `voice_test2`). Resolved the five `conversation/service.py` conflicts by keeping chatbot locale/paragraph/rewrite handling and Harry's Voice channel prompts. CB-006 pins `confirm_prompt_voice` to English.
- [x] Verification: Resolve **523 passed, 49 skipped**; PostgreSQL opt-in **14/14 files**; Voice **71**; frontend typecheck/build; mock Playwright 32/32 (CE-013 test route fix); live chat → review ticket → dashboard; signed two-turn Voice probe through real Gemini Live. Details in root `context.md`.
- [ ] Physical microphone call; Harry's unpushed v4/decision-readback code; Harry review of CB-004..006.
