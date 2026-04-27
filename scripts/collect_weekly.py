from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.dates import current_week_start
from app.db.session import SessionLocal, init_db
from app.github.collector import run_weekly_collection


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect GitHub AI repositories and generate weekly ranking.")
    parser.add_argument("--week-start", type=date.fromisoformat, default=current_week_start())
    parser.add_argument("--mock", action="store_true", help="Use built-in sample data instead of GitHub API.")
    parser.add_argument("--limit", type=int, default=None, help="Candidate repository limit.")
    parser.add_argument("--no-report", action="store_true", help="Skip Markdown/JSON/CSV report files.")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        run = run_weekly_collection(
            db,
            week_start=args.week_start,
            trigger_source="cli",
            force_mock=args.mock,
            candidate_limit=args.limit,
            generate_report=not args.no_report,
        )
        print(
            f"run_id={run.id} status={run.status} week_start={run.week_start} "
            f"candidates={run.candidate_count} filtered={run.filtered_count} ranked={run.ranked_count}"
        )
        if run.error_message:
            print(f"error={run.error_message}")
        return 0 if run.status in {"succeeded", "partial_succeeded"} else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

