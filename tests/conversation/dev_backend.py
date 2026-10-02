"""HUTCH Resolve DEV BACKEND: simulation for testing the conversation module end to end.

NOT Harry's API and not for deployment. It serves the /api/v1 customer routes that
Jayith's chat calls in live mode, using Tevin's real ConversationService and real Gemini.

    RESOLVE_BACKEND=dummy  python tests/conversation/dev_backend.py   # default
    RESOLVE_BACKEND=hybrid python tests/conversation/dev_backend.py   # Harry's real facade where it exists

dummy   All Resolve answers come from tests/conversation/fakes.py: contract examples for A/D,
        stand-ins for B/C/E/F, simulated operation success and receipts.
hybrid  Harry's real ResolveFacade (adapter) for all four complaint types (his H-06 landed in
        ResolveDev 9ab23d9), offers, confirmations, operations (his OperationRunner) and receipts;
        only turn storage and sign-in are still dev stand-ins. RESOLVE_DEV_FAULTS=1 enables Harry's
        seeded single-use fault profiles exactly as his app does (first A/D reads etc. show faults);
        off by default so demos are predictable. Needs a migrated database:
        RESOLVE_DEV_DATABASE_URL / RESOLVE_DEV_SANDBOX_URL (use a throwaway one).
real    Not available until Harry ships turn storage and the conversation routes.

Just open the chat: in dummy mode you are one DEMO CUSTOMER whose records hold every
problem (the matching fixture answers each complaint), and replies follow the language you
write in. Optional: test one specific line at  http://localhost:5174/api/v1/dev .
Limits: no real auth/CSRF; turn storage is in memory; restarting forgets everything; the demo
customer's records adapt to the complaint (a simulation shortcut, not evidence).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4, uuid5

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(REPO), str(REPO / "backend"), str(HERE)]

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request, Response  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse  # noqa: E402

import conftest  # noqa: E402  (fixture account IDs and clock)
from fakes import FakeConversationRepository, FakePackagePort, FakeResolveFacade, RecordingTelemetry  # noqa: E402
from resolve.conversation import ConversationService, opening_question  # noqa: E402
from resolve.conversation.rewrite import ReplyRewriter  # noqa: E402
from resolve.conversation.agent import PackageAgent  # noqa: E402
from resolve.conversation.answer import GroundedAnswerer  # noqa: E402
from resolve.conversation.dto import (  # noqa: E402
    AuthContext,
    Channel,
    ComplaintType,
    EscalationRequest,
    KnowledgeCard,
    MessageRequest,
    NormalizedTurn,
    OperationStatus,
    ReceiptReference,
    Role,
)
from resolve.conversation.errors import ResolveError  # noqa: E402
from resolve.conversation.extraction import Extractor  # noqa: E402
from resolve.conversation.model import GeminiModelClient  # noqa: E402
from resolve.conversation.state import PendingProposalRef  # noqa: E402
from resolve.conversation.try_extract import load_dotenv  # noqa: E402

MODE = os.environ.get("RESOLVE_BACKEND", "dummy").lower()
# Dummy default: the all-problems demo customer. Hybrid uses Harry's real fixtures, one problem per line.
DEFAULT_LINE = os.environ.get("DEV_LINE", "DEMO" if MODE == "dummy" else "A").upper()
PORT = int(os.environ.get("DEV_BACKEND_PORT", "8080"))
LINES = {
    "A": ("SIM-LK-0001", "Balance adds up; video-alerts add-on renews"),
    "B": ("SIM-LK-0002", "Data bundle used up, out-of-bundle charge"),
    "C": ("SIM-LK-0003", "No connection: regional incident"),
    "D": ("SIM-LK-0004", "Balance does not add up (LKR 70 conflict)"),
    "E": ("SIM-LK-0005", "Reload paid but not credited"),
    "F": ("SIM-LK-0006", "Charged for a service with no activation record"),
}
ACCOUNTS = {k: getattr(conftest, f"ACCOUNT_{k}") for k in LINES}
DEMO_ACCOUNT = UUID("20000000-0000-0000-0000-0000000000de")
ACCOUNTS["DEMO"] = DEMO_ACCOUNT
# The demo customer's reported description is Gemini's English summary; this only picks a fixture.
MISSING_RECHARGE = re.compile(
    r"(not|n't|never)\b.{0,40}\b(credit|add|arriv|receiv|reflect|show|come|came|go through|went through)"
    r"|\b(missing|pending|didn't get|did not get)\b", re.IGNORECASE)
DUMMY_SIMULATED_SUCCESS_AFTER = timedelta(seconds=4)
HARRY_COMPLAINTS = set(ComplaintType)  # all implemented on ResolveDev 9ab23d9 (H-06); keep the switch for regressions


def real_now() -> datetime:
    return datetime.now(UTC)


# --- knowledge: the 12 reviewed cards from database/knowledge_seed.sql -------------


def _sql_values(row: str) -> list[str]:
    values, i = [], 0
    while i < len(row):
        if row[i] == "'":
            j, chunk = i + 1, []
            while j < len(row):
                if row[j] == "'" and j + 1 < len(row) and row[j + 1] == "'":
                    chunk.append("'"); j += 2
                elif row[j] == "'":
                    break
                else:
                    chunk.append(row[j]); j += 1
            values.append("".join(chunk)); i = j + 1
        elif row[i].isdigit():
            j = i
            while j < len(row) and row[j].isdigit():
                j += 1
            values.append(row[i:j]); i = j
        else:
            i += 1
    return values


def load_knowledge() -> list[tuple[KnowledgeCard, set[str]]]:
    cards = []
    for line in (REPO / "database" / "knowledge_seed.sql").read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith("('"):
            continue
        v = _sql_values(line)
        card = KnowledgeCard(article_id=v[0], article_key=v[1], language=v[2], title=v[3], content=v[4],
                             url=v[6], reviewed_at=v[7], version=int(v[8]), scope=v[9])
        aliases = {a.strip('"').lower() for a in v[5].strip("{}").split(",") if a}
        cards.append((card, aliases | set(re.findall(r"[a-z]+", (v[3] + " " + v[1]).lower()))))
    drafts = json.loads((REPO / "backend/resolve/conversation/knowledge/hutch_public_drafts.json").read_text(encoding="utf-8"))
    for i, d in enumerate(drafts["cards"]):  # proposed additions for Harry's seed; dev only
        card = KnowledgeCard(article_id=UUID(int=0x91000000_0000_4000_8000_000000000100 + i), article_key=d["article_key"],
                             language="en", title=d["title"], content=d["content"], url=d["url"],
                             reviewed_at=drafts["fetched_at"], version=1, scope=d.get("scope", "PUBLIC"))
        terms = {w for a in d["aliases"] for w in re.findall(r"[a-z]+", a.lower())} | set(re.findall(r"[a-z]+", d["title"].lower()))
        cards.append((card, terms))
    return cards


class DemoPersonaFacade(FakeResolveFacade):
    """Dummy facade where the DEMO customer's records contain every fixture problem."""

    def scenario_for(self, ctx, request) -> str:
        if ctx.account_id != DEMO_ACCOUNT:
            return super().scenario_for(ctx, request)
        complaint = request.complaint_type
        if complaint is ComplaintType.BALANCE_RECHARGE:
            facts = request.reported_facts
            return "E" if facts.recharge_reference or MISSING_RECHARGE.search(facts.description or "") else "A"
        return {ComplaintType.DATA_DEPLETION: "B", ComplaintType.CONNECTIVITY: "C", ComplaintType.VAS_DISPUTE: "F"}[complaint]

    async def get_account(self, ctx):
        account = await super().get_account(ctx)
        return account.model_copy(update={"line_alias": "SIM-LK-DEMO", "display_name": "Demo customer"}) if ctx.account_id == DEMO_ACCOUNT else account


