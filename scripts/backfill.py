from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.dates import current_week_start
from app.db.session import SessionLocal, init_db
from app.github.collector import run_weekly_collection


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill sample weekly rankings.")
    parser.add_argument("--weeks", type=int, default=4, help="Number of weeks to generate.")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        start = current_week_start() - timedelta(days=7 * (args.weeks - 1))
        for index in range(args.weeks):
            week_start = start + timedelta(days=7 * index)
            run = run_weekly_collection(
                db,
                week_start=week_start,
                trigger_source="backfill",
                force_mock=True,
                generate_report=True,
            )
            print(f"{week_start}: {run.status}, ranked={run.ranked_count}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

