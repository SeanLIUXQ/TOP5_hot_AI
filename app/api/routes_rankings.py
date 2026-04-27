from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.api.errors import api_error
from app.api.serializers import ranking_item
from app.core.config import get_settings
from app.db.models import Repository, RepoSnapshot, WeeklyRanking
from app.db.session import get_db
from app.ranking.reports import render_json, render_markdown

router = APIRouter(prefix="/api/v1/rankings", tags=["rankings"])


@router.get("/latest")
def latest_ranking(
    limit: int = Query(default=10, ge=1, le=50),
    language: str | None = None,
    min_score: float | None = Query(default=None, ge=0, le=100),
    score_version: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    version = score_version or settings.score_version
    week = latest_week(db, version)
    if week is None:
        raise api_error(404, "ranking_not_found", "No ranking has been generated yet.")
    return ranking_response(db, week, version, limit, language, min_score)


@router.get("/weeks")
def ranking_weeks(score_version: str | None = None, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    version = score_version or settings.score_version
    rows = (
        db.query(WeeklyRanking.week_start)
        .filter(WeeklyRanking.score_version == version)
        .group_by(WeeklyRanking.week_start)
        .order_by(WeeklyRanking.week_start.desc())
        .all()
    )
    return {"score_version": version, "weeks": [row[0].isoformat() for row in rows]}


@router.get("/{week_start}")
def ranking_by_week(
    week_start: date,
    limit: int = Query(default=10, ge=1, le=50),
    language: str | None = None,
    min_score: float | None = Query(default=None, ge=0, le=100),
    score_version: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    version = score_version or settings.score_version
    return ranking_response(db, week_start, version, limit, language, min_score)


@router.get("/{week_start}/export")
def export_ranking(
    week_start: date,
    format: str = Query(default="markdown", pattern="^(json|csv|markdown)$"),
    limit: int = Query(default=10, ge=1, le=50),
    score_version: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    settings = get_settings()
    version = score_version or settings.score_version
    rankings = rankings_query(db, week_start, version, limit).all()
    if not rankings:
        raise api_error(404, "ranking_not_found", "Ranking for the requested week was not found.")
    if format == "json":
        return Response(render_json(rankings), media_type="application/json")
    if format == "csv":
        lines = ["rank,full_name,hot_score,stars_delta_7d,activity_score"]
        for item in rankings:
            stars_delta = ranking_item(item).get("score_breakdown", {}).get("raw_metrics", {}).get("stars_delta_7d", 0)
            lines.append(f"{item.rank},{item.repository.full_name},{item.hot_score},{stars_delta},{item.activity_score}")
        return Response("\n".join(lines) + "\n", media_type="text/csv")
    return Response(render_markdown(rankings), media_type="text/markdown")


def latest_week(db: Session, score_version: str) -> date | None:
    return (
        db.query(func.max(WeeklyRanking.week_start))
        .filter(WeeklyRanking.score_version == score_version)
        .scalar()
    )


def rankings_query(db: Session, week_start: date, score_version: str, limit: int):
    return (
        db.query(WeeklyRanking)
        .options(joinedload(WeeklyRanking.repository))
        .join(Repository, Repository.id == WeeklyRanking.repository_id)
        .filter(WeeklyRanking.week_start == week_start)
        .filter(WeeklyRanking.score_version == score_version)
        .order_by(WeeklyRanking.rank.asc())
        .limit(limit)
    )


def ranking_response(
    db: Session,
    week_start: date,
    score_version: str,
    limit: int,
    language: str | None,
    min_score: float | None,
) -> dict:
    query = (
        db.query(WeeklyRanking)
        .options(joinedload(WeeklyRanking.repository))
        .join(Repository, Repository.id == WeeklyRanking.repository_id)
        .filter(WeeklyRanking.week_start == week_start)
        .filter(WeeklyRanking.score_version == score_version)
    )
    if language:
        query = query.filter(Repository.primary_language == language)
    if min_score is not None:
        query = query.filter(WeeklyRanking.hot_score >= min_score)
    rankings = query.order_by(WeeklyRanking.rank.asc()).limit(limit).all()
    if not rankings:
        raise api_error(
            404,
            "ranking_not_found",
            "Ranking for the requested week was not found.",
            {"week_start": week_start.isoformat()},
        )
    repo_ids = [item.repository_id for item in rankings]
    snapshots = (
        db.query(RepoSnapshot)
        .filter(RepoSnapshot.week_start == week_start)
        .filter(RepoSnapshot.repository_id.in_(repo_ids))
        .all()
    )
    snapshots_by_repo = {snapshot.repository_id: snapshot for snapshot in snapshots}
    return {
        "week_start": week_start.isoformat(),
        "score_version": score_version,
        "generated_at": rankings[0].generated_at.isoformat() if rankings else None,
        "items": [ranking_item(item, snapshots_by_repo.get(item.repository_id)) for item in rankings],
    }
