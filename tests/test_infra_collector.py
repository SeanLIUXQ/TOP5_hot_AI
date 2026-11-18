from datetime import UTC, date, datetime
from functools import partial

import httpx
import pytest

from app.github.client import GitHubClient, GitHubClientError
from app.infra import collector

DAY = date(2026, 11, 18)
SEEDS = {
    1385946301: "Niko1221/Strata",
    1429601047: "wici-ai/inferssd",
    1286080397: "JustVugg/colibri",
}


def repo(repo_id=8, name="lab/tiny", description="On-device NPU inference engine.", **extra):
    return {
        "id": repo_id,
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "description": description,
        "language": "C++",
        "license": {"spdx_id": "MIT"},
        "stargazers_count": 0,
        "created_at": "2026-11-10T12:00:00Z",
        "pushed_at": "2026-11-17T12:00:00Z",
        "topics": [],
        "archived": False,
        "private": False,
        "fork": False,
        **extra,
    }


def release(tag, published, **extra):
    return {
        "tag_name": tag,
        "published_at": published,
        "html_url": f"https://github.com/lab/tiny/releases/{tag}",
        **extra,
    }


class API:
    """Mutable responses behind the real GitHubClient, including its HTTP error wrapping."""

    def __init__(self):
        self.watched = {
            uid: repo(
                uid,
                name,
                created_at="2023-01-01T00:00:00Z",
                pushed_at="2024-05-01T00:00:00Z",
                description=None,
            )
            for uid, name in SEEDS.items()
        }
        self.rows = [repo()]
        self.releases = {}
        self.overrides = {}
        self.calls = []

    def respond(self, request):
        assert request.method == "GET"
        path = request.url.path
        self.calls.append(request.url)
        if path in self.overrides:
            value = self.overrides[path]
            if isinstance(value, Exception):
                raise value
            return value
        if path.startswith("/repositories/"):
            data = self.watched[int(path.removeprefix("/repositories/"))]
        elif path == "/search/repositories":
            size = int(request.url.params["per_page"])
            data = {
                "incomplete_results": False,
                "total_count": len(self.rows),
                "items": self.rows[:size],
            }
        elif path.endswith("/releases"):
            name = path.removeprefix("/repos/").removesuffix("/releases")
            data = self.releases.get(name, [])
        else:
            pytest.fail(f"Unexpected GitHub request {request.url}")
        return httpx.Response(200, json=data)


@pytest.fixture
def github(monkeypatch):
    api = API()
    monkeypatch.setattr(
        httpx, "Client", partial(httpx.Client, transport=httpx.MockTransport(api.respond))
    )
    client = GitHubClient("test", max_retries=0)
    try:
        yield api, client
    finally:
        client.close()


def test_order_windows_and_utc_timestamps_are_reproducible(github, monkeypatch):
    api, client = github
    clock = datetime(2026, 11, 19, 2, 15, tzinfo=UTC)
    monkeypatch.setattr(collector, "utc_now", lambda: clock)
    api.rows = [
        repo(pushed_at="2026-11-19T00:30:00+02:00", language=None),
        repo(
            9,
            "lab/cloud",
            "A model serving framework for large language models.",
            stargazers_count=90000,
            created_at="2024-01-01T00:00:00Z",
        ),
    ]
    api.releases["lab/tiny"] = [
        release("tomorrow", "2026-11-18T23:55:00-02:00"),
        release("draft", None, draft=True),
        release("rc2", "2026-11-19T01:00:00+03:00", prerelease=True),
        release("v1", "2026-11-17T09:00:00Z"),
    ]
    first = collector.collect_infra(client, DAY)
    queries = [url.params["q"] for url in api.calls if url.path == "/search/repositories"]
    assert any("created:2026-10-20..2026-11-18" in q for q in queries)
    assert any("pushed:2026-11-05..2026-11-18" in q for q in queries)
    assert all("is:public" in q and "fork:false" in q and "archived:false" in q for q in queries)
    assert first["date"] == "2026-11-18" and first["generated_at"] == "2026-11-19T02:15:00Z"
    assert first["items"][0] == {
        "full_name": "lab/tiny",
        "html_url": "https://github.com/lab/tiny",
        "description": "On-device NPU inference engine.",
        "language": None,
        "license": "MIT",
        "stars": 0,
        "created_at": "2026-11-10T12:00:00Z",
        "pushed_at": "2026-11-18T22:30:00Z",
        "category": "runtime",
        "is_watchlist": False,
        "release_checked": True,
        "release": {
            "tag": "rc2",
            "url": "https://github.com/lab/tiny/releases/rc2",
            "published_at": "2026-11-18T22:00:00Z",
        },
    }
    assert first["source"] == "github" and first["warnings"] == []
    api.rows.reverse()
    assert collector.collect_infra(client, DAY) == first


