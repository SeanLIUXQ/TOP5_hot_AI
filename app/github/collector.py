from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.dates import current_week_start, utc_now
from app.core.json import dumps
from app.db.models import CollectionRun, Repository, RepoSnapshot
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
) -> CollectionRun:
    settings = get_settings()
    target_week = week_start or current_week_start()
    limit = candidate_limit or settings.candidate_limit
    use_mock = force_mock or (not settings.github_token and settings.use_mock_when_no_token)

    run = CollectionRun(
        run_type="weekly",
        trigger_source=trigger_source,
        week_start=target_week,
        status="pending",
        score_version=settings.score_version,
        started_at=utc_now(),
        metadata_json=dumps({"use_mock": use_mock, "candidate_limit": limit}),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        run.status = "collecting"
        db.commit()
        if use_mock:
            raw_items = [(item, "mock") for item in make_sample_repositories(target_week)[:limit]]
            rate_limit_remaining = None
        else:
            raw_items, rate_limit_remaining = collect_from_github(
                token=settings.github_token or "",
                rest_endpoint=settings.github_rest_endpoint,
                min_stars=settings.min_stars,
                limit=limit,
                week_start=target_week,
            )

        run.candidate_count = len(raw_items)
        db.commit()

        run.status = "normalizing"
        db.commit()
        saved_count = 0
        filtered: dict[str, int] = {}
        warning_count = 0
        for raw, source_query in raw_items:
            normalized = normalize_repository(raw, source_query=source_query)
            reason = None if use_mock else filter_repository(normalized, min_stars=settings.min_stars)
            if reason:
                filtered[reason] = filtered.get(reason, 0) + 1
                continue
            repository = upsert_repository(db, normalized)
            snapshot = upsert_snapshot(db, repository, normalized, raw, run.id, target_week)
            if snapshot.data_quality_level != "complete":
                warning_count += 1
            saved_count += 1

        if saved_count == 0:
            run.status = "failed"
            run.finished_at = utc_now()
            run.error_message = "No candidate repository survived filtering."
            run.filtered_count = sum(filtered.values())
            run.metadata_json = dumps({"use_mock": use_mock, "filters": filtered})
            db.commit()
            return run

        run.filtered_count = sum(filtered.values())
        run.warning_count = warning_count
        run.rate_limit_remaining = rate_limit_remaining
        run.metadata_json = dumps({"use_mock": use_mock, "filters": filtered, "saved_count": saved_count})
        db.commit()

        run.status = "scoring"
        db.commit()
        rankings = generate_weekly_ranking(db, target_week, settings.score_version)
        run.ranked_count = len(rankings)

        if generate_report:
            run.status = "reporting"
            db.commit()
            write_reports(rankings[:10], settings.report_output_dir)

        run.status = "succeeded" if len(rankings) >= 10 else "partial_succeeded"
        run.finished_at = utc_now()
        db.commit()
        db.refresh(run)
        return run
    except Exception as exc:
        run.status = "failed"
        run.finished_at = utc_now()
        run.error_message = str(exc)
        db.commit()
        db.refresh(run)
        return run


def collect_from_github(
    token: str,
    rest_endpoint: str,
    min_stars: int,
    limit: int,
    week_start: date,
) -> tuple[list[tuple[dict[str, Any], str]], int | None]:
    if not token:
        raise GitHubClientError("GITHUB_TOKEN is required for live GitHub collection")

    client = GitHubClient(token=token, rest_endpoint=rest_endpoint)
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
            reason = filter_repository(normalized, min_stars=min_stars)
            if reason:
                enriched.append(({**raw, "filter_reason": reason}, query))
                continue
            owner, name = normalized.owner, normalized.name
            raw["has_readme"] = client.readme_exists(owner, name)
            release_count, latest_release_at = client.latest_release(owner, name)
            raw["release_count"] = release_count
            raw["latest_release_at"] = latest_release_at
            raw["contributor_count"] = client.contributors_count(owner, name)
            raw.update(activity_metrics(client, normalized.full_name, owner, name, week_start))
            enriched.append((raw, query))
        return enriched, client.rate_limit_remaining
    finally:
        client.close()


def activity_metrics(
    client: GitHubClient,
    full_name: str,
    owner: str,
    name: str,
    week_start: date,
) -> dict[str, int | None]:
    week_end = week_start + timedelta(days=6)
    start = week_start.isoformat()
    end = week_end.isoformat()
    return {
        "commits_7d": client.count_commits(owner, name, week_start, week_end),
        "prs_opened_7d": client.count_issue_search(f"repo:{full_name} type:pr created:{start}..{end}"),
        "prs_merged_7d": client.count_issue_search(f"repo:{full_name} type:pr is:merged merged:{start}..{end}"),
        "issues_opened_7d": client.count_issue_search(f"repo:{full_name} type:issue created:{start}..{end}"),
        "issues_closed_7d": client.count_issue_search(f"repo:{full_name} type:issue closed:{start}..{end}"),
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
    db.commit()
    db.refresh(existing)
    return existing


def upsert_snapshot(
    db: Session,
    repository: Repository,
    repo: NormalizedRepository,
    raw: dict[str, Any],
    collection_run_id: int,
    week_start: date,
) -> RepoSnapshot:
    previous = (
        db.query(RepoSnapshot)
        .filter(RepoSnapshot.repository_id == repository.id)
        .filter(RepoSnapshot.week_start < week_start)
        .order_by(RepoSnapshot.week_start.desc())
        .first()
    )
    existing = (
        db.query(RepoSnapshot)
        .filter(RepoSnapshot.repository_id == repository.id)
        .filter(RepoSnapshot.week_start == week_start)
        .one_or_none()
    )
    snapshot = existing or RepoSnapshot(repository_id=repository.id, week_start=week_start)
    if existing is None:
        db.add(snapshot)

    stars_delta = int(raw.get("stars_delta_7d") or 0)
    forks_delta = int(raw.get("forks_delta_7d") or 0)
    watchers_delta = int(raw.get("watchers_delta_7d") or 0)
    if previous:
        stars_delta = repo.stars - previous.stars
        forks_delta = repo.forks - previous.forks
        watchers_delta = repo.watchers - previous.watchers
    previous_stars = previous.stars if previous else max(repo.stars - stars_delta, 100)
    growth_rate = stars_delta / max(previous_stars, 100)

    missing_fields = [
        field
        for field in ["commits_7d", "prs_opened_7d", "prs_merged_7d", "issues_opened_7d", "issues_closed_7d"]
        if raw.get(field) is None
    ]
    data_quality_level = "complete" if not missing_fields else "partial"

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
    snapshot.data_quality_json = dumps({"missing_fields": missing_fields})
    snapshot.collected_at = utc_now()
    db.commit()
    db.refresh(snapshot)
    return snapshot

