"""Portable weekly SQL checkpoints and read-only import of published TOP10 history."""

from __future__ import annotations

import gzip
import json
import math
import tempfile
import zlib
from collections import Counter
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.core.dates import parse_week_start
from app.db.models import CollectionRun, RepoDailyCounter, Repository, RepoSnapshot, WeeklyRanking

# Explicit checkpoint projections. Local primary/foreign keys never enter the checkpoint.
# JSON columns are stored as JSON values under their names without the SQL suffix.
_FIELDS = {
    Repository: "github_id owner name full_name html_url description primary_language license_spdx topics_json ai_relevance_score source_query created_at pushed_at archived fork has_readme updated_at".split(),
    RepoSnapshot: "stars forks watchers open_issues stars_delta_7d stars_growth_rate_7d forks_delta_7d watchers_delta_7d default_branch pushed_at release_count latest_release_at contributor_count commit_count_7d pr_opened_7d pr_merged_7d issues_opened_7d issues_closed_7d data_quality_level data_quality_json collected_at".split(),
    RepoDailyCounter: "observed_on observed_at stars forks watchers provenance".split(),
    WeeklyRanking: "rank previous_rank rank_change is_new_entry weeks_on_chart score_version hot_score attention_score activity_score community_score freshness_score health_score maturity_confidence data_quality_level score_breakdown_json generated_at".split(),
    CollectionRun: "run_type trigger_source status score_version started_at finished_at candidate_count filtered_count ranked_count warning_count rate_limit_remaining error_message metadata_json".split(),
}
_FILTER_REASONS = {
    "fork",
    "archived",
    "below_min_stars",
    "inactive",
    "list_repository",
    "low_ai_relevance",
}
_SAFE_ERRORS = {
    "No candidate repository survived filtering.",
    "GitHub API rate limit exhausted",
    "GITHUB_TOKEN is required for live GitHub collection",
}


class _InvalidArchive(ValueError):
    """A structural error whose message is safe to include in a build log."""


def _safe_error(message: str | None) -> str | None:
    return (
        message
        if not message or message in _SAFE_ERRORS
        else "Collection failed (details omitted)."
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False
    )


def _run_metadata(metadata: dict) -> dict:
    # Do not serialize arbitrary diagnostic data, request headers or exception strings.
    safe = {
        k: metadata[k] for k in ("candidate_limit", "saved_count") if type(metadata.get(k)) is int
    }
    if type(metadata.get("use_mock")) is bool:
        safe["use_mock"] = metadata["use_mock"]
    if "as_of" in metadata:
        safe["as_of"] = date.fromisoformat(metadata["as_of"]).isoformat()
    if isinstance(metadata.get("filters"), dict):
        safe["filters"] = {
            k: v for k, v in metadata["filters"].items() if k in _FILTER_REASONS and type(v) is int
        }
    return safe


def _encode(row) -> dict:
    values = {}
    for field in _FIELDS[type(row)]:
        value = getattr(row, field)
        if isinstance(value, datetime):
            value = _utc(value).isoformat()
        elif isinstance(value, date):
            value = value.isoformat()
        elif value is not None and field.endswith("_json"):
            value = json.loads(value)
            if field == "metadata_json":
                value = _run_metadata(value)
        elif field == "error_message":
            value = _safe_error(value)
        values[field.removesuffix("_json")] = value
    return values


def _decode(model, row: dict, *refs: str) -> dict:
    fields = _FIELDS[model]
    keys = {field.removesuffix("_json") for field in fields}
    if not isinstance(row, dict) or row.keys() != keys.union(refs):
        raise _InvalidArchive(f"invalid {model.__tablename__} fields")
    values = {}
    for field in fields:
        value = row[field.removesuffix("_json")]
        column = model.__table__.c[field]
        kind = column.type.python_type
        if value is None:
            valid = column.nullable
        elif field.endswith("_json"):
            valid = isinstance(value, list if field == "topics_json" else dict)
            if valid:
                if field == "metadata_json":
                    value = _run_metadata(value)
                value = _json(value)
        elif kind is datetime:
            valid = isinstance(value, str)
            if valid:
                value = _utc(datetime.fromisoformat(value))
        elif kind is date:
            valid = isinstance(value, str)
            if valid:
                value = date.fromisoformat(value)
        elif kind is float:
            valid = type(value) in (float, int) and math.isfinite(value)
        else:
            valid = type(value) is kind
        if not valid:
            raise _InvalidArchive(f"invalid {model.__tablename__}.{field}")
        values[field] = _safe_error(value) if field == "error_message" else value
    return values


