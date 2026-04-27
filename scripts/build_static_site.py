from __future__ import annotations

import argparse
import re
import shutil
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.dates import current_week_start
from app.db.models import WeeklyRanking
from app.db.session import SessionLocal, init_db
from app.github.collector import run_weekly_collection
from app.main import app

ATTR_RE = re.compile(r'(?P<attr>href|src|action)="(?P<url>[^"]+)"')


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a GitHub Pages compatible static site from the FastAPI app."
    )
    parser.add_argument("--output", default="site", help="Static output directory.")
    parser.add_argument("--archive-dir", default=None, help="Persistent public data directory.")
    parser.add_argument("--collect", action="store_true", help="Run collection before building.")
    parser.add_argument("--mock", action="store_true", help="Force mock collection data.")
    parser.add_argument("--week-start", type=date.fromisoformat, default=current_week_start())
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        settings = get_settings()
        if args.collect or latest_week(db, settings.score_version) is None:
            run = run_weekly_collection(
                db,
                week_start=args.week_start,
                trigger_source="static-build",
                force_mock=args.mock,
                candidate_limit=args.limit,
                generate_report=True,
            )
            if run.status not in {"succeeded", "partial_succeeded"}:
                raise RuntimeError(f"collection failed: {run.error_message}")
        output = Path(args.output)
        archive_dir = Path(args.archive_dir) if args.archive_dir else None
        build_site(output, archive_dir)
        print(f"Static site written to {output.resolve()}")
        return 0
    finally:
        db.close()


def build_site(output: Path, archive_dir: Path | None = None) -> None:
    reset_dir(output)
    copy_static_assets(output)
    (output / ".nojekyll").write_text("", encoding="utf-8")
    with TestClient(app) as client:
        write_data_files(client, output, archive_dir)
        pages = page_plan(client)
        for route, target in pages:
            response = client.get(route)
            response.raise_for_status()
            target_path = output / target
            target_path.parent.mkdir(parents=True, exist_ok=True)
            html = rewrite_html(response.text, target_path, output)
            target_path.write_text(html, encoding="utf-8")


def page_plan(client: TestClient) -> list[tuple[str, Path]]:
    pages: list[tuple[str, Path]] = [
        ("/", Path("index.html")),
        ("/weeks", Path("weeks/index.html")),
        ("/compare", Path("compare/index.html")),
        ("/methodology", Path("methodology/index.html")),
        ("/runs", Path("runs/index.html")),
        ("/zh/", Path("zh/index.html")),
        ("/zh/weeks", Path("zh/weeks/index.html")),
        ("/zh/compare", Path("zh/compare/index.html")),
        ("/zh/methodology", Path("zh/methodology/index.html")),
        ("/zh/runs", Path("zh/runs/index.html")),
    ]
    latest = client.get("/api/v1/rankings/latest")
    if latest.status_code == 200:
        for item in latest.json()["items"]:
            repo = item["repository"]
            pages.extend(
                [
                    (
                        f"/repos/{repo['owner']}/{repo['name']}",
                        Path("repos") / repo["owner"] / repo["name"] / "index.html",
                    ),
                    (
                        f"/zh/repos/{repo['owner']}/{repo['name']}",
                        Path("zh") / "repos" / repo["owner"] / repo["name"] / "index.html",
                    ),
                ]
            )
    return pages