class SeedKnowledgeRepository:
    """Bounded lexical lookup standing in for Harry's KnowledgeRepository."""

    def __init__(self) -> None:
        self._cards = load_knowledge()

    async def search(self, ctx, query, language, limit=3):
        words = set(re.findall(r"[a-z]+", query.lower()))
        scored = sorted(((len(words & terms), card) for card, terms in self._cards), key=lambda x: -x[0])
        return [card for score, card in scored if score > 0][:limit]


# --- hybrid: route each case to Harry's real facade or the dummy ---------------------


class HybridFacade:
    """Harry's facade for the paths he has implemented; the dummy for the rest.

    Owner of every case/proposal/operation is remembered so later calls go to the same side.
    Works around integration finding #1 (create_case also bumps the conversation version) by
    tracking the database conversation version itself; dev-only.
    """

    def __init__(self, real, dummy) -> None:
        self.real, self.dummy = real, dummy
        self.owner: dict[UUID, Any] = {}
        self.db_version: dict[UUID, int] = {}
        self._seen_turns: set[tuple[UUID, UUID]] = set()

    def _of(self, key: UUID):
        return self.owner.get(key, self.dummy)

    async def get_account(self, ctx):
        return await self.real.get_account(ctx)

    async def create_case(self, ctx, conversation_id, turn_id, complaint_type, *, expected_conversation_version):
        if complaint_type in HARRY_COMPLAINTS:
            version = self.db_version.setdefault(conversation_id, 1)
            case = await self.real.create_case(ctx, conversation_id, turn_id, complaint_type, expected_conversation_version=version)
            if (conversation_id, turn_id) not in self._seen_turns:
                self._seen_turns.add((conversation_id, turn_id))
                self.db_version[conversation_id] = version + 1
            self.owner[case.id] = self.real
            return case
        case = await self.dummy.create_case(ctx, conversation_id, turn_id, complaint_type,
                                            expected_conversation_version=expected_conversation_version)
        self.owner[case.id] = self.dummy
        return case

    async def get_case(self, ctx, case_id):
        return await self._of(case_id).get_case(ctx, case_id)

    async def investigate(self, ctx, case_id, request, command_key):
        return await self._of(case_id).investigate(ctx, case_id, request, command_key)

    async def propose_action(self, ctx, case_id, request, command_key):
        side = self._of(case_id)
        proposal = await side.propose_action(ctx, case_id, request, command_key)
        self.owner[proposal.id] = side
        return proposal

    async def confirm_action(self, ctx, proposal_id, request, command_key, voice_evidence=None):
        side = self._of(proposal_id)
        result = await side.confirm_action(ctx, proposal_id, request, command_key, voice_evidence)
        if result.operation:
            self.owner[result.operation.id] = side
        return result

    async def prepare_escalation(self, ctx, case_id, request, command_key):
        side = self._of(case_id)
        proposal = await side.prepare_escalation(ctx, case_id, request, command_key)
        self.owner[proposal.id] = side
        return proposal

    async def get_operation(self, ctx, operation_id):
        return await self._of(operation_id).get_operation(ctx, operation_id)

    async def get_receipt(self, ctx, case_id, revision=None):
        return await self._of(case_id).get_receipt(ctx, case_id, revision)


