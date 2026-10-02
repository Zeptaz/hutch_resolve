from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str

    @classmethod
    def from_environment(cls) -> Settings:
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            raise RuntimeError("DATABASE_URL must be configured")
        if not database_url.startswith("postgresql+psycopg://"):
            raise RuntimeError("DATABASE_URL must use the psycopg PostgreSQL driver")
        return cls(database_url=database_url)
