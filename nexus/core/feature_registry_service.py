from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

from nexus.core.config import (
    NEXUS_FEATURE_REGISTRY_DEDUP_SIMILARITY,
    NEXUS_FEATURE_REGISTRY_ENABLED,
    NEXUS_FEATURE_REGISTRY_MAX_ITEMS_PER_PROJECT,
    NEXUS_STATE_DIR,
    NEXUS_STORAGE_BACKEND,
    NEXUS_STORAGE_DSN,
)

try:
    import sqlalchemy as sa
    from sqlalchemy.orm import DeclarativeBase, Session

    _SA_AVAILABLE = True
except ImportError:
    sa = None  # type: ignore[assignment]
    DeclarativeBase = object  # type: ignore[assignment]
    Session = None  # type: ignore[assignment]
    _SA_AVAILABLE = False


logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def normalize_feature_title(value: str) -> str:
    lowered = str(value or "").strip().lower()
    lowered = re.sub(r"[^a-z0-9\s]+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def canonical_title_hash(value: str) -> str:
    normalized = normalize_feature_title(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass
class FeatureRegistryRecord:
    project_key: str
    feature_id: str
    canonical_title: str
    aliases: list[str]
    source_issue: str
    source_pr: str
    status: str
    implemented_at: str
    updated_at: str
    canonical_title_hash: str
    manual_override: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_key": self.project_key,
            "feature_id": self.feature_id,
            "canonical_title": self.canonical_title,
            "aliases": list(self.aliases),
            "source_issue": self.source_issue,
            "source_pr": self.source_pr,
            "status": self.status,
            "implemented_at": self.implemented_at,
            "updated_at": self.updated_at,
            "canonical_title_hash": self.canonical_title_hash,
            "manual_override": bool(self.manual_override),
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "FeatureRegistryRecord":
        aliases = payload.get("aliases")
        if not isinstance(aliases, list):
            aliases = []
        return FeatureRegistryRecord(
            project_key=str(payload.get("project_key") or "").strip(),
            feature_id=str(payload.get("feature_id") or "").strip(),
            canonical_title=str(payload.get("canonical_title") or "").strip(),
            aliases=[str(item).strip() for item in aliases if str(item).strip()],
            source_issue=str(payload.get("source_issue") or "").strip(),
            source_pr=str(payload.get("source_pr") or "").strip(),
            status=str(payload.get("status") or "implemented").strip() or "implemented",
            implemented_at=str(payload.get("implemented_at") or "").strip() or _utc_now(),
            updated_at=str(payload.get("updated_at") or "").strip() or _utc_now(),
            canonical_title_hash=str(payload.get("canonical_title_hash") or "").strip()
            or canonical_title_hash(str(payload.get("canonical_title") or "")),
            manual_override=bool(payload.get("manual_override", False)),
        )


class _FilesystemFeatureRegistryStore:
    def __init__(self, path: str) -> None:
        self._path = path
        os.makedirs(os.path.dirname(self._path), exist_ok=True)

    def list_records(self, project_key: str) -> list[FeatureRegistryRecord]:
        data = self._load()
        project = data.get("projects", {}).get(project_key, {})
        items = project.get("features", []) if isinstance(project, dict) else []
        if not isinstance(items, list):
            return []
        records = [
            FeatureRegistryRecord.from_dict(item) for item in items if isinstance(item, dict)
        ]
        records.sort(key=lambda item: (item.implemented_at, item.feature_id), reverse=True)
        return records

    def upsert_record(self, record: FeatureRegistryRecord, max_items: int) -> FeatureRegistryRecord:
        data = self._load()
        projects = data.setdefault("projects", {})
        project = projects.setdefault(record.project_key, {})
        items = project.setdefault("features", [])
        if not isinstance(items, list):
            items = []
            project["features"] = items

        existing_idx = -1
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if str(item.get("canonical_title_hash") or "") == record.canonical_title_hash:
                existing_idx = idx
                break

        if existing_idx >= 0:
            existing = FeatureRegistryRecord.from_dict(items[existing_idx])
            if existing.manual_override and not record.manual_override:
                return existing
            record.implemented_at = existing.implemented_at
            record.feature_id = existing.feature_id or record.feature_id
            items[existing_idx] = record.to_dict()
        else:
            items.append(record.to_dict())

        if len(items) > max_items:
            parsed = [
                FeatureRegistryRecord.from_dict(item) for item in items if isinstance(item, dict)
            ]
            parsed.sort(key=lambda item: (item.updated_at, item.feature_id), reverse=True)
            items = [item.to_dict() for item in parsed[:max_items]]
            project["features"] = items

        project["updated_at"] = _utc_now()
        self._save(data)
        return record

    def delete_record(self, project_key: str, feature_ref: str) -> FeatureRegistryRecord | None:
        data = self._load()
        project = data.get("projects", {}).get(project_key, {})
        items = project.get("features", []) if isinstance(project, dict) else []
        if not isinstance(items, list):
            return None

        normalized_ref = normalize_feature_title(feature_ref)
        removed: FeatureRegistryRecord | None = None
        kept: list[dict[str, Any]] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            candidate = FeatureRegistryRecord.from_dict(item)
            aliases = [normalize_feature_title(alias) for alias in candidate.aliases]
            matches = (
                candidate.feature_id == str(feature_ref).strip()
                or normalize_feature_title(candidate.canonical_title) == normalized_ref
                or normalized_ref in aliases
            )
            if removed is None and matches:
                removed = candidate
                continue
            kept.append(item)

        if removed is None:
            return None

        project["features"] = kept
        project["updated_at"] = _utc_now()
        self._save(data)
        return removed

    def _load(self) -> dict[str, Any]:
        if not os.path.exists(self._path):
            return {"version": 1, "projects": {}}
        try:
            with open(self._path, encoding="utf-8") as fh:
                payload = json.load(fh)
            if isinstance(payload, dict):
                payload.setdefault("version", 1)
                payload.setdefault("projects", {})
                return payload
        except Exception as exc:
            logger.warning("Failed to read feature registry file %s: %s", self._path, exc)
        return {"version": 1, "projects": {}}

    def _save(self, data: dict[str, Any]) -> None:
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)


if _SA_AVAILABLE:

    class _FeatureRegistryBase(DeclarativeBase):
        pass

    class _FeatureRegistryRow(_FeatureRegistryBase):
        __tablename__ = "nexus_feature_registry"
        __table_args__ = (sa.UniqueConstraint("project_key", "canonical_title_hash"),)

        id: sa.orm.Mapped[int] = sa.orm.mapped_column(
            sa.Integer, primary_key=True, autoincrement=True
        )
        project_key: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128), nullable=False)
        feature_id: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(128), nullable=False)
        canonical_title: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.String(512), nullable=False)
        canonical_title_hash: sa.orm.Mapped[str] = sa.orm.mapped_column(
            sa.String(64), nullable=False
        )
        aliases: sa.orm.Mapped[str] = sa.orm.mapped_column(sa.Text, nullable=False, default="[]")
        source_issue: sa.orm.Mapped[str] = sa.orm.mapped_column(
            sa.String(128), nullable=False, default=""
        )
        source_pr: sa.orm.Mapped[str] = sa.orm.mapped_column(
            sa.String(512), nullable=False, default=""
        )
        status: sa.orm.Mapped[str] = sa.orm.mapped_column(
            sa.String(64), nullable=False, default="implemented"
        )
        implemented_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(tz=UTC)
        )
        updated_at: sa.orm.Mapped[datetime] = sa.orm.mapped_column(
            sa.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(tz=UTC)
        )
        manual_override: sa.orm.Mapped[bool] = sa.orm.mapped_column(
            sa.Boolean, nullable=False, default=False
        )


