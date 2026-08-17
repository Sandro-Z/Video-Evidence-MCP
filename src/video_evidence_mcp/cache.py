from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from video_evidence_mcp.jobs import JobStore
from video_evidence_mcp.schemas import AnalysisEvidence


class EvidenceCache:
    def __init__(self, root: Path, store: JobStore, ttl_days: int, max_bytes: int) -> None:
        self.root = root
        self.store = store
        self.ttl_days = ttl_days
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        cache_key: str,
        evidence: AnalysisEvidence,
        contact_sheet_source: Path | None,
        frame_sources: list[tuple[Path, float]] | None = None,
    ) -> str:
        analysis_id = evidence.analysis_id or uuid.uuid4().hex
        previous = self.store.analysis_paths_for_cache_key(cache_key)
        target = self.root / analysis_id
        target.mkdir(parents=True, exist_ok=False)
        evidence_path = target / "evidence.json"
        evidence_path.write_text(evidence.model_dump_json(indent=2), encoding="utf-8")
        sheet_path: Path | None = None
        if contact_sheet_source and contact_sheet_source.is_file():
            sheet_path = target / "contact-sheet.webp"
            shutil.copy2(contact_sheet_source, sheet_path)
        if frame_sources:
            frames_dir = target / "frames"
            frames_dir.mkdir()
            for index, (source, timestamp) in enumerate(frame_sources):
                if source.is_file():
                    shutil.copy2(source, frames_dir / f"{index:03d}-{timestamp:.3f}.jpg")
        size = sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
        self.store.register_analysis(
            analysis_id,
            cache_key,
            evidence_path,
            sheet_path,
            evidence.cached_at,
            evidence.expires_at,
            size,
        )
        if previous is not None:
            previous_dir = previous[0].parent
            if previous_dir != target and previous_dir.parent == self.root:
                shutil.rmtree(previous_dir, ignore_errors=True)
        return analysis_id

    def frame_paths(self, analysis_id: str) -> list[tuple[Path, float]]:
        paths = self.store.analysis_paths(analysis_id)
        if paths is None:
            return []
        frames_dir = paths[0].parent / "frames"
        output: list[tuple[Path, float]] = []
        for path in sorted(frames_dir.glob("*.jpg")):
            try:
                output.append((path, float(path.stem.rsplit("-", 1)[1])))
            except (IndexError, ValueError):
                continue
        return output

    def load(self, analysis_id: str) -> tuple[AnalysisEvidence, Path | None] | None:
        paths = self.store.analysis_paths(analysis_id)
        if paths is None:
            return None
        evidence_path, sheet_path = paths
        if not evidence_path.is_file():
            return None
        evidence = AnalysisEvidence.model_validate_json(evidence_path.read_text(encoding="utf-8"))
        if evidence.expires_at < datetime.now(UTC):
            return None
        return evidence, sheet_path if sheet_path and sheet_path.is_file() else None

    def cleanup(self, dry_run: bool = False) -> dict[str, int]:
        now = datetime.now(UTC)
        rows = self.store.analysis_rows()
        total = sum(int(str(row["size_bytes"])) for row in rows)
        remove: list[dict[str, object]] = [
            row for row in rows if datetime.fromisoformat(str(row["expires_at"])) <= now
        ]
        for row in rows:
            if total <= self.max_bytes:
                break
            if row not in remove:
                remove.append(row)
            total -= int(str(row["size_bytes"]))
        bytes_removed = sum(int(str(row["size_bytes"])) for row in remove)
        if not dry_run:
            for row in remove:
                path = Path(str(row["evidence_path"])).parent
                if path.parent == self.root and path.name == row["analysis_id"]:
                    shutil.rmtree(path, ignore_errors=True)
                self.store.delete_analysis(str(row["analysis_id"]))
        return {"entries": len(remove), "bytes": bytes_removed}

    def disk_report(self) -> dict[str, int]:
        usage = shutil.disk_usage(self.root)
        cache_bytes = sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())
        return {
            "cache_bytes": cache_bytes,
            "cache_limit_bytes": self.max_bytes,
            "filesystem_free_bytes": usage.free,
            "filesystem_total_bytes": usage.total,
        }


def default_expiry(ttl_days: int) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    return now, now + timedelta(days=ttl_days)
