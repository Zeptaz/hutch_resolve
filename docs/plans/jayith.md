# Jayith: customer frontend and internal dashboard

Read [context.md](../../context.md) and [contracts](../contracts.md). Update this plan and context after meaningful progress with implementation/test evidence. No frontend currently exists; every task below is pending.

## Scope and connections

One React 19 + TypeScript + Vite app, customer `/` and protected `/agent` routes. Development runs at localhost:5173 and proxies `/api` to Resolve:8080. Use credentialed same-origin API calls and the relevant CSRF token; no browser service secrets. Generate types from [OpenAPI](../contracts/openapi.json) and use [examples](../contracts/examples.json) for mock development. Harry owns all API/business rules; Tevin owns conversational behavior.

- [ ] J-01 Build application shell, separate customer/agent session restore/login, loading/error/expiry states and typed API client. Build against contract examples while backend is incomplete.
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
- Browser grant travels in WebSocket subprotocol `hutch-grant.{token}` with `zeptaz-hutch-v1`, never a URL. A grant is consumed once; reconnect requests a fresh binding from Resolve.
- Send `proposal_presented` with exact ID/hash only after the complete proposal playback. Clear pending acknowledgement on interruption/disconnect. Handle `proposal_ack`, `resolve_result`, transcript, ready, error and ended events; implement the proposed `interrupted` event by stopping/discarding queued audio and pending acknowledgement. Fetch conversation after call end for canonical state.
- Microphone permission denial, provider outage and session expiry offer text continuation. Expired authorization requires login; never silently switch to another synthetic account.

## Acceptance and order

Hours 0-4 shell/mocks; 4-14 chat/evidence and dashboard detail; 14-24 confirmations/receipts/review; 24-32 Voice; 32-40 browser regression; 40-48 presentation/video/accessibility fixes.

Playwright cases: A login -> investigate -> accept -> one success -> receipt; D conflict -> confirmed review handoff -> agent note/disposition; decline and stale proposal; duplicate click/retry; cross-role denied routes; stale agent update preserves draft; model/Voice unavailable text flow; Voice interruption cannot confirm; call disconnect resumes same case by text. Manually verify actual microphone/playback with Harry and native-language critical phrases with the team.

Dependencies: J-01 can start immediately; J-02 integrates H-04/H-05/T-02; J-03 integrates H-07/H-08. Customer UI and required minimal dashboard take priority over cosmetic polish. Produce the demo recording from the actual release and verify sharing links.

## Verification log

| Date | Task | Evidence | Remaining |
| --- | --- | --- | --- |
| 2026-10-02 | Baseline | No frontend code found in either target repository | J-01 through J-04 pending |
| 2026-10-02 | J-01 (partial) | `frontend/` React 19 + TS + Vite + shadcn app: typed client from OpenAPI, mock mode over `examples.json`, customer guest-session chat shell at `/`, agent login/queue/case-packet shell at `/agent`. Static: `tsc -b` and `vite build` pass; oxlint clean apart from shadcn fast-refresh notices and two fetch-in-effect false positives. Commits `3b88bcd`, `a96c435`, `b0e46de` (HutchChat) and `41faa4c` (ResolveDashboard) | Browser verification not yet run (no headless browser in session). Customer upgrade from GUEST to CUSTOMER (demo line picker vs guest-only FAQ) needs a team decision. Contract issue for Harry: `TurnInput`/`Card` discriminators lack `mapping`, so generators expect schema names (`TextInput`) instead of `const` values (`text`); `scripts/gen-api.mjs` strips them until the contract adds explicit mappings |
