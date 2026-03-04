"""Postgres-backed persistence for auth sessions, credentials, ACLs, and requester bindings."""

from __future__ import annotations

import atexit
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

try:
    import sqlalchemy as sa
    from sqlalchemy.orm import DeclarativeBase, Session

    _SA_AVAILABLE = True
except Exception:
    _SA_AVAILABLE = False
    sa = None  # type: ignore[assignment]
    DeclarativeBase = object  # type: ignore[assignment]
    Session = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _require_sqlalchemy() -> None:
    if not _SA_AVAILABLE:
        raise RuntimeError(
            "sqlalchemy and psycopg2-binary are required for auth storage. "
            "Install with: pip install nexus-arc[postgres]"
        )


def _normalize_dsn(raw_dsn: str) -> str:
    dsn = str(raw_dsn or "").strip()
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+psycopg2://", 1)
    if dsn.startswith("postgres://"):
        return dsn.replace("postgres://", "postgresql+psycopg2://", 1)
    return dsn


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def hash_oauth_state(state: str) -> str:
    value = str(state or "").strip().encode("utf-8")
    return hashlib.sha256(value).hexdigest()


if _SA_AVAILABLE:

    class _AuthBase(DeclarativeBase):
        pass

    class _AuthSessionRow(_AuthBase):
        __tablename__ = "nexus_auth_sessions"

        session_id: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(64), primary_key=True)
        nexus_id: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(64), nullable=False, index=True)
        discord_user_id: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(64), nullable=False)
        discord_username: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.String(255))
        oauth_provider: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.String(32))
        oauth_state_hash: sa.orm.Mapped[str | None] = sa.orm.mapped_column(
            sa.String(128), index=True
        )
        status: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(32), nullable=False, default="pending")
        expires_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True), nullable=False
        )
        last_error: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.Text)
        used_at: sa.orm.Mapped[datetime | None] = sa.orm.mapped_column(sa.DateTime(timezone=True))
        created_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True), nullable=False, default=_now_utc
        )
        updated_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True), nullable=False, default=_now_utc
        )

    class _UserCredentialRow(_AuthBase):
        __tablename__ = "nexus_user_credentials"

        nexus_id: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(64), primary_key=True)
        auth_provider: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.String(32))
        github_user_id: sa.orm.Mapped[int | None] = sa.orm.mapped_column(sa.BigInteger)
        github_login: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.String(255))
        github_token_enc: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.Text)
        github_refresh_token_enc: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.Text)
        github_token_expires_at: sa.orm.Mapped[datetime | None] = sa.orm.mapped_column(
            sa.DateTime(timezone=True)
        )
        gitlab_user_id: sa.orm.Mapped[int | None] = sa.orm.mapped_column(sa.BigInteger)
        gitlab_username: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.String(255))
        gitlab_token_enc: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.Text)
        gitlab_refresh_token_enc: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.Text)
        gitlab_token_expires_at: sa.orm.Mapped[datetime | None] = sa.orm.mapped_column(
            sa.DateTime(timezone=True)
        )
        codex_api_key_enc: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.Text)
        gemini_api_key_enc: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.Text)
        claude_api_key_enc: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.Text)
        copilot_github_token_enc: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.Text)
        org_verified: sa.orm.Mapped[bool] = sa.orm.mapped_column(sa.Boolean, nullable=False, default=False)
        org_verified_at: sa.orm.Mapped[datetime | None] = sa.orm.mapped_column(sa.DateTime(timezone=True))
        last_access_sync_at: sa.orm.Mapped[datetime | None] = sa.orm.mapped_column(
            sa.DateTime(timezone=True)
        )
        key_version: sa.orm.Mapped[int] = sa.orm.mapped_column(sa.Integer, nullable=False, default=1)
        created_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True), nullable=False, default=_now_utc
        )
        updated_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True), nullable=False, default=_now_utc
        )

    class _UserProjectAccessRow(_AuthBase):
        __tablename__ = "nexus_user_project_access"
        __table_args__ = (
            sa.UniqueConstraint("nexus_id", "project_key", name="uq_nexus_user_project_access"),
        )

        id: sa.orm.Mapped[int] = sa.orm.mapped_column(sa.Integer, primary_key=True, autoincrement=True)
        nexus_id: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(64), nullable=False, index=True)
        project_key: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128), nullable=False, index=True)
        granted_via: sa.orm.Mapped[str] = sa.orm.mapped_column(
            sa.String(64), nullable=False, default="github_team"
        )
        source_team_slug: sa.orm.Mapped[str | None] = sa.orm.mapped_column(sa.String(255))
        granted_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True), nullable=False, default=_now_utc
        )
        synced_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True), nullable=False, default=_now_utc
        )

    class _IssueRequesterBindingRow(_AuthBase):
        __tablename__ = "nexus_issue_requester_binding"
        __table_args__ = (
            sa.UniqueConstraint("repo_key", "issue_number", name="uq_issue_repo_issue_number"),
            sa.UniqueConstraint("issue_url", name="uq_issue_requester_issue_url"),
        )

        id: sa.orm.Mapped[int] = sa.orm.mapped_column(sa.Integer, primary_key=True, autoincrement=True)
        repo_key: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(255), nullable=False, index=True)
        issue_number: sa.orm.Mapped[int] = sa.orm.mapped_column(sa.Integer, nullable=False, index=True)
        issue_url: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(512), nullable=False)
        project_key: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128), nullable=False, index=True)
        requester_nexus_id: sa.orm.Mapped[str] = sa.orm.mapped_column(
            sa.String(64), nullable=False, index=True
        )
        created_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True), nullable=False, default=_now_utc
        )
        updated_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True), nullable=False, default=_now_utc
        )


