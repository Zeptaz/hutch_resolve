"""Operator control for the synthetic sandbox's one-shot fault profiles (demo and rehearsal only).

A fresh fixture run arms 13 single-use faults (late/duplicate postings, VAS write failures, a CRM
outage ...), so the first journeys after a reset are deliberately imperfect. Before a demo, clear
them for a predictable happy path, then arm exactly the failure you want to show.

    python scripts/demo_faults.py list
    python scripts/demo_faults.py clear                 # disarm every fault in the active run
    python scripts/demo_faults.py arm crm-outage        # next review-ticket delivery fails once, then recovers
    python scripts/demo_faults.py arm crm-outage --uses 2

Uses SANDBOX_DATABASE_URL from the repository .env (the sandbox role). Never touches Resolve data.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

REPO = Path(__file__).resolve().parents[1]

PRESETS = {
    "crm-outage": ("crm", "create_ticket", "PROVIDER_UNAVAILABLE", "Review-ticket delivery is unavailable once; the runner retries."),
    "crm-lost-response": ("crm", "create_ticket", "COMMITTED_RESPONSE_LOST", "The ticket is created but the reply is lost; recovery finds it."),
    "review-sync-outage": ("crm", "update_ticket", "PROVIDER_UNAVAILABLE", "An agent review update cannot reach the CRM once."),
    "vas-lost-response": ("vas", "deactivate", "COMMITTED_RESPONSE_LOST", "VAS deactivation commits but the reply is lost."),
    "vas-rejected": ("vas", "deactivate", "WRITE_REJECTED", "The VAS provider rejects the deactivation."),
    "lookup-outage": ("operations", "lookup", "LOOKUP_UNAVAILABLE", "Recovery lookup is unavailable once."),
}


def _engine():
    load_dotenv(REPO / ".env")
    url = os.getenv("SANDBOX_DATABASE_URL", "").strip()
    if not url:
        sys.exit("SANDBOX_DATABASE_URL is not set in .env")
    return create_engine(url)


def _active_run(connection) -> str:
    runs = connection.execute(text(
        "SELECT id FROM sandbox.sandbox_runs WHERE run_status='ACTIVE' ORDER BY id")).scalars().all()
    if len(runs) != 1:
        sys.exit(f"Expected exactly one active fixture run, found {len(runs)}. Reset the database first.")
    return str(runs[0])


def list_faults() -> None:
    with _engine().connect() as connection:
        run = _active_run(connection)
        rows = connection.execute(text("""
            SELECT provider,operation,fault_type,selector,remaining_uses FROM sandbox.fault_profiles
            WHERE sandbox_id=:run ORDER BY remaining_uses DESC,provider,operation,fault_type
        """), {"run": run}).mappings().all()
    print(f"Active run {run}")
    for row in rows:
        scope = f" account {row['selector']['account']}" if (row["selector"] or {}).get("account") else ""
        state = f"armed x{row['remaining_uses']}" if row["remaining_uses"] else "off"
        print(f"  {state:9} {row['provider']}/{row['operation']} {row['fault_type']}{scope}")


def clear() -> None:
    with _engine().begin() as connection:
        run = _active_run(connection)
        count = connection.execute(text("""
            UPDATE sandbox.fault_profiles SET remaining_uses=0 WHERE sandbox_id=:run AND remaining_uses>0
        """), {"run": run}).rowcount
    print(f"Disarmed {count} fault(s) in run {run}. Journeys now follow the happy path.")


def arm(preset: str, uses: int) -> None:
    provider, operation, fault_type, meaning = PRESETS[preset]
    with _engine().begin() as connection:
        run = _active_run(connection)
        updated = connection.execute(text("""
            UPDATE sandbox.fault_profiles SET remaining_uses=:uses
            WHERE sandbox_id=:run AND provider=:provider AND operation=:operation AND fault_type=:fault
              AND selector='{}'::jsonb
        """), {"uses": uses, "run": run, "provider": provider, "operation": operation, "fault": fault_type}).rowcount
        if not updated:
            connection.execute(text("""
                INSERT INTO sandbox.fault_profiles(id,sandbox_id,provider,operation,selector,fault_type,parameters,remaining_uses)
                VALUES (:id,:run,:provider,:operation,'{}'::jsonb,:fault,CAST(:parameters AS jsonb),:uses)
            """), {"id": uuid4(), "run": run, "provider": provider, "operation": operation, "fault": fault_type,
                   "parameters": json.dumps({"armed_by": "scripts/demo_faults.py"}), "uses": uses})
    print(f"Armed {preset} x{uses}: {meaning}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("clear")
    arm_parser = sub.add_parser("arm")
    arm_parser.add_argument("preset", choices=sorted(PRESETS))
    arm_parser.add_argument("--uses", type=int, default=1, choices=range(1, 4))
    args = parser.parse_args()
    if args.command == "list":
        list_faults()
    elif args.command == "clear":
        clear()
    else:
        arm(args.preset, args.uses)


if __name__ == "__main__":
    main()
