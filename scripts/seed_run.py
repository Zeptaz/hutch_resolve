"""Produce a fresh deterministic fixture run without modifying tracked seed data."""
import argparse
import re
from pathlib import Path
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
RUN = UUID("00000000-0000-0000-0000-000000000001")
PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def render(run_id: UUID, source: str) -> str:
    values = set(PATTERN.findall(source))
    mapping = {str(RUN): str(run_id)}
    for value in values - {str(RUN)}:
        mapping[value] = str(uuid5(run_id, value))
    return PATTERN.sub(lambda match: mapping[match.group()], source)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id", type=UUID)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(render(args.run_id, (ROOT / "database/seed.sql").read_text(encoding="utf-8")), encoding="utf-8")


if __name__ == "__main__":
    main()
