import gzip
import json
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.json import dumps, loads
from app.db.archive import restore_history, save_week_state
from app.db.models import (
    Base,
    CollectionRun,
    RepoDailyCounter,
    Repository,
    RepoSnapshot,
    WeeklyRanking,
)
from app.github.collector import run_weekly_collection

WEEK = date(2026, 11, 16)
PUBLIC = Path(__file__).resolve().parents[1] / "public-data"


@pytest.fixture
def database():
    engines = []

    def new():
        engine = create_engine("sqlite://")
        engines.append(engine)
        Base.metadata.create_all(engine)
        return Session(engine, autoflush=False, expire_on_commit=False)

    yield new
    for engine in engines:
        engine.dispose()


def collect(db, week=WEEK, limit=12):
    run = run_weekly_collection(
        db, week, force_mock=True, candidate_limit=limit, generate_report=False
    )
    assert run.status in {"succeeded", "partial_succeeded"}, run.error_message
    return run


def original_weekly_file(week):
    # The first daily publication preserves the original weekly edition here
    # before replacing that week's rankings alias with the latest daily results.
    preserved = PUBLIC / "editions" / f"{week}.json"
    if week == WEEK and preserved.is_file():
        assert json.loads(preserved.read_bytes())["kind"] == "weekly"
        return preserved
    return PUBLIC / f"rankings-{week}.json"


