from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.api.serializers import ranking_item
from app.core.config import get_settings
from app.core.json import dumps, loads
from app.db.models import CollectionRun, Repository, RepoSnapshot, WeeklyRanking
from app.db.session import get_db

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory="app/web/templates")

COMPONENT_KEYS = [
    "attention_score",
    "activity_score",
    "community_score",
    "freshness_score",
    "health_score",
    "maturity_confidence",
]

TRANSLATIONS = {
    "en": {
        "meta_description": "Track the hottest open-source AI projects on GitHub every week.",
        "main_navigation": "Main navigation",
        "brand_tagline": "Weekly open-source signal",
        "latest_ranking": "Latest Ranking",
        "history": "History",
        "compare": "Compare",
        "methodology": "Methodology",
        "runs": "Runs",
        "not_collected": "Not collected yet",
        "status_no_data": "no data",
        "week_selector": "Select week",
        "language_toggle": "中文",
        "latest_title": "Latest AI Open-Source Projects TOP10",
        "latest_page_title": "Latest Ranking",
        "empty_ranking_title": "No ranking has been generated yet",
        "empty_ranking_body": "Run the collection script to show the latest TOP10, score breakdown, and data quality.",
        "week": "Week",
        "items": "Items",
        "run": "Run",
        "updated": "Updated",
        "weekly_top10": "Weekly TOP10",
        "ranking_intro": "Ranked by attention growth, development activity, community spread, freshness, and engineering health.",
        "language": "Language",
        "all_languages": "All languages",
        "export": "Export",
        "selected_repository": "Selected repository",
        "hot_score": "hot score",
        "hot_score_title": "Hot score",
        "details": "Details",
        "rank": "Rank",
        "repository": "Repository",
        "stars_7d": "Stars +7d",
        "activity": "Activity",
        "release": "Release",
        "score": "Score",
        "change": "Change",
        "no_description": "No description",
        "commits": "commits",
        "commits_title": "Commits",
        "since_release": "since release",
        "unknown": "unknown",
        "unknown_language": "Unknown language",
        "day_short": "d",
        "new": "new",
        "history_title": "Historical Rankings",
        "history_page_title": "Historical Rankings",
        "history_intro": "Weekly snapshots make it easy to review new entries, rank changes, and long-running projects.",
        "json_export": "JSON Export",
        "empty_history_title": "No historical rankings yet",
        "no_week_selected": "No week selected",
        "repo_not_found": "Repository not found",
        "repo_not_found_body": "This repository is not available in the current database.",
        "repo_missing_title": "Repository does not exist",
        "score_breakdown": "Score breakdown",
        "latest_snapshot": "Latest snapshot",
        "stars": "Stars",
        "forks": "Forks",
        "pr_merged": "PR merged",
        "data": "Data",
        "trend_title": "12-week trend",
        "star_delta": "Star delta",
        "compare_title": "Repository Compare",
        "compare_heading": "Compare repositories",
        "compare_intro": "Compare up to 5 repositories by hot score, growth, and activity trend.",
        "compare_button": "Compare",
        "compare_placeholder": "owner/repo,owner/repo",
        "empty_compare_title": "No repositories to compare yet",
        "stars_delta_7d": "Stars +7d",
        "methodology_title": "Methodology",
        "candidate_repositories": "Candidate repositories",
        "candidate_body": "AI repositories are discovered from GitHub topics, description keywords, and known historical projects. Forks, archived repositories, inactive repositories, and list-only repositories are filtered.",
        "overall_score": "Overall score",
        "objectivity": "Objectivity",
        "objectivity_body": "Each component keeps raw metrics, normalized scores, warnings, and estimated fields. Score versions are saved with rankings so future tuning does not overwrite historical meaning.",
        "data_quality": "Data quality",
        "data_quality_body": "Collection failures, missing releases, incomplete activity metrics, and missing licenses are shown in rankings or repository detail pages.",
        "runs_title": "Collection Runs",
        "runs_intro": "Track each collection, filtering, scoring, and report generation run.",
        "status": "Status",
        "candidates": "Candidates",
        "filtered": "Filtered",
        "ranked": "Ranked",
        "started": "Started",
        "error": "Error",
        "empty_runs_title": "No collection runs yet",
        "attention_score": "attention score",
        "activity_score": "activity score",
        "community_score": "community score",
        "freshness_score": "freshness score",
        "health_score": "health score",
        "maturity_confidence": "maturity confidence",
        "ai_relevance_borderline": "AI relevance borderline",
        "high_growth_low_activity": "high growth, low activity",
        "latest_release_older_than_90_days": "latest release older than 90 days",
        "license_missing": "license missing",
        "release_unknown": "release unknown",
        "activity_metrics_incomplete": "activity metrics incomplete",
        "succeeded": "succeeded",
        "partial_succeeded": "partial succeeded",
        "failed": "failed",
        "running": "running",
    },
    "zh": {
        "meta_description": "每周追踪 GitHub 热门 AI 开源项目。",
        "main_navigation": "主导航",
        "brand_tagline": "每周开源趋势信号",
        "latest_ranking": "最新榜单",
        "history": "历史榜单",
        "compare": "项目对比",
        "methodology": "评分方法",
        "runs": "采集运行",
        "not_collected": "尚未采集",
        "status_no_data": "暂无数据",
        "week_selector": "选择周榜",
        "language_toggle": "English",
        "latest_title": "最新 AI 开源项目 TOP10",
        "latest_page_title": "最新榜单",
        "empty_ranking_title": "还没有生成榜单",
        "empty_ranking_body": "运行采集脚本后，这里会显示最新一周的 TOP10、分项得分和数据质量。",
        "week": "周次",
        "items": "项目数",
        "run": "运行状态",
        "updated": "更新时间",
        "weekly_top10": "每周 TOP10",
        "ranking_intro": "按关注增长、开发活跃度、社区扩散、新鲜度和工程健康度综合排序。",
        "language": "编程语言",
        "all_languages": "全部语言",
        "export": "导出",
        "selected_repository": "选中项目",
        "hot_score": "热度分",
        "hot_score_title": "热度分",
        "details": "详情",
        "rank": "排名",
        "repository": "项目",
        "stars_7d": "Stars 7 天增量",
        "activity": "活跃度",
        "release": "发布",
        "score": "分数",
        "change": "变化",
        "no_description": "暂无简介",
        "commits": "次提交",
        "commits_title": "提交",
        "since_release": "距发布",
        "unknown": "未知",
        "unknown_language": "未知语言",
        "day_short": "天",
        "new": "新上榜",
        "history_title": "历史周榜",
        "history_page_title": "历史榜单",
        "history_intro": "保留每周快照，方便回看新上榜、排名变化和连续上榜项目。",
        "json_export": "导出 JSON",
        "empty_history_title": "暂无历史榜单",
        "no_week_selected": "未选择周榜",
        "repo_not_found": "项目未找到",
        "repo_not_found_body": "当前数据库里没有这个仓库。",
        "repo_missing_title": "项目不存在",
        "score_breakdown": "分数构成",
        "latest_snapshot": "最新快照",
        "stars": "Stars",
        "forks": "Forks",
        "pr_merged": "已合并 PR",
        "data": "数据质量",
        "trend_title": "12 周趋势",
        "star_delta": "Star 增量",
        "compare_title": "项目对比",
        "compare_heading": "对比项目",
        "compare_intro": "最多比较 5 个项目的热度分、增长和活跃趋势。",
        "compare_button": "对比",
        "compare_placeholder": "owner/repo,owner/repo",
        "empty_compare_title": "暂无可对比项目",
        "stars_delta_7d": "Stars 7 天增量",
        "methodology_title": "评分方法",
        "candidate_repositories": "候选仓库",
        "candidate_body": "通过 GitHub topic、描述关键词和历史入库项目发现 AI 相关仓库，并过滤 fork、归档、长期无维护和纯列表类仓库。",
        "overall_score": "综合分数",
        "objectivity": "客观性",
        "objectivity_body": "每个分项保留原始指标、归一化得分、告警和估算字段。评分版本写入榜单，后续调参不会覆盖历史口径。",
        "data_quality": "数据质量",
        "data_quality_body": "采集失败、缺失 release、活动指标不完整、许可证缺失等情况都会在项目详情或榜单中显示。",
        "runs_title": "采集运行",
        "runs_intro": "记录每次采集、过滤、评分和报告生成状态。",
        "status": "状态",
        "candidates": "候选数",
        "filtered": "过滤数",
        "ranked": "排名数",
        "started": "开始时间",
        "error": "错误",
        "empty_runs_title": "还没有采集记录",
        "attention_score": "关注得分",
        "activity_score": "活跃得分",
        "community_score": "社区得分",
        "freshness_score": "新鲜度得分",
        "health_score": "健康度得分",
        "maturity_confidence": "成熟度置信",
        "ai_relevance_borderline": "AI 相关性边界",
        "high_growth_low_activity": "高增长但低活跃",
        "latest_release_older_than_90_days": "最新 release 超过 90 天",
        "license_missing": "许可证缺失",
        "release_unknown": "release 未知",
        "activity_metrics_incomplete": "活跃指标不完整",
        "succeeded": "成功",
        "partial_succeeded": "部分成功",
        "failed": "失败",
        "running": "运行中",
    },
}