# --- wiring ---------------------------------------------------------------------------

RUN_ID = conftest.SANDBOX  # the fixture run; hybrid mode switches to the database's active run


def main_database_urls() -> tuple[str, str]:
    """App-role and sandbox-role URLs for the compose PostgreSQL, from the repo's .env (defaults as in compose.yaml)."""
    from urllib.parse import quote

    env = {}
    path = REPO / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                env[key.strip()] = value.strip().strip('"').strip("'")
    get = lambda key, default: os.environ.get(key) or env.get(key) or default  # noqa: E731
    host = f"127.0.0.1:{get('POSTGRES_PORT', '55432')}/{get('POSTGRES_DB', 'hutch_resolve')}"
    app = f"{quote(get('RESOLVE_DB_USER', 'hutch_resolve_app'))}:{quote(get('RESOLVE_DB_PASSWORD', 'resolve-local-change-me'))}"
    box = f"{quote(get('SANDBOX_DB_USER', 'hutch_sandbox'))}:{quote(get('SANDBOX_DB_PASSWORD', 'sandbox-local-change-me'))}"
    return f"postgresql+psycopg://{app}@{host}", f"postgresql+psycopg://{box}@{host}"


def active_run(engine) -> UUID:
    """The newest ACTIVE sandbox run (Harry's reset retires older ones); the fixture run if none is marked."""
    from sqlalchemy import text

    with engine.connect() as connection:
        found = connection.execute(text(
            "SELECT id FROM sandbox.sandbox_runs WHERE run_status='ACTIVE' ORDER BY created_at DESC LIMIT 1")).scalar_one_or_none()
    return UUID(str(found)) if found else conftest.SANDBOX


load_dotenv(REPO / ".env")
if MODE == "real":
    sys.exit("RESOLVE_BACKEND=real is not available yet: it needs Harry's turn storage and conversation routes.")
