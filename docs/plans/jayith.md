# Jayith: customer frontend and internal dashboard

Read [context.md](../../context.md) and [contracts](../contracts.md). Update this plan and context after meaningful progress with implementation/test evidence. Jayith's `HutchChat` and `VoiceFrontend` UI work was selectively integrated into the combined Resolve app on `ResolveDev` (`f2c9bab`). The later `ResolveDashboard` branch (`6fe6acb`) is also being selectively integrated on `ResolveDev`, preserving the newer Voice and backend work. Browser/live Voice verification remains open; keep corresponding task checkboxes pending.

## Scope and connections

One React 19 + TypeScript + Vite app, customer `/` and protected `/agent` routes. Development runs at localhost:5173 and proxies `/api` to Resolve:8080. Use credentialed same-origin API calls and the relevant CSRF token; no browser service secrets. Generate types from [OpenAPI](../contracts/openapi.json) and use [examples](../contracts/examples.json) for mock development. Harry owns all API/business rules; Tevin owns conversational behavior.

- [x] J-01 Application shell, separate customer/agent session restore/login, loading/error/expiry states and typed API client are implemented. Contract-backed mock browser suite passes 15/15 on desktop and phone; live journeys remain J-02/J-04.
- [ ] J-02 Customer: chat, category/detail fallback, transcript correction, language choice, evidence/calculation cards, explicit accept/decline, pending operation display, human-review request, case switching and JSON receipt download. Agent: queue/filter/search, case packet, review notes/disposition and conflict refresh.
- [ ] J-03 Add external Voice controls: request grant from Resolve, direct WebSocket connection, mono PCM16 capture/resampling at 16 kHz, 24 kHz playback, interruption flush and call/text continuation.
- [ ] J-04 Complete browser integration tests, keyboard/contrast checks, error/retry states and demo recording. Mark completion only after actual backend integration passes.

## Minimal useful dashboard

Queue columns: case reference, synthetic line, complaint type, evidence state, review status, ticket delivery and updated time. Filters/search follow the API; no client-side authorization or fabricated totals. Detail shows complaint/relevant turns, evidence/source status, calculations and missing information, proposal/confirmation/action history, ticket state, receipts and internal notes.

Agent can append a note, start review, close with disposition/reason, or reopen with reason. Submit `expected_version`; on 409 refresh and preserve the unsent note for human retry. No billing editor, refund controls, account administration or analytics suite. Agent notes are internal, never displayed in customer receipt/chat projections.

## Interaction rules

- Render fixed card variants; never execute model HTML or calculate authoritative monetary outcomes.
- Keep investigation, operation, review and delivery states visually distinct. Pending is not success. Always label simulation and missing/conflicting evidence.
- Use stable client_turn_id/idempotency key for retries of the same request; generate a new ID for an intentional new turn. On stale conversation version, reload; do not automatically replay a confirmation against changed state.
- Poll pending operations every second until terminal state or page departure. UNKNOWN remains visible until recovery; REVIEW_REQUIRED is terminal for automatic recovery. Poll dashboard every five seconds only while visible; provide manual refresh.
- Display exact target, consequences and expiry for confirmation. Confirmation must never be preselected/submitted automatically.
- Browser grant travels in WebSocket subprotocol `hutch-grant.{token}` with `zeptaz-hutch-v2`, never a URL. A grant is consumed once; reconnect requests a fresh binding from Resolve.
- Drain the scoped audio queue after `audio_end`, send `playback_complete` with the matching response ID, and require accepted `playback_ack` before sending `proposal_presented` with response ID and exact proposal ID/hash. Clear queued audio and pending acknowledgement on interruption/disconnect. Handle `resolve_result`, `audio_fallback`, transcript, ready, error and ended. Fetch the same conversation after call end for canonical state.
- Microphone permission denial, provider outage and session expiry offer text continuation. Expired authorization requires login; never silently switch to another synthetic account.

## Acceptance and order

Hours 0-4 shell/mocks; 4-14 chat/evidence and dashboard detail; 14-24 confirmations/receipts/review; 24-32 Voice; 32-40 browser regression; 40-48 presentation/video/accessibility fixes.

Playwright cases: A login -> investigate -> accept -> one success -> receipt; D conflict -> confirmed review handoff -> agent note/disposition; decline and stale proposal; duplicate click/retry; cross-role denied routes; stale agent update preserves draft; model/Voice unavailable text flow; Voice interruption cannot confirm; call disconnect resumes same case by text. Manually verify actual microphone/playback with Harry and native-language critical phrases with the team.

