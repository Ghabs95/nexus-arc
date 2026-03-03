"""PostgreSQL-backed inbox queue for task ingestion.

Used by the example Telegram bot when ``NEXUS_INBOX_BACKEND=postgres``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from config import NEXUS_STORAGE_DSN

try:
    import sqlalchemy as sa
    from sqlalchemy.orm import DeclarativeBase, Session

    _SA_AVAILABLE = True
except ImportError:
    _SA_AVAILABLE = False
    sa: Any = None
    DeclarativeBase: Any = object
    Session: Any = None


logger = logging.getLogger(__name__)
_DEDUP_WINDOW_SECONDS = max(0, int(os.getenv("NEXUS_INBOX_DEDUPE_WINDOW_SECONDS", "900")))
_DONE_DEDUP_WINDOW_SECONDS = max(
    0, int(os.getenv("NEXUS_INBOX_DONE_DEDUPE_WINDOW_SECONDS", "86400"))
)


def _require_sqlalchemy() -> None:
    if not _SA_AVAILABLE:
        raise ImportError(
            "sqlalchemy and psycopg2-binary are required for Postgres inbox queue. "
            "Install with: pip install nexus-arc[postgres]"
        )


def _normalize_dsn(raw_dsn: str) -> str:
    dsn = str(raw_dsn or "").strip()
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+psycopg2://", 1)
    if dsn.startswith("postgres://"):
        return dsn.replace("postgres://", "postgresql+psycopg2://", 1)
    return dsn


if _SA_AVAILABLE:

    class _InboxBase(DeclarativeBase):
        pass

    class _InboxTaskRow(_InboxBase):
        __tablename__ = "nexus_inbox_tasks"

        id: sa.orm.Mapped[int] = sa.orm.mapped_column(
            sa.Integer, primary_key=True, autoincrement=True
        )
        project_key: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128), nullable=False)
        workspace: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(256), nullable=False)
        filename: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(256), nullable=False)
        markdown_content: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.Text, nullable=False)
        status: sa.orm.Mapped[str] = sa.orm.mapped_column(
            sa.String(32), nullable=False, default="pending"
        )
        created_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True),
            nullable=False,
            default=lambda: datetime.now(tz=UTC),
        )
        claimed_at: sa.orm.Mapped[datetime | None] = sa.orm.mapped_column(
            sa.DateTime(timezone=True)
        )
        processed_at: sa.orm.Mapped[datetime | None] = sa.orm.mapped_column(
            sa.DateTime(timezone=True)
        )
        claimed_by: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.String(256))
        error: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.Text)


@dataclass
class InboxQueueTask:
    id: int
    project_key: str
    workspace: str
    filename: str
    markdown_content: str


_ENGINE = None


def _get_engine():
    global _ENGINE
    _require_sqlalchemy()

    if _ENGINE is not None:
        return _ENGINE

    dsn = _normalize_dsn(NEXUS_STORAGE_DSN)
    if not dsn:
        raise ValueError("NEXUS_STORAGE_DSN is required for postgres inbox backend")

    _ENGINE = sa.create_engine(dsn, pool_size=5)
    _InboxBase.metadata.create_all(_ENGINE)
    logger.info("Postgres inbox queue initialized")
    return _ENGINE


def enqueue_task(
    *,
    project_key: str,
    workspace: str,
    filename: str,
    markdown_content: str,
) -> int:
    """Insert a task into postgres inbox queue and return row id."""
    engine = _get_engine()
    normalized_project = str(project_key)
    normalized_workspace = str(workspace)
    normalized_filename = str(filename)
    normalized_content = str(markdown_content)
    now = datetime.now(tz=UTC)
    with Session(engine) as session:
        existing: Any = (
            session.query(_InboxTaskRow)
            .filter(_InboxTaskRow.project_key == normalized_project)
            .filter(_InboxTaskRow.workspace == normalized_workspace)
            .filter(_InboxTaskRow.filename == normalized_filename)
            .order_by(_InboxTaskRow.id.desc())
            .first()
        )
        if existing:
            status = str(existing.status or "").lower()
            if status in {"pending", "processing"}:
                logger.warning(
                    "Duplicate inbox enqueue suppressed: id=%s project=%s workspace=%s filename=%s status=%s",
                    existing.id,
                    normalized_project,
                    normalized_workspace,
                    normalized_filename,
                    existing.status,
                )
                return int(existing.id)
            if status == "done" and _DONE_DEDUP_WINDOW_SECONDS > 0:
                done_cutoff = now - timedelta(seconds=_DONE_DEDUP_WINDOW_SECONDS)
                reference_time = existing.processed_at or existing.created_at
                if isinstance(reference_time, datetime):
                    if reference_time.tzinfo is None:
                        reference_time = reference_time.replace(tzinfo=UTC)
                    if reference_time >= done_cutoff:
                        logger.warning(
                            "Duplicate done-task enqueue suppressed: id=%s project=%s workspace=%s filename=%s age_window=%ss",
                            existing.id,
                            normalized_project,
                            normalized_workspace,
                            normalized_filename,
                            _DONE_DEDUP_WINDOW_SECONDS,
                        )
                        return int(existing.id)

        if _DEDUP_WINDOW_SECONDS > 0:
            dedupe_cutoff = now - timedelta(seconds=_DEDUP_WINDOW_SECONDS)
            exact_existing: Any = (
                session.query(_InboxTaskRow)
                .filter(_InboxTaskRow.project_key == normalized_project)
                .filter(_InboxTaskRow.workspace == normalized_workspace)
                .filter(_InboxTaskRow.filename == normalized_filename)
                .filter(_InboxTaskRow.markdown_content == normalized_content)
                .filter(_InboxTaskRow.created_at >= dedupe_cutoff)
                .order_by(_InboxTaskRow.id.desc())
                .first()
            )
            if exact_existing and str(exact_existing.status or "").lower() in {
                "pending",
                "processing",
                "done",
            }:
                logger.warning(
                    "Duplicate inbox enqueue suppressed (exact match): id=%s project=%s workspace=%s filename=%s status=%s",
                    exact_existing.id,
                    normalized_project,
                    normalized_workspace,
                    normalized_filename,
                    exact_existing.status,
                )
                return int(exact_existing.id)

        row = _InboxTaskRow(
            project_key=normalized_project,
            workspace=normalized_workspace,
            filename=normalized_filename,
            markdown_content=normalized_content,
            status="pending",
            created_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.id)


def claim_pending_tasks(*, limit: int, worker_id: str) -> list[InboxQueueTask]:
    """Claim pending tasks and return them for processing."""
    engine = _get_engine()
    claimed: list[InboxQueueTask] = []
    now = datetime.now(tz=UTC)
    claim_limit = max(1, int(limit))
    # Scan a wider window so we can suppress duplicate rows and still return up to claim_limit tasks.
    scan_limit = max(claim_limit, claim_limit * 5)

    with Session(engine) as session:
        query = (
            session.query(_InboxTaskRow)
            .filter(_InboxTaskRow.status == "pending")
            .order_by(_InboxTaskRow.id.asc())
            .limit(scan_limit)
            .with_for_update(skip_locked=True)
        )
        rows = query.all()
        if not rows:
            session.commit()
            return []

        for r_item in rows:
            row: Any = r_item
            prior_nonfailed = (
                session.query(_InboxTaskRow.id, _InboxTaskRow.status)
                .filter(_InboxTaskRow.project_key == row.project_key)
                .filter(_InboxTaskRow.workspace == row.workspace)
                .filter(_InboxTaskRow.filename == row.filename)
                .filter(_InboxTaskRow.id < row.id)
                .filter(_InboxTaskRow.status.in_(("pending", "processing", "done")))
                .order_by(_InboxTaskRow.id.asc())
                .first()
            )
            if prior_nonfailed:
                prior_id, prior_status = prior_nonfailed
                row.status = "done"
                row.processed_at = now
                row.error = (
                    "Duplicate queue row suppressed; "
                    f"prior row id={int(prior_id)} status={str(prior_status)}"
                )[:2000]
                logger.warning(
                    "Duplicate pending inbox row suppressed at claim time: "
                    "id=%s duplicate_of=%s project=%s workspace=%s filename=%s",
                    row.id,
                    prior_id,
                    row.project_key,
                    row.workspace,
                    row.filename,
                )
                continue

            if len(claimed) >= claim_limit:
                continue

            row.status = "processing"
            row.claimed_by = worker_id
            row.claimed_at = now
            row.error = None
            claimed.append(
                InboxQueueTask(
                    id=int(row.id),
                    project_key=row.project_key,
                    workspace=row.workspace,
                    filename=row.filename,
                    markdown_content=row.markdown_content,
                )
            )
        session.commit()

    return claimed


def mark_task_done(task_id: int) -> None:
    """Mark a claimed task as done."""
    engine = _get_engine()
    with Session(engine) as session:
        row = session.get(_InboxTaskRow, int(task_id))
        if not row:
            return
        row.status = "done"
        row.processed_at = datetime.now(tz=UTC)
        row.error = None
        session.commit()


def mark_task_failed(task_id: int, error: str) -> None:
    """Mark a claimed task as failed with error details."""
    engine = _get_engine()
    with Session(engine) as session:
        row = session.get(_InboxTaskRow, int(task_id))
        if not row:
            return
        row.status = "failed"
        row.error = str(error or "")[:2000]
        row.processed_at = datetime.now(tz=UTC)
        session.commit()


def get_queue_overview(*, limit: int = 10) -> dict[str, Any]:
    """Return queue counts and recent rows for monitoring commands."""
    engine = _get_engine()
    safe_limit = max(1, min(int(limit), 50))
    counts = {
        "pending": 0,
        "processing": 0,
        "done": 0,
        "failed": 0,
    }
    recent: list[dict[str, Any]] = []
    pending_by_project: dict[str, dict[str, Any]] = {}

    with Session(engine) as session:
        grouped = (
            session.query(_InboxTaskRow.status, sa.func.count(_InboxTaskRow.id))
            .group_by(_InboxTaskRow.status)
            .all()
        )
        for status, count in grouped:
            if status in counts:
                counts[status] = int(count)

        rows = (
            session.query(_InboxTaskRow).order_by(_InboxTaskRow.id.desc()).limit(safe_limit).all()
        )

        for r_item in rows:
            row: Any = r_item
            recent.append(
                {
                    "id": int(row.id),
                    "project_key": row.project_key,
                    "filename": row.filename,
                    "status": row.status,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "claimed_by": row.claimed_by,
                    "error": row.error,
                }
            )

        pending_counts = (
            session.query(_InboxTaskRow.project_key, sa.func.count(_InboxTaskRow.id))
            .filter(_InboxTaskRow.status == "pending")
            .group_by(_InboxTaskRow.project_key)
            .all()
        )
        for project_key, count in pending_counts:
            pending_by_project[str(project_key)] = {
                "count": int(count),
                "samples": [],
            }

        sample_rows = (
            session.query(_InboxTaskRow)
            .filter(_InboxTaskRow.status == "pending")
            .order_by(_InboxTaskRow.id.desc())
            .limit(max(50, safe_limit * 10))
            .all()
        )
        for smp_item in sample_rows:
            row: Any = smp_item
            bucket = pending_by_project.get(str(row.project_key))
            if not bucket:
                continue
            samples = bucket["samples"]
            if len(samples) >= 3:
                continue
            samples.append(
                {
                    "id": int(row.id),
                    "filename": row.filename,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
            )

    counts["total"] = sum(counts.values())
    return {
        "counts": counts,
        "recent": recent,
        "pending_by_project": pending_by_project,
        "limit": safe_limit,
    }
