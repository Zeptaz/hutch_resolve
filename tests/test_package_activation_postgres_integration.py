"""Opt-in proof of the package path against a disposable migrated PostgreSQL."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import create_engine, text


@pytest.mark.skipif(
    not os.getenv("PACKAGE_IT_DATABASE_URL") or not os.getenv("PACKAGE_IT_SANDBOX_DATABASE_URL"),
    reason="requires a disposable migrated PostgreSQL with app and sandbox roles",
)
def test_package_action_replay_debits_once_and_issues_receipt() -> None:
    from backend.resolve.app.auth import AuthContext
    from backend.resolve.app.auth_store import AuthStore
    from backend.resolve.app.auth import ResolveError
    from backend.resolve.providers.sandbox import PostgresSandboxProvider
    from backend.resolve.services.facade import ResolveFacade
    from backend.resolve.services.operations import OperationRunner

    app = create_engine(os.environ["PACKAGE_IT_DATABASE_URL"])
    sandbox = create_engine(os.environ["PACKAGE_IT_SANDBOX_DATABASE_URL"])
    run_id = UUID(os.getenv("PACKAGE_IT_RUN_ID", "11111111-1111-4111-8111-111111111111"))
    account_id = uuid5(run_id, "20000000-0000-0000-0000-000000000001")
    offer_id = uuid5(run_id, "30000000-0000-0000-0000-000000000005")
    session_id = uuid4()
    principal_id = f"package-it:{session_id}"
    now = datetime.now(UTC)

    try:
        AuthStore(app).create_session(
            session_id=session_id,
            credential_hash=hashlib.sha256(uuid4().bytes).digest(),
            csrf_hash=hashlib.sha256(uuid4().bytes).digest(),
            role="CUSTOMER",
            principal_id=principal_id,
            sandbox_id=run_id,
            account_id=account_id,
            expires_at=now + timedelta(hours=1),
        )
        context = AuthContext(
            session_id=session_id,
            principal_id=principal_id,
            role="CUSTOMER",
            sandbox_id=run_id,
            account_id=account_id,
            request_id=uuid4(),
            channel="TEXT",
        )
        provider = PostgresSandboxProvider(app)
        facade = ResolveFacade(
            app,
            provider,
            cursor_secret=b"package-integration-cursor-secret",
            action_execution_available=True,
            package_activation_enabled=True,
        )
        before = provider.get_account(run_id, account_id)
        before_main = next(row["amount_minor"] for row in before["balances"] if row["wallet"] == "MAIN")
        conversation = facade.create_conversation(context)
        command_key = f"package-it:{uuid4()}"
        proposal = facade.propose_package_activation(
            context, UUID(str(conversation["id"])), offer_id, command_key
        )
        replayed_proposal = facade.propose_package_activation(
            context, UUID(str(conversation["id"])), offer_id, command_key
        )
        assert replayed_proposal["id"] == proposal["id"]
        assert proposal["action_type"] == "ACTIVATE_PACKAGE"
        assert proposal["package_terms"]["price_minor"] == 4900
        competing_proposal = facade.propose_package_activation(
            context, UUID(str(conversation["id"])), offer_id, f"package-it:{uuid4()}"
        )
        barrier = Barrier(2)

        def accept(candidate: dict) -> tuple[dict, UUID, dict | str]:
            turn_id = uuid4()
            barrier.wait()
            try:
                result = facade.confirm_action(
                    context,
                    proposal_id=UUID(str(candidate["id"])),
                    proposal_hash=candidate["proposal_hash"],
                    decision="ACCEPT",
                    client_turn_id=turn_id,
                )
                return candidate, turn_id, result
            except ResolveError as error:
                return candidate, turn_id, error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = list(pool.map(accept, (proposal, competing_proposal)))
        accepted = [item for item in attempts if isinstance(item[2], dict)]
        rejected = [item for item in attempts if item[2] == "ACTION_ALREADY_CONFIRMED"]
        assert len(accepted) == len(rejected) == 1
        proposal, client_turn_id, confirmation = accepted[0]
        confirmation_replay = facade.confirm_action(
            context,
            proposal_id=UUID(str(proposal["id"])),
            proposal_hash=proposal["proposal_hash"],
            decision="ACCEPT",
            client_turn_id=client_turn_id,
        )
        assert confirmation_replay["id"] == confirmation["id"]
        operation_id = UUID(str(confirmation["operation_id"]))

        runner = OperationRunner(app, sandbox)
        assert runner.run_once()
        assert not runner.run_once()
        operation = facade.get_operation(context, operation_id)
        receipt = facade.get_receipt(context, UUID(str(proposal["case_id"])))
        after = provider.get_account(run_id, account_id)
        after_main = next(row["amount_minor"] for row in after["balances"] if row["wallet"] == "MAIN")
        assert operation["status"] == "SUCCEEDED"
        assert after_main == before_main - 4900
        assert UUID(str(receipt["case_id"])) == UUID(str(proposal["case_id"]))

        with app.connect() as connection:
            writes = connection.execute(text("""
                SELECT
                  (SELECT count(*) FROM sandbox.money_entries
                   WHERE sandbox_id=:run AND reference=:reference AND amount_minor=-4900) AS debits,
                  (SELECT count(*) FROM sandbox.subscriptions
                   WHERE sandbox_id=:run AND account_id=:account AND offer_id=:offer
                     AND activation_evidence_ref=:evidence AND status='ACTIVE' AND renew_enabled=false) AS subscriptions,
                  (SELECT count(*) FROM sandbox.provider_operations
                   WHERE sandbox_id=:run AND id=:operation AND status='SUCCEEDED') AS provider_operations
            """), {
                "run": run_id,
                "reference": f"resolve-package:{operation_id}",
                "account": account_id,
                "offer": offer_id,
                "evidence": f"resolve:{proposal['case_id']}:{operation_id}",
                "operation": operation_id,
            }).mappings().one()
        assert writes["debits"] == writes["subscriptions"] == writes["provider_operations"] == 1
    finally:
        app.dispose()
        sandbox.dispose()
