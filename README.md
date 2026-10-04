# HUTCH Resolve

**Evidence-backed AI resolution for telecom customer complaints.**

| | |
| --- | --- |
| Product | HUTCH Resolve |
| Team | Zeptaz |
| University / batch | IIT · 23SEP |
| Event / track | IgnitX 2026 · Track A: Resolve & Support |
| Release | `v1.0.1` in both repositories (tagged 4 October 2026) |
| Live demo | https://resolve.zeptaz.com (customer chat `/chat`, agent dashboard `/agent`) |
| Repositories | [`Zeptaz/hutch_resolve`](https://github.com/Zeptaz/hutch_resolve) (this repo) · [`Zeptaz/hutch_zeptazvoice`](https://github.com/Zeptaz/hutch_zeptazvoice) (Voice service) |

**Team Zeptaz**

| Member | Role |
| --- | --- |
| Naveen Harry | Backend and integration: Resolve APIs, business services, database and migrations, security, Voice adapter integration |
| Tevin Silverster | Conversation and AI: Gemini extraction, reply rewrite, knowledge-card answers, dialogue flow, CRM adapter |
| Jayith Wijethunge | Frontend: customer chat and voice call interface, agent dashboard, landing page, hosting |
| Ojith Adithya | Documentation and presentation: technical document, README and pitch deck |

> All data in this repository is **synthetic**. No HUTCH system, API, credential or real customer record is used or connected.

---

## Problem statement

Customers can see their balance, package and usage figures, but not **why** they changed. A typical complaint is *"I reloaded Rs. 1,000 this morning. Why is my balance only Rs. 420?"* Answering it means rebuilding several events (a recharge, a package renewal, a VAS charge and billable usage) in the right order. Self-care shows the figures, agents check several systems by hand, and a generic chatbot cannot prove anything about money.

- **Primary users:** HUTCH prepaid customers with a balance, recharge, data, package, VAS or connectivity complaint.
- **Secondary users:** customer-care agents who receive escalated cases.

## Solution overview

Resolve is a resolution layer behind HUTCH's customer channels. It turns a complaint in English, Sinhala or Tamil (text or voice) into a structured case, reads the relevant account records, and reconciles them with deterministic rules. It then shows the calculation and its source records, offers a safe allow-listed action that the customer must confirm, and issues a **Trust Receipt**. When evidence is partial or conflicting it does not guess: it creates a review ticket for a human agent with the investigation attached.

**AI handles language only. Every number, decision and account change is made by code.**

## Key features

- **Four complaint types, six seeded cases (A–F):** balance/recharge deduction (A), data depletion (B), no internet with an active package (C), balance conflict (D), payment captured but not credited (E), VAS dispute without an activation record (F).
- **Eleven seeded lines (fixture v3):** lines 0007–0011 add a partly explained deduction (records explain LKR 73 of a reported LKR 100), a duplicate package renewal, heavy calling with a failed top-up, bonus, transfer and fee (fully explained), a refunded VAS charge with earlier support history, and a line with no balance snapshots.
- **Exact reconciliation** in integer cents (e.g. 1,000 − 499 − 60 − 21 = 420); quota and money kept in separate calculations.
- **Balance reconstruction:** opening balance + recharges, bonuses and refunds − calls, SMS, data, packages, VAS, transfers and fees = expected balance, from ledger postings and itemised usage, compared with the recorded balance and the customer's claim ("LKR 100 was deducted", "my balance is only LKR 420"). Each investigation is classified EXPLAINED, PARTIALLY_EXPLAINED, UNEXPLAINED or INSUFFICIENT_EVIDENCE with the explained and unexplained amounts; nothing is estimated.
- **Escalation only when justified:** an explained balance is proved with its breakdown; a human review is offered for unexplained money or missing evidence, or when the customer asks. Chat shows the full breakdown, Voice says the same conclusion briefly.
- **Evidence states:** SUFFICIENT, PARTIAL or CONFLICTING, decided by rules; conflicts block account changes. Seeded one-shot provider faults are resilience-test controls and stay off unless `SANDBOX_FAULTS_ENABLED=true`.
- **Three allowed actions**, each needing an explicit, case-bound confirmation with a 5-minute hash-bound proposal: `DEACTIVATE_VAS`, `SEND_SETTINGS_INSTRUCTIONS`, `CREATE_REVIEW_TICKET`.
- **Live operation status:** PENDING until the worker records SUCCEEDED or FAILED; pending is never shown as success. Idempotency keys give exactly one account change on retries.
- **Trust Receipt:** append-only, with a SHA-256 digest.
- **Trilingual chat:** English, Sinhala and Tamil, including Singlish and Tanglish. An EN / Sinhala / Tamil interface toggle shows draft (unreviewed) Sinhala and Tamil strings.
- **Voice and text on one engine:** final voice transcripts and typed messages use the same conversation service.
- **Grounded how-to answers** from approved knowledge cards only.
- **Package activation (feature-flagged, off in the demo):** a fourth action, `ACTIVATE_PACKAGE`, is in the code and contract v1.2.0 but stays disabled unless `RESOLVE_PACKAGE_ACTIVATION_ENABLED=true`.
- **Optional HubSpot CRM:** with `CRM_PROVIDER=hubspot`, review tickets are created in HubSpot with the evidence summary, linked to a synthetic contact and kept in sync with the agent's notes and closure. The hosted demo uses the built-in mock CRM.
- **Agent dashboard (`/agent`):** review queue, evidence, actions, conversation, receipt and history, internal notes and review status.

## Technology stack

| Layer | Technology (pinned versions) |
| --- | --- |
| Frontend | React 19, TypeScript 5.9, Vite 8, Tailwind CSS 4, shadcn/ui (Radix UI), React Router, Lucide, Sonner |
| Backend | Python 3.12, FastAPI 0.115.12, Uvicorn 0.34, Pydantic 2.13, SQLAlchemy 2.0.41, Alembic 1.18.5, psycopg 3, httpx 0.28 |
| Data | PostgreSQL 18 (Docker, `postgres:18-alpine`): `sandbox` schema (synthetic CRM, charging, recharge, VAS, usage, incidents) and `resolve` schema (cases, investigations, proposals, operations, receipts, audit) |
| AI | Google Gemini via `google-genai` 2.19.0 |
| Voice | Zeptaz Voice adapter (separate repo): Python, FastAPI, WebSocket PCM16 audio, HMAC-SHA256 to Resolve |
| Testing | pytest 8.4, Playwright 1.63, oxlint |

## Architecture overview

```
Browser (React)  ── customer chat /, voice panel, agent dashboard /agent
   │  HTTP /api/v1 (session cookie + CSRF)        │ WebSocket audio
   ▼                                              ▼
Resolve backend (FastAPI, ONE process)       Zeptaz Voice service ── Gemini Live
 ├─ API routes ◄──────── HMAC-signed final transcripts ─┘
 ├─ Conversation service ── Google Gemini API (extract · rewrite · answer)
 ├─ Resolve facade → Investigation engine (ledger, quota, service rules)
 └─ Action worker (leased, idempotent) → receipts
   │
   ▼
PostgreSQL 18:  resolve schema (app role)  ·  sandbox schema (synthetic; separate write role)
   ┊ replaced by adapters in production
Future HUTCH systems: NOT connected (identity, billing, VAS, usage, CRM)
```

There is no separate chatbot service, message broker or Redis. The full diagram (Figure 1) and data flow are in the Solution & Technical Document.

## Repository map

| Path | Contents |
| --- | --- |
| `backend/resolve/app/` | FastAPI app, auth/sessions, account, case, action, review, conversation and voice routes |
| `backend/resolve/conversation/` | Conversation module: extraction, routing, rewrite, grounded answers, telemetry |
| `backend/resolve/providers/` | Sandbox adapters and deterministic reconciliation |
| `backend/resolve/migrations/` | Alembic migrations |
| `database/` | Bootstrap, SQL schemas, seed fixture v3 (eleven lines, itemised usage), knowledge cards |
| `frontend/` | React app (customer `/` and agent `/agent`), Playwright tests in `frontend/e2e/` |
| `docs/contracts/` | OpenAPI 3.1 contract (`openapi.json`) and examples |
| `scripts/` | Start / stop / reset scripts and fixture generator |
| `tests/` | Backend unit and opt-in PostgreSQL integration tests |

## Setup and installation

**Requirements:** Docker Desktop, Python 3.12, Node.js 20.19+ or 22.12+ with npm (required by Vite 8), and a Google Gemini API key (paid tier recommended; the free tier runs out of quota quickly).

1. **Configure the environment**
   ```sh
   cp .env.example .env
   ```
   Edit `.env`: set local database passwords, replace `APP_SECRET_KEY`, and add:
   ```
   GEMINI_API_KEY=<your key>
   GEMINI_TEXT_MODEL=gemini-3.5-flash-lite
   ```
   Demo sign-in stays disabled until `DEMO_IDENTITIES_JSON` holds credential hashes; the demo identities are shared separately with the judges.

2. **Start PostgreSQL** (a fresh volume applies the SQL schemas and loads the first fixture run)
   ```powershell
   # Windows
   powershell -ExecutionPolicy Bypass -File scripts/start.ps1
   ```
   ```sh
   # macOS / Linux
   docker compose --env-file .env up -d --wait
   ```

3. **Install the backend and apply migrations**
   ```sh
   python -m venv .venv
   source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements-dev.txt
   python -m alembic upgrade head
   ```

4. **Create a fresh synthetic fixture run** (retires earlier runs and revokes old sessions)
   ```powershell
   # Windows
   powershell -ExecutionPolicy Bypass -File scripts/reset.ps1
   ```
   ```sh
   # macOS / Linux
   python scripts/seed_run.py "$(uuidgen)" /tmp/hutch-seed.sql --retire-active
   docker compose --env-file .env cp /tmp/hutch-seed.sql postgres:/tmp/hutch-seed-run.sql
   docker compose --env-file .env exec -T postgres sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/hutch-seed-run.sql'
   ```

5. **Install the frontend**
   ```sh
   cd frontend && npm ci
   ```

## How to run

```sh
# Terminal 1: Resolve backend on http://127.0.0.1:8080
python -m uvicorn backend.resolve.app.main:app --host 127.0.0.1 --port 8080

# Terminal 2: frontend on http://localhost:5173 (proxies /api to :8080)
cd frontend && npm run dev
```

- Landing page: **http://localhost:5173/**
- Customer chat: **http://localhost:5173/chat**
- Agent dashboard: **http://localhost:5173/agent**
- Health: `GET /api/v1/healthz` (process) · `GET /api/v1/readyz` (database and migrations)

**Voice (optional):** run the Zeptaz Voice service from `Zeptaz/hutch_zeptazvoice` (tag `v1.0.1` on `main`) following its README. Set Resolve's `VOICE_BASE_URL` and `VOICE_HMAC_SECRET`; the secret must equal Voice's `HUTCH_RESOLVE_HMAC_SECRET`, and Voice must allow the browser origin. Text chat works without Voice.

**Suggested demo:** case A (balance deduction → evidence → stop VAS → Trust Receipt), then case D (LKR 70 conflict → no account change → review ticket in `/agent`).

## How to test

```sh
# Backend unit tests (fakes; no database needed). v1.0.1: 529 passed, 49 PostgreSQL-gated skipped
python -m pytest -q

# Conversation module only
python -m pytest tests/conversation -q

# Opt-in integration tests against a THROWAWAY PostgreSQL (never the shared one)
sh tests/conversation/run_integration.sh

# Frontend checks
cd frontend
npm run typecheck && npm run lint
npx playwright install chromium
npm run test:e2e          # mock-mode browser tests on an isolated Vite port
```

The live Playwright suite (`npm run test:e2e:live`) changes data and must only run against a disposable, freshly migrated database; see `frontend/e2e/README.md`.

## API and external service requirements

| Service | Required? | Notes |
| --- | --- | --- |
| Google Gemini API | Yes for free-text chat and voice | `GEMINI_API_KEY`, `GEMINI_TEXT_MODEL`. There is no automatic fallback model; on a timeout or HTTP 429 the chat shows structured buttons. Without a key, structured buttons still work and voice returns a safe "unavailable" response. |
| Docker / PostgreSQL 18 | Yes | Local only, bound to `127.0.0.1:55432`; never expose it to a network. |
| Zeptaz Voice service | Optional | Separate process; needed only for the voice call panel. |
| HubSpot CRM API | Optional | Only with `CRM_PROVIDER=hubspot` and `HUBSPOT_ACCESS_TOKEN`; the default `mock` keeps tickets in the sandbox. Sends only case references, a synthetic summary and the synthetic contact. |
| HUTCH systems | No | Not connected. All adapters read and write the synthetic sandbox. |

The prototype's own API (all under `/api/v1`) is defined in `docs/contracts/openapi.json` (OpenAPI 3.1). These are prototype endpoints, not HUTCH endpoints.

**Secrets** (`GEMINI_API_KEY`, `APP_SECRET_KEY`, `VOICE_HMAC_SECRET`, voice grant keys, database passwords, `DEMO_IDENTITIES_JSON`) live only in `.env`, which Git ignores. Never commit them.

## Known limitations

- **No HUTCH integration:** synthetic sandbox only; production would replace the adapters.
- **Demo identity:** pre-set demo sign-in; no OTP or real customer verification.
- **Voice:** tested with a fake model and a live probe using real Gemini Live and synthesized speech (9/9 turns spoken); a physical microphone call has not been verified. Calls are capped at 120 s.
- **AI quota:** the Gemini free tier ran out during testing; a paid key is needed for a reliable demo. There is no automatic fallback model; quota errors fall back to structured buttons.
- **Languages:** Sinhala and Tamil test cases, UI strings and machine-rewritten replies have not been reviewed by a fluent speaker; the interface toggle shows the draft strings.
- **AI accuracy:** extraction scored 71/71 on a 71-case team-written set (`extract-v7`); an earlier 57-case run had 2 timeouts. Critical values are confirmed before any action.
- **CRM:** the HubSpot adapter is verified with one worker; a lease overrun with several workers is a known open issue.
- **Package activation:** implemented behind a flag that is off by default; not part of the demo.
- **Knowledge:** 12 seeded cards (all from public HUTCH pages); 7 further drafts from public HUTCH pages are unreviewed and excluded.
- **Scale:** designed for one Resolve worker and one Voice worker; multi-worker soak testing is open.
- **Setup scripts** are PowerShell; macOS/Linux commands are given above.
- **Status updates** use polling, not push.

## Third-party components

| Component | Use | Licence |
| --- | --- | --- |
| FastAPI, Uvicorn, Pydantic, SQLAlchemy, Alembic, psycopg, httpx, python-dotenv, cryptography | Backend | Open source (see each package) |
| google-genai | Gemini SDK | Apache 2.0 |
| PostgreSQL 18 (Docker image) | Database | PostgreSQL Licence |
| React, Vite, TypeScript, Tailwind CSS, shadcn/ui, Radix UI, React Router, Lucide, Sonner | Frontend | Open source (see each package) |
| Noto Sans Sinhala / Tamil, Plus Jakarta Sans | Fonts | SIL Open Font Licence |
| pytest, Playwright, oxlint | Testing | Open source |
| Google Gemini API | Hosted AI service | Google terms of service |
| HubSpot CRM API (optional) | Review tickets in a real CRM | HubSpot terms of service |
| **Zeptaz Voice service** | Voice layer | **Pre-existing team IP**, disclosed; kept in a separate repository. The Resolve adapter and integration are part of this submission. |

Exact versions are pinned in `requirements*.txt` and `frontend/package-lock.json`.

## AI tools and models used

**In the product**

| Purpose | Model |
| --- | --- |
| Complaint extraction, reply rewrite, how-to answers | `gemini-3.5-flash-lite` (prompts `extract-v7`, `rewrite-v3`, `answer-v3`) |
| Voice (speech in/out) | `gemini-3.1-flash-live-preview` (Gemini Live) |

Prompts are versioned, outputs forced to a JSON schema at temperature 0, and every model output is checked by code before use. Retrieval is lightweight: top-3 matching knowledge cards, no vector database or fine-tuning. Arithmetic, evidence state, eligibility, confirmation, actions and receipts are never delegated to the model.

**Token usage (measured):** 25 logged extraction calls on 3 October 2026 averaged 1,788 input / 127 output / 1,915 total tokens (median latency 1.6 s). At Google's Standard-tier price for `gemini-3.5-flash-lite` (US$0.30 / US$2.50 per 1M input / output tokens, pricing page updated 1 October 2026), a text journey costs about US$0.0017 in English and US$0.0037 in Singlish. That is about US$2.72 per 1,000 journeys, assuming 2 or 5 calls per journey (see Section 9 of the Solution & Technical Document; Section 17 has the 16-week implementation plan and Gantt chart).

**In development**

AI coding assistants used: Naveen Harry: OpenAI Codex; Tevin Silverster: Claude (Anthropic); Jayith Wijethunge: Claude Code (Anthropic); Ojith Adithya: Claude (Anthropic). AI-generated code was used, and all of it was reviewed and tested by the team, which is responsible for its correctness, security and originality.

## Hosted demo

The live demo at https://resolve.zeptaz.com follows `main`:

- **Vercel** serves the React app; `frontend/vercel.json` sends `/api/*` to Resolve.
- **Railway** runs Resolve (`railway.json` runs `scripts/hosted_db_setup.py`, then Uvicorn), the Voice service and PostgreSQL 18.
- The hosted demo uses the built-in mock CRM and synthetic data only. Demo sign-in details are shared separately with the judges and are never committed.
- `database/seed.sql` only inserts missing rows (`ON CONFLICT DO NOTHING`). When it declares a newer `fixture_version` than the hosted run, `hosted_db_setup.py` re-applies it, which adds the new lines and records and never changes or removes existing data.

## Project documents

| Document | Contents |
| --- | --- |
| [`context.md`](context.md) | Team source of truth: status, verification results and open items |
| [`docs/contracts.md`](docs/contracts.md) | Canonical API, authorization and data contracts |
| [`docs/contracts/openapi.json`](docs/contracts/openapi.json) | OpenAPI 3.1 contract for all `/api/v1` routes |
| [`docs/plans/`](docs/plans) | Owner plans for backend, conversation, frontend and the HubSpot CRM work |
| [`docs/changelogs/`](docs/changelogs) | Chatbot and core-engine change logs |

## Submission materials

| Item | Where |
| --- | --- |
| Solution & Technical Document (PDF) | Provided with the IgnitX 2026 submission |
| Presentation deck | Provided with the IgnitX 2026 submission |
| Demo video | To be added |
| Live demo | https://resolve.zeptaz.com |
| Demo credentials | Shared separately with the judges |
