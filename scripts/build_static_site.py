from __future__ import annotations

import argparse
import re
import shutil
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes_rankings import export_ranking, ranking_response
from app.core.config import get_settings
from app.core.dates import current_week_start, utc_now
from app.db.archive import restore_history, save_week_state
from app.db.models import Base
from app.db.session import SessionLocal, get_db, init_db
from app.editions import EditionArchive, write_json
from app.github.client import GitHubClient
from app.github.collector import run_weekly_collection
from app.infra.collector import collect_infra, mock_infra
from app.main import create_app

ATTR_RE = re.compile(r'(?P<attr>\b(?:href|src|action))="(?P<url>[^"<>]+)"')


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Collect a daily edition or render an existing archive for Pages."
    )
    parser.add_argument("--output", type=Path, default=Path("site"))
    parser.add_argument("--archive-dir", type=Path, default=settings.archive_dir)
    parser.add_argument("--state-dir", type=Path, default=settings.state_dir)
    parser.add_argument(
        "--collect", action="store_true", help="Collect real GitHub data before rendering."
    )
    parser.add_argument(
        "--mock", action="store_true", help="Preview with an isolated scratch database and archive."
    )
    parser.add_argument(
        "--week-start",
        type=date.fromisoformat,
        help="Week to preview (historical dates require --mock).",
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    observed = utc_now().date()
    if args.week_start and args.week_start != current_week_start(observed):
        if not args.mock or args.week_start.weekday() != 0:
            parser.error("historical week-start must be a Monday and is for mock previews only")
        observed = args.week_start
    if args.collect and not args.mock and not settings.github_token:
        parser.error("GITHUB_TOKEN is required for live collection (use --mock to preview)")

    with TemporaryDirectory() as scratch:
        archive_dir, state_dir = args.archive_dir, args.state_dir
        sessions = SessionLocal
        if args.mock:
            archive_dir = Path(scratch) / "public"
            state_dir = Path(scratch) / "state"
            engine = create_engine(f"sqlite:///{Path(scratch).as_posix()}/preview.db")
            sessions = sessionmaker(engine, expire_on_commit=False)
            Base.metadata.create_all(engine)
        else:
            engine = None
            init_db()
        try:
            with sessions() as db:
                restore_history(db, state_dir, archive_dir)
                if args.collect or args.mock:
                    archive = EditionArchive(archive_dir)
                    if archive.editions and observed.isoformat() < max(archive.editions):
                        raise ValueError(
                            "refusing to roll the live archive back to an earlier date"
                        )
                    generate_edition(db, archive, state_dir, observed, args.limit, args.mock)
            build_site(args.output, archive_dir, sessions)
        finally:
            if engine is not None:
                engine.dispose()
    print(f"Static site written to {args.output.resolve()}")
    return 0


def generate_edition(
    db, archive: EditionArchive, state_dir: Path, observed: date, limit: int | None, mock: bool
) -> None:
    settings = get_settings()
    # Collect the independent feed first, before intensive ranking enrichment.
    if mock:
        report = mock_infra(observed)
    else:
        client = GitHubClient(
            settings.github_token,
            settings.github_rest_endpoint,
            settings.http_timeout_seconds,
            settings.http_max_retries,
        )
        try:
            report = collect_infra(client, observed)
        finally:
            client.close()
    week = current_week_start(observed)
    run = run_weekly_collection(
        db,
        week_start=week,
        as_of=observed,
        trigger_source="daily-pages",
        force_mock=mock,
        candidate_limit=limit,
        generate_report=False,
    )
    if run.status not in {"succeeded", "partial_succeeded"}:
        raise RuntimeError(f"Ranking collection failed: {run.error_message}")
    ranking = ranking_response(db, week, settings.score_version, 10, None, None)
    ranking["collection_status"] = run.status

    reports = [
        export_ranking(week, fmt, 10, settings.score_version, db).body.decode()
        for fmt in ("markdown", "csv")
    ]
    save_week_state(db, state_dir, week)
    archive.publish(observed, ranking, report, *reports)
    print(
        f"Edition {observed}: {len(ranking['items'])} ranking entries, {len(report['items'])} inference projects"
    )


def build_site(output: Path, archive_dir: Path, sessions=SessionLocal) -> None:
    archive = EditionArchive(archive_dir)
    app = create_app(archive_dir=archive_dir, restore_on_startup=False)
    app.state.archive = archive

    def build_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = build_db
    # Construct the complete site before replacing the last good build.
    output = output.resolve()
    source = archive_dir.resolve()
    if (
        source.is_relative_to(output)
        or output.is_relative_to(source)
        or Path.cwd().is_relative_to(output)
    ):
        raise ValueError("site output must be separate from the archive and source tree root")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as staging, TestClient(app) as client:
        root = Path(staging) / "site"
        shutil.copytree("app/web/static", root / "static")
        (root / ".nojekyll").touch()
        pages = write_data_files(client, archive, root / "data")
        pages.update({"", "weeks", "compare", "methodology", "runs", "infra"})
        for path in sorted(pages):
            for prefix in ("", "zh/"):
                route = "/" + prefix + path
                response = client.get(route)
                response.raise_for_status()
                target = root / prefix / path / "index.html"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(rewrite_html(response.text, target, root), encoding="utf-8")
        if output.exists():
            # Do not clear an unrelated directory supplied as --output.
            if any(output.iterdir()) and not (output / ".nojekyll").is_file():
                raise ValueError(f"output is not an existing radar site: {output}")
            shutil.rmtree(output)
        root.rename(output)


def write_data_files(client: TestClient, archive: EditionArchive, folder: Path) -> set[str]:
    folder.mkdir(parents=True)
    routes = set()
    weeks_response = client.get("/api/v1/rankings/weeks")
    weeks_response.raise_for_status()
    week_index = weeks_response.json()
    weeks = sorted(archive.weeks.keys() | set(week_index["weeks"]), reverse=True)
    write_json(folder / "ranking-weeks.json", {**week_index, "weeks": weeks})
    dates = set(archive.editions)
    for key in dates:
        payload = archive.edition(key)
        write_json(folder / "editions" / f"{key}.json", payload)
        routes.add(f"weeks/{key}")
        routes.update("repos/" + item["repository"]["full_name"] for item in payload["items"])
    for key in weeks:
        src = archive.weeks.get(key)
        if src:
            shutil.copy2(src, folder / src.name)
        else:  # A locally collected SQL week which hasn't been published yet.
            response = client.get(f"/api/v1/rankings/{key}")
            response.raise_for_status()
            payload = response.json()
            write_json(folder / f"rankings-{key}.json", payload)
            write_json(
                folder / "editions" / f"{key}.json", {"date": key, "kind": "weekly", **payload}
            )
            routes.update("repos/" + i["repository"]["full_name"] for i in payload["items"])
            routes.add(f"weeks/{key}")
            dates.add(key)
        for ext, fmt in (("md", "markdown"), ("csv", "csv")):
            report_name = f"weekly-{key}.{ext}"
            report = archive.root / report_name
            if report.is_file():
                shutil.copy2(report, folder / report_name)
            else:
                response = client.get(f"/api/v1/rankings/{key}/export", params={"format": fmt})
                response.raise_for_status()
                (folder / report_name).write_bytes(response.content)
    write_json(folder / "editions.json", {"dates": sorted(dates, reverse=True)})
    if weeks:
        shutil.copy2(folder / f"rankings-{weeks[0]}.json", folder / "rankings-latest.json")
    for key, src in archive.infra.items():
        (folder / "infra").mkdir(exist_ok=True)
        shutil.copy2(src, folder / "infra" / f"{key}.json")
        routes.add(f"infra/{key}")
    write_json(folder / "infra-dates.json", {"dates": sorted(archive.infra, reverse=True)})
    if archive.infra:
        shutil.copy2(archive.infra[max(archive.infra)], folder / "infra" / "latest.json")
    return routes


def rewrite_html(html: str, target_path: Path, site_root: Path) -> str:
    relroot = "../" * len(target_path.parent.relative_to(site_root).parts)
    return ATTR_RE.sub(lambda m: f'{m["attr"]}="{rewrite_url(m["url"], relroot)}"', html)


def rewrite_url(url: str, relroot: str) -> str:
    parsed = urlsplit(url)
    if parsed.netloc and parsed.netloc != "testserver" or not parsed.path.startswith("/"):
        return url
    path = parsed.path.strip("/")
    if path.startswith("static/"):
        return relroot + path
    api_files = {
        "api/v1/rankings/latest": "rankings-latest.json",
        "api/v1/rankings/weeks": "ranking-weeks.json",
        "api/v1/editions": "editions.json",
        "api/v1/infra": "infra-dates.json",
    }
    if path in api_files:
        return relroot + "data/" + api_files[path]
    if path.startswith(("api/v1/infra/", "api/v1/editions/")):
        return relroot + "data/" + path.removeprefix("api/v1/") + ".json"
    if path.startswith("api/v1/rankings/"):
        key = path.split("/")[3]
        fmt = parse_qs(parsed.query).get("format", ["json"])[0]
        if fmt in {"markdown", "csv"}:
            return f"{relroot}data/weekly-{key}.{'md' if fmt == 'markdown' else 'csv'}"
        return f"{relroot}data/rankings-{key}.json"
    result = relroot + path + "/" if path else relroot or "./"
    return (
        result
        + ("?" + parsed.query if parsed.query else "")
        + ("#" + parsed.fragment if parsed.fragment else "")
    )


if __name__ == "__main__":
    raise SystemExit(main())
