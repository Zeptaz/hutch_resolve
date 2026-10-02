import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5

import pytest
from psycopg.errors import UniqueViolation
from sqlalchemy import create_engine, text

from backend.resolve.app.auth import AuthContext
from backend.resolve.app.auth_store import AuthStore
from backend.resolve.providers.sandbox import PostgresSandboxProvider
from backend.resolve.services.facade import ResolveFacade
from scripts.seed_run import ROOT, render


SOURCE_RUN = UUID("00000000-0000-0000-0000-000000000001")
NEW_RUN = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ACCOUNT = "20000000-0000-0000-0000-000000000001"


def test_render_keeps_fixture_generation_non_retiring_by_default():
    rendered = render(NEW_RUN, f"BEGIN;\nINSERT INTO sample VALUES ('{SOURCE_RUN}','{ACCOUNT}');\nCOMMIT;")

    assert str(NEW_RUN) in rendered
    assert str(uuid5(NEW_RUN, ACCOUNT)) in rendered
    assert "UPDATE resolve.sessions" not in rendered
    assert "UPDATE sandbox.sandbox_runs" not in rendered


def test_retiring_render_revokes_old_sessions_atomically_before_new_fixture_insert():
    rendered = render(
        NEW_RUN,
        f"BEGIN;\nINSERT INTO sample VALUES ('{SOURCE_RUN}','{ACCOUNT}');\nCOMMIT;",
        retire_active=True,
    )

    assert rendered.index("UPDATE resolve.voice_bindings") > rendered.index("BEGIN;")
    assert rendered.index("UPDATE resolve.voice_bindings") < rendered.index("UPDATE resolve.sessions")
    assert rendered.index("UPDATE resolve.sessions") < rendered.index("UPDATE sandbox.sandbox_runs")
    assert rendered.index("UPDATE sandbox.sandbox_runs") < rendered.index("INSERT INTO sample")
    assert f"id<>'{NEW_RUN}'" in rendered
    assert rendered.rstrip().endswith("COMMIT;")


@pytest.mark.skipif(not os.getenv("RESET_IT_DATABASE_URL"),
                    reason="requires disposable migrated PostgreSQL admin URL")
def test_reset_retires_old_runs_and_rolls_back_retirement_if_seed_fails():
    engine = create_engine(os.environ["RESET_IT_DATABASE_URL"])
    try:
        with engine.connect() as connection:
            old_run = connection.execute(text("""
                SELECT id FROM sandbox.sandbox_runs WHERE run_status='ACTIVE' ORDER BY created_at,id LIMIT 1
            """)).scalar_one()
            account_id = connection.execute(text("""
                SELECT id FROM sandbox.accounts WHERE sandbox_id=:run AND line_alias='SIM-LK-0001'
            """), {"run": old_run}).scalar_one()
        session_id = uuid4()
        principal_id = f"reset-it-{session_id}"
        AuthStore(engine).create_session(session_id=session_id, credential_hash=uuid4().bytes,
            csrf_hash=uuid4().bytes, role="CUSTOMER", principal_id=principal_id,
            sandbox_id=old_run, account_id=account_id,
            expires_at=datetime.now(UTC) + timedelta(hours=1))
        context = AuthContext(session_id, principal_id, "CUSTOMER", old_run, account_id, uuid4(), "TEXT")
        facade = ResolveFacade(engine, PostgresSandboxProvider(engine), cursor_secret=b"reset-integration-secret-32-bytes")
        conversation = facade.create_conversation(context)
        binding_id = uuid4()
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO resolve.voice_bindings
                  (id,sandbox_id,conversation_id,voice_session_id,account_id,origin,expires_at)
                VALUES (:id,:sandbox,:conversation,:voice,:account,'http://localhost:5173',:expires)
            """), {"id": binding_id, "sandbox": old_run, "conversation": conversation["id"],
                "voice": f"reset-it-{uuid4()}", "account": account_id,
                "expires": datetime.now(UTC) + timedelta(minutes=5)})

        reset_run = uuid4()
        sql = render(reset_run, (ROOT / "database/seed.sql").read_text(encoding="utf-8"), retire_active=True)
        sql = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("\\"))
        raw = engine.raw_connection()
        try:
            raw.autocommit = True
            with raw.cursor() as cursor:
                cursor.execute(sql, prepare=False)
        finally:
            raw.close()

        with engine.connect() as connection:
            assert connection.execute(text("""
                SELECT run_status FROM sandbox.sandbox_runs WHERE id=:id
            """), {"id": old_run}).scalar_one() == "RETIRED"
            assert connection.execute(text("""
                SELECT revoked_at IS NOT NULL FROM resolve.sessions WHERE id=:id
            """), {"id": session_id}).scalar_one()
            assert connection.execute(text("""
                SELECT revoked_at IS NOT NULL FROM resolve.voice_bindings WHERE id=:id
            """), {"id": binding_id}).scalar_one()
            assert connection.execute(text("""
                SELECT run_status FROM sandbox.sandbox_runs WHERE id=:id
            """), {"id": reset_run}).scalar_one() == "ACTIVE"
            reset_account = connection.execute(text("""
                SELECT id FROM sandbox.accounts WHERE sandbox_id=:run AND line_alias='SIM-LK-0001'
            """), {"run": reset_run}).scalar_one()
        reset_session = uuid4()
        AuthStore(engine).create_session(session_id=reset_session, credential_hash=uuid4().bytes,
            csrf_hash=uuid4().bytes, role="CUSTOMER", principal_id=f"reset-it-{reset_session}",
            sandbox_id=reset_run, account_id=reset_account,
            expires_at=datetime.now(UTC) + timedelta(hours=1))

        # A duplicate run UUID causes the fixture insert to fail. Retirement and
        # revocation must roll back with it, leaving the current run usable.
        failed_reset = render(reset_run, (ROOT / "database/seed.sql").read_text(encoding="utf-8"),
                              retire_active=True)
        failed_reset = "\n".join(line for line in failed_reset.splitlines() if not line.lstrip().startswith("\\"))
        raw = engine.raw_connection()
        try:
            raw.autocommit = True
            with raw.cursor() as cursor, pytest.raises(UniqueViolation):
                cursor.execute(failed_reset, prepare=False)
        finally:
            raw.close()
        with engine.connect() as connection:
            assert connection.execute(text("""
                SELECT run_status FROM sandbox.sandbox_runs WHERE id=:id
            """), {"id": reset_run}).scalar_one() == "ACTIVE"
            assert connection.execute(text("""
                SELECT count(*) FROM resolve.sessions WHERE sandbox_id=:id AND revoked_at IS NULL
            """), {"id": reset_run}).scalar_one() == 1
    finally:
        engine.dispose()
