# Browser checks

Run from `frontend` after `npm ci` and `npx playwright install chromium`.

```sh
npm run test:e2e
```

The default script runs `e2e/run-mock.mjs`. It starts its own isolated Vite process on port 5175 with `VITE_API_MODE=mock`, runs the `mock` and `mock-phone` projects, then stops only that Vite process. It refuses to run if the port is occupied. The tests cover queue filtering, case evidence, review controls, conflict handling, keyboard and phone layouts, and a customer chat journey. They use synthetic contract fixtures and do not write to Resolve.

The tests check the mock banner before using controls. `E2E_CHROMIUM_EXECUTABLE` can point to an installed Chrome executable if the Playwright Chromium download is unavailable.

The `live` project changes review state. Run it only with a **disposable, freshly migrated PostgreSQL instance** and a locally started Resolve backend seeded through `live/seed_cases.py`. Never point the seed script or live suite at a shared database. Both the seeder and live suite require `E2E_DISPOSABLE_DB=1`; the live suite also requires an explicit `E2E_BASE_URL`. Set these only after verifying the connection strings and backend configuration point to the disposable instance.

```powershell
# From the repository root, after bringing up an isolated PostgreSQL project:
$env:E2E_DISPOSABLE_DB = '1'
$env:E2E_DATABASE_URL = 'postgresql+psycopg://...isolated Resolve DB...'
$env:E2E_SANDBOX_DATABASE_URL = 'postgresql+psycopg://...isolated sandbox DB...'
python frontend/e2e/live/seed_cases.py

# Start Resolve and Vite in live mode in separate terminals, then from frontend:
$env:E2E_BASE_URL = 'http://localhost:5173'
$env:E2E_AGENT_ID = '...'
$env:E2E_AGENT_CREDENTIAL = '...'
$env:E2E_CUSTOMER_ID = '...'          # optional role isolation checks
$env:E2E_CUSTOMER_CREDENTIAL = '...'
npm run test:e2e:live
```

The live suite checks the seeded recharge conflict, review optimistic concurrency, close/reopen rules, and agent API authorization. It does not cover real microphone audio, provider integrations, or production authentication.
