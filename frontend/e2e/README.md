# Browser tests (Playwright)

```sh
npm run test:e2e            # mock suite: no backend, starts its own Vite on :5175
npm run test:e2e:live       # live suite: needs the stack below (E2E_LIVE=1)
npx playwright show-report  # last HTML report
```

## Mock suite (default)

Runs against the app's contract-example mock (`VITE_API_MODE=mock`), so it is deterministic and needs nothing else running.

| Spec | Covers |
| --- | --- |
| `agent.mock.spec.ts` | Queue and attention flags; why-here summary and key numbers; tabs; filters, chips, status selector, exact search, `/`; review start, close (outcome and note required), reopen (reason required); another agent's change gives 409, keeps the draft and waits for "I've checked it"; drafts survive reload; `j`/`k` move between cases but not while typing; expired session; Resolve outage with retry |
| `phone.mock.spec.ts` | Phone width (Pixel 7): queue and case take turns, no sideways scroll |
| `chat.mock.spec.ts` | Customer journey A: complaint, evidence, explicit accept, success |

`mockControl(page, name, ...args)` in `helpers.ts` calls `mockControls` from `src/api/mock.ts` inside the page (for example `reviewElsewhere` to act as a second agent).

## Live suite (`E2E_LIVE=1`)

Runs against Harry's real Resolve app and a **throwaway** PostgreSQL; it adds notes and changes review status.

1. Throwaway database (Docker; same roles as `tests/conversation/hybrid_db.sh`, any free port), then from the repo root `MIGRATION_DATABASE_URL=... python -m alembic upgrade head`.
2. Seed cases A-F through Harry's facade: `E2E_DATABASE_URL=... E2E_SANDBOX_DATABASE_URL=... python frontend/e2e/live/seed_cases.py`. Each run adds a fresh case per line.
3. Start Resolve with `DATABASE_URL`, `SANDBOX_DATABASE_URL`, `APP_ORIGINS=http://localhost:5173`, `APP_SECRET_KEY` and `DEMO_IDENTITIES_JSON` holding a test AGENT (and optionally a CUSTOMER for line D): `python -m uvicorn backend.resolve.app.main:app --port 8080`.
4. Start the frontend in live mode: `VITE_API_MODE=live npm run dev`.
5. Run with the test credentials in the environment (never commit them):

```sh
E2E_AGENT_ID=... E2E_AGENT_CREDENTIAL=... \
E2E_CUSTOMER_ID=... E2E_CUSTOMER_CREDENTIAL=... \
npm run test:e2e:live
```

| Spec | Covers |
| --- | --- |
| `agent.live.spec.ts` | Newest D case shows the server's 420 / 350 / −70; a second agent session's note causes a real 409, the draft is kept and both notes end up stored; close/reopen rules; anonymous, customer and missing-CSRF calls to agent APIs are refused; a customer session does not open the dashboard |

Not covered yet: the live customer chat (needs Harry's conversation routes), Voice, and the remaining items in `docs/plans/jayith.md`.
