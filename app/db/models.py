from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    owner: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    full_name: Mapped[str] = mapped_column(String(512), index=True)
    html_url: Mapped[str] = mapped_column(String(1024))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_language: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    license_spdx: Mapped[str | None] = mapped_column(String(128), nullable=True)
    topics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    source_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    fork: Mapped[bool] = mapped_column(Boolean, default=False)
    has_readme: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    snapshots: Mapped[list["RepoSnapshot"]] = relationship(back_populates="repository")
    rankings: Mapped[list["WeeklyRanking"]] = relationship(back_populates="repository")


class RepoSnapshot(Base):
    __tablename__ = "repo_snapshots"
    __table_args__ = (UniqueConstraint("repository_id", "week_start", name="uq_snapshot_repo_week"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    collection_run_id: Mapped[int | None] = mapped_column(ForeignKey("collection_runs.id"), nullable=True)
    week_start: Mapped[date] = mapped_column(Date, index=True)
    stars: Mapped[int] = mapped_column(Integer, default=0)
    forks: Mapped[int] = mapped_column(Integer, default=0)
    watchers: Mapped[int] = mapped_column(Integer, default=0)
    open_issues: Mapped[int] = mapped_column(Integer, default=0)
    stars_delta_7d: Mapped[int] = mapped_column(Integer, default=0)
    stars_growth_rate_7d: Mapped[float] = mapped_column(Float, default=0.0)
    forks_delta_7d: Mapped[int] = mapped_column(Integer, default=0)
    watchers_delta_7d: Mapped[int] = mapped_column(Integer, default=0)
    default_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_count: Mapped[int] = mapped_column(Integer, default=0)
    latest_release_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contributor_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    commit_count_7d: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_opened_7d: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_merged_7d: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issues_opened_7d: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issues_closed_7d: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_quality_level: Mapped[str] = mapped_column(String(32), default="complete")
    data_quality_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    repository: Mapped[Repository] = relationship(back_populates="snapshots")
    collection_run: Mapped["CollectionRun | None"] = relationship(back_populates="snapshots")


class WeeklyRanking(Base):
    __tablename__ = "weekly_rankings"
    __table_args__ = (
        UniqueConstraint(
            "week_start",
            "score_version",
            "repository_id",
            name="uq_ranking_week_version_repo",
        ),
        UniqueConstraint("week_start", "score_version", "rank", name="uq_ranking_week_version_rank"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, index=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    rank: Mapped[int] = mapped_column(Integer, index=True)
    previous_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rank_change: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_new_entry: Mapped[bool] = mapped_column(Boolean, default=False)
    weeks_on_chart: Mapped[int] = mapped_column(Integer, default=1)
    score_version: Mapped[str] = mapped_column(String(32), index=True)
    hot_score: Mapped[float] = mapped_column(Float)
    attention_score: Mapped[float] = mapped_column(Float)
    activity_score: Mapped[float] = mapped_column(Float)
    community_score: Mapped[float] = mapped_column(Float)
    freshness_score: Mapped[float] = mapped_column(Float)
    health_score: Mapped[float] = mapped_column(Float)
    maturity_confidence: Mapped[float] = mapped_column(Float)
    data_quality_level: Mapped[str] = mapped_column(String(32), default="complete")
    score_breakdown_json: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    repository: Mapped[Repository] = relationship(back_populates="rankings")


class CollectionRun(Base):
    __tablename__ = "collection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_type: Mapped[str] = mapped_column(String(64), default="weekly")
    trigger_source: Mapped[str] = mapped_column(String(64), default="manual")
    week_start: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    score_version: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    filtered_count: Mapped[int] = mapped_column(Integer, default=0)
    ranked_count: Mapped[int] = mapped_column(Integer, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    rate_limit_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    snapshots: Mapped[list[RepoSnapshot]] = relationship(back_populates="collection_run")