def compact_number(value: int | float | None) -> str:
    if value is None:
        return "-"
    number = float(value)
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if abs(number) >= 1_000:
        return f"{number / 1_000:.1f}k"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"


templates.env.filters["compact_number"] = compact_number
templates.env.filters["pct"] = pct


def language_from_request(request: Request) -> str:
    return "zh" if request.url.path == "/zh" or request.url.path.startswith("/zh/") else "en"


def page_href_factory(lang: str):
    prefix = "/zh" if lang == "zh" else ""

    def page_href(path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        if path == "/":
            return "/zh/" if lang == "zh" else "/"
        return f"{prefix}{path}"

    return page_href


def alternate_href_for_request(request: Request) -> str:
    path = request.url.path
    if path == "/zh":
        target = "/"
    elif path.startswith("/zh/"):
        target = path.removeprefix("/zh") or "/"
    elif path == "/":
        target = "/zh/"
    else:
        target = f"/zh{path}"
    return f"{target}?{request.url.query}" if request.url.query else target


def localized_label(lang: str, key: str) -> str:
    labels = TRANSLATIONS.get(lang, TRANSLATIONS["en"])
    return labels.get(key) or TRANSLATIONS["en"].get(key) or key.replace("_", " ")


def status_label(lang: str, status: str | None) -> str:
    if not status:
        return localized_label(lang, "unknown")
    return localized_label(lang, status)


@router.get("/", response_class=HTMLResponse)
@router.get("/zh/", response_class=HTMLResponse)
def home(
    request: Request,
    language: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    settings = get_settings()
    week = latest_week(db, settings.score_version)
    context = base_context(request, db, "home")
    if week is None:
        context.update({"items": [], "selected_week": None, "languages": [], "ranking_items_json": "[]"})
        return templates.TemplateResponse(request, "index.html", context)
    items, snapshots = ranking_items_for_week(db, week, settings.score_version, language=language, limit=10)
    languages = available_languages(db, week, settings.score_version)
    context.update(
        {
            "items": items,
            "snapshots": snapshots,
            "selected_week": week,
            "selected_language": language,
            "languages": languages,
            "ranking_items_json": dumps(items),
            "score_version": settings.score_version,
        }
    )
    return templates.TemplateResponse(request, "index.html", context)


@router.get("/weeks", response_class=HTMLResponse)
@router.get("/zh/weeks", response_class=HTMLResponse)
def weeks(
    request: Request,
    week_start: date | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    settings = get_settings()
    week = week_start or latest_week(db, settings.score_version)
    context = base_context(request, db, "weeks")
    items: list[dict] = []
    if week is not None:
        items, _ = ranking_items_for_week(db, week, settings.score_version, limit=10)
    context.update({"items": items, "selected_week": week, "ranking_items_json": dumps(items)})
    return templates.TemplateResponse(request, "weeks.html", context)


@router.get("/repos/{owner}/{repo}", response_class=HTMLResponse)
@router.get("/zh/repos/{owner}/{repo}", response_class=HTMLResponse)
def repo_detail(request: Request, owner: str, repo: str, db: Session = Depends(get_db)) -> HTMLResponse:
    repository = (
        db.query(Repository)
        .filter(Repository.owner == owner)
        .filter(Repository.name == repo)
        .one_or_none()
    )
    context = base_context(request, db, "repos")
    if repository is None:
        context.update({"repository": None})
        return templates.TemplateResponse(request, "repo_detail.html", context, status_code=404)

    snapshots = (
        db.query(RepoSnapshot)
        .filter(RepoSnapshot.repository_id == repository.id)
        .order_by(RepoSnapshot.week_start.asc())
        .all()
    )
    rankings = (
        db.query(WeeklyRanking)
        .filter(WeeklyRanking.repository_id == repository.id)
        .filter(WeeklyRanking.score_version == get_settings().score_version)
        .order_by(WeeklyRanking.week_start.asc())
        .all()
    )
    latest_snapshot = snapshots[-1] if snapshots else None
    latest_ranking = rankings[-1] if rankings else None
    score_breakdown = loads(latest_ranking.score_breakdown_json, {}) if latest_ranking else {}
    chart_data = {
        "stars": [{"week_start": s.week_start.isoformat(), "value": s.stars} for s in snapshots[-12:]],
        "stars_delta": [{"week_start": s.week_start.isoformat(), "value": s.stars_delta_7d} for s in snapshots[-12:]],
        "hot_score": [{"week_start": r.week_start.isoformat(), "value": r.hot_score} for r in rankings[-12:]],
    }
    context.update(
        {
            "repository": repository,
            "topics": loads(repository.topics_json, []),
            "latest_snapshot": latest_snapshot,
            "latest_ranking": latest_ranking,
            "score_breakdown": score_breakdown,
            "chart_data_json": dumps(chart_data),
        }
    )
    return templates.TemplateResponse(request, "repo_detail.html", context)


@router.get("/compare", response_class=HTMLResponse)
@router.get("/zh/compare", response_class=HTMLResponse)
def compare(
    request: Request,
    repos: str | None = Query(default=None),
    metric: str = Query(default="hot_score"),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    context = base_context(request, db, "compare")
    selected_names = [item.strip() for item in (repos or "").split(",") if item.strip()]
    if not selected_names:
        selected_names = default_compare_repos(db)
    items = []
    for full_name in selected_names[:5]:
        if "/" not in full_name:
            continue
        owner, name = full_name.split("/", 1)
        repository = (
            db.query(Repository)
            .filter(Repository.owner == owner)
            .filter(Repository.name == name)
            .one_or_none()
        )
        if repository is None:
            continue
        points = compare_points(db, repository.id, metric)
        items.append({"full_name": repository.full_name, "language": repository.primary_language, "points": points})
    context.update(
        {
            "selected_repos": ",".join(selected_names),
            "metric": metric,
            "compare_items": items,
            "compare_json": dumps(items),
        }
    )
    return templates.TemplateResponse(request, "compare.html", context)


@router.get("/methodology", response_class=HTMLResponse)
@router.get("/zh/methodology", response_class=HTMLResponse)
def methodology(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse(request, "methodology.html", base_context(request, db, "methodology"))


@router.get("/runs", response_class=HTMLResponse)
@router.get("/zh/runs", response_class=HTMLResponse)
def runs(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    context = base_context(request, db, "runs")
    rows = db.query(CollectionRun).order_by(CollectionRun.started_at.desc()).limit(30).all()
    context.update({"runs": rows})
    return templates.TemplateResponse(request, "runs.html", context)


def base_context(request: Request, db: Session, active_page: str) -> dict:
    settings = get_settings()
    lang = language_from_request(request)

    def t(key: str) -> str:
        return localized_label(lang, key)

    def component_label(key: str) -> str:
        return localized_label(lang, key)

    def warning_label(key: str) -> str:
        return localized_label(lang, key)

    def run_status_label(value: str | None) -> str:
        return status_label(lang, value)

    weeks = (
        db.query(WeeklyRanking.week_start)
        .filter(WeeklyRanking.score_version == settings.score_version)
        .group_by(WeeklyRanking.week_start)
        .order_by(WeeklyRanking.week_start.desc())
        .all()
    )
    latest_run = db.query(CollectionRun).order_by(CollectionRun.started_at.desc()).first()
    component_labels = {key: localized_label(lang, key) for key in COMPONENT_KEYS}
    return {
        "request": request,
        "app_name": settings.app_name,
        "score_version": settings.score_version,
        "weeks": [row[0] for row in weeks],
        "latest_run": latest_run,
        "lang": lang,
        "html_lang": "zh-CN" if lang == "zh" else "en",
        "t": t,
        "page_href": page_href_factory(lang),
        "alternate_href": alternate_href_for_request(request),
        "active_page": active_page,
        "component_label": component_label,
        "warning_label": warning_label,
        "status_label": run_status_label,
        "component_labels_json": dumps(component_labels),
        "ui_text_json": dumps({"noDescription": t("no_description"), "components": component_labels}),
    }


def latest_week(db: Session, score_version: str) -> date | None:
    return (
        db.query(func.max(WeeklyRanking.week_start))
        .filter(WeeklyRanking.score_version == score_version)
        .scalar()
    )


def ranking_items_for_week(
    db: Session,
    week_start: date,
    score_version: str,
    language: str | None = None,
    limit: int = 10,
) -> tuple[list[dict], dict[int, RepoSnapshot]]:
    query = (
        db.query(WeeklyRanking)
        .options(joinedload(WeeklyRanking.repository))
        .join(Repository, Repository.id == WeeklyRanking.repository_id)
        .filter(WeeklyRanking.week_start == week_start)
        .filter(WeeklyRanking.score_version == score_version)
    )
    if language:
        query = query.filter(Repository.primary_language == language)
    rankings = query.order_by(WeeklyRanking.rank.asc()).limit(limit).all()
    repo_ids = [item.repository_id for item in rankings]
    snapshots = (
        db.query(RepoSnapshot)
        .filter(RepoSnapshot.week_start == week_start)
        .filter(RepoSnapshot.repository_id.in_(repo_ids))
        .all()
    )
    snapshots_by_repo = {snapshot.repository_id: snapshot for snapshot in snapshots}
    return [ranking_item(item, snapshots_by_repo.get(item.repository_id)) for item in rankings], snapshots_by_repo


def available_languages(db: Session, week_start: date, score_version: str) -> list[str]:
    rows = (
        db.query(Repository.primary_language)
        .join(WeeklyRanking, WeeklyRanking.repository_id == Repository.id)
        .filter(WeeklyRanking.week_start == week_start)
        .filter(WeeklyRanking.score_version == score_version)
        .filter(Repository.primary_language.is_not(None))
        .group_by(Repository.primary_language)
        .order_by(Repository.primary_language.asc())
        .all()
    )
    return [row[0] for row in rows if row[0]]


def default_compare_repos(db: Session) -> list[str]:
    settings = get_settings()
    week = latest_week(db, settings.score_version)
    if week is None:
        return []
    rankings = (
        db.query(WeeklyRanking)
        .options(joinedload(WeeklyRanking.repository))
        .filter(WeeklyRanking.week_start == week)
        .filter(WeeklyRanking.score_version == settings.score_version)
        .order_by(WeeklyRanking.rank.asc())
        .limit(3)
        .all()
    )
    return [item.repository.full_name for item in rankings]


def compare_points(db: Session, repository_id: int, metric: str) -> list[dict]:
    settings = get_settings()
    if metric.endswith("_score") or metric == "hot_score":
        rows = (
            db.query(WeeklyRanking)
            .filter(WeeklyRanking.repository_id == repository_id)
            .filter(WeeklyRanking.score_version == settings.score_version)
            .order_by(WeeklyRanking.week_start.asc())
            .limit(12)
            .all()
        )
        return [{"week_start": row.week_start.isoformat(), "value": getattr(row, metric)} for row in rows]
    rows = (
        db.query(RepoSnapshot)
        .filter(RepoSnapshot.repository_id == repository_id)
        .order_by(RepoSnapshot.week_start.asc())
        .limit(12)
        .all()
    )
    return [{"week_start": row.week_start.isoformat(), "value": getattr(row, metric, 0)} for row in rows]