def write_data_files(client: TestClient, output: Path, archive_dir: Path | None) -> None:
    data_dir = output / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    if archive_dir and archive_dir.exists():
        for source in archive_dir.glob("*"):
            if is_public_data_file(source):
                shutil.copy2(source, data_dir / source.name)

    latest = client.get("/api/v1/rankings/latest")
    if latest.status_code != 200:
        return
    latest_payload = latest.text
    latest_json = latest.json()
    week_start = latest_json["week_start"]
    (data_dir / "rankings-latest.json").write_text(latest_payload, encoding="utf-8")
    (data_dir / f"rankings-{week_start}.json").write_text(latest_payload, encoding="utf-8")

    weeks = client.get("/api/v1/rankings/weeks")
    if weeks.status_code == 200:
        (data_dir / "ranking-weeks.json").write_text(weeks.text, encoding="utf-8")

    markdown = client.get(f"/api/v1/rankings/{week_start}/export?format=markdown")
    if markdown.status_code == 200:
        (data_dir / f"weekly-{week_start}.md").write_text(markdown.text, encoding="utf-8")
    csv_response = client.get(f"/api/v1/rankings/{week_start}/export?format=csv")
    if csv_response.status_code == 200:
        (data_dir / f"weekly-{week_start}.csv").write_text(csv_response.text, encoding="utf-8")

    if archive_dir:
        archive_dir.mkdir(parents=True, exist_ok=True)
        for source in data_dir.glob("*"):
            if is_public_data_file(source):
                shutil.copy2(source, archive_dir / source.name)


def rewrite_html(html: str, target_path: Path, site_root: Path) -> str:
    relroot = relative_root(target_path, site_root)

    def replace(match: re.Match[str]) -> str:
        attr = match.group("attr")
        url = match.group("url")
        return f'{attr}="{rewrite_url(url, relroot)}"'

    return ATTR_RE.sub(replace, html)


def rewrite_url(url: str, relroot: str) -> str:
    if url.startswith(("https://", "http://")) and not url.startswith("http://testserver/"):
        return url
    parsed = urlparse(url)
    path = parsed.path
    if parsed.netloc == "testserver":
        path = parsed.path
    elif not path.startswith("/"):
        return url
    clean_path = path.lstrip("/")
    query = parse_qs(parsed.query)

    if clean_path in {"", "index.html"}:
        return relroot or "./"
    if clean_path in {"zh", "zh/"}:
        return relroot + "zh/"
    if clean_path.startswith("static/"):
        return relroot + clean_path
    if clean_path == "api/v1/rankings/latest":
        return relroot + "data/rankings-latest.json"
    if clean_path == "api/v1/rankings/weeks":
        return relroot + "data/ranking-weeks.json"
    if clean_path.startswith("api/v1/rankings/"):
        parts = clean_path.split("/")
        if len(parts) >= 4:
            week = parts[3]
            if len(parts) >= 5 and parts[4] == "export":
                fmt = (query.get("format") or ["markdown"])[0]
                if fmt == "json":
                    return relroot + f"data/rankings-{week}.json"
                suffix = "csv" if fmt == "csv" else "md"
                return relroot + f"data/weekly-{week}.{suffix}"
            return relroot + f"data/rankings-{week}.json"
    if clean_path in {"weeks", "compare", "methodology", "runs"}:
        return relroot + clean_path + "/"
    if clean_path.startswith("repos/"):
        return relroot + clean_path.rstrip("/") + "/"
    if clean_path.startswith("zh/"):
        zh_path = clean_path.removeprefix("zh/").strip("/")
        if not zh_path:
            return relroot + "zh/"
        if zh_path in {"weeks", "compare", "methodology", "runs"}:
            return relroot + "zh/" + zh_path + "/"
        if zh_path.startswith("repos/"):
            return relroot + "zh/" + zh_path.rstrip("/") + "/"
    return relroot + clean_path


def relative_root(target_path: Path, site_root: Path) -> str:
    parent = target_path.parent
    try:
        relative_parent = parent.relative_to(site_root)
    except ValueError:
        relative_parent = parent
    if str(relative_parent) == ".":
        return ""
    return "../" * len(relative_parent.parts)


def copy_static_assets(output: Path) -> None:
    static_output = output / "static"
    static_output.mkdir(parents=True, exist_ok=True)
    for source in Path("app/web/static").glob("*"):
        if source.is_file():
            shutil.copy2(source, static_output / source.name)


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def is_public_data_file(path: Path) -> bool:
    return path.is_file() and path.suffix in {".json", ".md", ".csv"}


def latest_week(db: Session, score_version: str) -> date | None:
    return (
        db.query(func.max(WeeklyRanking.week_start))
        .filter(WeeklyRanking.score_version == score_version)
        .scalar()
    )


if __name__ == "__main__":
    raise SystemExit(main())
