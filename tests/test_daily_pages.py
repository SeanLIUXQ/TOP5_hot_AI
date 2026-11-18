"""Exercise the clean-runner -> collection -> published archive -> static Pages path."""

import json
import shutil
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.archive import restore_history
from app.db.models import Base
from app.db.session import get_db
from app.editions import EditionArchive
from app.main import create_app
from scripts.build_static_site import build_site, generate_edition


class DocumentLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        self.links.extend(
            value for key, value in attrs if key in {"href", "src", "action"} and value
        )


def assert_links_resolve(site: Path) -> None:
    for page in site.rglob("*.html"):
        parser = DocumentLinks()
        parser.feed(page.read_text(encoding="utf-8"))
        for url in parser.links:
            ref = urlsplit(url)
            if ref.scheme or ref.netloc or not ref.path:
                continue
            assert not ref.path.startswith("/"), (page, url)
            target = (page.parent / unquote(ref.path)).resolve()
            assert target.is_relative_to(site)
            assert (target / "index.html" if target.is_dir() else target).is_file(), (page, url)


def session_dependency(sessions):
    def provide_session():
        with sessions() as db:
            yield db

    return provide_session


def test_archive_survives_two_daily_clean_builds_and_same_day_rerun(tmp_path):
    public = tmp_path / "public-data"
    public.mkdir()
    state = tmp_path / "state"
    # Actual v1 file: this edition's Monday label predates its capture timestamp by one day.
    source = Path("public-data/rankings-2026-04-27.json")
    shutil.copy2(source, public / source.name)
    before = json.loads(source.read_bytes())
    for number, day in enumerate((date(2026, 4, 29), date(2026, 4, 30), date(2026, 4, 30))):
        engine = create_engine(f"sqlite:///{tmp_path.as_posix()}/runner-{number}.db")
        sessions = sessionmaker(engine, expire_on_commit=False)
        Base.metadata.create_all(engine)
        try:
            with sessions() as db:
                restore_history(db, state, public)
                generate_edition(db, EditionArchive(public), state, day, 16, mock=True)
            if number < 2:
                continue
            site = tmp_path / "site"
            build_site(site, public, sessions)
            assert_links_resolve(site)
            ranking_dates = ["2026-04-30", "2026-04-29", "2026-04-27"]
            assert (
                json.loads((site / "data" / "editions.json").read_bytes())["dates"] == ranking_dates
            )
            assert (
                json.loads((site / "data" / "infra-dates.json").read_bytes())["dates"]
                == ranking_dates[:2]
            )
            assert json.loads((site / "data" / "ranking-weeks.json").read_bytes())["weeks"] == [
                ranking_dates[-1]
            ]
            old = json.loads((site / "data/editions/2026-04-27.json").read_bytes())
            assert old == {**before, "date": "2026-04-27", "kind": "weekly"}
            assert (
                json.loads((site / "data/rankings-latest.json").read_bytes())["date"]
                == ranking_dates[0]
            )
            for prefix in ("", "zh/"):
                for date_key in ranking_dates:
                    assert (site / f"{prefix}weeks/{date_key}/index.html").is_file()
                assert (site / f"{prefix}infra/2026-04-29/index.html").is_file()
                assert (site / f"{prefix}repos/voxel51/fiftyone/index.html").is_file()
            app = create_app(public, restore_on_startup=False)

            app.dependency_overrides[get_db] = session_dependency(sessions)
            with TestClient(app) as client:
                assert client.get("/api/v1/infra/latest").json()["date"] == ranking_dates[0]
                assert client.get("/api/v1/editions/2026-04-27").json() == old
                assert client.get("/zh/infra/2026-04-27").status_code == 404
                assert client.get("/weeks/2026-04-28").status_code == 404
                assert client.get("/zh/compare?metric=unknown").status_code == 400
        finally:
            engine.dispose()


def test_first_midweek_collection_does_not_invent_monday_editions(tmp_path):
    public = tmp_path / "public"
    for day, week in (("2026-05-06", "2026-05-04"), ("2026-05-12", "2026-05-11")):
        ranking = {"week_start": week, "score_version": "v1", "items": [{"rank": 1}]}
        report = {"date": day, "items": [{"full_name": "demo/runtime"}]}
        EditionArchive(public).publish(date.fromisoformat(day), ranking, report, "markdown", "csv")
    archive = EditionArchive(public)
    assert sorted(archive.editions) == ["2026-05-06", "2026-05-12"]
    assert sorted(archive.weeks) == ["2026-05-04", "2026-05-11"]
    assert archive.edition("2026-05-04") is None
    assert archive.infra_report()["date"] == "2026-05-12"