@dataclass
class CredentialRecord:
    nexus_id: str
    auth_provider: str | None
    github_user_id: int | None
    github_login: str | None
    github_token_enc: str | None
    github_refresh_token_enc: str | None
    github_token_expires_at: datetime | None
    gitlab_user_id: int | None
    gitlab_username: str | None
    gitlab_token_enc: str | None
    gitlab_refresh_token_enc: str | None
    gitlab_token_expires_at: datetime | None
    codex_api_key_enc: str | None
    gemini_api_key_enc: str | None
    claude_api_key_enc: str | None
    copilot_github_token_enc: str | None
    org_verified: bool
    org_verified_at: datetime | None
    last_access_sync_at: datetime | None
    key_version: int


@dataclass
class AuthSessionRecord:
    session_id: str
    nexus_id: str
    discord_user_id: str
    discord_username: str | None
    oauth_provider: str | None
    oauth_state_hash: str | None
    status: str
    expires_at: datetime
    last_error: str | None
    used_at: datetime | None


@dataclass
class ProjectGrant:
    project_key: str
    granted_via: str
    source_team_slug: str | None
    granted_at: datetime
    synced_at: datetime


_ENGINE: Any | None = None


def _run_schema_migrations(engine: Any) -> None:
    """Run additive migrations and safe backfills for existing Postgres schemas."""
    # noinspection SqlNoDataSourceInspection
    statements = [
        # Auth sessions
        "ALTER TABLE IF EXISTS nexus_auth_sessions ADD COLUMN IF NOT EXISTS oauth_provider VARCHAR(32)",
        "ALTER TABLE IF EXISTS nexus_auth_sessions ADD COLUMN IF NOT EXISTS oauth_state_hash VARCHAR(128)",
        "CREATE INDEX IF NOT EXISTS ix_nexus_auth_sessions_oauth_state_hash ON nexus_auth_sessions (oauth_state_hash)",
        # User credentials
        "ALTER TABLE IF EXISTS nexus_user_credentials ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(32)",
        "ALTER TABLE IF EXISTS nexus_user_credentials ADD COLUMN IF NOT EXISTS gitlab_user_id BIGINT",
        "ALTER TABLE IF EXISTS nexus_user_credentials ADD COLUMN IF NOT EXISTS gitlab_username VARCHAR(255)",
        "ALTER TABLE IF EXISTS nexus_user_credentials ADD COLUMN IF NOT EXISTS gitlab_token_enc TEXT",
        "ALTER TABLE IF EXISTS nexus_user_credentials ADD COLUMN IF NOT EXISTS gitlab_refresh_token_enc TEXT",
        "ALTER TABLE IF EXISTS nexus_user_credentials ADD COLUMN IF NOT EXISTS gitlab_token_expires_at TIMESTAMPTZ",
        "ALTER TABLE IF EXISTS nexus_user_credentials ADD COLUMN IF NOT EXISTS codex_api_key_enc TEXT",
        "ALTER TABLE IF EXISTS nexus_user_credentials ADD COLUMN IF NOT EXISTS gemini_api_key_enc TEXT",
        "ALTER TABLE IF EXISTS nexus_user_credentials ADD COLUMN IF NOT EXISTS claude_api_key_enc TEXT",
        "ALTER TABLE IF EXISTS nexus_user_credentials ADD COLUMN IF NOT EXISTS copilot_github_token_enc TEXT",
        "ALTER TABLE IF EXISTS nexus_user_credentials ADD COLUMN IF NOT EXISTS last_access_sync_at TIMESTAMPTZ",
        "ALTER TABLE IF EXISTS nexus_user_credentials ADD COLUMN IF NOT EXISTS key_version INTEGER",
        "UPDATE nexus_user_credentials SET key_version = 1 WHERE key_version IS NULL OR key_version < 1",
        (
            "UPDATE nexus_user_credentials "
            "SET auth_provider = 'gitlab' "
            "WHERE (auth_provider IS NULL OR auth_provider = '') "
            "AND gitlab_token_enc IS NOT NULL"
        ),
        (
            "UPDATE nexus_user_credentials "
            "SET auth_provider = 'github' "
            "WHERE (auth_provider IS NULL OR auth_provider = '') "
            "AND github_token_enc IS NOT NULL"
        ),
        # Project access
        "ALTER TABLE IF EXISTS nexus_user_project_access ADD COLUMN IF NOT EXISTS granted_via VARCHAR(64)",
        "ALTER TABLE IF EXISTS nexus_user_project_access ADD COLUMN IF NOT EXISTS source_team_slug VARCHAR(255)",
        "ALTER TABLE IF EXISTS nexus_user_project_access ADD COLUMN IF NOT EXISTS granted_at TIMESTAMPTZ",
        "ALTER TABLE IF EXISTS nexus_user_project_access ADD COLUMN IF NOT EXISTS synced_at TIMESTAMPTZ",
        (
            "UPDATE nexus_user_project_access "
            "SET granted_via = 'github_team' "
            "WHERE granted_via IS NULL OR granted_via = ''"
        ),
        "UPDATE nexus_user_project_access SET granted_at = NOW() WHERE granted_at IS NULL",
        "UPDATE nexus_user_project_access SET synced_at = NOW() WHERE synced_at IS NULL",
        # Issue-requester binding
        "ALTER TABLE IF EXISTS nexus_issue_requester_binding ADD COLUMN IF NOT EXISTS project_key VARCHAR(128)",
        "UPDATE nexus_issue_requester_binding SET project_key = 'unknown' WHERE project_key IS NULL OR project_key = ''",
        "CREATE INDEX IF NOT EXISTS ix_issue_requester_binding_project_key ON nexus_issue_requester_binding (project_key)",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(sa.text(statement))


def _get_engine():
    global _ENGINE
    _require_sqlalchemy()
    if _ENGINE is not None:
        return _ENGINE

    from nexus.core.config import NEXUS_STORAGE_DSN

    dsn = _normalize_dsn(NEXUS_STORAGE_DSN)
    if not dsn:
        raise ValueError("NEXUS_STORAGE_DSN is required for auth storage")
    _ENGINE = sa.create_engine(dsn, pool_size=5)
    _AuthBase.metadata.create_all(_ENGINE)
    _run_schema_migrations(_ENGINE)
    return _ENGINE


def ensure_schema() -> None:
    _get_engine()


def dispose_engine() -> None:
    global _ENGINE
    engine: Any | None = _ENGINE
    _ENGINE = None
    if engine is None:
        return
    try:
        engine.dispose()
    except Exception:
        pass


atexit.register(dispose_engine)


def _row_to_session(row: Any) -> AuthSessionRecord:
    return AuthSessionRecord(
        session_id=str(row.session_id),
        nexus_id=str(row.nexus_id),
        discord_user_id=str(row.discord_user_id),
        discord_username=row.discord_username,
        oauth_provider=row.oauth_provider,
        oauth_state_hash=row.oauth_state_hash,
        status=str(row.status),
        expires_at=row.expires_at,
        last_error=row.last_error,
        used_at=row.used_at,
    )


def _row_to_credential(row: Any) -> CredentialRecord:
    return CredentialRecord(
        nexus_id=str(row.nexus_id),
        auth_provider=row.auth_provider,
        github_user_id=int(row.github_user_id) if row.github_user_id is not None else None,
        github_login=row.github_login,
        github_token_enc=row.github_token_enc,
        github_refresh_token_enc=row.github_refresh_token_enc,
        github_token_expires_at=row.github_token_expires_at,
        gitlab_user_id=int(row.gitlab_user_id) if row.gitlab_user_id is not None else None,
        gitlab_username=row.gitlab_username,
        gitlab_token_enc=row.gitlab_token_enc,
        gitlab_refresh_token_enc=row.gitlab_refresh_token_enc,
        gitlab_token_expires_at=row.gitlab_token_expires_at,
        codex_api_key_enc=row.codex_api_key_enc,
        gemini_api_key_enc=row.gemini_api_key_enc,
        claude_api_key_enc=row.claude_api_key_enc,
        copilot_github_token_enc=row.copilot_github_token_enc,
        org_verified=bool(row.org_verified),
        org_verified_at=row.org_verified_at,
        last_access_sync_at=row.last_access_sync_at,
        key_version=int(row.key_version or 1),
    )


def create_auth_session(
    *,
    nexus_id: str,
    discord_user_id: str,
    discord_username: str | None,
    ttl_seconds: int,
) -> str:
    engine = _get_engine()
    session_id = uuid.uuid4().hex
    now = _now_utc()
    expires_at = now + timedelta(seconds=max(60, int(ttl_seconds)))
    with Session(engine) as session:
        row = _AuthSessionRow(
            session_id=session_id,
            nexus_id=str(nexus_id),
            discord_user_id=str(discord_user_id),
            discord_username=str(discord_username or "").strip() or None,
            status="pending",
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
    return session_id


def get_auth_session(session_id: str) -> AuthSessionRecord | None:
    engine = _get_engine()
    with Session(engine) as session:
        row = session.get(_AuthSessionRow, str(session_id))
        return _row_to_session(row) if row else None


def update_auth_session(
    *,
    session_id: str,
    nexus_id: str | None = None,
    oauth_provider: str | None = None,
    oauth_state_hash: str | None = None,
    status: str | None = None,
    last_error: str | None = None,
    used_at: datetime | None = None,
) -> None:
    engine = _get_engine()
    with Session(engine) as session:
        row = session.get(_AuthSessionRow, str(session_id))
        if not row:
            return
        if nexus_id is not None:
            row.nexus_id = str(nexus_id or "").strip() or row.nexus_id
        if oauth_provider is not None:
            row.oauth_provider = str(oauth_provider or "").strip().lower() or None
        if oauth_state_hash is not None:
            row.oauth_state_hash = str(oauth_state_hash or "").strip() or None
        if status is not None:
            row.status = str(status or "").strip() or row.status
        if last_error is not None:
            row.last_error = str(last_error or "").strip() or None
        if used_at is not None:
            row.used_at = used_at
        row.updated_at = _now_utc()
        session.commit()


def get_auth_session_by_state(state: str) -> AuthSessionRecord | None:
    engine = _get_engine()
    state_hash = hash_oauth_state(state)
    with Session(engine) as session:
        row = (
            session.query(_AuthSessionRow)
            .filter(_AuthSessionRow.oauth_state_hash == state_hash)
            .order_by(_AuthSessionRow.updated_at.desc())
            .first()
        )
        return _row_to_session(row) if row else None


def cleanup_expired_auth_sessions() -> int:
    engine = _get_engine()
    now = _now_utc()
    with Session(engine) as session:
        rows = session.query(_AuthSessionRow).filter(_AuthSessionRow.expires_at < now).all()
        count = len(rows)
        for row in rows:
            session.delete(row)
        session.commit()
        return count


def upsert_github_credentials(
    *,
    nexus_id: str,
    github_user_id: int,
    github_login: str,
    github_token_enc: str,
    github_refresh_token_enc: str | None = None,
    github_token_expires_at: datetime | None = None,
    org_verified: bool = False,
    org_verified_at: datetime | None = None,
    key_version: int = 1,
) -> None:
    engine = _get_engine()
    with Session(engine) as session:
        row = session.get(_UserCredentialRow, str(nexus_id))
        now = _now_utc()
        if not row:
            row = _UserCredentialRow(
                nexus_id=str(nexus_id),
                created_at=now,
                updated_at=now,
            )
            session.add(row)

        row.github_user_id = int(github_user_id)
        row.github_login = str(github_login or "").strip() or None
        row.github_token_enc = str(github_token_enc or "").strip() or None
        row.github_refresh_token_enc = str(github_refresh_token_enc or "").strip() or None
        row.github_token_expires_at = github_token_expires_at
        row.auth_provider = "github"
        row.org_verified = bool(org_verified)
        row.org_verified_at = org_verified_at or (_now_utc() if org_verified else None)
        row.key_version = max(1, int(key_version or 1))
        row.updated_at = now
        session.commit()


def upsert_gitlab_credentials(
    *,
    nexus_id: str,
    gitlab_user_id: int,
    gitlab_username: str,
    gitlab_token_enc: str,
    gitlab_refresh_token_enc: str | None = None,
    gitlab_token_expires_at: datetime | None = None,
    org_verified: bool = False,
    org_verified_at: datetime | None = None,
    key_version: int = 1,
) -> None:
    engine = _get_engine()
    with Session(engine) as session:
        row = session.get(_UserCredentialRow, str(nexus_id))
        now = _now_utc()
        if not row:
            row = _UserCredentialRow(
                nexus_id=str(nexus_id),
                created_at=now,
                updated_at=now,
            )
            session.add(row)

        row.gitlab_user_id = int(gitlab_user_id)
        row.gitlab_username = str(gitlab_username or "").strip() or None
        row.gitlab_token_enc = str(gitlab_token_enc or "").strip() or None
        row.gitlab_refresh_token_enc = str(gitlab_refresh_token_enc or "").strip() or None
        row.gitlab_token_expires_at = gitlab_token_expires_at
        row.auth_provider = "gitlab"
        row.org_verified = bool(org_verified)
        row.org_verified_at = org_verified_at or (_now_utc() if org_verified else None)
        row.key_version = max(1, int(key_version or 1))
        row.updated_at = now
        session.commit()


def upsert_ai_provider_keys(
    *,
    nexus_id: str,
    codex_api_key_enc: str | None = None,
    gemini_api_key_enc: str | None = None,
    claude_api_key_enc: str | None = None,
    copilot_github_token_enc: str | None = None,
    key_version: int = 1,
) -> None:
    engine = _get_engine()
    with Session(engine) as session:
        row = session.get(_UserCredentialRow, str(nexus_id))
        now = _now_utc()
        if not row:
            row = _UserCredentialRow(
                nexus_id=str(nexus_id),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        if codex_api_key_enc is not None:
            row.codex_api_key_enc = str(codex_api_key_enc or "").strip() or None
        if gemini_api_key_enc is not None:
            row.gemini_api_key_enc = str(gemini_api_key_enc or "").strip() or None
        if claude_api_key_enc is not None:
            row.claude_api_key_enc = str(claude_api_key_enc or "").strip() or None
        if copilot_github_token_enc is not None:
            row.copilot_github_token_enc = str(copilot_github_token_enc or "").strip() or None
        row.key_version = max(1, int(key_version or 1))
        row.updated_at = now
        session.commit()


def upsert_codex_key(*, nexus_id: str, codex_api_key_enc: str, key_version: int = 1) -> None:
    upsert_ai_provider_keys(
        nexus_id=nexus_id,
        codex_api_key_enc=codex_api_key_enc,
        key_version=key_version,
    )


def update_gitlab_oauth_tokens(
    *,
    nexus_id: str,
    gitlab_token_enc: str,
    gitlab_refresh_token_enc: str | None = None,
    gitlab_token_expires_at: datetime | None = None,
    key_version: int | None = None,
) -> None:
    """Update only GitLab OAuth token fields for an existing credential row."""
    engine = _get_engine()
    with Session(engine) as session:
        row = session.get(_UserCredentialRow, str(nexus_id))
        if not row:
            return
        now = _now_utc()
        row.gitlab_token_enc = str(gitlab_token_enc or "").strip() or None
        if gitlab_refresh_token_enc is not None:
            row.gitlab_refresh_token_enc = str(gitlab_refresh_token_enc or "").strip() or None
        row.gitlab_token_expires_at = gitlab_token_expires_at
        if key_version is not None:
            row.key_version = max(1, int(key_version or 1))
        row.updated_at = now
        session.commit()


def get_user_credentials(nexus_id: str) -> CredentialRecord | None:
    engine = _get_engine()
    with Session(engine) as session:
        row = session.get(_UserCredentialRow, str(nexus_id))
        return _row_to_credential(row) if row else None


def find_user_credentials_by_github_identity(
    *,
    github_user_id: int | None = None,
    github_login: str | None = None,
) -> CredentialRecord | None:
    engine = _get_engine()
    login = str(github_login or "").strip().lower()
    user_id = int(github_user_id or 0)
    if user_id <= 0 and not login:
        return None
    with Session(engine) as session:
        query = session.query(_UserCredentialRow)
        if user_id > 0 and login:
            row = (
                query.filter(
                    sa.or_(
                        _UserCredentialRow.github_user_id == user_id,
                        sa.func.lower(_UserCredentialRow.github_login) == login,
                    )
                )
                .order_by(_UserCredentialRow.updated_at.desc())
                .first()
            )
        elif user_id > 0:
            row = (
                query.filter(_UserCredentialRow.github_user_id == user_id)
                .order_by(_UserCredentialRow.updated_at.desc())
                .first()
            )
        else:
            row = (
                query.filter(sa.func.lower(_UserCredentialRow.github_login) == login)
                .order_by(_UserCredentialRow.updated_at.desc())
                .first()
            )
        return _row_to_credential(row) if row else None


def find_user_credentials_by_gitlab_identity(
    *,
    gitlab_user_id: int | None = None,
    gitlab_username: str | None = None,
) -> CredentialRecord | None:
    engine = _get_engine()
    username = str(gitlab_username or "").strip().lower()
    user_id = int(gitlab_user_id or 0)
    if user_id <= 0 and not username:
        return None
    with Session(engine) as session:
        query = session.query(_UserCredentialRow)
        if user_id > 0 and username:
            row = (
                query.filter(
                    sa.or_(
                        _UserCredentialRow.gitlab_user_id == user_id,
                        sa.func.lower(_UserCredentialRow.gitlab_username) == username,
                    )
                )
                .order_by(_UserCredentialRow.updated_at.desc())
                .first()
            )
        elif user_id > 0:
            row = (
                query.filter(_UserCredentialRow.gitlab_user_id == user_id)
                .order_by(_UserCredentialRow.updated_at.desc())
                .first()
            )
        else:
            row = (
                query.filter(sa.func.lower(_UserCredentialRow.gitlab_username) == username)
                .order_by(_UserCredentialRow.updated_at.desc())
                .first()
            )
        return _row_to_credential(row) if row else None


def mark_access_sync(nexus_id: str, *, at: datetime | None = None) -> None:
    engine = _get_engine()
    with Session(engine) as session:
        row = session.get(_UserCredentialRow, str(nexus_id))
        if not row:
            return
        now = at or _now_utc()
        row.last_access_sync_at = now
        row.updated_at = now
        session.commit()


def list_credentials_for_sync(limit: int = 200) -> list[CredentialRecord]:
    engine = _get_engine()
    safe_limit = max(1, int(limit))
    with Session(engine) as session:
        rows = (
            session.query(_UserCredentialRow)
            .filter(_UserCredentialRow.org_verified.is_(True))
            .order_by(_UserCredentialRow.updated_at.asc())
            .limit(safe_limit)
            .all()
        )
        return [_row_to_credential(row) for row in rows]


def replace_user_project_access(
    *,
    nexus_id: str,
    grants: list[tuple[str, str]],
    granted_via: str = "github_team",
    replace_all: bool = True,
) -> int:
    """Replace all grants for a user with fresh sync results.

    Args:
        grants: list of (project_key, source_team_slug)
    """
    engine = _get_engine()
    now = _now_utc()
    seen: set[str] = set()
    normalized: list[tuple[str, str]] = []
    for project_key, team_slug in grants:
        project = str(project_key or "").strip().lower()
        team = str(team_slug or "").strip().lower()
        if not project:
            continue
        key = f"{project}:{team}"
        if key in seen:
            continue
        seen.add(key)
        normalized.append((project, team))

    with Session(engine) as session:
        existing = session.query(_UserProjectAccessRow).filter(_UserProjectAccessRow.nexus_id == str(nexus_id))
        if not replace_all:
            existing = existing.filter(_UserProjectAccessRow.granted_via == str(granted_via or "").strip())
        existing.delete(synchronize_session=False)
        for project_key, team_slug in normalized:
            session.add(
                _UserProjectAccessRow(
                    nexus_id=str(nexus_id),
                    project_key=project_key,
                    granted_via=str(granted_via or "github_team"),
                    source_team_slug=team_slug or None,
                    granted_at=now,
                    synced_at=now,
                )
            )
        session.commit()
    mark_access_sync(str(nexus_id), at=now)
    return len(normalized)


def get_user_project_access(nexus_id: str) -> list[ProjectGrant]:
    engine = _get_engine()
    with Session(engine) as session:
        rows = (
            session.query(_UserProjectAccessRow)
            .filter(_UserProjectAccessRow.nexus_id == str(nexus_id))
            .order_by(_UserProjectAccessRow.project_key.asc())
            .all()
        )
        return [
            ProjectGrant(
                project_key=str(row.project_key),
                granted_via=str(row.granted_via),
                source_team_slug=(str(row.source_team_slug) if row.source_team_slug else None),
                granted_at=(
                    row.granted_at
                    if isinstance(row.granted_at, datetime)
                    else _now_utc()
                ),
                synced_at=(
                    row.synced_at
                    if isinstance(row.synced_at, datetime)
                    else _now_utc()
                ),
            )
            for row in rows
        ]


def has_user_project_access(nexus_id: str, project_key: str) -> bool:
    engine = _get_engine()
    with Session(engine) as session:
        row = (
            session.query(_UserProjectAccessRow.id)
            .filter(_UserProjectAccessRow.nexus_id == str(nexus_id))
            .filter(_UserProjectAccessRow.project_key == str(project_key).strip().lower())
            .first()
        )
        return bool(row)


def bind_issue_requester(
    *,
    repo_key: str,
    issue_number: int,
    issue_url: str,
    project_key: str,
    requester_nexus_id: str,
) -> None:
    engine = _get_engine()
    repo = str(repo_key or "").strip()
    issue = int(issue_number)
    url = str(issue_url or "").strip()
    project = str(project_key or "").strip().lower()
    requester = str(requester_nexus_id or "").strip()
    if not (repo and issue and url and project and requester):
        return

    with Session(engine) as session:
        row = (
            session.query(_IssueRequesterBindingRow)
            .filter(_IssueRequesterBindingRow.repo_key == repo)
            .filter(_IssueRequesterBindingRow.issue_number == issue)
            .first()
        )
        now = _now_utc()
        if not row:
            row = _IssueRequesterBindingRow(
                repo_key=repo,
                issue_number=issue,
                issue_url=url,
                project_key=project,
                requester_nexus_id=requester,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        else:
            row.issue_url = url
            row.project_key = project
            row.requester_nexus_id = requester
            row.updated_at = now
        session.commit()


def get_issue_requester_by_url(issue_url: str) -> str | None:
    engine = _get_engine()
    url = str(issue_url or "").strip()
    if not url:
        return None
    with Session(engine) as session:
        row = (
            session.query(_IssueRequesterBindingRow.requester_nexus_id)
            .filter(_IssueRequesterBindingRow.issue_url == url)
            .first()
        )
        return str(row[0]) if row and row[0] else None


def get_issue_requester(repo_key: str, issue_number: int | str) -> str | None:
    engine = _get_engine()
    repo = str(repo_key or "").strip()
    try:
        issue = int(str(issue_number or "").strip())
    except (TypeError, ValueError):
        return None
    if not repo:
        return None
    with Session(engine) as session:
        row = (
            session.query(_IssueRequesterBindingRow.requester_nexus_id)
            .filter(_IssueRequesterBindingRow.repo_key == repo)
            .filter(_IssueRequesterBindingRow.issue_number == issue)
            .first()
        )
        return str(row[0]) if row and row[0] else None