Dependencies: J-01 can start immediately; J-02 integrates H-04/H-05/T-02; J-03 integrates H-07/H-08. Customer UI and required minimal dashboard take priority over cosmetic polish. Produce the demo recording from the actual release and verify sharing links.

## Verification log

| Date | Task | Evidence | Remaining |
| --- | --- | --- | --- |
| 2026-10-02 | Baseline | No frontend code found in either target repository at the planning baseline | J-01 through J-04 pending |
| 2026-10-02 | UI branch intake | Fetched `HutchChat` (`9e7b8b1`) and `VoiceFrontend` (`4349eed`); selective import into the Resolve frontend is in progress | Combined build, live API/chat bridge, v2 browser verification, dashboard review writes and release tests pending |
| 2026-10-02 | Combined frontend static checkpoint | One React app now includes chat, scoped call panel and dashboard review writes; demo sign-in keeps the guest conversation pointer. Clean npm install, pinned OpenAPI generation, typecheck and production build pass. Lint exits 0 with warnings. No credentials or build artifacts are tracked. | Live Resolve/browser journey, microphone/Voice v2 playback and consent, conflict/retry browser tests and fluent Sinhala/Tamil review remain open; J checkboxes stay pending |
| 2026-10-02 | Resolve backend integration | The mounted text route and signed Voice bridge now share the conversation service; five disposable PostgreSQL conversation/Voice/guest checks pass. Frontend phase `f2c9bab` is pushed. | Run actual browser chat/call and dashboard journeys against the services, including interruption/playback acknowledgement, review writes, expiry/retry and accessibility. |
| 2026-10-03 | ResolveDashboard selective intake | Fetched `ResolveDashboard` tip `6fe6acb`; imported its queue, case evidence/actions/conversation/receipt/history tabs and review panel into the existing app while preserving chat, demo sign-in and Voice. Added Playwright mock/live suites. Typecheck/build pass; lint exits 0 with warnings. Mock browser suite passes 15/15, including conflict draft retention and keyboard navigation fixes found during integration. A unique disposable PostgreSQL migrated through 0007 and seeded six cases; live dashboard suite passed 5/5, including 409 recovery, review transitions and role/CSRF boundaries. Temporary project and volume were removed. | Real chat/Voice browser journey, accessibility and release recording remain open. |
| 2026-10-03 | Customer package flow | Added a Resolve-backed package catalogue card, explicit offer selection and typed terms in the shared confirmation card; UI tracks the canonical operation and never claims success while pending. Contract generation and production build pass; mock E2E suite 19/19 includes package confirmation and pending-to-success. | Live chat browser path through Resolve, Voice browser path, accessibility review and release recording remain open. |


## Audit remediation checkpoint — 2026-10-03

[Audit findings and verification limits](../audits/2026-10-03-resolve.md). AUD-04 now stops microphone streams that arrive after the call closes and makes startup cleanup idempotent. Focused fake-media regressions pass. AUD-07 now tracks all operation IDs and refreshes their canonical status; AUD-03 chat reload can resume a persisted text turn with its original ID and body. The remaining turn-reconciliation limitation and PostgreSQL verification blockers are tracked in [`context.md`](../../context.md). Final e2e rerun, real microphone, full chat/call journeys, accessibility and live Resolve integration remain open until rerun/qualification.

Update this plan and root context.md after each implemented and verified correction, recording commands/results and remaining work.

### 2026-10-03 review durability follow-up

Logout failures now preserve the recoverable agent session. Review updates persist the exact request body and idempotency key before sending, allowing a committed-but-lost response to replay after reload. Only unknown outcomes retain that key; definitive 409/422 responses retire it so a stale update remains blocked until the agent checks the latest case. Focused Playwright conflict and lost-response/reload scenarios both passed. The full mock E2E rerun reports **22/22 passed**; Windows Playwright teardown hung after all tests reported green and was interrupted. Frontend typecheck/build pass and lint exits 0 with six existing warnings.

### Main branch merge verification — 2026-10-03

The integrated frontend from `ResolveDev` is now on `main`, including the voice/call UI, dashboard, and browser scenarios. On the merged tree, typecheck and production build pass and the mock browser suite exits cleanly with **23/23 passed**. Live chat/call qualification with the real Resolve and Voice services remains open.

