from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import (  # noqa: E402
    FakeConversationRepository,
    FakeKnowledgeRepository,
    FakeModel,
    FakeResolveFacade,
    RecordingTelemetry,
)
from resolve.conversation import ConversationService  # noqa: E402
from resolve.conversation.extraction import Extractor  # noqa: E402
from resolve.conversation.dto import AuthContext, Channel, Language, NormalizedTurn, Role  # noqa: E402

SIM_START = datetime(2026, 10, 2, 2, 30, tzinfo=UTC)
SIM_END = datetime(2026, 10, 2, 6, 30, tzinfo=UTC)

ACCOUNT_A = UUID("20000000-0000-0000-0000-000000000001")
ACCOUNT_D = UUID("20000000-0000-0000-0000-000000000004")
ACCOUNT_PARTIAL = UUID("20000000-0000-0000-0000-000000000099")
SANDBOX = UUID("00000000-0000-0000-0000-000000000001")


class Clock:
    def __init__(self) -> None:
        self.now = SIM_END

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def customer(account_id: UUID, channel: Channel = Channel.TEXT) -> AuthContext:
    return AuthContext(
        session_id=uuid4(),
        principal_id=f"demo:{account_id}",
        role=Role.CUSTOMER,
        sandbox_id=SANDBOX,
        account_id=account_id,
        request_id=uuid4(),
        channel=channel,
    )


def guest() -> AuthContext:
    return AuthContext(session_id=uuid4(), principal_id="guest", role=Role.GUEST, request_id=uuid4(), channel=Channel.TEXT)


class Harness:
    def __init__(self, model: FakeModel | None = None, budget: float = 6.0) -> None:
        self.clock = Clock()
        self.repo = FakeConversationRepository(self.clock)
        self.facade = FakeResolveFacade(
            self.clock, {ACCOUNT_A: "A", ACCOUNT_D: "D", ACCOUNT_PARTIAL: "PARTIAL"}
        )
        self.knowledge = FakeKnowledgeRepository()
        self.model = model
        extractor = Extractor(model, budget_seconds=budget) if model else None

        async def simulation_now(ctx):
            return self.clock()

        self.telemetry = RecordingTelemetry()
        self.service = ConversationService(self.facade, self.repo, self.knowledge, extractor, simulation_now, self.telemetry)

    def open(self, ctx: AuthContext) -> UUID:
        return self.repo.create(ctx)

    def version(self, conversation_id: UUID) -> int:
        return self.repo.conversations[conversation_id].version

    def state(self, conversation_id: UUID):
        return self.repo.conversations[conversation_id].state

    def turn(self, conversation_id: UUID, input: dict, *, turn_id: UUID | None = None, version: int | None = None,
             language: Language = Language.EN, channel: Channel = Channel.TEXT, voice_evidence=None) -> NormalizedTurn:
        return NormalizedTurn.model_validate(
            {
                "conversation_id": conversation_id,
                "turn_id": turn_id or uuid4(),
                "channel": channel,
                "language": language,
                "input": input,
                "expected_version": version if version is not None else self.version(conversation_id),
                "voice_evidence": voice_evidence,
            }
        )

    def send(self, ctx: AuthContext, turn: NormalizedTurn):
        return asyncio.run(self.service.handle_turn(ctx, turn))


@pytest.fixture
def h() -> Harness:
    return Harness()


@pytest.fixture
def hm() -> Harness:
    """Harness with a scripted model for free-text routing."""
    return Harness(model=FakeModel())


def text(value: str) -> dict:
    return {"type": "text", "text": value}


def details(complaint_type: str = "BALANCE_RECHARGE", start: datetime = SIM_START, end: datetime = SIM_END, **facts) -> dict:
    return {
        "type": "complaint_details",
        "complaint_type": complaint_type,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "reported_facts": facts,
    }