if MODE not in {"dummy", "hybrid"}:
    sys.exit("RESOLVE_BACKEND must be dummy, hybrid or real")

client = GeminiModelClient.from_env()
dummy = DemoPersonaFacade(real_now, {account: name for name, account in ACCOUNTS.items() if name != "DEMO"}, strict_complaints=True)
repo = FakeConversationRepository(real_now, lease=timedelta(seconds=30))
telemetry = RecordingTelemetry()
facade: Any = dummy
harry_facade = harry_store = runner = sandbox_engine = None

if MODE == "hybrid":
    from sqlalchemy import create_engine, text

    from backend.resolve.app.auth_store import AuthStore
    from backend.resolve.providers.sandbox import PostgresSandboxProvider
    from backend.resolve.services.facade import ResolveFacade
    from backend.resolve.services.operations import OperationRunner
    from resolve.conversation.resolve_adapter import ResolveFacadeAdapter

    db_url, sandbox_url = os.environ.get("RESOLVE_DEV_DATABASE_URL"), os.environ.get("RESOLVE_DEV_SANDBOX_URL")
    if os.environ.get("RESOLVE_DEV_DB") == "main":  # the repo's compose PostgreSQL, credentials from .env (never printed)
        db_url, sandbox_url = main_database_urls()
    if not (db_url and sandbox_url):
        sys.exit("hybrid needs RESOLVE_DEV_DB=main or RESOLVE_DEV_DATABASE_URL and RESOLVE_DEV_SANDBOX_URL (a migrated database)")
    app_engine, sandbox_engine = create_engine(db_url), create_engine(sandbox_url)
    RUN_ID = active_run(sandbox_engine)
    if RUN_ID != conftest.SANDBOX:
        # Harry's seed_run.py derives every fixture ID of a new run as uuid5(run, original ID).
        ACCOUNTS.update({line: uuid5(RUN_ID, str(account)) for line, account in ACCOUNTS.items() if line != "DEMO"})
    faults = os.environ.get("RESOLVE_DEV_FAULTS", "0") == "1"
    provider = PostgresSandboxProvider(app_engine, sandbox_engine if faults else None)  # main.py passes the sandbox engine
    harry_facade = ResolveFacade(app_engine, provider, cursor_secret=os.urandom(32))
    harry_store = AuthStore(app_engine)
    runner = OperationRunner(app_engine, sandbox_engine)
    facade = HybridFacade(ResolveFacadeAdapter(harry_facade), dummy)


async def simulation_now(ctx: AuthContext) -> datetime:
    if sandbox_engine is None:
        return conftest.SIM_END
    def read() -> datetime:
        with sandbox_engine.connect() as connection:
            return connection.execute(text("SELECT simulation_clock FROM sandbox.sandbox_runs WHERE id=:id"),
                                      {"id": ctx.sandbox_id}).scalar_one()
    return await asyncio.to_thread(read)


rewriter = ReplyRewriter(client) if client and os.environ.get("DEV_REPLY_REWRITE", "1") != "0" else None
# Dev only: the free tier is currently slower than the 6 s contract budget; production keeps 6 s.
extract_budget = float(os.environ.get("DEV_EXTRACT_BUDGET", "6"))
# Package prototype (packages.py, agent.py): dummy mode only; Harry's facade has no package service yet.
packages = FakePackagePort(dummy) if MODE == "dummy" and os.environ.get("DEV_PACKAGES", "1") != "0" else None
service = ConversationService(facade, repo, SeedKnowledgeRepository(),
                              Extractor(client, budget_seconds=extract_budget) if client else None,
                              simulation_now, telemetry, rewriter,
                              {Channel.TEXT: max(15.0, extract_budget * 2 + 3)},
                              answerer=GroundedAnswerer(client, budget_seconds=extract_budget) if client else None,
                              packages=packages,
                              package_agent=PackageAgent(client, step_budget_seconds=extract_budget) if client and packages else None)
sessions: dict[str, tuple[AuthContext, str]] = {}
COOKIE = "resolve_customer_session"


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_run_operations()) if runner else None
    yield
    if task:
        task.cancel()


async def _run_operations() -> None:
    """Harry's OperationRunner, polled the way his app does (hybrid only)."""
    while True:
        try:
            worked = await asyncio.to_thread(runner.run_once)
        except Exception as err:  # noqa: BLE001
            print(f"[runner] {type(err).__name__}: {err}", flush=True)
            worked = False
        await asyncio.sleep(0.2 if worked else 1.0)


