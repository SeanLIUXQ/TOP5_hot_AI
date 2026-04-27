from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.github.queries import AI_KEYWORDS, AI_TOPICS, LIST_REPOSITORY_KEYWORDS


@dataclass(slots=True)
class NormalizedRepository:
    github_id: int
    owner: str
    name: str
    full_name: str
    html_url: str
    description: str | None
    primary_language: str | None
    license_spdx: str | None
    topics: list[str]
    created_at: datetime | None
    pushed_at: datetime | None
    archived: bool
    fork: bool
    stars: int
    forks: int
    watchers: int
    open_issues: int
    default_branch: str | None
    ai_relevance_score: float
    source_query: str | None = None
    has_readme: bool = False
    raw: dict[str, Any] | None = None


def parse_github_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def normalize_repository(raw: dict[str, Any], source_query: str | None = None) -> NormalizedRepository:
    owner_data = raw.get("owner") or {}
    owner = owner_data.get("login") or (raw.get("full_name", "").split("/")[0] if "/" in raw.get("full_name", "") else "")
    name = raw.get("name") or (raw.get("full_name", "").split("/")[-1] if raw.get("full_name") else "")
    full_name = raw.get("full_name") or f"{owner}/{name}"
    license_data = raw.get("license") or {}
    topics = raw.get("topics") or []
    description = raw.get("description")
    score = estimate_ai_relevance(full_name, description, topics, raw.get("language"))
    return NormalizedRepository(
        github_id=int(raw["id"]),
        owner=owner,
        name=name,
        full_name=full_name,
        html_url=raw.get("html_url") or f"https://github.com/{full_name}",
        description=description,
        primary_language=raw.get("language"),
        license_spdx=license_data.get("spdx_id") if isinstance(license_data, dict) else None,
        topics=list(topics),
        created_at=parse_github_datetime(raw.get("created_at")),
        pushed_at=parse_github_datetime(raw.get("pushed_at")),
        archived=bool(raw.get("archived", False)),
        fork=bool(raw.get("fork", False)),
        stars=int(raw.get("stargazers_count") or raw.get("stars") or 0),
        forks=int(raw.get("forks_count") or raw.get("forks") or 0),
        watchers=int(raw.get("watchers_count") or raw.get("watchers") or 0),
        open_issues=int(raw.get("open_issues_count") or raw.get("open_issues") or 0),
        default_branch=raw.get("default_branch"),
        ai_relevance_score=score,
        source_query=source_query,
        has_readme=bool(raw.get("has_readme", False)),
        raw=raw,
    )


def estimate_ai_relevance(
    full_name: str,
    description: str | None,
    topics: list[str],
    primary_language: str | None,
) -> float:
    haystack = " ".join(
        [
            full_name.lower(),
            (description or "").lower(),
            " ".join(topic.lower() for topic in topics),
            (primary_language or "").lower(),
        ]
    )
    topic_matches = sum(1 for topic in topics if topic.lower() in AI_TOPICS)
    keyword_matches = sum(1 for keyword in AI_KEYWORDS if keyword in haystack)
    topic_score = min(topic_matches / 3, 1.0) * 40
    description_score = min(keyword_matches / 4, 1.0) * 30
    readme_proxy_score = min(keyword_matches / 6, 1.0) * 20
    ecosystem_score = 10 if (primary_language or "").lower() in {"python", "jupyter notebook", "typescript"} else 5
    list_penalty = 25 if is_list_repository(full_name, description) else 0
    return max(0.0, min(100.0, topic_score + description_score + readme_proxy_score + ecosystem_score - list_penalty))


def is_list_repository(full_name: str, description: str | None) -> bool:
    text = f"{full_name} {description or ''}".lower()
    return any(keyword in text for keyword in LIST_REPOSITORY_KEYWORDS)


def filter_repository(repo: NormalizedRepository, min_stars: int, now: datetime | None = None) -> str | None:
    current = now or datetime.now(UTC)
    if repo.fork:
        return "fork"
    if repo.archived:
        return "archived"
    if repo.stars < min_stars:
        return "below_min_stars"
    if repo.pushed_at and repo.pushed_at < current - timedelta(days=180):
        return "inactive"
    if is_list_repository(repo.full_name, repo.description):
        return "list_repository"
    if repo.ai_relevance_score < 50:
        return "low_ai_relevance"
    return None

