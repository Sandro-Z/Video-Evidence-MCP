from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from redis import Redis

from video_evidence_mcp.schemas import JobPayload, JobStatus

CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")


def sanitize_diagnostic(value: str, limit: int = 1000) -> str:
    """Keep persisted diagnostics compact and safe for JSON/MCP clients."""
    return " ".join(CONTROL_CHARACTERS.sub(" ", value).split())[:limit]


def _sanitize_warnings(values: list[str]) -> list[str]:
    return [sanitize_diagnostic(value) for value in values[:50]]


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: str
    cache_key: str
    status: JobStatus
    progress: float
    stage: str
    payload: JobPayload
    analysis_id: str | None
    error_code: str | None
    error_message: str | None
    warnings: list[str]
    created_at: str
    updated_at: str


class JobStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    cache_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL,
                    stage TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    analysis_id TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_cache_key_idx
                    ON jobs(cache_key, status, updated_at DESC);
                CREATE TABLE IF NOT EXISTS analyses (
                    analysis_id TEXT PRIMARY KEY,
                    cache_key TEXT NOT NULL UNIQUE,
                    evidence_path TEXT NOT NULL,
                    contact_sheet_path TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS analyses_expiry_idx ON analyses(expires_at);
                """
            )

    def create_or_get(self, payload: JobPayload) -> tuple[JobRecord, bool]:
        now = utcnow_iso()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT j.* FROM jobs AS j
                WHERE j.cache_key = ? AND (
                    j.status IN ('queued', 'running') OR (
                        j.status = 'completed' AND EXISTS (
                            SELECT 1 FROM analyses AS a
                            WHERE a.analysis_id = j.analysis_id AND a.expires_at > ?
                        )
                    )
                )
                ORDER BY j.updated_at DESC LIMIT 1
                """,
                (payload.cache_key, now),
            ).fetchone()
            if existing is not None and not payload.force_refresh:
                return self._from_row(existing), False
            job_id = uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id, cache_key, status, progress, stage, payload_json,
                    warnings_json, created_at, updated_at
                ) VALUES (?, ?, 'queued', 0, 'queued', ?, '[]', ?, ?)
                """,
                (job_id, payload.cache_key, payload.model_dump_json(), now, now),
            )
        record = self.get(job_id)
        assert record is not None
        return record, True

    def get(self, job_id: str) -> JobRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._from_row(row) if row is not None else None

    def claim(self, job_id: str) -> JobRecord | None:
        now = utcnow_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status='running', stage='starting', progress=0.01, updated_at=?
                WHERE job_id=? AND status='queued'
                """,
                (now, job_id),
            )
            if cursor.rowcount != 1:
                return None
        return self.get(job_id)

    def update_progress(
        self, job_id: str, progress: float, stage: str, warnings: list[str] | None = None
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE jobs SET progress=?, stage=?, warnings_json=COALESCE(?, warnings_json), updated_at=?
                WHERE job_id=? AND status='running'
                """,
                (
                    min(max(progress, 0.0), 0.99),
                    stage,
                    json.dumps(_sanitize_warnings(warnings)) if warnings is not None else None,
                    utcnow_iso(),
                    job_id,
                ),
            )

    def complete(self, job_id: str, analysis_id: str, warnings: list[str]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE jobs SET status='completed', progress=1, stage='completed', analysis_id=?,
                    warnings_json=?, updated_at=? WHERE job_id=?
                """,
                (analysis_id, json.dumps(_sanitize_warnings(warnings)), utcnow_iso(), job_id),
            )

    def fail(self, job_id: str, error_code: str, error_message: str, warnings: list[str]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE jobs SET status='failed', stage='failed', error_code=?, error_message=?,
                    warnings_json=?, updated_at=? WHERE job_id=?
                """,
                (
                    error_code,
                    sanitize_diagnostic(error_message),
                    json.dumps(_sanitize_warnings(warnings)),
                    utcnow_iso(),
                    job_id,
                ),
            )

    def recover_interrupted(self) -> int:
        """Mark work interrupted by a prior worker shutdown as explicitly failed."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status='failed', stage='failed', error_code='processing_failed',
                    error_message='worker restarted while this job was running', updated_at=?
                WHERE status='running'
                """,
                (utcnow_iso(),),
            )
            return cursor.rowcount

    def queued_ids(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_id FROM jobs WHERE status='queued' ORDER BY created_at"
            ).fetchall()
        return [str(row["job_id"]) for row in rows]

    def register_analysis(
        self,
        analysis_id: str,
        cache_key: str,
        evidence_path: Path,
        contact_sheet_path: Path | None,
        created_at: datetime,
        expires_at: datetime,
        size_bytes: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO analyses (
                    analysis_id, cache_key, evidence_path, contact_sheet_path,
                    created_at, expires_at, size_bytes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    analysis_id=excluded.analysis_id,
                    evidence_path=excluded.evidence_path,
                    contact_sheet_path=excluded.contact_sheet_path,
                    created_at=excluded.created_at,
                    expires_at=excluded.expires_at,
                    size_bytes=excluded.size_bytes
                """,
                (
                    analysis_id,
                    cache_key,
                    str(evidence_path),
                    str(contact_sheet_path) if contact_sheet_path else None,
                    created_at.isoformat(),
                    expires_at.isoformat(),
                    size_bytes,
                ),
            )

    def analysis_paths(self, analysis_id: str) -> tuple[Path, Path | None] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT evidence_path, contact_sheet_path FROM analyses WHERE analysis_id=?",
                (analysis_id,),
            ).fetchone()
        if row is None:
            return None
        sheet = Path(row["contact_sheet_path"]) if row["contact_sheet_path"] else None
        return Path(row["evidence_path"]), sheet

    def analysis_paths_for_cache_key(self, cache_key: str) -> tuple[Path, Path | None] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT evidence_path, contact_sheet_path FROM analyses WHERE cache_key=?",
                (cache_key,),
            ).fetchone()
        if row is None:
            return None
        sheet = Path(row["contact_sheet_path"]) if row["contact_sheet_path"] else None
        return Path(row["evidence_path"]), sheet

    def analysis_rows(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM analyses ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]

    def delete_analysis(self, analysis_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM analyses WHERE analysis_id=?", (analysis_id,))

    @staticmethod
    def _from_row(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            job_id=str(row["job_id"]),
            cache_key=str(row["cache_key"]),
            status=JobStatus(str(row["status"])),
            progress=float(row["progress"]),
            stage=str(row["stage"]),
            payload=JobPayload.model_validate_json(row["payload_json"]),
            analysis_id=row["analysis_id"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            warnings=json.loads(row["warnings_json"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )


class RedisJobQueue:
    QUEUE_KEY = "video-evidence:jobs"

    def __init__(self, redis_url: str) -> None:
        self.client: Redis = Redis.from_url(redis_url, decode_responses=True)

    def ping(self) -> bool:
        return bool(self.client.ping())

    def enqueue(self, job_id: str) -> None:
        self.client.rpush(self.QUEUE_KEY, job_id)

    def dequeue(self, timeout: int = 5) -> str | None:
        item = cast(list[str] | None, self.client.blpop(self.QUEUE_KEY, timeout=timeout))
        return item[1] if item else None

    def ensure_queued(self, job_ids: list[str]) -> None:
        existing = set(cast(list[str], self.client.lrange(self.QUEUE_KEY, 0, -1)))
        missing = [job_id for job_id in job_ids if job_id not in existing]
        if missing:
            self.client.rpush(self.QUEUE_KEY, *missing)
