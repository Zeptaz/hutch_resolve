from __future__ import annotations

from sqlalchemy import Engine, create_engine, text

EXPECTED_SCHEMA_REVISION = "0003_case_investigations"


class Database:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def engine(self) -> Engine:
        return self._engine

    @classmethod
    def connect(cls, url: str) -> Database:
        engine = create_engine(url, pool_pre_ping=True)
        return cls(engine)

    def probe(self) -> bool:
        with self._engine.connect() as connection:
            alive = connection.execute(text("SELECT 1")).scalar_one() == 1
            if not alive:
                return False
            revision = connection.execute(
                text("SELECT version_num FROM resolve.alembic_version")
            ).scalar_one_or_none()
            return revision == EXPECTED_SCHEMA_REVISION

    def close(self) -> None:
        self._engine.dispose()