### Local browser verification — 2026-10-03

Against live Resolve/Voice and isolated PostgreSQL, customer sign-in, structured A evidence/confirmation/receipt, package browse/selection, agent D review and visible Voice unavailable/text continuation were exercised. The Call panel's absent-proposal render and unavailable-provider feedback were fixed. Frontend typecheck/build pass and mock browser suite exits **24/24 passed**; the recovery test now waits for chat initialization before simulating outage. A real microphone/provider call, broader accessibility review and release recording remain open. See root `context.md` for backend and database checks.

### Voice call response repair — 2026-10-03

On `voice_test`, the call frontend now sends `input_audio_end` after detected speech and quiet, shows whether the microphone is picking up voice, and speaks only the fixed greeting or Resolve-verified fallback text with browser speech synthesis. It suppresses outgoing microphone frames while synthesized speech plays and keeps fallback proposals text-only for consent. Typecheck/build pass; a live synthetic WAV browser probe reached Resolve response events and a mocked speech-synthesis observer recorded speech calls. A physical microphone/speaker check, real browser voice availability and native-language review remain open; see root `context.md`.

Follow-up: the call UI now holds microphone forwarding after `input_audio_end` until a verified model reply drains or fallback finishes, so a repeated capture cannot interrupt the pending answer. A real headless browser with a synthetic microphone received Gemini PCM, played it through a running AudioContext, sent `playback_complete`, and received `playback_ack`. Build/typecheck and **24/24** mock browser checks pass. Native Chrome/Edge speech synthesis failed on this machine, so the fallback remains best effort and shows a playback error if unavailable. J-03/J-04 still require a human microphone/speaker run, proposal consent and native-language review.

### Natural opening voice — 2026-10-04

The live `greeting` event previously used local browser `SpeechSynthesis`, which sounded robotic and differed from the Gemini Live `Kore` reply voice. The frontend on `voice_test` now plays a fixed greeting WAV generated by that same Live voice; its output transcript was verified against the exact 52-character greeting. The caption remains if the file fails to load. The real browser fetched the asset (HTTP 200), started AudioContext playback and invoked no browser speech synthesis. A complete synthetic balance call then heard the correct Resolve account reply, drained 30 PCM frames and received an accepted playback acknowledgement. Frontend typecheck/build and mock browser suite **24/24 passed**. Human microphone/speaker and language checks remain open.

Implementation commit **7c5ebd9** is remotely confirmed on `hutch_resolve/voice_test`.

### Shared reply repair checkpoint — 2026-10-04

The repeated generic chat/call prompt came from six-second extraction timeouts in Resolve, not from a dropped frontend transcript. Resolve now recovers clear balance reads and complete English complaint starters after model failure, and the ignored local demo setting uses a successfully probed 3.5 Flash Lite model. Two live text API turns returned the correct account and investigated case responses. No frontend files changed in this phase; the remaining physical microphone/speaker and native-language checks stay open. Backend implementation commit **d5d1214** is on `voice_test`.

### Faster voice playback — 2026-10-04

The customer call UI now sends `input_audio_end` after seven 100 ms quiet frames. It displays Resolve's canonical reply while streaming Live PCM immediately, removes the browser speech-synthesis fallback and no longer sends proposal-presentation acknowledgements. Proposal choices remain visible buttons only. Typecheck/build and mock browser suite **24/24** pass. Physical microphone/speaker and live model latency/meaning checks remain open; J-03/J-04 are not yet complete.

### Voice v3 caller interruption checkpoint — 2026-10-04

The current call UI offers `zeptaz-hutch-v3`, sends an increasing activity segment ID after two speech frames and ends it after seven quiet frames, keeps sending microphone PCM during replies, and flushes queued playback on new caller speech. `input_audio_end` now marks a mute/pause. A mock browser test exercises interruption while PCM keeps flowing; the complete mock browser suite **25/25**, typecheck and lint pass. Real browser microphone/speaker interruption and native-language checks remain open, so J-03/J-04 are not marked complete.

The external Voice/Resolve signed service path later passed two synthetic spoken turns on one v3 WebSocket with real Gemini and two accepted playback acknowledgements. This probe did not use the browser audio capture/playback UI. Keep J-03/J-04 open until a human browser microphone/speaker interruption and native-language review pass.
