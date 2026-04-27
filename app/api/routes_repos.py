from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.api.serializers import repository_summary, snapshot_summary
from app.core.config import get_settings
from app.db.models import Repository, RepoSnapshot, WeeklyRanking
from app.db.session import get_db

router = APIRouter(prefix="/api/v1", tags=["repositories"])


@router.get("/repos/search")
def search_repos(
    q: str | None = None,
    language: str | None = None,
    topic: str | None = None,
    min_stars: int | None = Query(default=None, ge=0),
    has_ranking: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(Repository)
    if q:
        pattern = f"%{q}%"
        query = query.filter((Repository.full_name.ilike(pattern)) | (Repository.description.ilike(pattern)))
    if language:
        query = query.filter(Repository.primary_language == language)
    if topic:
        query = query.filter(Repository.topics_json.ilike(f"%{topic}%"))
    if min_stars is not None:
        latest_snapshot_subquery = (
            db.query(RepoSnapshot.repository_id)
            .filter(RepoSnapshot.stars >= min_stars)
            .subquery()
        )
        query = query.filter(Repository.id.in_(latest_snapshot_subquery))
    if has_ranking:
        ranked_subquery = db.query(WeeklyRanking.repository_id).subquery()
        query = query.filter(Repository.id.in_(ranked_subquery))
    total = query.count()
    repos = query.order_by(Repository.full_name.asc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"page": page, "page_size": page_size, "total": total, "items": [repository_summary(repo) for repo in repos]}


@router.get("/repos/{owner}/{repo}")
def repo_detail(owner: str, repo: str, db: Session = Depends(get_db)) -> dict:
    repository = find_repository(db, owner, repo)
    latest_snapshot = (
        db.query(RepoSnapshot)
        .filter(RepoSnapshot.repository_id == repository.id)
        .order_by(RepoSnapshot.week_start.desc())
        .first()
    )
    latest_ranking = (
        db.query(WeeklyRanking)
        .filter(WeeklyRanking.repository_id == repository.id)
        .order_by(WeeklyRanking.week_start.desc())
        .first()
    )
    return {
        "repository": repository_summary(repository),
        "latest_snapshot": snapshot_summary(latest_snapshot),
        "latest_ranking": {
            "week_start": latest_ranking.week_start.isoformat(),
            "rank": latest_ranking.rank,
            "hot_score": latest_ranking.hot_score,
            "rank_change": latest_ranking.rank_change,
            "weeks_on_chart": latest_ranking.weeks_on_chart,
        }
        if latest_ranking
        else None,
    }


@router.get("/repos/{owner}/{repo}/history")
def repo_history(
    owner: str,
    repo: str,
    metric: str = Query(default="hot_score"),
    weeks: int = Query(default=12, ge=1, le=104),
    db: Session = Depends(get_db),
) -> dict:
    repository = find_repository(db, owner, repo)
    supported = {
        "stars",
        "stars_delta_7d",
        "forks",
        "forks_delta_7d",
        "hot_score",
        "attention_score",
        "activity_score",
        "community_score",
        "freshness_score",
        "health_score",
    }
    if metric not in supported:
        raise api_error(400, "invalid_metric", "Unsupported history metric.", {"metric": metric})
    points = metric_points(db, repository.id, metric, weeks)
    return {"repository": repository.full_name, "metric": metric, "points": points}


@router.get("/compare")
def compare_repos(
    repos: str = Query(..., description="Comma-separated owner/name values."),
    weeks: int = Query(default=12, ge=1, le=104),
    metric: str = Query(default="hot_score"),
    db: Session = Depends(get_db),
) -> dict:
    names = [item.strip() for item in repos.split(",") if item.strip()]
    if len(names) < 2 or len(names) > 5:
        raise api_error(400, "invalid_repos", "Compare requires 2 to 5 repositories.")
    items = []
    for full_name in names:
        if "/" not in full_name:
            raise api_error(400, "invalid_repos", "Repository must use owner/name format.", {"repo": full_name})
        owner, name = full_name.split("/", 1)
        repository = find_repository(db, owner, name)
        items.append(
            {
                "repository": repository_summary(repository),
                "history": metric_points(db, repository.id, metric, weeks),
            }
        )
    return {"metric": metric, "weeks": weeks, "items": items}


def find_repository(db: Session, owner: str, repo: str) -> Repository:
    repository = (
        db.query(Repository)
        .filter(Repository.owner == owner)
        .filter(Repository.name == repo)
        .one_or_none()
    )
    if repository is None:
        raise api_error(404, "repository_not_found", "Repository was not found.", {"full_name": f"{owner}/{repo}"})
    return repository


def metric_points(db: Session, repository_id: int, metric: str, weeks: int) -> list[dict]:
    if metric.endswith("_score") or metric == "hot_score":
        settings = get_settings()
        rows = (
            db.query(WeeklyRanking)
            .filter(WeeklyRanking.repository_id == repository_id)
            .filter(WeeklyRanking.score_version == settings.score_version)
            .order_by(WeeklyRanking.week_start.desc())
            .limit(weeks)
            .all()
        )
        rows.reverse()
        return [
            {
                "week_start": item.week_start.isoformat(),
                "value": getattr(item, metric),
                "rank": item.rank,
                "data_quality_level": item.data_quality_level,
            }
            for item in rows
        ]
    rows = (
        db.query(RepoSnapshot)
        .filter(RepoSnapshot.repository_id == repository_id)
        .order_by(RepoSnapshot.week_start.desc())
        .limit(weeks)
        .all()
    )
    rows.reverse()
    return [
        {
            "week_start": item.week_start.isoformat(),
            "value": getattr(item, metric),
            "data_quality_level": item.data_quality_level,
        }
        for item in rows
    ]