def test_all_matches_retained_release_budget_and_canonical_watchlist(github):
    api, client = github
    renamed_id = 1385946301
    current = "new-owner/renamed-engine"
    api.watched[renamed_id] = repo(
        renamed_id,
        current,
        "Legacy runtime",
        pushed_at="2024-02-01T00:00:00Z",
        created_at="2023-01-01T00:00:00Z",
        stargazers_count=4,
    )
    api.rows = [
        repo(renamed_id, SEEDS[renamed_id], stargazers_count=10000),
        repo(999, current.upper()),
        repo(100, "lab/unique-0"),
        *[repo(100 + i, f"lab/unique-{i}") for i in range(24)],
        repo(888, "lab/UNIQUE-1"),
        repo(101, "lab/unique-1-older-alias", pushed_at="2026-11-10T12:00:00Z"),
    ]
    payload = collector.collect_infra(client, DAY)
    items = payload["items"]
    assert len(items) == len({i["full_name"].casefold() for i in items}) == 27
    watched = {i["full_name"]: i for i in items if i["is_watchlist"]}
    assert set(watched) == {current, "wici-ai/inferssd", "JustVugg/colibri"}
    assert watched[current]["stars"] == 4 and watched[current]["category"] == "runtime"
    assert all(i["release_checked"] for i in watched.values())
    assert all(i["release"] is None for i in items)
    assert sum(i["release_checked"] for i in items) == 15
    assert [i["release_checked"] for i in items if not i["is_watchlist"]] == [True] * 12 + [
        False
    ] * 12
    paths = [url.path for url in api.calls]
    assert paths.count(f"/repos/{current}/releases") == 1
    assert f"/repos/{SEEDS[renamed_id]}/releases" not in paths
    assert paths.count("/search/repositories") <= 10
    assert len(paths) == len(SEEDS) + paths.count("/search/repositories") + 15
    coverage = payload["coverage"]
    assert coverage["qualified_matches"] == 27 and coverage["qualified_discoveries"] == 24
    assert coverage["release_check_limit"] == coverage["release_checks_completed"] == 15


def test_overlapping_searches_resolve_renames_before_reused_names(github):
    api, client = github
    api.rows = [
        repo(7, "lab/old", updated_at="2026-11-15T00:00:00Z", stargazers_count=999),
        repo(7, "lab/current", updated_at="2026-11-18T01:00:00Z"),
        repo(9, "lab/old", updated_at="2026-11-18T02:00:00Z", stargazers_count=1),
        repo(60, "lab/OLD", updated_at="2026-11-11T00:00:00Z"),
        repo(60, "lab/superseded", updated_at="2026-11-10T00:00:00Z"),
    ]

    def discoveries():
        result = collector.collect_infra(client, DAY)
        return {i["full_name"]: i["stars"] for i in result["items"] if not i["is_watchlist"]}

    assert discoveries() == {"lab/current": 0, "lab/old": 1}
    api.rows.reverse()
    assert discoveries() == {"lab/current": 0, "lab/old": 1}