def _week_runs(db: Session, week: date) -> list[CollectionRun]:
    return list(
        db.scalars(
            select(CollectionRun)
            .where(CollectionRun.week_start == week)
            .order_by(
                CollectionRun.started_at,
                CollectionRun.run_type,
                CollectionRun.trigger_source,
                CollectionRun.score_version,
                CollectionRun.id,
            )
        )
    )


def _keyed_runs(runs: Iterable[CollectionRun]) -> Iterator[tuple[str, CollectionRun]]:
    # Older runners recorded timestamps to the second. Preserve genuinely distinct
    # same-second attempts by their occurrence within the otherwise identical key.
    seen = Counter()
    for run in runs:
        key = (
            run.week_start.isoformat(),
            _utc(run.started_at).isoformat(),
            run.run_type,
            run.trigger_source,
            run.score_version,
        )
        seen[key] += 1
        yield uuid5(NAMESPACE_URL, _json([*key, seen[key]])).hex, run


def save_week_state(db: Session, state_dir: Path, week_start: date) -> Path:
    """Atomically write one deterministic checkpoint, including unsuccessful attempts."""
    parse_week_start(week_start.isoformat())
    db.flush()
    runs = _week_runs(db, week_start)
    run_keys = {run.id: key for key, run in _keyed_runs(runs)}
    snapshots = db.scalars(select(RepoSnapshot).where(RepoSnapshot.week_start == week_start)).all()
    rankings = db.scalars(
        select(WeeklyRanking)
        .where(WeeklyRanking.week_start == week_start)
        .order_by(WeeklyRanking.score_version, WeeklyRanking.rank)
    ).all()
    counters = db.scalars(
        select(RepoDailyCounter).where(
            RepoDailyCounter.observed_on >= week_start,
            RepoDailyCounter.observed_on < week_start + timedelta(days=7),
        )
    ).all()
    if not (runs or snapshots or rankings or counters):
        raise ValueError(f"No SQL history to checkpoint for {week_start}")
    repo_ids = {row.repository_id for row in [*snapshots, *rankings, *counters]}
    repos = db.scalars(
        select(Repository).where(Repository.id.in_(repo_ids)).order_by(Repository.github_id)
    ).all()
    github_ids = {repo.id: repo.github_id for repo in repos}
    data = {
        "format_version": 2,
        "week_start": week_start.isoformat(),
        "runs": [dict(_encode(run), run_key=run_keys[run.id]) for run in runs],
        "repositories": [_encode(repo) for repo in repos],
        "snapshots": [
            dict(
                _encode(row),
                github_id=github_ids[row.repository_id],
                run_key=run_keys[row.collection_run_id]
                if row.collection_run_id is not None
                else None,
            )
            for row in sorted(snapshots, key=lambda s: github_ids[s.repository_id])
        ],
        "rankings": [
            dict(_encode(row), github_id=github_ids[row.repository_id]) for row in rankings
        ],
        "daily_counters": [
            dict(_encode(row), github_id=github_ids[row.repository_id])
            for row in sorted(counters, key=lambda c: (c.observed_on, github_ids[c.repository_id]))
        ],
    }
    content = _json(data).encode("utf-8")
    state_dir.mkdir(parents=True, exist_ok=True)
    destination = state_dir / f"week-{week_start}.json.gz"
    with tempfile.NamedTemporaryFile(
        dir=state_dir, prefix=destination.name + ".", delete=False
    ) as temp:
        temporary = Path(temp.name)
    try:
        with (
            temporary.open("wb") as stream,
            gzip.GzipFile(fileobj=stream, filename="", mode="wb", mtime=0) as compressed,
        ):
            compressed.write(content)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _legacy_data(document: dict, week: date) -> dict:
    """Map the published fields. Never infer run history or recompute old scores."""
    if document["week_start"] != week.isoformat() or not isinstance(document["items"], list):
        raise _InvalidArchive("invalid legacy week")
    data = {
        "format_version": 1,
        "week_start": week.isoformat(),
        "repositories": [],
        "snapshots": [],
        "rankings": [],
        "runs": [],
    }
    for item in document["items"]:
        repo = item["repository"]
        github_id = repo["github_id"]
        repo_row = {key: repo.get(key) for key in _FIELDS[Repository] if key != "topics_json"}
        # The SQL schema requires has_readme and release_count. Their storage
        # defaults are explicitly identified as unavailable in the provenance.
        repo_row.update(topics=repo.get("topics"), has_readme=False)
        data["repositories"].append(repo_row)
        snap = item.get("snapshot")
        if snap is not None:
            if snap["week_start"] != week.isoformat():
                raise _InvalidArchive("legacy snapshot week mismatch")
            snap_row = {
                key: snap.get(key) for key in _FIELDS[RepoSnapshot] if key != "data_quality_json"
            }
            for sql_name, published_name in (
                ("commit_count_7d", "commits_7d"),
                ("pr_opened_7d", "prs_opened_7d"),
                ("pr_merged_7d", "prs_merged_7d"),
            ):
                snap_row[sql_name] = snap.get(published_name)
            snap_row.update(
                github_id=github_id,
                run_key=None,
                pushed_at=repo.get("pushed_at"),
                release_count=0,
                data_quality_level="partial",
                data_quality={
                    "source": "legacy_public_top10",
                    "published_quality": snap.get("data_quality_level"),
                    "missing_fields": [
                        "collection_run_id",
                        "default_branch",
                        "release_count",
                        "has_readme",
                    ],
                },
            )
            data["snapshots"].append(snap_row)
        ranking = {
            key: item.get(key) for key in _FIELDS[WeeklyRanking] if key != "score_breakdown_json"
        }
        ranking.update(
            github_id=github_id,
            score_version=document["score_version"],
            generated_at=document["generated_at"],
            data_quality_level="partial",
            score_breakdown=item["score_breakdown"],
        )
        data["rankings"].append(ranking)
    return data


