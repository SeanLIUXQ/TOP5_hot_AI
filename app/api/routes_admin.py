from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.api.serializers import collection_run_item
from app.core.config import get_settings
from app.db.models import CollectionRun
from app.db.session import get_db
from app.github.collector import run_weekly_collection

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.post("/collect")
def collect(
    week_start: date | None = None,
    mock: bool = Query(default=False),
    x_admin_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    require_admin(x_admin_token)
    run = run_weekly_collection(
        db,
        week_start=week_start,
        trigger_source="api",
        force_mock=mock,
        generate_report=True,
    )
    return {"run": collection_run_item(run)}


@router.get("/runs")
def runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    x_admin_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    require_admin(x_admin_token)
    query = db.query(CollectionRun).order_by(CollectionRun.started_at.desc())
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"page": page, "page_size": page_size, "total": total, "items": [collection_run_item(row) for row in rows]}


@router.get("/runs/{run_id}")
def run_detail(
    run_id: int,
    x_admin_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    require_admin(x_admin_token)
    run = db.query(CollectionRun).filter(CollectionRun.id == run_id).one_or_none()
    if run is None:
        raise api_error(404, "run_not_found", "Collection run was not found.", {"run_id": run_id})
    return {"run": collection_run_item(run)}


def require_admin(token: str | None) -> None:
    settings = get_settings()
    if not settings.admin_token:
        raise api_error(403, "admin_disabled", "Admin routes are disabled.")
    if token != settings.admin_token:
        raise api_error(401, "admin_token_required", "A valid X-Admin-Token header is required.")