class _PostgresFeatureRegistryStore:
    def __init__(self, dsn: str) -> None:
        if not _SA_AVAILABLE:
            raise RuntimeError("sqlalchemy is not available")
        normalized = str(dsn or "").strip()
        if normalized.startswith("postgresql://"):
            normalized = normalized.replace("postgresql://", "postgresql+psycopg2://", 1)
        if normalized.startswith("postgres://"):
            normalized = normalized.replace("postgres://", "postgresql+psycopg2://", 1)
        self._engine = sa.create_engine(normalized, pool_size=5)
        _FeatureRegistryBase.metadata.create_all(self._engine)

    def list_records(self, project_key: str) -> list[FeatureRegistryRecord]:
        with Session(self._engine) as session:
            rows = (
                session.query(_FeatureRegistryRow)
                .filter(_FeatureRegistryRow.project_key == project_key)
                .order_by(_FeatureRegistryRow.implemented_at.desc())
                .all()
            )
            return [self._row_to_record(row) for row in rows]

    def upsert_record(self, record: FeatureRegistryRecord, max_items: int) -> FeatureRegistryRecord:
        with Session(self._engine) as session:
            row = (
                session.query(_FeatureRegistryRow)
                .filter(
                    _FeatureRegistryRow.project_key == record.project_key,
                    _FeatureRegistryRow.canonical_title_hash == record.canonical_title_hash,
                )
                .first()
            )
            now = datetime.now(tz=UTC)

            if row:
                if bool(row.manual_override) and not record.manual_override:
                    return self._row_to_record(row)
                row.feature_id = record.feature_id or row.feature_id
                row.canonical_title = record.canonical_title
                row.aliases = json.dumps(record.aliases)
                row.source_issue = record.source_issue
                row.source_pr = record.source_pr
                row.status = record.status
                row.updated_at = now
                row.manual_override = bool(record.manual_override)
                row.implemented_at = row.implemented_at or now
            else:
                row = _FeatureRegistryRow(
                    project_key=record.project_key,
                    feature_id=record.feature_id,
                    canonical_title=record.canonical_title,
                    canonical_title_hash=record.canonical_title_hash,
                    aliases=json.dumps(record.aliases),
                    source_issue=record.source_issue,
                    source_pr=record.source_pr,
                    status=record.status,
                    implemented_at=_parse_timestamp(record.implemented_at) or now,
                    updated_at=now,
                    manual_override=bool(record.manual_override),
                )
                session.add(row)
            session.commit()

            extras = (
                session.query(_FeatureRegistryRow)
                .filter(_FeatureRegistryRow.project_key == record.project_key)
                .order_by(_FeatureRegistryRow.updated_at.desc())
                .offset(max_items)
                .all()
            )
            for extra in extras:
                session.delete(extra)
            if extras:
                session.commit()

            session.refresh(row)
            return self._row_to_record(row)

    def delete_record(self, project_key: str, feature_ref: str) -> FeatureRegistryRecord | None:
        with Session(self._engine) as session:
            rows = (
                session.query(_FeatureRegistryRow)
                .filter(_FeatureRegistryRow.project_key == project_key)
                .all()
            )
            normalized_ref = normalize_feature_title(feature_ref)
            for row in rows:
                aliases = self._safe_aliases(str(row.aliases or "[]"))
                feature_id = str(row.feature_id or "").strip()
                canonical_title = str(row.canonical_title or "")
                if (
                    feature_id == str(feature_ref).strip()
                    or normalize_feature_title(canonical_title) == normalized_ref
                    or normalized_ref in [normalize_feature_title(alias) for alias in aliases]
                ):
                    record = self._row_to_record(row)
                    session.delete(row)
                    session.commit()
                    return record
        return None

    @staticmethod
    def _safe_aliases(raw: str) -> list[str]:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:
            pass
        return []

    @classmethod
    def _row_to_record(cls, row: Any) -> FeatureRegistryRecord:
        return FeatureRegistryRecord(
            project_key=str(row.project_key),
            feature_id=str(row.feature_id),
            canonical_title=str(row.canonical_title),
            aliases=cls._safe_aliases(str(row.aliases or "[]")),
            source_issue=str(row.source_issue or ""),
            source_pr=str(row.source_pr or ""),
            status=str(row.status or "implemented"),
            implemented_at=(row.implemented_at or datetime.now(tz=UTC)).isoformat(),
            updated_at=(row.updated_at or datetime.now(tz=UTC)).isoformat(),
            canonical_title_hash=str(row.canonical_title_hash),
            manual_override=bool(row.manual_override),
        )

    def close(self) -> None:
        try:
            self._engine.dispose()
        except Exception:
            pass

    def __del__(self) -> None:  # pragma: no cover - defensive finalizer
        try:
            self.close()
        except Exception:
            pass