def _apply(db: Session, week: date, document: dict, occupied: bool) -> bool:
    """Validate even skipped weeks. Existing candidates/rank positions win as a set."""
    sections = {"runs", "repositories", "snapshots", "rankings"}
    if document.get("format_version") == 2:
        sections.add("daily_counters")
    if (
        document.keys() != sections | {"format_version", "week_start"}
        or type(document["format_version"]) is not int
        or document["format_version"] not in (1, 2)
        or document["week_start"] != week.isoformat()
        or any(not isinstance(document[k], list) for k in sections)
    ):
        raise _InvalidArchive("invalid checkpoint format, version or week")
    repos = {}
    for record in document["repositories"]:
        row = Repository(**_decode(Repository, record))
        if row.github_id <= 0 or row.github_id in repos:
            raise _InvalidArchive("invalid or duplicate GitHub ID")
        repos[row.github_id] = row
    runs = document["runs"]
    refs = ("run_key",) if document["format_version"] == 2 else ()
    incoming = dict(
        _keyed_runs(
            CollectionRun(week_start=week, **_decode(CollectionRun, r, *refs)) for r in runs
        )
    )
    if refs and any(record["run_key"] != key for record, key in zip(runs, incoming, strict=True)):
        raise _InvalidArchive("invalid run key")
    if not any(document[k] for k in sections - {"repositories"}):
        raise _InvalidArchive("empty checkpoint")
    snapshots = {}
    for record in document["snapshots"]:
        values = _decode(RepoSnapshot, record, "github_id", "run_key")
        gid, key = record["github_id"], record["run_key"]
        if (
            type(gid) is not int
            or gid not in repos
            or gid in snapshots
            or (key is not None and key not in incoming)
        ):
            raise _InvalidArchive("invalid or duplicate snapshot reference")
        snapshots[gid] = (key, values)
    rankings = {}
    positions = set()
    for record in document["rankings"]:
        values = _decode(WeeklyRanking, record, "github_id")
        gid = record["github_id"]
        key = (values["score_version"], gid)
        position = (values["score_version"], values["rank"])
        if (
            type(gid) is not int
            or gid not in repos
            or key in rankings
            or position in positions
            or values["rank"] < 1
        ):
            raise _InvalidArchive("invalid or duplicate ranking reference")
        rankings[key] = values
        positions.add(position)

    counters = {}
    for record in document.get("daily_counters", []):
        values = _decode(RepoDailyCounter, record, "github_id")
        gid, observed_on = record["github_id"], values["observed_on"]
        if (
            type(gid) is not int
            or gid not in repos
            or (gid, observed_on) in counters
            or not week <= observed_on < week + timedelta(days=7)
            or values["provenance"] not in {"mock", "github"}
            or (values["provenance"] == "github" and values["observed_at"].date() != observed_on)
            or min(values[k] for k in ("stars", "forks", "watchers")) < 0
        ):
            raise _InvalidArchive("invalid or duplicate daily counter")
        counters[gid, observed_on] = values

    runs = dict(_keyed_runs(_week_runs(db, week))) if incoming else {}
    for key, run in incoming.items():
        if key not in runs:
            db.add(run)
            runs[key] = run
    needed = {gid for gid, _ in counters} if occupied else repos.keys()
    existing = {}
    if needed:
        existing = {
            r.github_id: r
            for r in db.scalars(select(Repository).where(Repository.github_id.in_(needed)))
        }
        for gid in needed:
            if gid not in existing:
                db.add(repos[gid])
                existing[gid] = repos[gid]
    db.flush()
    if counters:
        stored = set(
            db.execute(
                select(RepoDailyCounter.repository_id, RepoDailyCounter.observed_on).where(
                    RepoDailyCounter.observed_on >= week,
                    RepoDailyCounter.observed_on < week + timedelta(days=7),
                )
            )
        )
        for (gid, day), values in counters.items():
            if (existing[gid].id, day) not in stored:
                db.add(RepoDailyCounter(repository_id=existing[gid].id, **values))
    if occupied or not (snapshots or rankings):
        db.flush()
        return False
    for gid, (key, values) in snapshots.items():
        db.add(
            RepoSnapshot(
                week_start=week,
                repository_id=existing[gid].id,
                collection_run_id=runs[key].id if key is not None else None,
                **values,
            )
        )
    for (_, gid), values in rankings.items():
        db.add(WeeklyRanking(week_start=week, repository_id=existing[gid].id, **values))
    db.flush()
    return True


