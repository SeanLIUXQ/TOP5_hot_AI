from __future__ import annotations

import time
from datetime import date
from email.utils import parsedate_to_datetime
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
        # Search and core have independent primary limits. Retry-After or a
        # secondary limit can pause both. Budgets apply to the entire client.
        self._resume_at: dict[str, float] = {}
        self._wait_budget = 16 * 60.0
        self._retry_budget = 12
        self._deadline = time.monotonic() + 25 * 60
        self._page_links: dict = {}
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {token}",
        }
        self.client = httpx.Client(base_url=self.rest_endpoint, headers=headers, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def _wait_for_quota(self, bucket: str) -> None:
        delay = max(self._resume_at.get(bucket, 0), self._resume_at.get("all", 0)) - time.time()
        if (
            delay > min(65.0, self._wait_budget)
            or time.monotonic() + max(0, delay) >= self._deadline
        ):
            raise GitHubClientError("GitHub API rate limit exhausted")
        if delay > 0:
            self._wait_budget -= delay
            time.sleep(delay)

    def _record_quota(self, response: httpx.Response, bucket: str) -> bool:
        now = time.time()
        remaining = response.headers.get("x-ratelimit-remaining", "")
        if remaining.isdigit():
            self.rate_limit_remaining = int(remaining)
        empty = remaining == "0"
        if empty:
            reset = response.headers.get("x-ratelimit-reset", "")
            self._resume_at[bucket] = int(reset) + 1 if reset.isdigit() else now + 60
        if response.status_code not in (403, 429):
            return False
        retry = response.headers.get("retry-after")
        if retry is not None:
            try:
                resume_at = (
                    now + int(retry)
                    if retry.isdigit()
                    else parsedate_to_datetime(retry).timestamp()
                )
            except (ValueError, TypeError, OverflowError):
                resume_at = now + 60
            self._resume_at["all"] = max(self._resume_at.get("all", 0), resume_at)
        elif not empty and (
            response.status_code == 429 or "secondary rate limit" in response.text.lower()
        ):
            self._resume_at["all"] = now + 60
        else:
            return empty
        return True

    def request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any] | list[Any]:
        bucket = "search" if httpx.URL(url).path.startswith("/search/") else "core"
        for attempt in range(self.max_retries + 1):
            self._wait_for_quota(bucket)
            throttled = False
            try:
                response = self.client.request(method, url, **kwargs)
                throttled = self._record_quota(response, bucket)
                response.raise_for_status()
                self._page_links = response.links
                return {} if response.status_code == 204 else response.json()
            except httpx.HTTPError as exc:
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code < 500
                    and not throttled
                ):
                    raise GitHubClientError(str(exc)) from None
                if attempt == self.max_retries or self._retry_budget <= 0:
                    if throttled:
                        raise GitHubClientError("GitHub API rate limit exhausted") from None
                    raise GitHubClientError(str(exc)) from None
                self._retry_budget -= 1
                if not throttled:
                    time.sleep(0.4 * (attempt + 1))
        raise GitHubClientError("GitHub API request failed")

    def search_repositories(
        self, query: str, per_page: int = 30, page: int = 1
    ) -> list[dict[str, Any]]:
        payload = self.request(
            "GET",
            "/search/repositories",
            params={
                "q": query,
                "sort": "updated",
                "order": "desc",
                "per_page": per_page,
                "page": page,
            },
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
            releases = self.request(
                "GET", f"/repos/{owner}/{name}/releases", params={"per_page": 1}
            )
        except GitHubClientError:
            return 0, None
        if not isinstance(releases, list) or not releases:
            return 0, None
        return 1, releases[0].get("published_at") or releases[0].get("created_at")

    def contributors_count(self, owner: str, name: str) -> int | None:
        return self._count_list_endpoint(f"/repos/{owner}/{name}/contributors", {"anon": "false"})

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
        if isinstance(payload, dict) and not payload.get("incomplete_results"):
            total = payload.get("total_count")
            return int(total) if total is not None else None
        return None

    def _count_list_endpoint(self, url: str, params: dict[str, str]) -> int | None:
        # A one-item page makes the last page number the count. If GitHub
        # supplies only a next link, the total is unknown within this budget.
        try:
            payload = self.request("GET", url, params={**params, "per_page": 1, "page": 1})
        except GitHubClientError:
            return None
        if not isinstance(payload, list):
            return None
        if last := self._page_links.get("last"):
            page = httpx.URL(last["url"]).params.get("page", "")
            return int(page) if page.isdigit() and int(page) > 0 else None
        return None if "next" in self._page_links else len(payload)