def test_inference_scope_open_source_and_date_boundaries(github):
    api, client = github
    qualified = {
        "web": ("GPU acceleration kernels for WebGPU LLM inference", "acceleration"),
        "android": ("Execute LLMs on Android NPUs", "runtime"),
        "cpu": ("Run language models locally on CPUs and GPUs", "runtime"),
        "pack": ("Post-training INT4 quantization toolkit for neural networks", "quantization"),
        "disk": ("Run MoE models beyond RAM by streaming experts from NVMe SSD", "offload"),
        "serve": ("Distributed model serving framework for Kubernetes", "serving"),
        "moe": ("An inference runtime for mixture of experts models", "runtime"),
    }
    api.rows = [
        repo(i, f"lab/{name}", text, license={"spdx_id": "GPL-3.0-or-later"})
        for i, (name, (text, _)) in enumerate(qualified.items(), 10)
    ]
    api.rows += [
        repo(
            70, "lab/just-new", created_at="2026-10-20T00:00:00Z", pushed_at="2026-10-20T00:00:00Z"
        ),
        repo(
            71,
            "lab/just-active",
            created_at="2022-02-02T00:00:00Z",
            pushed_at="2026-11-05T00:00:00Z",
        ),
    ]
    rejected = [
        dict(description="Metal/WebGPU renderer for 3D games"),
        dict(description="A curated list of mobile inference engines"),
        dict(description="Causal inference library with GPU support"),
        dict(description="A client SDK for a hosted inference server"),
        dict(description="Local LLM agent framework for booking flights"),
        dict(
            description="Fine-tune models on laptop GPUs. A streaming trainer.",
            topics=["llm", "quantization"],
        ),
        dict(description="Compute image color quantization on the GPU"),
        dict(license=None),
        dict(license={"spdx_id": "NOASSERTION"}),
        dict(license={"spdx_id": "BUSL-1.1"}),
        dict(private=True),
        dict(visibility="internal"),
        dict(fork=True),
        dict(archived=True),
        dict(created_at="2026-10-19T23:59:59Z", pushed_at="2026-11-04T23:59:59Z"),
        dict(created_at="2026-11-19T00:00:00Z"),
        dict(pushed_at="2026-11-18T23:00:00-02:00"),
        dict(pushed_at=None),
    ]
    api.rows += [repo(200 + i, f"lab/excluded-{i}", **fields) for i, fields in enumerate(rejected)]
    actual = {
        i["full_name"]: i
        for i in collector.collect_infra(client, DAY)["items"]
        if not i["is_watchlist"]
    }
    assert set(actual) == {f"lab/{key}" for key in qualified} | {"lab/just-new", "lab/just-active"}
    for key, (_, category) in qualified.items():
        assert actual[f"lab/{key}"]["category"] == category
        assert actual[f"lab/{key}"]["license"] == "GPL-3.0-or-later"


@pytest.mark.parametrize(
    "unavailable,reason",
    [
        (404, "404"),
        (410, "410"),
        ({"archived": True}, "archived"),
        ({"private": True}, "private"),
        ({"license": {"spdx_id": "NOASSERTION"}}, "license"),
    ],
)
def test_missing_seed_warns_and_stale_search_hit_cannot_restore_it(github, unavailable, reason):
    api, client = github
    seed_id = 1429601047
    if isinstance(unavailable, int):
        api.overrides[f"/repositories/{seed_id}"] = httpx.Response(unavailable)
    else:
        api.watched[seed_id].update(unavailable)
    api.rows.append(repo(seed_id, SEEDS[seed_id]))
    result = collector.collect_infra(client, DAY)
    assert SEEDS[seed_id] not in [i["full_name"] for i in result["items"]]
    assert result["coverage"]["watchlist_available"] == 2
    assert result["coverage"]["watchlist_requested"] == 3
    assert any(SEEDS[seed_id] in note and reason in note for note in result["warnings"])
    assert result["coverage"]["qualified_matches"] == 3