def restore_history(db: Session, state_dir: Path, public_dir: Path) -> None:
    """Restore missing weeks in one transaction, keeping all existing SQL observations.

    Newest weeks get first choice of shared repository metadata on a cold DB.
    Checkpoints supersede public TOP10s for the same week. A run-only checkpoint
    can coexist with a legacy edition. No files or public editions are modified.
    """

    def files(directory: Path, pattern: str, prefix: str, suffix: str) -> dict[date, Path]:
        index = {}
        for path in directory.glob(pattern):
            try:
                week = parse_week_start(path.name.removeprefix(prefix).removesuffix(suffix))
            except ValueError:
                raise _InvalidArchive(f"Invalid weekly archive filename {path.name}") from None
            index[week] = path
        return index

    checkpoints = files(state_dir, "week-*.json.gz", "week-", ".json.gz")
    legacy = files(public_dir, "rankings-????-??-??.json", "rankings-", ".json")
    try:
        db.flush()
        occupied = set(
            db.scalars(select(RepoSnapshot.week_start).union(select(WeeklyRanking.week_start)))
        )
        for week in sorted(checkpoints.keys() | legacy.keys(), reverse=True):
            if week in checkpoints and _restore_file(
                db, week, checkpoints[week], occupied=week in occupied
            ):
                occupied.add(week)
            if week not in occupied and week in legacy:
                _restore_file(db, week, legacy[week], occupied=False)
        db.commit()
    except Exception:
        db.rollback()
        raise


def _restore_file(db: Session, week: date, path: Path, *, occupied: bool) -> bool:
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                data = json.load(stream)
        else:
            data = _legacy_data(json.loads(path.read_bytes()), week)
        return _apply(db, week, data, occupied)
    except _InvalidArchive as exc:
        raise ValueError(f"Invalid history archive {path.name}: {exc}") from None
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OverflowError,
        OSError,
        EOFError,
        zlib.error,
        IntegrityError,
        DataError,
    ) as exc:
        # Other errors may contain untrusted JSON, SQL parameters or URLs.
        raise ValueError(f"Invalid history archive {path.name} ({type(exc).__name__})") from None