app = FastAPI(title=f"DEV backend ({MODE}) - simulation, not Harry's API", lifespan=lifespan)


def error(code: str, message: str = "") -> JSONResponse:
    err = ResolveError(code, message)
    return JSONResponse(status_code=err.http_status, content={"error": {
        "code": code, "message": err.message, "retryable": err.retryable, "request_id": str(uuid4()), "details": err.details}})


def new_session(line: str) -> tuple[str, AuthContext]:
    token, session_id = uuid4().hex, uuid4()
    ctx = AuthContext(session_id=session_id, principal_id=f"dev:{line}", role=Role.CUSTOMER, sandbox_id=RUN_ID,
                      account_id=ACCOUNTS[line], request_id=uuid4(), channel=Channel.TEXT)
    if harry_store is not None:
        import secrets

        harry_store.create_session(session_id=session_id, credential_hash=secrets.token_bytes(32), csrf_hash=secrets.token_bytes(32),
                                   role="CUSTOMER", principal_id=ctx.principal_id, sandbox_id=ctx.sandbox_id,
                                   account_id=ctx.account_id, expires_at=real_now() + timedelta(minutes=30))
    sessions[token] = (ctx, line)
    return token, ctx


def session_of(request: Request) -> AuthContext | None:
    found = sessions.get(request.cookies.get(COOKIE, ""))
    return found[0].model_copy(update={"request_id": uuid4()}) if found else None


def session_view(ctx: AuthContext) -> dict:
    return {"id": str(ctx.session_id), "role": ctx.role.value, "account_id": str(ctx.account_id),
            "sandbox_id": str(ctx.sandbox_id), "expires_at": (real_now() + timedelta(minutes=30)).isoformat(),
            "csrf_token": "dev-no-csrf", "simulation": True}


def conversation_view(conversation_id: UUID, cases: list) -> dict:
    conv = repo.conversations[conversation_id]
    state = conv.state
    pending = None
    if state.pending_proposal:
        pending = proposal_cache.get(state.pending_proposal.proposal_id)
    return {
        "id": str(conversation_id), "version": conv.version, "language": state.language.value,
        "active_case_id": str(state.active_case_id) if state.active_case_id else None,
        "expires_at": (real_now() + timedelta(minutes=30)).isoformat(),
        "messages": [m.model_dump(mode="json") for m in conv.messages],
        "cases": [{"id": str(c.id), "complaint_type": c.complaint_type.value, "status": c.status} for c in cases],
        "pending_question": state.pending_question.model_dump(mode="json") if state.pending_question else None,
        "pending_proposal": pending.model_dump(mode="json") if pending else None,
        "operation_ids": [str(op) for c in cases for op in c.operation_ids],
    }


proposal_cache: dict[UUID, Any] = {}
conversation_cases: dict[UUID, list[UUID]] = {}


async def cases_of(ctx: AuthContext, conversation_id: UUID) -> list:
    found = []
    for case_id in conversation_cases.get(conversation_id, []):
        try:
            found.append(await facade.get_case(ctx, case_id))
        except ResolveError:
            pass
    return found


def remember(conversation_id: UUID, result) -> None:
    if result.case_id and result.case_id not in conversation_cases.setdefault(conversation_id, []):
        conversation_cases[conversation_id].append(result.case_id)
    for card in result.cards:
        if card.type == "confirmation":
            proposal_cache[card.data.id] = card.data


# --- dev pages ------------------------------------------------------------------------


