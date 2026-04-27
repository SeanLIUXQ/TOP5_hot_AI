from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy.orm import Session, joinedload

from app.core.json import dumps, loads
from app.db.models import WeeklyRanking


def ranking_rows(db: Session, week_start: str, score_version: str, limit: int = 10) -> list[WeeklyRanking]:
    return (
        db.query(WeeklyRanking)
        .options(joinedload(WeeklyRanking.repository))
        .filter(WeeklyRanking.week_start == week_start)
        .filter(WeeklyRanking.score_version == score_version)
        .order_by(WeeklyRanking.rank.asc())
        .limit(limit)
        .all()
    )


def render_markdown(rankings: list[WeeklyRanking]) -> str:
    if not rankings:
        return "# TOP5 Hot AI Project\n\nNo ranking data.\n"
    week_start = rankings[0].week_start.isoformat()
    lines = [
        f"# TOP5 Hot AI Project - {week_start}",
        "",
        "| Rank | Repository | Score | Stars +7d | Activity | Warnings |",
        "| ---: | --- | ---: | ---: | ---: | --- |",
    ]
    for item in rankings:
        breakdown = loads(item.score_breakdown_json, {})
        raw = breakdown.get("raw_metrics", {})
        warnings = ", ".join(breakdown.get("warnings", [])) or "-"
        repo = item.repository
        lines.append(
            f"| {item.rank} | [{repo.full_name}]({repo.html_url}) | {item.hot_score:.1f} | "
            f"{raw.get('stars_delta_7d', 0)} | {item.activity_score:.1f} | {warnings} |"
        )
    return "\n".join(lines) + "\n"


def render_json(rankings: list[WeeklyRanking]) -> str:
    payload = [
        {
            "rank": item.rank,
            "full_name": item.repository.full_name,
            "html_url": item.repository.html_url,
            "hot_score": item.hot_score,
            "score_breakdown": loads(item.score_breakdown_json, {}),
        }
        for item in rankings
    ]
    return dumps(payload)


def write_reports(rankings: list[WeeklyRanking], output_dir: str) -> list[Path]:
    if not rankings:
        return []
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    week_start = rankings[0].week_start.isoformat()
    markdown_path = directory / f"weekly-{week_start}.md"
    json_path = directory / f"weekly-{week_start}.json"
    csv_path = directory / f"weekly-{week_start}.csv"
    markdown_path.write_text(render_markdown(rankings), encoding="utf-8")
    json_path.write_text(render_json(rankings), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["rank", "full_name", "hot_score", "stars_delta_7d", "activity_score"])
        for item in rankings:
            raw = loads(item.score_breakdown_json, {}).get("raw_metrics", {})
            writer.writerow(
                [
                    item.rank,
                    item.repository.full_name,
                    item.hot_score,
                    raw.get("stars_delta_7d", 0),
                    item.activity_score,
                ]
            )
    return [markdown_path, json_path, csv_path]
