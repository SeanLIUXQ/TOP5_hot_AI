from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.json import loads
from app.db.archive import restore_history, save_week_state
from app.db.models import (
    Base,
    CollectionRun,
    RepoDailyCounter,
    Repository,
    RepoSnapshot,
    WeeklyRanking,
)
from app.github import client as github_client
from app.github import collector
from app.github.client import GitHubClient, GitHubClientError

MON = date(2026, 11, 16)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def github_repo(gid, stars):
    return {
        "id": gid,
        "full_name": f"code/llm-{gid}",
        "stargazers_count": stars,
        "forks_count": stars // 2,
        "watchers_count": stars // 4,
        "language": "Python",
        "description": "Machine learning, transformer inference and AI agents",
        "topics": ["llm", "machine-learning", "agents"],
        "pushed_at": f"{MON}T01:05:00Z",
        "commits_7d": 7,
        "prs_opened_7d": 1,
        "prs_merged_7d": 0,
        "issues_opened_7d": 2,
        "issues_closed_7d": 0,
    }


@pytest.fixture
def observe(monkeypatch):
    monkeypatch.setattr(
        collector,
        "get_settings",
        lambda: SimpleNamespace(
            github_token="test",
            github_rest_endpoint="https://api.github.com",
            use_mock_when_no_token=False,
            min_stars=100,
            candidate_limit=120,
            score_version="v1.0.0",
        ),
    )

    def run(db, day, entries):
        def search(**kwargs):
            assert kwargs["as_of"] == day
            return [(row, "topic:llm") for row in entries], 77

        monkeypatch.setattr(collector, "utc_now", lambda: datetime.combine(day, time(9, 17), UTC))
        monkeypatch.setattr(collector, "collect_from_github", search)
        return collector.run_weekly_collection(db, as_of=day, generate_report=False)

    return run


def test_exact_daily_counters_survive_rerun_and_cold_restore(db, observe, tmp_path):
    wed, sun = MON + timedelta(days=2), MON + timedelta(days=6)
    first = observe(db, wed, [github_repo(1, 700), github_repo(2, 600)])
    assert first.status == "partial_succeeded"
    second = observe(db, wed, [github_repo(1, 800), github_repo(2, 500)])
    assert db.query(RepoDailyCounter).count() == 2
    assert second.ranked_count == 2
    # Repo 2 drops out on Sunday. Its Wednesday observation still belongs in the checkpoint.
    sunday = observe(db, sun, [github_repo(1, 1500), github_repo(3, 500)])
    assert sunday.ranked_count == 2
    assert {s.repository.github_id for s in db.query(RepoSnapshot)} == {1, 3}
    assert {r.repository.github_id for r in db.query(WeeklyRanking)} == {1, 3}
    assert db.query(CollectionRun).count() == 3
    assert db.query(RepoDailyCounter).count() == 4
    save_week_state(db, tmp_path, MON)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as cold:
        restore_history(cold, tmp_path, tmp_path / "public")
        prior_ranks = {r.repository.github_id: r.rank for r in cold.query(WeeklyRanking)}
        next_wed = wed + timedelta(days=7)
        run = observe(
            cold,
            next_wed,
            [github_repo(1, 1600), github_repo(2, 750), github_repo(3, 650), github_repo(4, 350)],
        )
        assert run.week_start == MON + timedelta(days=7) and run.ranked_count == 4
        snaps = {
            s.repository.github_id: s
            for s in cold.query(RepoSnapshot).filter_by(week_start=run.week_start)
        }
        ranks = {
            r.repository.github_id: r
            for r in cold.query(WeeklyRanking).filter_by(week_start=run.week_start)
        }
        assert (snaps[1].stars_delta_7d, snaps[1].forks_delta_7d, snaps[1].watchers_delta_7d) == (
            800,
            400,
            200,
        )
        assert snaps[1].stars_growth_rate_7d == 1
        assert snaps[2].stars_delta_7d == 250
        assert snaps[1].data_quality_level == snaps[2].data_quality_level == "complete"
        assert ranks[1].previous_rank == prior_ranks[1] and ranks[2].previous_rank is None
        exact = loads(ranks[1].score_breakdown_json)
        assert exact["growth_baseline"]["source"] == "daily_counter"
        assert exact["growth_baseline"]["previous_observed_on"] == wed.isoformat()
        assert not any(w.startswith("growth_baseline_") for w in exact["warnings"])
        # A three-day observed difference and absent coverage are explicitly labeled.
        for gid, status, days in ((3, "interval_mismatch", 3), (4, "unavailable", None)):
            data = loads(ranks[gid].score_breakdown_json)
            assert data["growth_baseline"]["interval_days"] == days
            assert f"growth_baseline_{status}" in data["warnings"]
            assert "stars_delta_7d" in data["estimated_fields"]
            assert snaps[gid].data_quality_level == "partial"
        assert snaps[3].stars_delta_7d == 150 and snaps[4].stars_delta_7d == 0
        assert exact["raw_metrics"]["days_since_last_push"] == 9
        save_week_state(cold, tmp_path, run.week_start)
    engine.dispose()