def _parse_timestamp(value: str) -> datetime | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        return datetime.fromisoformat(candidate)
    except Exception:
        return None


class FeatureRegistryService:
    def __init__(
        self,
        *,
        enabled: bool = NEXUS_FEATURE_REGISTRY_ENABLED,
        backend: str = NEXUS_STORAGE_BACKEND,
        state_dir: str = NEXUS_STATE_DIR,
        postgres_dsn: str = NEXUS_STORAGE_DSN,
        max_items_per_project: int = NEXUS_FEATURE_REGISTRY_MAX_ITEMS_PER_PROJECT,
        dedup_similarity: float = NEXUS_FEATURE_REGISTRY_DEDUP_SIMILARITY,
    ) -> None:
        self.enabled = bool(enabled)
        self.backend = str(backend or "filesystem").strip().lower()
        self.max_items_per_project = max(10, int(max_items_per_project))
        self.dedup_similarity = min(1.0, max(0.0, float(dedup_similarity)))

        fs_store = _FilesystemFeatureRegistryStore(
            os.path.join(str(state_dir), "feature_registry.json")
        )
        self._store: Any = fs_store

        if self.backend == "postgres":
            if postgres_dsn and _SA_AVAILABLE:
                try:
                    self._store = _PostgresFeatureRegistryStore(postgres_dsn)
                except Exception as exc:
                    logger.warning(
                        "Postgres feature registry unavailable, falling back to filesystem: %s", exc
                    )
            else:
                logger.warning(
                    "Feature registry configured for postgres but sqlalchemy/dsn missing; using filesystem"
                )

    def is_enabled(self) -> bool:
        return self.enabled

    def close(self) -> None:
        close_fn = getattr(self._store, "close", None)
        if callable(close_fn):
            try:
                close_fn()
            except Exception:
                pass

    def __del__(self) -> None:  # pragma: no cover - defensive finalizer
        try:
            self.close()
        except Exception:
            pass

    def list_features(self, project_key: str) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        key = str(project_key or "").strip().lower()
        if not key:
            return []
        return [record.to_dict() for record in self._store.list_records(key)]

    def upsert_feature(
        self,
        *,
        project_key: str,
        canonical_title: str,
        aliases: list[str] | None = None,
        source_issue: str = "",
        source_pr: str = "",
        feature_id: str = "",
        manual_override: bool = False,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        normalized_project = str(project_key or "").strip().lower()
        title = str(canonical_title or "").strip()
        if not normalized_project or not title:
            return None

        now = _utc_now()
        dedup_hash = canonical_title_hash(title)
        merged_aliases = self._merge_aliases(title, aliases or [])
        record = FeatureRegistryRecord(
            project_key=normalized_project,
            feature_id=(str(feature_id).strip() or f"feat_{dedup_hash[:12]}"),
            canonical_title=title,
            aliases=merged_aliases,
            source_issue=str(source_issue or "").strip(),
            source_pr=str(source_pr or "").strip(),
            status="implemented",
            implemented_at=now,
            updated_at=now,
            canonical_title_hash=dedup_hash,
            manual_override=bool(manual_override),
        )
        saved = self._store.upsert_record(record, self.max_items_per_project)
        return saved.to_dict()

    def forget_feature(self, *, project_key: str, feature_ref: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        normalized_project = str(project_key or "").strip().lower()
        ref = str(feature_ref or "").strip()
        if not normalized_project or not ref:
            return None
        removed = self._store.delete_record(normalized_project, ref)
        return removed.to_dict() if removed else None

    def list_excluded_titles(self, project_key: str) -> list[str]:
        records = self.list_features(project_key)
        return [
            str(item.get("canonical_title") or "")
            for item in records
            if item.get("canonical_title")
        ]

    def filter_ideation_items(
        self,
        *,
        project_key: str,
        items: list[dict[str, Any]],
        similarity_threshold: float | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not self.enabled:
            return items, []
        threshold = (
            self.dedup_similarity if similarity_threshold is None else float(similarity_threshold)
        )
        threshold = min(1.0, max(0.0, threshold))

        existing_records = self.list_features(project_key)
        normalized_existing: list[str] = []
        for record in existing_records:
            canonical = normalize_feature_title(str(record.get("canonical_title") or ""))
            if canonical:
                normalized_existing.append(canonical)
            aliases = record.get("aliases")
            if isinstance(aliases, list):
                for alias in aliases:
                    normalized_alias = normalize_feature_title(str(alias or ""))
                    if normalized_alias:
                        normalized_existing.append(normalized_alias)

        kept: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []

        for item in items:
            title = str(item.get("title") or "").strip()
            normalized_title = normalize_feature_title(title)
            if not normalized_title:
                removed.append(item)
                continue

            duplicate = False
            for existing in normalized_existing:
                if normalized_title == existing:
                    duplicate = True
                    break
                ratio = SequenceMatcher(None, normalized_title, existing).ratio()
                if ratio >= threshold:
                    duplicate = True
                    break

            if duplicate:
                removed.append(item)
            else:
                kept.append(item)

        return kept, removed

    def ingest_completion(
        self,
        *,
        project_key: str,
        issue_number: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        if not self._is_valid_completion_payload(payload):
            return None

        extracted = self._extract_feature_title(payload)
        if not extracted:
            return None

        return self.upsert_feature(
            project_key=project_key,
            canonical_title=extracted,
            aliases=[str(payload.get("summary") or "").strip()],
            source_issue=str(issue_number or "").strip(),
            source_pr=str(payload.get("source_pr") or "").strip(),
            manual_override=False,
        )

    @staticmethod
    def _is_valid_completion_payload(payload: dict[str, Any]) -> bool:
        if str(payload.get("status") or "").strip().lower() != "complete":
            return False

        agent_type = str(payload.get("agent_type") or "").strip().lower()
        next_agent = str(payload.get("next_agent") or "").strip().lower()
        key_findings = payload.get("key_findings")

        if not agent_type or not next_agent:
            return False
        if not isinstance(key_findings, list):
            return False
        return True

    @staticmethod
    def _extract_feature_title(payload: dict[str, Any]) -> str:
        for key in ("feature_title", "canonical_title", "title"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value

        findings = payload.get("key_findings")
        if isinstance(findings, list):
            for finding in findings:
                line = str(finding or "").strip()
                if not line:
                    continue
                match = re.match(r"^(feature|implemented|title)\s*:\s*(.+)$", line, re.IGNORECASE)
                if match:
                    return str(match.group(2)).strip()

        return ""

    @staticmethod
    def _merge_aliases(title: str, aliases: list[str]) -> list[str]:
        unique: list[str] = []
        for candidate in [title, *aliases]:
            value = str(candidate or "").strip()
            if not value:
                continue
            if value not in unique:
                unique.append(value)
        return unique
