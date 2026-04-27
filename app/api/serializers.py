from __future__ import annotations

from app.core.json import loads
from app.db.models import CollectionRun, Repository, RepoSnapshot, WeeklyRanking


def repository_summary(repo: Repository) -> dict:
    return {
        "id": repo.id,
        "github_id": repo.github_id,
        "owner": repo.owner,
        "name": repo.name,
        "full_name": repo.full_name,
        "html_url": repo.html_url,
        "description": repo.description,
        "primary_language": repo.primary_language,
        "license_spdx": repo.license_spdx,
        "topics": loads(repo.topics_json, []),
        "ai_relevance_score": repo.ai_relevance_score,
        "pushed_at": repo.pushed_at.isoformat() if repo.pushed_at else None,
        "archived": repo.archived,
        "fork": repo.fork,
    }


def snapshot_summary(snapshot: RepoSnapshot | None) -> dict | None:
    if snapshot is None:
        return None
    return {
        "week_start": snapshot.week_start.isoformat(),
        "stars": snapshot.stars,
        "forks": snapshot.forks,
        "watchers": snapshot.watchers,
        "open_issues": snapshot.open_issues,
        "stars_delta_7d": snapshot.stars_delta_7d,
        "stars_growth_rate_7d": snapshot.stars_growth_rate_7d,
        "forks_delta_7d": snapshot.forks_delta_7d,
        "watchers_delta_7d": snapshot.watchers_delta_7d,
        "commits_7d": snapshot.commit_count_7d,
        "prs_opened_7d": snapshot.pr_opened_7d,
        "prs_merged_7d": snapshot.pr_merged_7d,
        "issues_opened_7d": snapshot.issues_opened_7d,
        "issues_closed_7d": snapshot.issues_closed_7d,
        "latest_release_at": snapshot.latest_release_at.isoformat() if snapshot.latest_release_at else None,
        "contributor_count": snapshot.contributor_count,
        "data_quality_level": snapshot.data_quality_level,
        "collected_at": snapshot.collected_at.isoformat() if snapshot.collected_at else None,
    }


def ranking_item(ranking: WeeklyRanking, snapshot: RepoSnapshot | None = None) -> dict:
    breakdown = loads(ranking.score_breakdown_json, {})
    return {
        "rank": ranking.rank,
        "previous_rank": ranking.previous_rank,
        "rank_change": ranking.rank_change,
        "is_new_entry": ranking.is_new_entry,
        "weeks_on_chart": ranking.weeks_on_chart,
        "hot_score": ranking.hot_score,
        "attention_score": ranking.attention_score,
        "activity_score": ranking.activity_score,
        "community_score": ranking.community_score,
        "freshness_score": ranking.freshness_score,
        "health_score": ranking.health_score,
        "maturity_confidence": ranking.maturity_confidence,
        "data_quality_level": ranking.data_quality_level,
        "warnings": breakdown.get("warnings", []),
        "score_breakdown": breakdown,
        "repository": repository_summary(ranking.repository),
        "snapshot": snapshot_summary(snapshot),
    }


def collection_run_item(run: CollectionRun) -> dict:
    return {
        "id": run.id,
        "run_type": run.run_type,
        "trigger_source": run.trigger_source,
        "week_start": run.week_start.isoformat(),
        "status": run.status,
        "score_version": run.score_version,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "candidate_count": run.candidate_count,
        "filtered_count": run.filtered_count,
        "ranked_count": run.ranked_count,
        "warning_count": run.warning_count,
        "rate_limit_remaining": run.rate_limit_remaining,
        "error_message": run.error_message,
        "metadata": loads(run.metadata_json, {}),
    }

