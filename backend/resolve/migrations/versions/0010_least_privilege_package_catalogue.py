"""Remove an unnecessary app-role write permission on synthetic offers."""

from alembic import op

revision = "0010_least_privilege_package_catalogue"
down_revision = "0009_package_activation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Resolve only reads the catalogue. Activation writes run through the
    # dedicated sandbox writer role, which remains the only application writer.
    op.execute('REVOKE UPDATE ON sandbox.offers FROM "hutch_resolve_app"')


def downgrade() -> None:
    raise RuntimeError("Least-privilege correction is intentionally forward-only")
