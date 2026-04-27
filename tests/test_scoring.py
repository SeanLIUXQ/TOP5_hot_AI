from datetime import UTC, date, datetime
from types import SimpleNamespace

from app.core.json import dumps
from app.ranking.scoring import percentile_scores, recency_score, score_snapshots


def snapshot(repo_id: int, stars_delta: int, commits: int, prs: int, topics: list[str]):
    repo = SimpleNamespace(
        id=repo_id,
        github_id=repo_id,
        license_spdx="MIT",
        has_readme=True,
        pushed_at=datetime(2026, 4, 25, tzinfo=UTC),
        topics_json=dumps(topics),
        ai_relevance_score=90,
    )
    return SimpleNamespace(
        repository=repo,
        repository_id=repo_id,
        stars=1000 + stars_delta,
        forks=100,
        watchers=30,
        stars_delta_7d=stars_delta,
        forks_delta_7d=10,
        watchers_delta_7d=3,
        pushed_at=datetime(2026, 4, 25, tzinfo=UTC),
        latest_release_at=datetime(2026, 4, 24, tzinfo=UTC),
        contributor_count=20,
        commit_count_7d=commits,
        pr_opened_7d=prs,
        pr_merged_7d=prs,
        issues_closed_7d=prs,
        data_quality_level="complete",
    )


def test_recency_score_linear_decay() -> None:
    assert recency_score(0, 30) == 1
    assert recency_score(15, 30) == 0.5
    assert recency_score(30, 30) == 0


def test_percentile_scores_are_stable() -> None:
    assert percentile_scores([10, 20, 30]) == [0.0, 50.0, 100.0]
    assert percentile_scores([5]) == [100.0]


def test_score_snapshots_orders_by_hotness() -> None:
    snapshots = [
        snapshot(1, stars_delta=10, commits=2, prs=1, topics=["llm"]),
        snapshot(2, stars_delta=100, commits=20, prs=10, topics=["llm", "rag", "agents"]),
        snapshot(3, stars_delta=50, commits=4, prs=2, topics=["machine-learning"]),
    ]
    scored = score_snapshots(snapshots, "v1.0.0", as_of=date(2026, 4, 27))
    assert scored[0].snapshot.repository_id == 2
    assert all(0 <= item.hot_score <= 100 for item in scored)
    assert "components" in scored[0].score_breakdown
