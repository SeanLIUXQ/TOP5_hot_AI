from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.api.routes_rankings import ranking_response, ranking_weeks
from app.core.config import get_settings
from app.db.session import get_db
from app.editions import EditionArchive

router = APIRouter(prefix="/api/v1", tags=["editions"])


def get_archive(request: Request) -> EditionArchive:
    # Static builds pin one catalog per render. A running API also sees editions
    # written by a separate collection process without an application restart.
    pinned = getattr(request.app.state, "archive", None)
    return pinned or EditionArchive(Path(request.app.state.archive_dir))


@router.get("/editions")
def edition_dates(
    archive: EditionArchive = Depends(get_archive), db: Session = Depends(get_db)
) -> dict:
    sql_only = set(ranking_weeks(get_settings().score_version, db)["weeks"]) - archive.weeks.keys()
    return {"dates": sorted(archive.editions.keys() | sql_only, reverse=True)}


@router.get("/editions/{day}")
def edition(
    day: date, archive: EditionArchive = Depends(get_archive), db: Session = Depends(get_db)
) -> dict:
    data = archive.edition(day.isoformat())
    if data is None:
        if day.isoformat() in archive.weeks:
            raise api_error(404, "edition_not_found", "No ranking edition for this date.")
        return {
            "date": day.isoformat(),
            "kind": "weekly",
            **ranking_response(db, day, get_settings().score_version, 10, None, None),
        }
    return data


@router.get("/infra")
def infra_dates(archive: EditionArchive = Depends(get_archive)) -> dict:
    return {"dates": sorted(archive.infra, reverse=True)}


@router.get("/infra/{day}")
@router.get("/infra/latest")
def infra_report(day: date | None = None, archive: EditionArchive = Depends(get_archive)) -> dict:
    data = archive.infra_report(day.isoformat() if day else None)
    if data is None:
        raise api_error(404, "infra_not_found", "No AI Infra edition for this date.")
    return data