@pytest.mark.parametrize("status", [401, 403, 429, 502])
def test_watchlist_auth_quota_and_transient_failures_raise(github, status):
    api, client = github
    api.overrides["/repositories/1385946301"] = httpx.Response(status)
    with pytest.raises(GitHubClientError, match="rate limit" if status == 429 else str(status)):
        collector.collect_infra(client, DAY)


@pytest.mark.parametrize(
    "path,response",
    [
        (
            "/search/repositories",
            httpx.Response(
                200, json={"total_count": 1, "items": [repo()], "incomplete_results": True}
            ),
        ),
        (
            "/search/repositories",
            httpx.Response(
                200, json={"total_count": 5, "items": [repo()], "incomplete_results": False}
            ),
        ),
        (
            "/search/repositories",
            httpx.Response(200, json={"total_count": 0, "items": [], "incomplete_results": False}),
        ),
        ("/search/repositories", httpx.Response(403, headers={"x-ratelimit-remaining": "0"})),
        ("/repositories/1385946301", httpx.ReadTimeout("connection interrupted")),
        ("/repositories/1385946301", httpx.Response(200, json=repo(404, "lab/wrong-identity"))),
        ("/repos/lab/tiny/releases", httpx.Response(503)),
        ("/repos/lab/tiny/releases", httpx.Response(200, json=[release("bad", None)])),
    ],
)
def test_incomplete_editions_are_never_returned_or_replaced_by_mock(github, path, response):
    api, client = github
    api.overrides[path] = response
    with pytest.raises(GitHubClientError):
        collector.collect_infra(client, DAY)


def test_mid_search_quota_error_does_not_publish_accumulated_items(github, monkeypatch):
    api, client = github
    request = client.request

    def fail_second_search(method, path, **kwargs):
        if path == "/search/repositories" and any(url.path == path for url in api.calls):
            raise GitHubClientError("GitHub API rate limit exhausted")
        return request(method, path, **kwargs)

    monkeypatch.setattr(client, "request", fail_second_search)
    with pytest.raises(GitHubClientError, match="rate limit"):
        collector.collect_infra(client, DAY)
    assert sum(url.path == "/search/repositories" for url in api.calls) == 1
    assert all(not url.path.endswith("/releases") for url in api.calls)


def test_release_disappears_between_requests(github):
    api, client = github
    api.overrides["/repos/JustVugg/colibri/releases"] = httpx.Response(410)
    result = collector.collect_infra(client, DAY)
    watched = next(i for i in result["items"] if i["full_name"] == "JustVugg/colibri")
    assert watched["release"] is None and watched["release_checked"] is False
    assert any("JustVugg/colibri" in note and "410" in note for note in result["warnings"])
    assert (
        result["coverage"]["release_checks_attempted"] == 4
        and result["coverage"]["release_checks_completed"] == 3
    )


def test_bounded_release_window_and_bounded_search_are_visible(github):
    api, client = github
    api.rows = [repo(100 + i, f"lab/project-{i}") for i in range(55)]
    api.releases["JustVugg/colibri"] = [release(str(i), "2026-11-19T00:00:00Z") for i in range(20)]
    result = collector.collect_infra(client, DAY)
    assert result["coverage"]["capped_queries"] == result["coverage"]["queries"]
    assert (
        result["coverage"]["qualified_discoveries"] == result["coverage"]["per_query_limit"] == 50
    )
    assert any("JustVugg/colibri" in note and "20 releases" in note for note in result["warnings"])


def test_mock_is_explicit_fictional_and_deterministic():
    result = collector.mock_infra(DAY)
    assert result == collector.mock_infra(DAY)
    assert result["source"] == "mock" and result["date"] == DAY.isoformat() and result["warnings"]
    assert all(i["license"] is None and i["full_name"].startswith("demo/") for i in result["items"])
    assert all("release_checked" in i for i in result["items"])
