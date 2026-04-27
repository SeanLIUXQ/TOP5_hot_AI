from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from math import log1p
from typing import Any

from app.core.json import loads
from app.db.models import RepoSnapshot


@dataclass(slots=True)
class ScoredSnapshot:
    snapshot: RepoSnapshot
    hot_score: float
    attention_score: float
    activity_score: float
    community_score: float
    freshness_score: float
    health_score: float
    maturity_confidence: float
    score_breakdown: dict[str, Any]


def recency_score(days: int | None, max_days: int) -> float:
    if days is None:
        return 0.0
    if days <= 0:
        return 1.0
    if days >= max_days:
        return 0.0
    return (max_days - days) / max_days


def percentile_scores(values: list[float]) -> list[float]:
    if not values:
        return []
    if len(values) == 1:
        return [100.0]
    sorted_values = sorted(values)
    scores: list[float] = []
    denominator = len(values) - 1
    for value in values:
        lower_or_equal = sum(1 for candidate in sorted_values if candidate <= value)
        rank_index = max(0, lower_or_equal - 1)
        scores.append(round(100 * rank_index / denominator, 3))
    return scores


def days_since(value: datetime | None, as_of: date) -> int | None:
    if value is None:
        return None
    end_dt = datetime.combine(as_of, time.max, tzinfo=value.tzinfo)
    return max(0, (end_dt - value).days)


def score_snapshots(snapshots: list[RepoSnapshot], score_version: str, as_of: date) -> list[ScoredSnapshot]:
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        repo = snapshot.repository
        topics = loads(repo.topics_json, [])
        contributor_count = snapshot.contributor_count or 0
        commit_count = snapshot.commit_count_7d or 0
        pr_opened = snapshot.pr_opened_7d or 0
        pr_merged = snapshot.pr_merged_7d or 0
        issues_closed = snapshot.issues_closed_7d or 0
        activity_raw = 0.35 * commit_count + 0.30 * pr_merged + 0.20 * issues_closed + 0.15 * pr_opened
        community_raw = (
            0.45 * snapshot.forks_delta_7d
            + 0.25 * snapshot.watchers_delta_7d
            + 0.30 * contributor_count
        )
        previous_stars = max(snapshot.stars - snapshot.stars_delta_7d, 100)
        growth_rate = snapshot.stars_delta_7d / previous_stars
        rows.append(
            {
                "snapshot": snapshot,
                "stars_delta_log": log1p(max(snapshot.stars_delta_7d, 0)),
                "growth_rate": growth_rate,
                "activity_raw_log": log1p(max(activity_raw, 0)),
                "community_raw_log": log1p(max(community_raw, 0)),
                "contributors_log": log1p(max(contributor_count, 0)),
                "maturity_log": log1p(max(snapshot.stars + snapshot.forks, 0)),
                "topics": topics,
                "activity_raw": activity_raw,
                "community_raw": community_raw,
            }
        )

    percentile_fields = {
        "stars_delta_pct": percentile_scores([row["stars_delta_log"] for row in rows]),
        "growth_pct": percentile_scores([row["growth_rate"] for row in rows]),
        "activity_pct": percentile_scores([row["activity_raw_log"] for row in rows]),
        "community_pct": percentile_scores([row["community_raw_log"] for row in rows]),
        "contributors_pct": percentile_scores([row["contributors_log"] for row in rows]),
        "maturity_pct": percentile_scores([row["maturity_log"] for row in rows]),
    }

    scored: list[ScoredSnapshot] = []
    for index, row in enumerate(rows):
        snapshot = row["snapshot"]
        repo = snapshot.repository
        attention = 0.70 * percentile_fields["stars_delta_pct"][index] + 0.30 * percentile_fields["growth_pct"][index]
        activity = percentile_fields["activity_pct"][index]
        community = percentile_fields["community_pct"][index]

        push_days = days_since(snapshot.pushed_at or repo.pushed_at, as_of)
        release_days = days_since(snapshot.latest_release_at, as_of)
        freshness = 50 * recency_score(push_days, 30) + 50 * recency_score(release_days, 90)

        has_license = 1 if repo.license_spdx and repo.license_spdx != "NOASSERTION" else 0
        has_readme = 1 if repo.has_readme else 0
        has_recent_release = 1 if release_days is not None and release_days <= 90 else 0
        topic_completeness = 0 if not row["topics"] else 0.5 if len(row["topics"]) <= 2 else 1
        health = (
            25 * has_license
            + 20 * has_readme
            + 20 * has_recent_release
            + 20 * percentile_fields["contributors_pct"][index] / 100
            + 15 * topic_completeness
        )
        maturity = percentile_fields["maturity_pct"][index]
        hot = (
            0.35 * attention
            + 0.25 * activity
            + 0.15 * community
            + 0.10 * freshness
            + 0.10 * health
            + 0.05 * maturity
        )

        warnings: list[str] = []
        penalties: list[str] = []
        estimated_fields: list[str] = []
        missing_activity = any(
            value is None
            for value in [
                snapshot.commit_count_7d,
                snapshot.pr_opened_7d,
                snapshot.pr_merged_7d,
                snapshot.issues_closed_7d,
            ]
        )
        if missing_activity:
            warnings.append("activity_metrics_incomplete")
            estimated_fields.append("activity_metrics")
            hot *= 0.95
        if not has_license:
            warnings.append("license_missing")
        if release_days is None:
            warnings.append("release_unknown")
        elif release_days > 90:
            warnings.append("latest_release_older_than_90_days")
        if snapshot.stars_delta_7d > 500 and activity < 35:
            warnings.append("high_growth_low_activity")
        if repo.ai_relevance_score < 60:
            warnings.append("ai_relevance_borderline")

        breakdown = {
            "score_version": score_version,
            "hot_score": round(hot, 1),
            "components": {
                "attention_score": round(attention, 1),
                "activity_score": round(activity, 1),
                "community_score": round(community, 1),
                "freshness_score": round(freshness, 1),
                "health_score": round(health, 1),
                "maturity_confidence": round(maturity, 1),
            },
            "raw_metrics": {
                "stars": snapshot.stars,
                "stars_delta_7d": snapshot.stars_delta_7d,
                "stars_growth_rate_7d": round(row["growth_rate"], 4),
                "forks": snapshot.forks,
                "forks_delta_7d": snapshot.forks_delta_7d,
                "watchers_delta_7d": snapshot.watchers_delta_7d,
                "commits_7d": snapshot.commit_count_7d,
                "prs_opened_7d": snapshot.pr_opened_7d,
                "prs_merged_7d": snapshot.pr_merged_7d,
                "issues_closed_7d": snapshot.issues_closed_7d,
                "contributor_count": snapshot.contributor_count,
                "days_since_last_push": push_days,
                "days_since_latest_release": release_days,
                "ai_relevance_score": round(repo.ai_relevance_score, 1),
            },
            "penalties": penalties,
            "warnings": warnings,
            "estimated_fields": estimated_fields,
        }
        scored.append(
            ScoredSnapshot(
                snapshot=snapshot,
                hot_score=round(hot, 1),
                attention_score=round(attention, 1),
                activity_score=round(activity, 1),
                community_score=round(community, 1),
                freshness_score=round(freshness, 1),
                health_score=round(health, 1),
                maturity_confidence=round(maturity, 1),
                score_breakdown=breakdown,
            )
        )

    return sorted(
        scored,
        key=lambda item: (
            -item.hot_score,
            -item.attention_score,
            -item.activity_score,
            -item.snapshot.stars_delta_7d,
            item.snapshot.repository.github_id,
        ),
    )