@app.get("/api/v1/dev", response_class=HTMLResponse)
async def dev_home(request: Request):
    found = sessions.get(request.cookies.get(COOKIE, ""))
    current = found[1] if found else None
    rows = "".join(
        f'<li><a href="/api/v1/dev/line/{k}"><b>Line {k}</b> {alias}</a> &mdash; {about}{" <i>(current)</i>" if k == current else ""}</li>'
        for k, (alias, about) in LINES.items()
    )
    model = client.model_name if client else "none: free text falls back to forms"
    faults = " Seeded faults are <b>on</b> (as in Harry's app)." if os.environ.get("RESOLVE_DEV_FAULTS") == "1" else ""
    source = ("All complaints use <b>Harry's real facade</b> (his records, offers, runner and receipts)." + faults
              if MODE == "hybrid" else "All Resolve answers are dummy stand-ins.")
    demo = ('<p><a href="/api/v1/dev/line/DEMO"><b>Demo customer</b></a> (default): one customer whose records hold '
            'every problem; just describe yours in any language.</p><p>Or test one specific line:</p>'
            if MODE == "dummy" else "<p>Choose a demo line; the chat opens signed in as that line:</p>")
    return f"""<!doctype html><meta charset="utf-8"><title>Resolve dev backend</title>
<body style="font-family:system-ui;max-width:640px;margin:2rem auto;padding:0 1rem;line-height:1.6">
<h2>Resolve dev backend: {MODE.upper()} mode</h2>
<p><b>Simulation only.</b> Gemini model: {model}.<br>
{source}</p>
{demo}<ul>{rows}</ul></body>"""


@app.get("/api/v1/dev/line/{line}")
async def dev_line(line: str):
    line = line.upper()
    if line not in ACCOUNTS or (line == "DEMO" and MODE != "dummy"):
        return error("RESOURCE_NOT_FOUND", "Unknown demo line")
    token, _ = new_session(line)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax")
    return response


# --- contract routes used by the customer chat -----------------------------------------


@app.get("/api/v1/session")
async def get_session(request: Request):
    ctx = session_of(request)
    return session_view(ctx) if ctx else error("UNAUTHENTICATED", "No session")


@app.post("/api/v1/sessions/anonymous", status_code=201)
async def anonymous(response: Response):
    # The guest->customer upgrade is undecided; the dev backend signs in as DEV_LINE instead.
    token, ctx = new_session(DEFAULT_LINE)
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax")
    return session_view(ctx)


@app.delete("/api/v1/session", status_code=204)
async def logout(request: Request, response: Response):
    sessions.pop(request.cookies.get(COOKIE, ""), None)
    response.delete_cookie(COOKIE)


@app.post("/api/v1/conversations", status_code=201)
async def create_conversation(request: Request):
    ctx = session_of(request)
    if ctx is None:
        return error("UNAUTHENTICATED")
    language = (await request.json()).get("language", "en")
    if harry_facade is not None:
        from backend.resolve.app.auth import AuthContext as HarryContext

        harry_ctx = HarryContext(session_id=ctx.session_id, principal_id=ctx.principal_id, role="CUSTOMER",
                                 sandbox_id=ctx.sandbox_id, account_id=ctx.account_id, request_id=ctx.request_id, channel="TEXT")
        conversation_id = UUID(str((await asyncio.to_thread(harry_facade.create_conversation, harry_ctx, language))["id"]))
        from fakes import _Conversation

        repo.conversations[conversation_id] = _Conversation(session_id=ctx.session_id)
    else:
        conversation_id = repo.create(ctx)
    conv = repo.conversations[conversation_id]
    # Open with free text plus suggested categories (the chat shows them as chips).
    conv.state = conv.state.evolve(language=language, pending_question=opening_question(language))
    return conversation_view(conversation_id, [])


def owned(ctx: AuthContext, conversation_id: UUID) -> bool:
    conv = repo.conversations.get(conversation_id)
    return conv is not None and conv.session_id == ctx.session_id


@app.get("/api/v1/conversations/{conversation_id}")
async def get_conversation(conversation_id: UUID, request: Request):
    ctx = session_of(request)
    if ctx is None:
        return error("UNAUTHENTICATED")
    if not owned(ctx, conversation_id):
        return error("RESOURCE_NOT_FOUND")
    return conversation_view(conversation_id, await cases_of(ctx, conversation_id))


@app.post("/api/v1/conversations/{conversation_id}/messages")
async def send_message(conversation_id: UUID, request: Request):
    ctx = session_of(request)
    if ctx is None:
        return error("UNAUTHENTICATED")
    try:
        message = MessageRequest.model_validate(await request.json())
    except Exception:  # noqa: BLE001
        return error("VALIDATION_ERROR", "Request does not match the expected format")
    try:
        result = await service.handle_turn(ctx, NormalizedTurn.from_message(conversation_id, message))
    except ResolveError as err:
        print(f"[turn] {err.code}: {err.message} {err.details or ''}", flush=True)
        return error(err.code, err.message)
    remember(conversation_id, result)
    for record in telemetry.records:
        if record.request_id == ctx.request_id:
            print(f"[gemini] {record.purpose} {record.model} {record.outcome}{(":" + record.error_type) if record.error_type else ""} {record.latency_ms} ms tokens {record.input_tokens}/{record.output_tokens}", flush=True)
    return result.model_dump(mode="json")