def read_state(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def write_state(path, data):
    path.write_bytes(gzip.compress(json.dumps(data).encode()))


def test_cold_checkpoint_full_candidates_same_second_runs_and_existing_db(database, tmp_path):
    state, public = tmp_path / "state", tmp_path / "public"
    with database() as db:
        run = collect(db)
        failed = CollectionRun(
            week_start=WEEK,
            run_type=run.run_type,
            started_at=run.started_at,
            trigger_source=run.trigger_source,
            score_version=run.score_version,
            status="failed",
            metadata_json=dumps({"token": "credential-example", "saved_count": 0}),
            error_message="Traceback: Authorization: Bearer credential-example",
        )
        db.add(failed)
        # Make sure restoration preserves a candidate outside the published/scored rows.
        db.delete(db.query(WeeklyRanking).filter_by(rank=12).one())
        db.commit()
        path = save_week_state(db, state, WEEK)
        first_bytes = path.read_bytes()
        assert save_week_state(db, state, WEEK).read_bytes() == first_bytes
        document = read_state(path)
        assert document["format_version"] == 2
        assert len(document["snapshots"]) == len(document["daily_counters"]) == 12
        assert len(document["rankings"]) == 11
        assert b"credential-example" not in gzip.decompress(first_bytes)
        assert b"Traceback" not in gzip.decompress(first_bytes)
        sample = db.query(RepoSnapshot).first()
        gid, old_id = sample.repository.github_id, sample.repository_id
        published_at = db.query(WeeklyRanking).filter_by(rank=1).one().generated_at

    with database() as fresh:
        fresh.add(
            Repository(
                id=1000,
                github_id=98765432,
                owner="seed",
                name="seed",
                full_name="seed/seed",
                html_url="https://github.com/seed/seed",
            )
        )
        fresh.commit()
        restore_history(fresh, state, public)
        restore_history(fresh, state, public)
        repo = fresh.query(Repository).filter_by(github_id=gid).one()
        snap = fresh.query(RepoSnapshot).filter_by(repository_id=repo.id).one()
        rank = fresh.query(WeeklyRanking).filter_by(rank=1).one()
        assert repo.id != old_id
        assert snap.collection_run.status == "succeeded"
        assert rank.generated_at == published_at
        assert fresh.query(RepoSnapshot).count() == 12
        assert fresh.query(WeeklyRanking).count() == 11
        assert fresh.query(RepoDailyCounter).count() == 12
        assert fresh.query(CollectionRun).count() == 2
        restored_failure = fresh.query(CollectionRun).filter_by(status="failed").one()
        assert loads(restored_failure.metadata_json) == {"saved_count": 0}
        assert save_week_state(fresh, tmp_path / "roundtrip", WEEK).read_bytes() == first_bytes

        repo.description = "locally refreshed"
        rank.hot_score = 43
        snap.stars += 100
        current_stars = snap.stars
        counter = fresh.query(RepoDailyCounter).filter_by(repository_id=repo.id).one()
        counter.stars = 7
        fresh.commit()
        restore_history(fresh, state, public)
        fresh.expire_all()
        assert repo.description == "locally refreshed" and rank.hot_score == 43
        assert snap.stars == current_stars and counter.stars == 7


def test_cold_restore_of_original_public_series_only_imports_facts(database, tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    first_week = date(2026, 4, 27)
    originals = {}
    expected = {}
    for offset in range(30):
        week = first_week + timedelta(weeks=offset)
        original = original_weekly_file(week)
        copy = public / f"rankings-{week}.json"
        shutil.copyfile(original, copy)
        originals[copy] = original
        doc = json.loads(original.read_bytes())
        for item in doc["items"]:
            expected[doc["week_start"], item["repository"]["github_id"]] = item
    # The mutable index/alias carries no additional historical evidence.
    (public / "rankings-latest.json").write_text("unused")
    with database() as db:
        restore_history(db, tmp_path / "state", public)
        restore_history(db, tmp_path / "state", public)
        assert db.query(CollectionRun).count() == db.query(RepoDailyCounter).count() == 0
        assert (
            db.query(WeeklyRanking).count()
            == db.query(RepoSnapshot).count()
            == len(expected)
            == 300
        )
        for ranking in db.scalars(select(WeeklyRanking)):
            item = expected[ranking.week_start.isoformat(), ranking.repository.github_id]
            assert ranking.data_quality_level == "partial"
            assert (
                ranking.rank,
                ranking.hot_score,
                ranking.previous_rank,
                ranking.weeks_on_chart,
            ) == (item["rank"], item["hot_score"], item["previous_rank"], item["weeks_on_chart"])
            assert loads(ranking.score_breakdown_json) == item["score_breakdown"]
        for snap in db.scalars(select(RepoSnapshot)):
            item = expected[snap.week_start.isoformat(), snap.repository.github_id]["snapshot"]
            assert snap.collection_run_id is None and snap.data_quality_level == "partial"
            assert snap.stars == item["stars"] and snap.pr_merged_7d == item["prs_merged_7d"]
            assert snap.collected_at == datetime.fromisoformat(item["collected_at"]).replace(
                tzinfo=None
            )
            assert "release_count" in loads(snap.data_quality_json)["missing_fields"]
        assert all(
            copy.read_bytes() == original.read_bytes() for copy, original in originals.items()
        )


def test_run_only_checkpoint_and_legacy_same_week(database, tmp_path):
    with database() as db:
        db.add(
            CollectionRun(
                week_start=WEEK,
                started_at=datetime.now(UTC),
                score_version="v1.0.0",
                status="failed",
            )
        )
        db.commit()
        save_week_state(db, tmp_path, WEEK)
    shutil.copyfile(original_weekly_file(WEEK), tmp_path / f"rankings-{WEEK}.json")
    with database() as db:
        restore_history(db, tmp_path, tmp_path)
        assert db.query(WeeklyRanking).count() == 10
        assert db.query(CollectionRun).one().status == "failed"
        assert all(s.collection_run_id is None for s in db.scalars(select(RepoSnapshot)))


def test_v1_checkpoint_wins_over_legacy_and_does_not_invent_daily_counters(database, tmp_path):
    with database() as db:
        collect(db, limit=2)
        path = save_week_state(db, tmp_path, WEEK)
        data = read_state(path)
        data.pop("daily_counters")
        for run in data["runs"]:
            run.pop("run_key")
        data["format_version"] = 1
        write_state(path, data)
    shutil.copyfile(original_weekly_file(WEEK), tmp_path / f"rankings-{WEEK}.json")
    with database() as db:
        restore_history(db, tmp_path, tmp_path)
        assert db.query(WeeklyRanking).count() == db.query(RepoSnapshot).count() == 2
        assert db.query(CollectionRun).one().status == "partial_succeeded"
        assert db.query(RepoDailyCounter).count() == 0


@pytest.mark.parametrize(
    "damage", ["gzip", "version", "rank", "run_ref", "daily_ref", "missing_field"]
)
def test_corrupted_older_checkpoint_rolls_back_entire_restore(database, tmp_path, damage):
    with database() as db:
        collect(db, limit=2)
        path = save_week_state(db, tmp_path, WEEK)
        later = WEEK + timedelta(days=7)
        collect(db, later, limit=1)
        save_week_state(db, tmp_path, later)
        data = read_state(path)
        if damage == "gzip":
            path.write_bytes(path.read_bytes()[:-4])
        else:
            if damage == "version":
                data["format_version"] = 999
            elif damage == "rank":
                data["rankings"][1]["rank"] = data["rankings"][0]["rank"]
            elif damage == "run_ref":
                data["snapshots"][0]["run_key"] = "unknown"
            elif damage == "daily_ref":
                data["daily_counters"][0]["github_id"] = 99
            else:
                del data["snapshots"][0]["forks"]
            write_state(path, data)
    with database() as db:
        with pytest.raises(ValueError, match=path.name):
            restore_history(db, tmp_path, tmp_path / "public")
        assert (
            db.query(CollectionRun).count()
            == db.query(Repository).count()
            == db.query(RepoDailyCounter).count()
            == 0
        )
