# HUTCH Resolve frontend

One React 19 + TypeScript + Vite app with two areas:

- `/`: customer chat and call panel (starts a guest session and offers demo customer sign-in)
- `/agent`: internal review dashboard (agent sign-in required)

Owner: Jayith. See [the plan](../docs/plans/jayith.md) and [shared contracts](../docs/contracts.md).

## Run

```sh
cd frontend
npm ci
npm run dev        # http://localhost:5173
```

`VITE_API_MODE` selects the data source. The integrated development default is `live`:

| Mode | Behaviour |
| --- | --- |
| `mock` | Explicit standalone scripted UI demo using `docs/contracts/examples.json`. Any agent identity/credential works. The banner's **Mock controls** expire the session, toggle a 503 outage or reset data. Mock voice uses a local scripted socket, not Zeptaz Voice. |
| `live` | Calls Resolve through the Vite proxy (`/api` → `http://localhost:8080`). |

Copy `.env.example` to `.env.development.local` to run against Resolve. Set `VITE_API_MODE=mock` only for an offline UI demo; check the visible mock banner before presenting results.

## Scripts

| Command | Purpose |
| --- | --- |
| `npm run gen:api` | Regenerate `src/api/schema.d.ts` from `docs/contracts/openapi.json` (run after any contract change) |
| `npm run typecheck` | TypeScript project check |
| `npm run lint` | oxlint |
| `npm run build` | Typecheck and production build |
| `npm run test:e2e` | Mock dashboard and customer browser checks; starts an isolated Vite server |
| `npm run test:e2e:live` | Opt-in review workflow checks against a disposable Resolve stack; see [e2e/README.md](e2e/README.md) |

The agent dashboard shows server-filtered cases, investigation evidence, action/ticket/receipt history and internal review notes. Review updates send the current case version and a stable retry key. A stale update keeps the unsent draft until the agent reviews the refreshed case. The customer chat/call experience continues on `/` using the same session and conversation.

## Layout

```
src/
  api/          typed client, endpoints, error envelope, mock transport, generated schema
  session/      customer/agent session restore, expiry and CSRF handling
  components/   shared UI (status badges, states, simulation banner) and shadcn/ui
  routes/       customer/ and agent/ screens
  lib/          display-only formatting (Asia/Colombo time, LKR, GB)
```

## Rules

- Render server results only; never compute authoritative amounts, findings or permissions in the browser.
- Keep investigation, operation, review and delivery states visually distinct. Pending is never shown as success.
- Session credentials live in HttpOnly cookies; the CSRF token is held in memory only.
- Theme: tweakcn "autoblog", adjusted for WCAG AA contrast and state colours (see `src/index.css`).
