from __future__ import annotations

import time
from datetime import date
from typing import Any

import httpx


class GitHubClientError(RuntimeError):
    pass


class GitHubClient:
    def __init__(
        self,
        token: str,
        rest_endpoint: str = "https://api.github.com",
        timeout: float = 20.0,
        max_retries: int = 2,
    ) -> None:
        self.rest_endpoint = rest_endpoint.rstrip("/")
        self.max_retries = max_retries
        self.rate_limit_remaining: int | None = None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {token}",
        }
        self.client = httpx.Client(base_url=self.rest_endpoint, headers=headers, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any] | list[Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.request(method, url, **kwargs)
                remaining = response.headers.get("x-ratelimit-remaining")
                if remaining is not None and remaining.isdigit():
                    self.rate_limit_remaining = int(remaining)
                if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
                    raise GitHubClientError("GitHub API rate limit exhausted")
                response.raise_for_status()
                if response.status_code == 204:
                    return {}
                return response.json()
            except (httpx.HTTPError, GitHubClientError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(0.4 * (attempt + 1))
        raise GitHubClientError(str(last_error))

    def search_repositories(self, query: str, per_page: int = 30, page: int = 1) -> list[dict[str, Any]]:
        payload = self.request(
            "GET",
            "/search/repositories",
            params={"q": query, "sort": "updated", "order": "desc", "per_page": per_page, "page": page},
        )
        if not isinstance(payload, dict):
            return []
        return list(payload.get("items") or [])

    def repository(self, owner: str, name: str) -> dict[str, Any]:
        payload = self.request("GET", f"/repos/{owner}/{name}")
        if not isinstance(payload, dict):
            raise GitHubClientError("unexpected repository payload")
        return payload

    def readme_exists(self, owner: str, name: str) -> bool:
        try:
            self.request("GET", f"/repos/{owner}/{name}/readme")
            return True
        except GitHubClientError:
            return False

    def latest_release(self, owner: str, name: str) -> tuple[int, str | None]:
        try:
            releases = self.request("GET", f"/repos/{owner}/{name}/releases", params={"per_page": 1})
        except GitHubClientError:
            return 0, None
        if not isinstance(releases, list) or not releases:
            return 0, None
        return 1, releases[0].get("published_at") or releases[0].get("created_at")

    def contributors_count(self, owner: str, name: str) -> int | None:
        try:
            contributors = self.request(
                "GET",
                f"/repos/{owner}/{name}/contributors",
                params={"per_page": 1, "anon": "false"},
            )
        except GitHubClientError:
            return None
        if isinstance(contributors, list):
            return len(contributors)
        return None

    def count_commits(self, owner: str, name: str, since: date, until: date) -> int | None:
        return self._count_list_endpoint(
            f"/repos/{owner}/{name}/commits",
            {"since": f"{since.isoformat()}T00:00:00Z", "until": f"{until.isoformat()}T23:59:59Z"},
        )

    def count_issue_search(self, query: str) -> int | None:
        try:
            payload = self.request("GET", "/search/issues", params={"q": query, "per_page": 1})
        except GitHubClientError:
            return None
        if isinstance(payload, dict):
            total = payload.get("total_count")
            return int(total) if total is not None else None
        return None

    def _count_list_endpoint(self, url: str, params: dict[str, str]) -> int | None:
        try:
            payload = self.request("GET", url, params={**params, "per_page": 100, "page": 1})
        except GitHubClientError:
            return None
        if isinstance(payload, list):
            return len(payload)
        return None

