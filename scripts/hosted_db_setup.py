"""Prepare a hosted PostgreSQL the way database/00-bootstrap.sh prepares the local Docker volume.

Safe to run before every deploy: roles are created or their passwords re-synced, the SQL baseline and
synthetic fixture load only into an empty database, and Alembic then applies any new revisions.
Role names and passwords come from DATABASE_URL (application) and SANDBOX_DATABASE_URL (sandbox);
MIGRATION_DATABASE_URL is the admin connection.
"""
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "database"


def libpq_url(name: str) -> str:
    value = os.environ.get(name, "")
    if not value.startswith("postgresql+psycopg://"):
        sys.exit(f"{name} must be a postgresql+psycopg:// URL")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def role_from(name: str) -> tuple[str, str]:
    parts = urlsplit(libpq_url(name))
    if not parts.username or not parts.password:
        sys.exit(f"{name} must include a user and password")
    return unquote(parts.username), unquote(parts.password)


def ensure_role(conn: psycopg.Connection, user: str, password: str) -> None:
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (user,)).fetchone()
    verb = "ALTER" if exists else "CREATE"
    conn.execute(sql.SQL(verb + " ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(user), sql.Literal(password)))


def baseline(conn: psycopg.Connection, sandbox: str, resolve: str) -> None:
    """The schema part of database/00-bootstrap.sh, in one transaction."""
    s, r = sql.Identifier(sandbox), sql.Identifier(resolve)
    with conn.transaction():
        conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        conn.execute("CREATE SCHEMA IF NOT EXISTS sandbox")
        conn.execute("CREATE SCHEMA IF NOT EXISTS resolve")
        for stmt in (
            "GRANT USAGE ON SCHEMA sandbox TO {s}",
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA sandbox TO {s}",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA sandbox GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {s}",
            "GRANT USAGE ON SCHEMA resolve TO {r}",
            "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA resolve TO {r}",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA resolve GRANT SELECT, INSERT ON TABLES TO {r}",
            "GRANT USAGE ON SCHEMA sandbox TO {r}",
            "GRANT SELECT ON ALL TABLES IN SCHEMA sandbox TO {r}",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA sandbox GRANT SELECT ON TABLES TO {r}",
        ):
            conn.execute(sql.SQL(stmt).format(s=s, r=r))
        for name in ("migrations/001_sandbox.sql", "migrations/002_resolve.sql", "migrations/003_scope_constraints.sql", "knowledge_seed.sql"):
            conn.execute((DATABASE / name).read_text(encoding="utf-8"))
        conn.execute(sql.SQL("GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA resolve TO {r}").format(r=r))
        conn.execute(sql.SQL(
            "GRANT UPDATE ON resolve.sessions, resolve.conversations, resolve.cases, resolve.action_proposals,"
            " resolve.operations, resolve.voice_bindings TO {r}").format(r=r))
        conn.execute(sql.SQL(
            "REVOKE UPDATE ON resolve.receipts, resolve.audit_events, resolve.integration_events,"
            " resolve.model_calls, resolve.knowledge_articles FROM {r}").format(r=r))


def main() -> None:
    resolve_user, resolve_password = role_from("DATABASE_URL")
    sandbox_user, sandbox_password = role_from("SANDBOX_DATABASE_URL")
    with psycopg.connect(libpq_url("MIGRATION_DATABASE_URL"), autocommit=True) as conn:
        ensure_role(conn, sandbox_user, sandbox_password)
        ensure_role(conn, resolve_user, resolve_password)
        if conn.execute("SELECT to_regclass('sandbox.sandbox_runs')").fetchone()[0] is None:
            print("hosted_db_setup: loading the SQL baseline")
            baseline(conn, sandbox_user, resolve_user)
        if conn.execute("SELECT count(*) FROM sandbox.sandbox_runs").fetchone()[0] == 0:
            # The local volume loads the fixture before Alembic adopts the baseline; keep that order.
            print("hosted_db_setup: loading the synthetic fixture")
            seed = "\n".join(line for line in (DATABASE / "seed.sql").read_text(encoding="utf-8").splitlines()
                             if not line.startswith("\\"))
            conn.execute(seed)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True)
    print("hosted_db_setup: database ready")


if __name__ == "__main__":
    main()
