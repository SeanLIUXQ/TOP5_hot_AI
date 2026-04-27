from datetime import UTC, datetime, timedelta

from app.github.normalizer import estimate_ai_relevance, filter_repository, normalize_repository


def raw_repo(**overrides):
    base = {
        "id": 1,
        "name": "agent-kit",
        "full_name": "example/agent-kit",
        "owner": {"login": "example"},
        "html_url": "https://github.com/example/agent-kit",
        "description": "LLM agent framework for RAG applications",
        "language": "Python",
        "topics": ["llm", "rag", "ai-agent"],
        "license": {"spdx_id": "MIT"},
        "created_at": "2025-01-01T00:00:00Z",
        "pushed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "archived": False,
        "fork": False,
        "stargazers_count": 500,
        "forks_count": 50,
        "watchers_count": 20,
        "open_issues_count": 12,
        "default_branch": "main",
    }
    base.update(overrides)
    return base


def test_ai_relevance_scores_topic_and_description() -> None:
    score = estimate_ai_relevance(
        "example/rag-agent",
        "LLM agent framework for retrieval augmented generation",
        ["llm", "rag", "ai-agent"],
        "Python",
    )
    assert score >= 80


def test_filter_repository_rejects_inactive() -> None:
    old = datetime.now(UTC) - timedelta(days=300)
    repo = normalize_repository(raw_repo(pushed_at=old.isoformat().replace("+00:00", "Z")))
    assert filter_repository(repo, min_stars=100) == "inactive"


def test_filter_repository_accepts_valid_ai_repo() -> None:
    repo = normalize_repository(raw_repo())
    assert filter_repository(repo, min_stars=100) is None