@app.get("/api/v1/cases/{case_id}")
async def get_case(case_id: UUID, request: Request):
    ctx = session_of(request)
    if ctx is None:
        return error("UNAUTHENTICATED")
    try:
        return (await facade.get_case(ctx, case_id)).model_dump(mode="json")
    except ResolveError as err:
        return error(err.code)


@app.get("/api/v1/operations/{operation_id}")
async def get_operation(operation_id: UUID, request: Request):
    ctx = session_of(request)
    if ctx is None:
        return error("UNAUTHENTICATED")
    if operation_id in dummy.operations:
        _simulate_dummy_completion(operation_id)
    try:
        return (await facade.get_operation(ctx, operation_id)).model_dump(mode="json")
    except ResolveError as err:
        return error(err.code)


def _simulate_dummy_completion(operation_id: UUID) -> None:
    """Dummy-owned operations only: flip to SUCCEEDED and issue a receipt reference."""
    operation = dummy.operations[operation_id]
    if operation.status is not OperationStatus.PENDING or real_now() - operation.created_at < DUMMY_SIMULATED_SUCCESS_AFTER:
        return
    if operation.case_id in dummy.package_requests:  # package prototype: debit the price, add the package
        dummy.complete_activation(operation_id, real_now())
        return
    outcome = operation.outcome.model_copy(update={"code": "SIMULATED", "message": "Simulated by the dev backend."})
    if operation.action_type.value == "CREATE_REVIEW_TICKET":
        outcome = outcome.model_copy(update={"provider_ticket_id": f"SIM-TKT-{str(operation.id)[:6]}"})
    dummy.operations[operation_id] = operation.model_copy(update={
        "status": OperationStatus.SUCCEEDED, "updated_at": real_now(), "outcome": outcome,
        "next_step": "Simulated success from the dev backend, not a real provider readback."})
    case = dummy.cases[operation.case_id]
    dummy.cases[operation.case_id] = case.model_copy(update={
        "status": "RESOLVED", "receipt": ReceiptReference(id=uuid4(), revision=1)})


@app.get("/api/v1/cases/{case_id}/receipt")
async def get_receipt(case_id: UUID, request: Request):
    ctx = session_of(request)
    if ctx is None:
        return error("UNAUTHENTICATED")
    try:
        case = await facade.get_case(ctx, case_id)
        if case.receipt is None:
            return error("RESOURCE_NOT_FOUND")
        return (await facade.get_receipt(ctx, case_id)).model_dump(mode="json")
    except ResolveError as err:
        return error(err.code)


@app.post("/api/v1/cases/{case_id}/escalations", status_code=201)
async def escalate(case_id: UUID, request: Request):
    ctx = session_of(request)
    if ctx is None:
        return error("UNAUTHENTICATED")
    try:
        body = EscalationRequest.model_validate(await request.json())
        proposal = await facade.prepare_escalation(ctx, case_id, body, f"dev:escalate:{uuid4()}")
    except ResolveError as err:
        return error(err.code)
    proposal_cache[proposal.id] = proposal
    case = await facade.get_case(ctx, case_id)
    conv = repo.conversations[case.conversation_id]
    conv.state = conv.state.evolve(pending_proposal=PendingProposalRef(
        proposal_id=proposal.id, proposal_hash=proposal.proposal_hash, case_id=case_id,
        action_type=proposal.action_type, expires_at=proposal.expires_at, presented_turn_id=uuid4()))
    return proposal.model_dump(mode="json")


@app.get("/api/v1/agent/session")
async def agent_session():
    return error("UNAUTHENTICATED", "The agent dashboard is not served by the dev backend")


@app.get("/api/v1/healthz")
async def health():
    return {"status": "ok", "dev_backend": MODE, "default_line": DEFAULT_LINE, "model": client.model_name if client else None}


if __name__ == "__main__":
    print(f"DEV BACKEND ({MODE}): run {RUN_ID}, default line {DEFAULT_LINE}, model={client.model_name if client else 'NONE (forms only)'}", flush=True)
    print("Choose a line: http://localhost:5174/api/v1/dev", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
