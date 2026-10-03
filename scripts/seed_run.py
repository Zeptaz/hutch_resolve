"""Produce a fresh deterministic fixture run without modifying tracked seed data."""
import argparse
import re
from pathlib import Path
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
RUN = UUID("00000000-0000-0000-0000-000000000001")
PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def render(run_id: UUID, source: str, *, retire_active: bool = False) -> str:
    values = set(PATTERN.findall(source))
    mapping = {str(RUN): str(run_id)}
    for value in values - {str(RUN)}:
        mapping[value] = str(uuid5(run_id, value))
    rendered = PATTERN.sub(lambda match: mapping[match.group()], source)
    if rendered.count("BEGIN;") != 1 or rendered.count("COMMIT;") != 1 or not rendered.rstrip().endswith("COMMIT;"):
        raise ValueError("Seed SQL must contain one outer transaction")
    retirement = ""
    if retire_active:
        retirement = f"""
UPDATE resolve.voice_bindings SET revoked_at=now() WHERE revoked_at IS NULL;
UPDATE resolve.sessions SET revoked_at=now() WHERE revoked_at IS NULL;
UPDATE sandbox.sandbox_runs SET run_status='RETIRED',retired_at=now()
WHERE run_status='ACTIVE' AND id<>'{run_id}';
"""
    if retirement:
        rendered = rendered.replace("BEGIN;\n", "BEGIN;\n" + retirement, 1)
    package_ids = ",".join(
        f"'{uuid5(run_id, f'30000000-0000-0000-0000-00000000000{suffix}')}'"
        for suffix in range(5, 9)
    )
    # The package fixture predates the catalogue flag. Scope the allowlist to
    # this generated run and keep it in the fixture transaction.
    allowlist = f"\nUPDATE sandbox.offers SET available_for_purchase=true\nWHERE sandbox_id='{run_id}' AND id IN ({package_ids})\n  AND offer_kind='PACKAGE' AND recurring=false;\n"
    commit_at = rendered.rfind("COMMIT;")
    return rendered[:commit_at] + allowlist + rendered[commit_at:]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id", type=UUID)
    parser.add_argument("output", type=Path)
    parser.add_argument("--retire-active", action="store_true",
                        help="atomically retire prior runs and revoke sessions/bindings with the new fixture insert")
    args = parser.parse_args()
    args.output.write_text(render(args.run_id, (ROOT / "database/seed.sql").read_text(encoding="utf-8"),
                                  retire_active=args.retire_active), encoding="utf-8")


if __name__ == "__main__":
    main()
