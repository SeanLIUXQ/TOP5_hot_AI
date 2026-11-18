"""Collect a bounded daily sample of inference infrastructure from GitHub REST."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from app.core.dates import utc_now
from app.github.client import GitHubClient, GitHubClientError

# GitHub repository IDs survive renames and transfers. The names identify the original
# selections for maintainers. All published names, links and metrics come from the API.
_WATCHLIST = {
    1385946301: ("Niko1221/Strata", "runtime"),
    1429601047: ("wici-ai/inferssd", "offload"),
    1286080397: ("JustVugg/colibri", "offload"),
}
_RELEASE_CHECK_LIMIT = 15
_SEARCH_PAGE = 50
_RELEASE_PAGE = 20

# Each query gets one page. A creation window finds small, new projects independently
# of the push window used for established projects. No minimum star count for entrants.
_SEARCHES = (
    ("created", "inference", "stars"),
    ("created", '"on-device" OR WebGPU OR NPU', "stars"),
    ("created", 'quantization OR offload OR "edge inference"', "updated"),
    ("pushed", "topic:llm-inference", "updated"),
    (
        "pushed",
        '"on-device inference" OR "mobile inference" OR "edge inference" OR "CPU inference"',
        "updated",
    ),
    ("pushed", "WebGPU inference", "updated"),
    ("pushed", "MoE SSD", "updated"),
    ("pushed", "quantization inference", "stars"),
    ("pushed", '"model serving" OR "inference server" OR "inference serving"', "stars"),
)

# Conservative positive signal for code licensing. Preserve the actual SPDX string in
# the edition. A public repo, GitHub's "Other" or a source-available license is insufficient.
_OSS_LICENSES = frozenset(
    """
    0BSD AFL-3.0 AGPL-3.0 Apache-1.1 Apache-2.0 Artistic-2.0 BSD-2-Clause BSD-3-Clause
    BSL-1.0 CDDL-1.0 EPL-1.0 EPL-2.0 EUPL-1.1 EUPL-1.2 GPL-2.0 GPL-3.0 ISC
    LGPL-2.0 LGPL-2.1 LGPL-3.0 MIT MIT-0 MPL-1.1 MPL-2.0 MS-PL NCSA OFL-1.1
    PostgreSQL Python-2.0 UPL-1.0 Unlicense Zlib
