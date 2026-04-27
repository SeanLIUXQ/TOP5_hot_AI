from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session, joinedload

from app.core.dates import previous_week_start, utc_now
from app.core.json import dumps
from app.db.models import RepoSnapshot, WeeklyRanking
from app.ranking.scoring import score_snapshots


def generate_weekly_ranking(db: Session, week_start: date, score_version: str) -> list[WeeklyRanking]:
    snapshots = (
        db.query(RepoSnapshot)
        .options(joinedload(RepoSnapshot.repository))
        .filter(RepoSnapshot.week_start == week_start)
        .filter(RepoSnapshot.data_quality_level != "invalid")
        .all()
    )
    if not snapshots:
        return []

    previous_rankings = (
        db.query(WeeklyRanking)
        .filter(WeeklyRanking.week_start == previous_week_start(week_start))
        .filter(WeeklyRanking.score_version == score_version)
        .all()
    )
    previous_rank_by_repo = {ranking.repository_id: ranking.rank for ranking in previous_rankings}
    previous_weeks_by_repo = {ranking.repository_id: ranking.weeks_on_chart for ranking in previous_rankings}

    scored = score_snapshots(snapshots, score_version=score_version, as_of=week_start)
    db.query(WeeklyRanking).filter(WeeklyRanking.week_start == week_start).filter(
        WeeklyRanking.score_version == score_version
    ).delete(synchronize_session=False)
    generated_at = utc_now()
    rankings: list[WeeklyRanking] = []
    for rank, item in enumerate(scored, start=1):
        snapshot = item.snapshot
        previous_rank = previous_rank_by_repo.get(snapshot.repository_id)
        weeks_on_chart = previous_weeks_by_repo.get(snapshot.repository_id, 0) + 1
        ranking = WeeklyRanking(
            week_start=week_start,
            repository_id=snapshot.repository_id,
            rank=rank,
            previous_rank=previous_rank,
            rank_change=(previous_rank - rank) if previous_rank else None,
            is_new_entry=previous_rank is None,
            weeks_on_chart=weeks_on_chart,
            score_version=score_version,
            hot_score=item.hot_score,
            attention_score=item.attention_score,
            activity_score=item.activity_score,
            community_score=item.community_score,
            freshness_score=item.freshness_score,
            health_score=item.health_score,
            maturity_confidence=item.maturity_confidence,
            data_quality_level=snapshot.data_quality_level,
            score_breakdown_json=dumps(item.score_breakdown),
            generated_at=generated_at,
        )
        db.add(ranking)
        rankings.append(ranking)
    db.commit()
    for ranking in rankings:
        db.refresh(ranking)
    return rankings

