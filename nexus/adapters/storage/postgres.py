"""PostgreSQL storage backend using SQLAlchemy (sync psycopg2 driver).

Requires the ``postgres`` optional extra::

    pip install nexus-arc[postgres]

All async methods delegate to synchronous SQLAlchemy sessions via
``asyncio.to_thread`` so the declared dependency (``psycopg2-binary``) is
used directly without needing an async driver.

Schema is created automatically on first use (``create_all``).
"""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from nexus.adapters.storage.base import StorageBackend
from nexus.core.completion import budget_completion_payload
from nexus.core.models import AuditEvent, Workflow, WorkflowState

try:
    import sqlalchemy as sa
    from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

    _SA_AVAILABLE = True
except ImportError:
    _SA_AVAILABLE = False

logger = logging.getLogger(__name__)
_SEEN_CONNECTION_LOGS: set[str] = set()


def _require_sqlalchemy() -> None:
    if not _SA_AVAILABLE:
        raise ImportError(
            "sqlalchemy and psycopg2-binary are required for PostgreSQLStorageBackend. "
            "Install them with: pip install nexus-arc[postgres]"
        )


# ---------------------------------------------------------------------------
# ORM / table definitions (only constructed when SA is available)
# ---------------------------------------------------------------------------

if _SA_AVAILABLE:

    class _Base(DeclarativeBase):
        pass

    class _WorkflowRow(_Base):
        __tablename__ = "nexus_workflows"

        id: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128), primary_key=True)
        state: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(32))
        definition_id: sa.orm.Mapped[str | None] = sa.orm.mapped_column(
            sa.String(128), nullable=True
        )
        current_step: sa.orm.Mapped[int] = sa.orm.mapped_column(sa.Integer, default=0)
        data: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.Text)  # JSON blob
        created_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True), default=lambda: datetime.now(tz=UTC)
        )
        updated_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True),
            default=lambda: datetime.now(tz=UTC),
            onupdate=lambda: datetime.now(tz=UTC),
        )

    class _AuditRow(_Base):
        __tablename__ = "nexus_audit"

        id: sa.orm.Mapped[int] = sa.orm.mapped_column(
            sa.Integer, primary_key=True, autoincrement=True
        )
        workflow_id: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128), index=True)
        timestamp: sa.orm.Mapped[datetime] = sa.orm.mapped_column(sa.DateTime(timezone=True))
        event_type: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(64))
        user_id: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.String(128), nullable=True)
        data: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.Text)  # JSON blob

    class _AgentMetaRow(_Base):
        __tablename__ = "nexus_agent_meta"

        workflow_id: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128), primary_key=True)
        agent_name: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128), primary_key=True)
        data: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.Text)  # JSON blob
        updated_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True),
            default=lambda: datetime.now(tz=UTC),
            onupdate=lambda: datetime.now(tz=UTC),
        )

    class _CompletionRow(_Base):
        __tablename__ = "nexus_completions"

        id: sa.orm.Mapped[int] = sa.orm.mapped_column(
            sa.Integer, primary_key=True, autoincrement=True
        )
        issue_number: sa.orm.Mapped[str] = sa.orm.mapped_column(
            sa.String(32), index=True, nullable=False
        )
        agent_type: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128), nullable=False)
        status: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(32), default="complete")
        summary_text: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.Text, default="")
        data: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.Text)  # full JSON blob
        dedup_key: sa.orm.Mapped[str] = sa.orm.mapped_column(
            sa.String(256), unique=True, nullable=False
        )
        created_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True), default=lambda: datetime.now(tz=UTC)
        )

    class _HostStateRow(_Base):
        __tablename__ = "nexus_host_state"

        key: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128), primary_key=True)
        data: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.Text)  # JSON blob
        updated_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True),
            default=lambda: datetime.now(tz=UTC),
            onupdate=lambda: datetime.now(tz=UTC),
        )

    class _WorkflowMappingRow(_Base):
        __tablename__ = "nexus_workflow_mappings"

        issue_num: sa.orm.Mapped[str] = sa.orm.mapped_column(
            sa.String(64),
            primary_key=True,
        )
        workflow_id: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128))
        updated_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True),
            default=lambda: datetime.now(tz=UTC),
            onupdate=lambda: datetime.now(tz=UTC),
        )

    class _ApprovalStateRow(_Base):
        __tablename__ = "nexus_approval_state"

        issue_num: sa.orm.Mapped[str] = sa.orm.mapped_column(
            sa.String(64),
            primary_key=True,
        )
        step_num: sa.orm.Mapped[int] = sa.orm.mapped_column(sa.Integer)
        step_name: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(256))
        approvers: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.Text)  # JSON list
        approval_timeout: sa.orm.Mapped[int] = sa.orm.mapped_column(sa.Integer)
        requested_at: sa.orm.Mapped[float] = sa.orm.mapped_column(sa.Float)


