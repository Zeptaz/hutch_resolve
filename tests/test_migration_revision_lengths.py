from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migration_revision_ids_fit_alembic_version_column():
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    revisions = list(scripts.walk_revisions())

    assert revisions
    assert all(len(revision.revision) <= 32 for revision in revisions)
