from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.dates import current_week_start, previous_week_start, utc_now
from app.core.json import dumps, loads
from app.db.models import CollectionRun, RepoDailyCounter, Repository, RepoSnapshot
from app.github.client import GitHubClient, GitHubClientError
from app.github.mock_data import make_sample_repositories
from app.github.normalizer import (
    NormalizedRepository,
    filter_repository,
    normalize_repository,
    parse_github_datetime,
)
from app.github.queries import build_search_queries
from app.ranking.pipeline import generate_weekly_ranking
from app.ranking.reports import write_reports


def run_weekly_collection(
    db: Session,
    week_start: date | None = None,
    trigger_source: str = "manual",
    force_mock: bool = False,
    candidate_limit: int | None = None,
    generate_report: bool = True,
    *,
    as_of: date | None = None,
) -> CollectionRun:
    """Refresh one Monday bucket. An explicit week alone keeps the weekly CLI anchor."""
    settings = get_settings()
    observed = as_of or week_start or utc_now().date()
    target_week = week_start or current_week_start(observed)
    if current_week_start(observed) != target_week:
        raise ValueError("week_start must be the Monday of the observation week")
    limit = settings.candidate_limit if candidate_limit is None else candidate_limit
    if not 1 <= limit <= 1000:
        raise ValueError("candidate_limit must be between 1 and 1000")
    use_mock = force_mock or (not settings.github_token and settings.use_mock_when_no_token)

    metadata = {"use_mock": use_mock, "candidate_limit": limit, "as_of": observed.isoformat()}
    run = CollectionRun(
        run_type="weekly",
        trigger_source=trigger_source,
        week_start=target_week,
        status="pending",
        score_version=settings.score_version,
        started_at=utc_now(),
        metadata_json=dumps(metadata),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        run.status = "collecting"
        db.commit()
        if use_mock:
            sample_start = target_week if as_of is None else observed - timedelta(days=6)
            raw_items = [(item, "mock") for item in make_sample_repositories(sample_start)[:limit]]
            rate_limit_remaining = None
        else:
            raw_items, rate_limit_remaining = collect_from_github(
                token=settings.github_token or "",
                rest_endpoint=settings.github_rest_endpoint,
                min_stars=settings.min_stars,
                limit=limit,
                week_start=target_week,
                as_of=observed,
            )

        run.candidate_count = len(raw_items)
        run.status = "normalizing"
        db.commit()
        saved_count = 0
        filtered: dict[str, int] = {}
        warning_count = 0
        for raw, source_query in raw_items:
            normalized = normalize_repository(raw, source_query=source_query)
            reason = (
                None
                if use_mock
                else filter_repository(
                    normalized,
                    min_stars=settings.min_stars,
                    now=datetime.combine(observed, time.max, UTC),
                )
            )
            if reason:
                filtered[reason] = filtered.get(reason, 0) + 1
                continue
            repository = upsert_repository(db, normalized)
            snapshot = upsert_snapshot(
                db,
                repository,
                normalized,
                raw,
                run.id,
                target_week,
                as_of=observed,
                use_mock=use_mock,
            )
            if snapshot.data_quality_level != "complete":
                warning_count += 1
            saved_count += 1

        if saved_count == 0:
            run.status = "failed"
            run.finished_at = utc_now()
            run.error_message = "No candidate repository survived filtering."
            run.filtered_count = sum(filtered.values())
            run.metadata_json = dumps({**metadata, "filters": filtered})
            db.commit()
            return run

        run.filtered_count = sum(filtered.values())
        run.warning_count = warning_count
        run.rate_limit_remaining = rate_limit_remaining
        run.metadata_json = dumps({**metadata, "filters": filtered, "saved_count": saved_count})
        db.flush()
        # The successful refresh owns this week's candidate set. An empty or
        # failed refresh leaves the previous snapshots and rankings intact.
        db.execute(
            delete(RepoSnapshot).where(
                RepoSnapshot.week_start == target_week,
                RepoSnapshot.collection_run_id.is_distinct_from(run.id),
            )
        )

        run.status = "scoring"
        rankings = generate_weekly_ranking(
            db, target_week, settings.score_version, as_of=observed, commit=False
        )
        run.ranked_count = len(rankings)

        if generate_report:
            run.status = "reporting"
            write_reports(rankings[:10], settings.report_output_dir)

        run.status = "succeeded" if len(rankings) >= 10 else "partial_succeeded"
        run.finished_at = utc_now()
        db.commit()
        db.refresh(run)
        return run
    except Exception as exc:
        db.rollback()
        run.status = "failed"
        run.finished_at = utc_now()
        # Exception strings may include credentialed URLs, SQL or request headers.
        run.error_message = (
            str(exc)
            if isinstance(exc, GitHubClientError)
            and str(exc)
            in {
                "GitHub API rate limit exhausted",
                "GITHUB_TOKEN is required for live GitHub collection",
            }
            else f"Collection failed ({type(exc).__name__})."
        )
        db.commit()
        db.refresh(run)
        return run


def collect_from_github(
    token: str,
    rest_endpoint: str,
    min_stars: int,
    limit: int,
    week_start: date,
    *,
    as_of: date | None = None,
) -> tuple[list[tuple[dict[str, Any], str]], int | None]:
    if not token:
        raise GitHubClientError("GITHUB_TOKEN is required for live GitHub collection")

    observed = as_of or week_start
    settings = get_settings()
    client = GitHubClient(
        token=token,
        rest_endpoint=rest_endpoint,
        timeout=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
    )
    try:
        candidates: dict[int, tuple[dict[str, Any], str]] = {}
        for query in build_search_queries(min_stars):
            if len(candidates) >= limit:
                break
            for raw in client.search_repositories(query, per_page=30, page=1):
                repo_id = int(raw["id"])
                if repo_id not in candidates:
                    candidates[repo_id] = (raw, query)
                if len(candidates) >= limit:
                    break

        enriched: list[tuple[dict[str, Any], str]] = []
        for raw, query in candidates.values():
            normalized = normalize_repository(raw, source_query=query)
            reason = filter_repository(
                normalized, min_stars=min_stars, now=datetime.combine(observed, time.max, UTC)
            )
            if reason:
                enriched.append(({**raw, "filter_reason": reason}, query))
                continue
            owner, name = normalized.owner, normalized.name
            raw["has_readme"] = client.readme_exists(owner, name)
            release_count, latest_release_at = client.latest_release(owner, name)
            raw["release_count"] = release_count
            raw["latest_release_at"] = latest_release_at
            raw["contributor_count"] = client.contributors_count(owner, name)
            raw.update(activity_metrics(client, normalized.full_name, owner, name, observed))
            enriched.append((raw, query))
        return enriched, client.rate_limit_remaining
    finally:
        client.close()


def activity_metrics(
    client: GitHubClient,
    full_name: str,
    owner: str,
    name: str,
    as_of: date,
) -> dict[str, int | None]:
    since = as_of - timedelta(days=6)
    start = since.isoformat()
    end = as_of.isoformat()
    return {
        "commits_7d": client.count_commits(owner, name, since, as_of),
        "prs_opened_7d": client.count_issue_search(
            f"repo:{full_name} type:pr created:{start}..{end}"
        ),
        "prs_merged_7d": client.count_issue_search(
            f"repo:{full_name} type:pr is:merged merged:{start}..{end}"
        ),
        "issues_opened_7d": client.count_issue_search(
            f"repo:{full_name} type:issue created:{start}..{end}"
        ),
        "issues_closed_7d": client.count_issue_search(
            f"repo:{full_name} type:issue closed:{start}..{end}"
        ),
    }


def upsert_repository(db: Session, repo: NormalizedRepository) -> Repository:
    existing = db.query(Repository).filter(Repository.github_id == repo.github_id).one_or_none()
    if existing is None:
        existing = Repository(github_id=repo.github_id)
        db.add(existing)
    existing.owner = repo.owner
    existing.name = repo.name
    existing.full_name = repo.full_name
    existing.html_url = repo.html_url
    existing.description = repo.description
    existing.primary_language = repo.primary_language
    existing.license_spdx = repo.license_spdx
    existing.topics_json = dumps(repo.topics)
    existing.ai_relevance_score = repo.ai_relevance_score
    existing.source_query = repo.source_query
    existing.created_at = repo.created_at
    existing.pushed_at = repo.pushed_at
    existing.archived = repo.archived
    existing.fork = repo.fork
    existing.has_readme = repo.has_readme
    existing.updated_at = utc_now()
    db.flush()
    return existing


def upsert_snapshot(
    db: Session,
    repository: Repository,
    repo: NormalizedRepository,
    raw: dict[str, Any],
    collection_run_id: int,
    week_start: date,
    *,
    as_of: date | None = None,
    use_mock: bool = False,
) -> RepoSnapshot:
    existing = (
        db.query(RepoSnapshot)
        .filter(RepoSnapshot.repository_id == repository.id)
        .filter(RepoSnapshot.week_start == week_start)
        .one_or_none()
    )
    snapshot = existing or RepoSnapshot(repository_id=repository.id, week_start=week_start)
    collected_at = utc_now()
    observed = as_of or collected_at.date()
    counter_date = observed if use_mock else collected_at.date()
    deltas, growth_rate, baseline = _counter_growth(
        db, repository.id, week_start, observed, counter_date, collected_at, repo, raw, use_mock
    )
    stars_delta, forks_delta, watchers_delta = deltas

    missing_fields = [
        field
        for field in [
            "commits_7d",
            "prs_opened_7d",
            "prs_merged_7d",
            "issues_opened_7d",
            "issues_closed_7d",
        ]
        if raw.get(field) is None
    ]
    data_quality_level = (
        "complete" if not missing_fields and baseline["status"] == "observed" else "partial"
    )

    snapshot.collection_run_id = collection_run_id
    snapshot.stars = repo.stars
    snapshot.forks = repo.forks
    snapshot.watchers = repo.watchers
    snapshot.open_issues = repo.open_issues
    snapshot.stars_delta_7d = stars_delta
    snapshot.stars_growth_rate_7d = growth_rate
    snapshot.forks_delta_7d = forks_delta
    snapshot.watchers_delta_7d = watchers_delta
    snapshot.default_branch = repo.default_branch
    snapshot.pushed_at = repo.pushed_at
    snapshot.release_count = int(raw.get("release_count") or 0)
    snapshot.latest_release_at = parse_github_datetime(raw.get("latest_release_at"))
    snapshot.contributor_count = raw.get("contributor_count")
    snapshot.commit_count_7d = raw.get("commits_7d")
    snapshot.pr_opened_7d = raw.get("prs_opened_7d")
    snapshot.pr_merged_7d = raw.get("prs_merged_7d")
    snapshot.issues_opened_7d = raw.get("issues_opened_7d")
    snapshot.issues_closed_7d = raw.get("issues_closed_7d")
    snapshot.data_quality_level = data_quality_level
    snapshot.data_quality_json = dumps(
        {
            "missing_fields": missing_fields,
            "growth_baseline": baseline,
            "as_of": observed.isoformat(),
            "counter_observed_on": counter_date.isoformat(),
            "use_mock": use_mock,
        }
    )
    snapshot.collected_at = collected_at
    if existing is None:
        db.add(snapshot)
    db.flush()
    return snapshot


_COUNTERS = (
    ("stargazers_count", "stars"),
    ("forks_count", "forks"),
    ("watchers_count", "watchers"),
)


def _counter_growth(
    db: Session,
    repository_id: int,
    week: date,
    as_of: date,
    observed_on: date,
    collected_at: datetime,
    repo: NormalizedRepository,
    raw: dict,
    use_mock: bool,
) -> tuple[tuple[int, int, int], float, dict]:
    provenance = "mock" if use_mock else "github"
    current = (repo.stars, repo.forks, repo.watchers)
    baseline = {
        "status": "unavailable",
        "source": None,
        "provenance": None,
        "previous_week": None,
        "previous_observed_on": None,
        "observed_on": observed_on.isoformat(),
        "interval_days": None,
    }
    reported = tuple(raw.get(f"{name}_delta_7d") for _, name in _COUNTERS)
    deltas = tuple(int(value or 0) for value in reported)
    denominator = max(repo.stars - deltas[0], 100)
    if any(value is not None for value in reported):
        baseline.update(status="estimated", source="reported_delta", provenance=provenance)
    # The normalizer has neutral defaults. Only actual counter triples can
    # become future baselines, including explicitly observed zero counts.
    if any(raw.get(key) is None and raw.get(name) is None for key, name in _COUNTERS):
        return deltas, deltas[0] / denominator, baseline

    prior_date = observed_on - timedelta(days=7)
    prior = db.get(RepoDailyCounter, (repository_id, prior_date))
    if prior is not None and prior.provenance == provenance:
        source, source_provenance = "daily_counter", prior.provenance
    else:
        # A v1 checkpoint or a legacy TOP10 only retains a weekly snapshot.
        # Never substitute an older week, or use synthetic counters for live growth.
        prior = (
            db.query(RepoSnapshot)
            .filter_by(repository_id=repository_id, week_start=previous_week_start(week))
            .one_or_none()
        )
        source, source_provenance = "weekly_snapshot", None
        if prior is not None:
            quality = loads(prior.data_quality_json, {})
            prior_date = date.fromisoformat(
                quality.get("counter_observed_on") or prior.collected_at.date().isoformat()
            )
            run_meta = loads(prior.collection_run.metadata_json, {}) if prior.collection_run else {}
            prior_mock = quality.get("use_mock", run_meta.get("use_mock", False))
            source_provenance = quality.get("source") or (
                "mock" if prior_mock else "weekly_snapshot"
            )
            if use_mock != prior_mock or prior.data_quality_level == "invalid":
                prior = None

    if prior is not None:
        interval = (observed_on - prior_date).days
        status = "estimated"
        if interval != 7 or as_of != observed_on:
            status = "interval_mismatch"
        elif source == "daily_counter" and not use_mock:
            status = "observed"
        baseline.update(
            status=status,
            source=source,
            provenance=source_provenance,
            previous_week=current_week_start(prior_date).isoformat(),
            previous_observed_on=prior_date.isoformat(),
            interval_days=interval,
        )
        deltas = (
            repo.stars - prior.stars,
            repo.forks - prior.forks,
            repo.watchers - prior.watchers,
        )
        denominator = max(prior.stars, 100)

    today = db.get(RepoDailyCounter, (repository_id, observed_on))
    if today is None:
        today = RepoDailyCounter(repository_id=repository_id, observed_on=observed_on)
        db.add(today)
    if today.provenance != "github" or not use_mock:
        today.stars, today.forks, today.watchers = current
        today.observed_at = collected_at
        today.provenance = provenance
    return deltas, deltas[0] / denominator, baseline