""".split()
)

# Match implementation roles as well as model/inference context. A hardware name,
# the word "inference", or an LLM topic on its own is a weak discovery signal.
_MODEL = re.compile(
    r"\b(?:[lv]lms?|slms?|language models?|neural networks?|deep learning|machine learning|"
    r"transformers?|llama(?:\d+| cpp)?|onnx|tflite|executorch|ggml|gguf|moe|mixture of experts|"
    r"kv cache|text generation|speech recognition|computer vision)\b|大模型|神经网络"
)
_INFERENCE = re.compile(r"\b(?:inference|inferencing|model serving|text generation)\b|推理")
_EDGE = re.compile(
    r"\b(?:on device|device native|edge(?: ai| devices?| inference| deployment| runtime| computing)|"
    r"at the edge|consumer|laptops?|phones?|mobile|android|ios|iphone|"
    r"embedded|microcontrollers?|tinyml|raspberry pi|arm64|aarch64|apple silicon|snapdragon|"
    r"npu|webgpu|webnn|webassembly|wasm|metal|core ?ml|cpu only)\b|端侧|边缘设备"
)
_HARDWARE = re.compile(r"\b(?:cpus?|gpus?|cuda|rocm|vulkan|opencl|simd|avx\w*|neon)\b")
_LOCAL = re.compile(r"\b(?:local(?:ly|host)?|on your own (?:hardware|computer))\b")
_OFFLOAD = re.compile(
    r"\b(?:offload(?:ing|ed|s)?|nvme|ssds?|out of core|memory tiering|expert (?:streaming|paging))\b"
)
_QUANT = re.compile(
    r"\b(?:quantiz\w*|quantis\w*|quant toolkit|gptq|awq|int[248]|[1248] bit|low bit)\b|量化"
)
_ACCELERATION = re.compile(
    r"\b(?:accelerat\w*|kernels?|compilers?|delegates?|flash ?attention|speculative decoding|"
    r"continuous batching|operator fusion)\b|加速"
)
_SERVING = re.compile(
    r"\b(?:serving|model servers?|inference servers?|serve(?:s)? (?:llms?|models?))\b|推理服务"
)
_RUNTIME = re.compile(
    r"\b(?:engines?|runtimes?|backends?|runners?|frameworks?|toolkits?|tools?|librar(?:y|ies)|"
    r"sdks?|executors?|interpreters?|implementations?|converters?|quantizers?|compilers?|kernels?)\b|引擎|框架"
)
_RUN_MODELS = re.compile(
    r"\b(?:run(?:s|ning)?|execut(?:e|es|ing)|deploy(?:s|ing)?) (?:\w+ ){0,4}"
    r"(?:[lv]lms?|models?|llama|onnx)\b"
)
_CORE_RUNTIME = re.compile(
    r"\b(?:engines?|runtimes?|backends?|executors?|interpreters?|runners?)\b|引擎"
)
_TRAINING = re.compile(r"\b(?:fine ?tun(?:e|ing)|trainers?|training pipeline)\b")
_NON_INFRA = re.compile(
    r"\b(?:awesome|curated (?:list|collection)|reading list|(?:list|collection) of (?:papers|links)|"
    r"tutorials?|coursework|cheat sheet|cookbook|model cards?|model weights only|survey of|"
    r"chat(?:bot)? (?:ui|frontend)|(?:api|inference) client|client (?:sdk|for)|"
    r"causal inference|statistical inference|bayesian inference)\b"
)


def _classify(name: str, raw: dict[str, Any]) -> tuple[str | None, int]:
    topics = raw.get("topics") or []
    text = " ".join([name.split("/")[1], raw.get("description") or "", *topics]).casefold()
    text = re.sub(r"[._/-]+", " ", text).replace("cutting edge", "").replace("bleeding edge", "")
    inference = _INFERENCE.search(text)
    model = _MODEL.search(text)
    if (
        _NON_INFRA.search(text)
        or not (inference or model)
        or (_TRAINING.search(text) and not inference)
    ):
        return None, 0

    role = _RUNTIME.search(text)
    if _EDGE.search(text):
        focus = 3
    elif _HARDWARE.search(text):
        focus = 2
    else:
        focus = int(bool(_LOCAL.search(text)))
    if _OFFLOAD.search(text) and (role or inference or _RUN_MODELS.search(text)):
        return "offload", 3
    if _QUANT.search(text) and (role or _ACCELERATION.search(text)):
        return "quantization", max(1, focus)
    if _SERVING.search(text):
        return "serving", focus
    if _ACCELERATION.search(text):
        return "acceleration", focus
    if (role and inference) or (
        focus and model and (_CORE_RUNTIME.search(text) or _RUN_MODELS.search(text))
    ):
        return "runtime", focus
    return None, 0


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _timestamp(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("timezone missing")
        return parsed.astimezone(UTC)
    except (TypeError, ValueError) as exc:
        raise GitHubClientError(f"Invalid GitHub timestamp for {field}") from exc


@dataclass
class _Candidate:
    repo_id: int
    item: dict[str, Any]
    focus: int
    created: date
    pushed: datetime
    updated: datetime

    def order(self, new_since: date) -> tuple:
        name = self.item["full_name"]
        return (
            -self.focus,
            self.created < new_since,
            -self.pushed.timestamp(),
            -self.item["stars"],
            name.casefold(),
            self.repo_id,
            name,
        )


def _exclusion(raw: dict[str, Any]) -> str | None:
    if raw.get("private") or raw.get("visibility", "public") != "public":
        return "the repository is private or internal"
    for flag in ("archived", "disabled", "fork"):
        if raw.get(flag):
            return f"GitHub marked the repository as {flag}"
    if not {"license", "pushed_at", "created_at", "full_name", "id"} <= raw.keys():
        raise GitHubClientError("Incomplete GitHub repository metadata")
    license_info = raw["license"]
    spdx = license_info.get("spdx_id") if isinstance(license_info, dict) else None
    if (
        not isinstance(spdx, str)
        or spdx.removesuffix("-only").removesuffix("-or-later").removesuffix("+")
        not in _OSS_LICENSES
    ):
        return "GitHub did not identify a supported open-source code license"
    if raw["pushed_at"] is None:
        return "GitHub reports no push"
    return None


def _candidate(
    raw: dict[str, Any],
    as_of: date,
    new_since: date,
    active_since: date,
    watch_category: str | None = None,
) -> _Candidate | None:
    name = raw.get("full_name")
    repo_id = raw.get("id")
    if not isinstance(name, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", name, re.ASCII):
        raise GitHubClientError("Invalid GitHub repository name")
    if type(repo_id) is not int or repo_id <= 0:
        raise GitHubClientError(f"Invalid GitHub repository ID for {name}")
    created = _timestamp(raw.get("created_at"), f"{name} created_at")
    pushed = _timestamp(raw["pushed_at"], f"{name} pushed_at")
    if max(created.date(), pushed.date()) > as_of:
        return None
    if not watch_category and created.date() < new_since and pushed.date() < active_since:
        return None

    category, focus = _classify(name, raw)
    category = category or watch_category
    if not category:
        return None
    url, stars = raw.get("html_url"), raw.get("stargazers_count")
    if (
        not isinstance(url, str)
        or not url.startswith("https://")
        or type(stars) is not int
        or stars < 0
    ):
        raise GitHubClientError(f"Invalid GitHub repository URL or star count for {name}")
    item = {
        "full_name": name,
        "html_url": url,
        "description": raw.get("description"),
        "language": raw.get("language"),
        "license": raw["license"]["spdx_id"],
        "stars": stars,
        "created_at": _iso(created),
        "pushed_at": _iso(pushed),
        "category": category,
        "is_watchlist": watch_category is not None,
        "release_checked": False,
        "release": None,
    }
    updated = (
        _timestamp(raw["updated_at"], f"{name} updated_at") if raw.get("updated_at") else pushed
    )
    return _Candidate(repo_id, item, focus, created.date(), pushed, updated)


def _search_page(client: GitHubClient, query: str, sort: str) -> tuple[list, bool]:
    # search_repositories() omits incomplete_results and total_count. A partial API
    # response must interrupt publication even if its first hits look convincing.
    data = client.request(
        "GET",
        "/search/repositories",
        params={
            "q": query,
            "sort": sort,
            "order": "desc",
            "per_page": _SEARCH_PAGE,
            "page": 1,
        },
    )
    if not isinstance(data, dict) or data.get("incomplete_results") is not False:
        raise GitHubClientError(f"Incomplete GitHub repository search for {query}")
    items, total = data.get("items"), data.get("total_count")
    if (
        not isinstance(items, list)
        or type(total) is not int
        or total < 0
        or len(items) != min(_SEARCH_PAGE, total)
        or not all(isinstance(r, dict) for r in items)
    ):
        raise GitHubClientError(f"Truncated or invalid GitHub search page for {query}")
    return items, total > len(items)


def _deduplicate(
    watched: list[_Candidate], found: list[_Candidate], new_since: date
) -> list[_Candidate]:
    # Watchlist lookups are authoritative over potentially stale search aliases.
    seen_ids = set(_WATCHLIST)  # Also suppress stale search hits for unavailable seeds.
    seen_names = {c.item["full_name"].casefold() for c in watched}
    unique = []
    # A transferred repo and a reused name can appear together in overlapping search
    # indexes. Resolve the freshest observation per ID before accepting its name.
    for c in sorted(found, key=lambda c: (-c.updated.timestamp(), c.order(new_since))):
        if c.repo_id in seen_ids:
            continue
        seen_ids.add(c.repo_id)
        name = c.item["full_name"].casefold()
        if name not in seen_names:
            unique.append(c)
            seen_names.add(name)
    if not unique:
        raise GitHubClientError("No eligible inference discoveries beyond the watchlist")

    return sorted([*watched, *unique], key=lambda c: c.order(new_since))


def _unavailable_status(error: GitHubClientError) -> int | None:
    # The shared client currently wraps HTTPStatusError with its message only. Match
    # the status prefix, never an incidental 404 in a URL, a quota error or a timeout.
    status = getattr(error, "status_code", None)
    if status in {404, 410}:
        return status
    match = re.match(r"^(?:Client error ['\"]|HTTP )?(404|410)\b", str(error))
    return int(match[1]) if match else None


def _release(client: GitHubClient, name: str, as_of: date, warnings: list[str]) -> dict | None:
    # A successful [] means no published releases. The shared latest_release() helper
    # collapses API errors into an empty result and doesn't return a tag or public URL.
    rows = client.request("GET", f"/repos/{name}/releases", params={"per_page": _RELEASE_PAGE})
    if not isinstance(rows, list):
        raise GitHubClientError(f"Invalid GitHub releases response for {name}")
    eligible = []
    for row in rows:
        if not isinstance(row, dict):
            raise GitHubClientError(f"Invalid GitHub release for {name}")
        if row.get("draft"):
            continue
        published = _timestamp(row.get("published_at"), f"{name} release.published_at")
        if published.date() <= as_of:
            tag, url = row.get("tag_name"), row.get("html_url")
            if (
                not isinstance(tag, str)
                or not tag
                or not isinstance(url, str)
                or not url.startswith("https://")
            ):
                raise GitHubClientError(f"Incomplete GitHub release for {name}")
            eligible.append((published, tag, url))
    if not eligible:
        if len(rows) == _RELEASE_PAGE:
            warnings.append(
                f"{name}: no public release dated by {as_of} in the first {_RELEASE_PAGE} releases. Older tags were not checked."
            )
        return None
    published, tag, url = max(eligible)
    return {"tag": tag, "url": url, "published_at": _iso(published)}


def collect_infra(client: GitHubClient, as_of: date) -> dict[str, Any]:
    """Return one edition or raise GitHubClientError so the caller can retain its archive.

    Date windows and ordering use the supplied UTC day. Stars and other mutable fields
    are observations at collection time. Past editions should be read from storage.
    The client is owned by the caller. No cache, archive or mock data is written here.
    """
    new_since = as_of - timedelta(days=29)
    active_since = as_of - timedelta(days=13)
    watched = []
    warnings: list[str] = []
    for repo_id, (seed, category) in _WATCHLIST.items():
        try:
            raw = client.request("GET", f"/repositories/{repo_id}")
        except GitHubClientError as exc:
            status = _unavailable_status(exc)
            if status is None:
                raise
            warnings.append(f"Watchlist project {seed} was unavailable on GitHub (HTTP {status}).")
            continue
        if not isinstance(raw, dict) or raw.get("id") != repo_id:
            raise GitHubClientError(f"Cannot verify watched repository {seed} (ID {repo_id})")
        reason = _exclusion(raw)
        if reason is None:
            candidate = _candidate(raw, as_of, new_since, active_since, category)
            if candidate is not None:
                watched.append(candidate)
                continue
            reason = f"its creation or latest push falls after {as_of}"
        warnings.append(f"Watchlist project {seed} was omitted because {reason}.")

    found = []
    capped = 0
    scanned = 0
    for window, terms, sort in _SEARCHES:
        since = new_since if window == "created" else active_since
        query = f"{terms} in:name,description,topics is:public fork:false archived:false {window}:{since}..{as_of}"
        rows, limited = _search_page(client, query, sort)
        capped += limited
        scanned += len(rows)
        for raw in rows:
            if raw.get("id") in _WATCHLIST or _exclusion(raw):
                continue
            candidate = _candidate(raw, as_of, new_since, active_since)
            if candidate:
                found.append(candidate)

    matches = _deduplicate(watched, found, new_since)
    # Every qualified match stays in the edition. Spend release calls on the watchlist
    # first, then on the highest-ranked discoveries. HTTP 404/410 can also occur if a
    # project disappears between discovery and the release lookup.
    shortlist = sorted(matches, key=lambda c: not c.item["is_watchlist"])[:_RELEASE_CHECK_LIMIT]
    checks_completed = 0
    for c in shortlist:
        name = c.item["full_name"]
        try:
            c.item["release"] = _release(client, name, as_of, warnings)
        except GitHubClientError as exc:
            status = _unavailable_status(exc)
            if status is None:
                raise
            warnings.append(
                f"GitHub returned HTTP {status} for releases of {name}. Its release is unknown."
            )
            continue
        c.item["release_checked"] = True
        checks_completed += 1
    if not checks_completed:
        raise GitHubClientError("No release lookups succeeded for the inference edition")
    return {
        "date": as_of.isoformat(),
        "generated_at": _iso(utc_now()),
        "source": "github",
        "items": [c.item for c in matches],
        "warnings": warnings,
        "coverage": {
            "created_since": new_since.isoformat(),
            "pushed_since": active_since.isoformat(),
            "queries": len(_SEARCHES),
            "per_query_limit": _SEARCH_PAGE,
            "capped_queries": capped,
            "search_hits_scanned": scanned,
            "qualified_matches": len(matches),
            "qualified_discoveries": len(matches) - len(watched),
            "watchlist_requested": len(_WATCHLIST),
            "watchlist_available": len(watched),
            "release_check_limit": _RELEASE_CHECK_LIMIT,
            "release_checks_attempted": len(shortlist),
            "release_checks_completed": checks_completed,
            "releases_checked_per_item": _RELEASE_PAGE,
        },
    }


def mock_infra(as_of: date) -> dict[str, Any]:
    """Deterministic fictional data for an explicitly requested mock build."""
    stamp = datetime.combine(as_of, time.min, tzinfo=UTC)
    items = []
    for i, (slug, category, summary) in enumerate(
        (
            (
                "demo/ssd-experts",
                "offload",
                "Sample MoE expert streaming engine for consumer hardware.",
            ),
            (
                "demo/pocket-runtime",
                "runtime",
                "Sample on-device inference runtime for mobile NPUs.",
            ),
            ("demo/batch-server", "serving", "Sample inference server with continuous batching."),
        )
    ):
        url = f"https://example.invalid/{slug}"
        items.append(
            {
                "full_name": slug,
                "html_url": url,
                "description": summary,
                "language": None,
                "license": None,
                "stars": 0,
                "created_at": _iso(stamp - timedelta(days=i + 2)),
                "pushed_at": _iso(stamp),
                "category": category,
                "is_watchlist": i == 0,
                "release_checked": i < 2,
                "release": {
                    "tag": "demo-v1",
                    "url": f"{url}/releases/demo-v1",
                    "published_at": _iso(stamp),
                }
                if i == 0
                else None,
            }
        )
    return {
        "date": as_of.isoformat(),
        "generated_at": _iso(stamp),
        "items": items,
        "source": "mock",
        "warnings": ["Synthetic preview data for an explicit mock build."],
        "coverage": {
            "qualified_matches": len(items),
            "qualified_discoveries": len(items) - 1,
            "watchlist_requested": 1,
            "watchlist_available": 1,
            "release_check_limit": _RELEASE_CHECK_LIMIT,
            "release_checks_attempted": 2,
            "release_checks_completed": 2,
        },
    }