# ---------------------------------------------------------------------------
# Storage backend
# ---------------------------------------------------------------------------


class PostgreSQLStorageBackend(StorageBackend):
    """PostgreSQL-backed workflow storage using SQLAlchemy.

    .. note::
        For production deployments, it is highly recommended to use
        the ``NEXUS_STORAGE_DSN`` environment variable instead of hardcoding
        credentials in configuration files.

    Args:
        connection_string: SQLAlchemy DSN, e.g.
            ``postgresql+psycopg2://user:pass@localhost/dbname``.
        pool_size: SQLAlchemy connection pool size (default 5).
        echo: Echo SQL statements for debugging (default False).
    """

    def __init__(
        self,
        connection_string: str,
        pool_size: int = 5,
        echo: bool = False,
    ):
        _require_sqlalchemy()

        # Ensure the DSN uses the psycopg2 driver
        dsn = connection_string
        if dsn.startswith("postgresql://") or dsn.startswith("postgres://"):
            dsn = dsn.replace("postgresql://", "postgresql+psycopg2://", 1)
            dsn = dsn.replace("postgres://", "postgresql+psycopg2://", 1)

        engine_kwargs: dict[str, Any] = {"echo": echo}
        if dsn.startswith("sqlite://"):
            engine_kwargs["connect_args"] = {"check_same_thread": False}
            engine_kwargs["poolclass"] = sa.pool.StaticPool
        else:
            engine_kwargs["pool_size"] = pool_size

        self._engine = sa.create_engine(dsn, **engine_kwargs)
        self._session_factory: sessionmaker = sessionmaker(
            bind=self._engine, expire_on_commit=False
        )
        # Create tables if they don't exist
        _Base.metadata.create_all(self._engine)
        dsn_label = dsn.split("@")[-1]
        if dsn_label not in _SEEN_CONNECTION_LOGS:
            logger.info("PostgreSQLStorageBackend connected (%s)", dsn_label)
            _SEEN_CONNECTION_LOGS.add(dsn_label)
        else:
            logger.debug("PostgreSQLStorageBackend reused (%s)", dsn_label)

    # ------------------------------------------------------------------
    # StorageBackend interface
    # ------------------------------------------------------------------

    async def save_workflow(self, workflow: Workflow) -> None:
        await asyncio.to_thread(self._sync_save_workflow, workflow)

    async def load_workflow(self, workflow_id: str) -> Workflow | None:
        return await asyncio.to_thread(self._sync_load_workflow, workflow_id)

    async def list_workflows(
        self, state: WorkflowState | None = None, limit: int = 100
    ) -> list[Workflow]:
        return await asyncio.to_thread(self._sync_list_workflows, state, limit)

    async def delete_workflow(self, workflow_id: str) -> bool:
        return await asyncio.to_thread(self._sync_delete_workflow, workflow_id)

    async def append_audit_event(self, event: AuditEvent) -> None:
        await asyncio.to_thread(self._sync_append_audit, event)

    async def get_audit_log(
        self, workflow_id: str, since: datetime | None = None
    ) -> list[AuditEvent]:
        return await asyncio.to_thread(self._sync_get_audit, workflow_id, since)

    async def save_agent_metadata(
        self, workflow_id: str, agent_name: str, metadata: dict[str, Any]
    ) -> None:
        await asyncio.to_thread(self._sync_save_agent_meta, workflow_id, agent_name, metadata)

    async def get_agent_metadata(self, workflow_id: str, agent_name: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._sync_get_agent_meta, workflow_id, agent_name)

    async def cleanup_old_workflows(self, older_than_days: int = 30) -> int:
        return await asyncio.to_thread(self._sync_cleanup, older_than_days)

    async def save_completion(
        self, issue_number: str, agent_type: str, data: dict[str, Any]
    ) -> str:
        return await asyncio.to_thread(self._sync_save_completion, issue_number, agent_type, data)

    async def list_completions(self, issue_number: str | None = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._sync_list_completions, issue_number)

    async def save_host_state(self, key: str, data: dict[str, Any]) -> None:
        await asyncio.to_thread(self._sync_save_host_state, key, data)

    async def load_host_state(self, key: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._sync_load_host_state, key)

    async def map_issue_to_workflow(self, issue_num: str, workflow_id: str) -> None:
        await asyncio.to_thread(self._sync_map_issue_to_workflow, issue_num, workflow_id)

    async def get_workflow_id_for_issue(self, issue_num: str) -> str | None:
        return await asyncio.to_thread(self._sync_get_workflow_id_for_issue, issue_num)

    async def remove_issue_workflow_mapping(self, issue_num: str) -> None:
        await asyncio.to_thread(self._sync_remove_issue_workflow_mapping, issue_num)

    async def load_issue_workflow_mappings(self) -> dict[str, str]:
        return await asyncio.to_thread(self._sync_load_issue_workflow_mappings)

    async def set_pending_workflow_approval(
        self,
        issue_num: str,
        step_num: int,
        step_name: str,
        approvers: list[str],
        approval_timeout: int,
    ) -> None:
        await asyncio.to_thread(
            self._sync_set_pending_workflow_approval,
            issue_num,
            step_num,
            step_name,
            approvers,
            approval_timeout,
        )

    async def clear_pending_workflow_approval(self, issue_num: str) -> None:
        await asyncio.to_thread(self._sync_clear_pending_workflow_approval, issue_num)

    async def get_pending_workflow_approval(self, issue_num: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._sync_get_pending_workflow_approval, issue_num)

    async def load_pending_workflow_approvals(self) -> dict[str, dict[str, Any]]:
        return await asyncio.to_thread(self._sync_load_pending_workflow_approvals)

    # ------------------------------------------------------------------
    # Synchronous DB helpers
    # ------------------------------------------------------------------

    def _sync_save_workflow(self, workflow: Workflow) -> None:
        with Session(self._engine) as session:
            row = session.get(_WorkflowRow, workflow.id)
            data = self._workflow_to_dict(workflow)
            now = datetime.now(tz=UTC)
            if row:
                row.state = workflow.state.value
                row.current_step = workflow.current_step
                row.data = json.dumps(data, default=str)
                row.updated_at = now
            else:
                session.add(
                    _WorkflowRow(
                        id=workflow.id,
                        state=workflow.state.value,
                        definition_id=getattr(workflow, "definition_id", None),
                        current_step=workflow.current_step,
                        data=json.dumps(data, default=str),
                        created_at=now,
                        updated_at=now,
                    )
                )
            session.commit()

    def _sync_load_workflow(self, workflow_id: str) -> Workflow | None:
        with Session(self._engine) as session:
            row = session.get(_WorkflowRow, workflow_id)
            if not row:
                return None
            return self._dict_to_workflow(json.loads(row.data))

    def _sync_list_workflows(self, state: WorkflowState | None, limit: int) -> list[Workflow]:
        with Session(self._engine) as session:
            q = session.query(_WorkflowRow).order_by(_WorkflowRow.updated_at.desc())
            if state is not None:
                q = q.filter(_WorkflowRow.state == state.value)
            rows = q.limit(limit).all()
            workflows = []
            for row in rows:
                try:
                    workflows.append(self._dict_to_workflow(json.loads(row.data)))
                except Exception as exc:
                    logger.warning("Failed to deserialize workflow %s: %s", row.id, exc)
            return workflows

    def _sync_delete_workflow(self, workflow_id: str) -> bool:
        with Session(self._engine) as session:
            row = session.get(_WorkflowRow, workflow_id)
            if not row:
                return False
            session.delete(row)
            session.commit()
            return True

    def _sync_append_audit(self, event: AuditEvent) -> None:
        with Session(self._engine) as session:
            session.add(
                _AuditRow(
                    workflow_id=event.workflow_id,
                    timestamp=event.timestamp,
                    event_type=event.event_type,
                    user_id=event.user_id,
                    data=json.dumps(event.data, default=str),
                )
            )
            session.commit()

    def _sync_get_audit(self, workflow_id: str, since: datetime | None) -> list[AuditEvent]:
        with Session(self._engine) as session:
            q = (
                session.query(_AuditRow)
                .filter(_AuditRow.workflow_id == workflow_id)
                .order_by(_AuditRow.timestamp)
            )
            if since:
                q = q.filter(_AuditRow.timestamp >= since)
            rows = q.all()
            return [
                AuditEvent(
                    workflow_id=r.workflow_id,
                    timestamp=r.timestamp,
                    event_type=r.event_type,
                    user_id=r.user_id,
                    data=json.loads(r.data),
                )
                for r in rows
            ]

    def _sync_save_agent_meta(
        self, workflow_id: str, agent_name: str, metadata: dict[str, Any]
    ) -> None:
        with Session(self._engine) as session:
            row = session.get(_AgentMetaRow, (workflow_id, agent_name))
            now = datetime.now(tz=UTC)
            if row:
                row.data = json.dumps(metadata, default=str)
                row.updated_at = now
            else:
                session.add(
                    _AgentMetaRow(
                        workflow_id=workflow_id,
                        agent_name=agent_name,
                        data=json.dumps(metadata, default=str),
                        updated_at=now,
                    )
                )
            session.commit()

    def _sync_get_agent_meta(self, workflow_id: str, agent_name: str) -> dict[str, Any] | None:
        with Session(self._engine) as session:
            row = session.get(_AgentMetaRow, (workflow_id, agent_name))
            if not row:
                return None
            return json.loads(row.data)

    def _sync_cleanup(self, older_than_days: int) -> int:
        from datetime import timedelta

        cutoff = datetime.now(tz=UTC) - timedelta(days=older_than_days)
        with Session(self._engine) as session:
            rows = session.query(_WorkflowRow).filter(_WorkflowRow.updated_at < cutoff).all()
            count = len(rows)
            for row in rows:
                session.delete(row)
            session.commit()
            return count

    def _sync_save_completion(
        self, issue_number: str, agent_type: str, data: dict[str, Any]
    ) -> str:
        payload = budget_completion_payload(data)
        status = str(payload.get("status") or "complete")
        payload["status"] = status
        payload.setdefault("issue_number", str(issue_number))
        payload.setdefault("_issue_number", str(issue_number))
        payload.setdefault("agent_type", str(agent_type))
        payload.setdefault("_agent_type", str(agent_type))

        dedup_key = f"{issue_number}:{agent_type}:{status}"
        now = datetime.now(tz=UTC)
        with Session(self._engine) as session:
            existing = (
                session.query(_CompletionRow).filter(_CompletionRow.dedup_key == dedup_key).first()
            )
            if existing:
                existing.data = json.dumps(payload, default=str)
                existing.summary_text = payload.get("summary", "")
                existing.status = status
                # Keep row recency aligned with latest completion payload updates.
                existing.created_at = now
            else:
                session.add(
                    _CompletionRow(
                        issue_number=issue_number,
                        agent_type=agent_type,
                        status=status,
                        summary_text=payload.get("summary", ""),
                        data=json.dumps(payload, default=str),
                        dedup_key=dedup_key,
                        created_at=now,
                    )
                )
            session.commit()
        return dedup_key

    def _sync_list_completions(self, issue_number: str | None) -> list[dict[str, Any]]:
        with Session(self._engine) as session:
            q = session.query(_CompletionRow).order_by(_CompletionRow.created_at.desc())
            if issue_number:
                q = q.filter(_CompletionRow.issue_number == issue_number)

            results: list[dict[str, Any]] = []
            seen_issues: set[str] = set()
            for row in q.all():
                if row.issue_number in seen_issues:
                    continue
                seen_issues.add(row.issue_number)
                try:
                    payload = json.loads(row.data)
                except Exception:
                    payload = {}
                payload = budget_completion_payload(payload)
                issue_number = str(row.issue_number or "").strip()
                agent_type = str(row.agent_type or "").strip()
                status = str(row.status or "complete").strip() or "complete"
                payload["issue_number"] = issue_number
                payload.setdefault("_issue_number", issue_number)
                payload_agent = str(payload.get("agent_type") or "").strip()
                if not payload_agent or payload_agent == "unknown":
                    payload_agent = agent_type
                payload["agent_type"] = payload_agent
                payload.setdefault("_agent_type", agent_type)
                payload["status"] = str(payload.get("status") or status).strip() or "complete"
                payload["_db_id"] = row.id
                payload["_dedup_key"] = row.dedup_key
                payload["_created_at"] = row.created_at.isoformat() if row.created_at else None
                payload["_updated_at"] = payload["_created_at"]
                results.append(payload)
            return results

    def _sync_save_host_state(self, key: str, data: dict[str, Any]) -> None:
        with Session(self._engine) as session:
            existing = session.get(_HostStateRow, key)
            if existing:
                existing.data = json.dumps(data, default=str)
            else:
                session.add(_HostStateRow(key=key, data=json.dumps(data, default=str)))
            session.commit()

    def _sync_load_host_state(self, key: str) -> dict[str, Any] | None:
        with Session(self._engine) as session:
            row = session.get(_HostStateRow, key)
            if not row:
                return None
            try:
                return json.loads(row.data)
            except Exception:
                return None

    def _sync_map_issue_to_workflow(self, issue_num: str, workflow_id: str) -> None:
        with Session(self._engine) as session:
            row = session.get(_WorkflowMappingRow, str(issue_num))
            now = datetime.now(tz=UTC)
            if row:
                row.workflow_id = str(workflow_id)
                row.updated_at = now
            else:
                session.add(
                    _WorkflowMappingRow(
                        issue_num=str(issue_num),
                        workflow_id=str(workflow_id),
                        updated_at=now,
                    )
                )
            session.commit()

    def _sync_get_workflow_id_for_issue(self, issue_num: str) -> str | None:
        with Session(self._engine) as session:
            row = session.get(_WorkflowMappingRow, str(issue_num))
            return row.workflow_id if row else None

    def _sync_remove_issue_workflow_mapping(self, issue_num: str) -> None:
        with Session(self._engine) as session:
            row = session.get(_WorkflowMappingRow, str(issue_num))
            if row:
                session.delete(row)
                session.commit()

    def _sync_load_issue_workflow_mappings(self) -> dict[str, str]:
        with Session(self._engine) as session:
            rows = session.query(_WorkflowMappingRow).all()
            return {r.issue_num: r.workflow_id for r in rows}

    def _sync_set_pending_workflow_approval(
        self,
        issue_num: str,
        step_num: int,
        step_name: str,
        approvers: list[str],
        approval_timeout: int,
    ) -> None:
        with Session(self._engine) as session:
            row = session.get(_ApprovalStateRow, str(issue_num))
            if row:
                row.step_num = int(step_num)
                row.step_name = str(step_name)
                row.approvers = json.dumps(list(approvers))
                row.approval_timeout = int(approval_timeout)
                row.requested_at = time.time()
            else:
                session.add(
                    _ApprovalStateRow(
                        issue_num=str(issue_num),
                        step_num=int(step_num),
                        step_name=str(step_name),
                        approvers=json.dumps(list(approvers)),
                        approval_timeout=int(approval_timeout),
                        requested_at=time.time(),
                    )
                )
            session.commit()

    def _sync_clear_pending_workflow_approval(self, issue_num: str) -> None:
        with Session(self._engine) as session:
            row = session.get(_ApprovalStateRow, str(issue_num))
            if row:
                session.delete(row)
                session.commit()

    def _sync_get_pending_workflow_approval(self, issue_num: str) -> dict[str, Any] | None:
        with Session(self._engine) as session:
            row = session.get(_ApprovalStateRow, str(issue_num))
            if not row:
                return None
            return {
                "step_num": row.step_num,
                "step_name": row.step_name,
                "approvers": json.loads(row.approvers),
                "approval_timeout": row.approval_timeout,
                "requested_at": row.requested_at,
            }

    def _sync_load_pending_workflow_approvals(self) -> dict[str, dict[str, Any]]:
        with Session(self._engine) as session:
            rows = session.query(_ApprovalStateRow).all()
            return {
                r.issue_num: {
                    "step_num": r.step_num,
                    "step_name": r.step_name,
                    "approvers": json.loads(r.approvers),
                    "approval_timeout": r.approval_timeout,
                    "requested_at": r.requested_at,
                }
                for r in rows
            }

    # ------------------------------------------------------------------
    # Serialization helpers — shared with FileStorage
    # ------------------------------------------------------------------

    @staticmethod
    def _workflow_to_dict(workflow: Workflow) -> dict[str, Any]:
        from nexus.adapters.storage._workflow_serde import workflow_to_dict

        return workflow_to_dict(workflow)

    @staticmethod
    def _dict_to_workflow(data: dict[str, Any]) -> Workflow:
        from nexus.adapters.storage._workflow_serde import dict_to_workflow

        return dict_to_workflow(data)

    def close(self) -> None:
        """Dispose underlying SQLAlchemy engine resources."""
        try:
            self._engine.dispose()
        except Exception as exc:
            logger.debug("Failed to dispose PostgreSQLStorageBackend engine: %s", exc)

    def __del__(self) -> None:  # pragma: no cover - defensive finalizer
        try:
            self.close()
        except Exception:
            pass