def test_missing_previous_week_is_not_a_growth_baseline(db, observe):
    observe(db, MON, [github_repo(1, 1000)])
    later = MON + timedelta(weeks=2)
    observe(db, later, [github_repo(1, 9000)])
    snap = db.query(RepoSnapshot).filter_by(week_start=later).one()
    assert snap.stars_delta_7d == 0
    assert loads(snap.data_quality_json)["growth_baseline"]["status"] == "unavailable"


def test_same_week_failed_refresh_leaves_last_good_snapshot_rank_and_counter(db, observe):
    observe(db, MON, [github_repo(1, 1000), github_repo(2, 2000)])
    failed = observe(db, MON, [github_repo(1, 5000), {"malformed": True}])
    assert failed.status == "failed"
    assert observe(db, MON, []).status == "failed"
    assert db.query(CollectionRun).count() == 3
    db.expire_all()
    assert sorted(s.stars for s in db.query(RepoSnapshot)) == [1000, 2000]
    assert sorted(s.stars for s in db.query(RepoDailyCounter)) == [1000, 2000]
    assert db.query(WeeklyRanking).count() == db.query(Repository).count() == 2


def test_mock_cli_week_anchor_and_daily_activity_windows(db):
    requested = date(2024, 12, 30)
    run = collector.run_weekly_collection(db, requested, force_mock=True, generate_report=False)
    assert run.week_start == requested
    assert loads(run.metadata_json)["as_of"] == requested.isoformat()
    assert all(row.provenance == "mock" for row in db.query(RepoDailyCounter))
    assert all(
        "growth_baseline_estimated" in loads(row.score_breakdown_json)["warnings"]
        for row in db.query(WeeklyRanking)
    )
    sample = SimpleNamespace(commit_windows=[], searches=[])
    sample.count_commits = lambda owner, name, lo, hi: sample.commit_windows.append((lo, hi)) or 12
    sample.count_issue_search = lambda query: sample.searches.append(query) or 8
    collector.activity_metrics(sample, "x/y", "x", "y", date(2025, 1, 2))
    assert sample.commit_windows == [(date(2024, 12, 27), date(2025, 1, 2))]
    assert len(sample.searches) == 4 and all(
        q.endswith("2024-12-27..2025-01-02") for q in sample.searches
    )


@pytest.fixture
def transport(monkeypatch):
    now = [1000.0]
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(
        github_client,
        "time",
        SimpleNamespace(time=lambda: now[0], monotonic=lambda: now[0], sleep=sleep),
    )
    clients = []

    def build(handler):
        client = GitHubClient("test")
        client.client.close()
        client.client = httpx.Client(
            base_url="https://api.github.com", transport=httpx.MockTransport(handler)
        )
        clients.append(client)
        return client, now, waits

    yield build
    for client in clients:
        client.close()


def test_rate_limit_headers_respect_search_reset_and_global_retry_after(transport):
    replies = iter(
        [
            (200, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1015"}),
            (200, {"x-ratelimit-remaining": "400"}),
            (429, {"Retry-After": "8"}),
            (200, {"x-ratelimit-remaining": "26"}),
            (200, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999"}),
        ]
    )
    calls = []

    def handle(request):
        calls.append(request.url.path)
        code, headers = next(replies)
        return httpx.Response(code, headers=headers, json={})

    client, now, waits = transport(handle)
    client.request("GET", "/search/repositories")
    client.request("GET", "/repos/x/y")
    assert now[0] == 1000 and not waits
    client.request("GET", "/search/issues")
    assert waits == [16, 8] and client.rate_limit_remaining == 26
    client.request("GET", "/search/issues")
    with pytest.raises(GitHubClientError, match="rate limit"):
        client.request("GET", "/search/issues")
    assert len(calls) == 5 and waits == [16, 8]


def test_paginated_counts_are_exact_or_unknown_in_one_request(transport):
    requests = []
    replies = iter(
        [
            (
                200,
                [{"login": "x"}],
                {"Link": '<https://api.github.com/page?per_page=1&page=549>; rel="last"'},
            ),
            (200, [{}], {"Link": '<https://api.github.com/page?page=2>; rel="next"'}),
            (200, [], {}),
            (200, {"incomplete_results": True, "total_count": 1234}, {}),
            (404, {}, {}),
        ]
    )

    def handle(request):
        requests.append(request)
        status, data, headers = next(replies)
        return httpx.Response(status, headers=headers, json=data)

    client, _, waits = transport(handle)
    assert client.contributors_count("x", "y") == 549
    assert client.count_commits("x", "y", MON, MON) is None
    assert client.count_commits("x", "y", MON, MON) == 0
    assert client.count_issue_search("repo:x/y") is None
    assert not client.readme_exists("x", "y")
    assert len(requests) == 5 and not waits
    assert requests[1].url.params["since"] == f"{MON}T00:00:00Z"
