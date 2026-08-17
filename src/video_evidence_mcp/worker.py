from __future__ import annotations

import logging
import multiprocessing as mp
import signal
import time
from dataclasses import dataclass
from typing import Any

from video_evidence_mcp.cache import EvidenceCache
from video_evidence_mcp.config import Settings, get_settings
from video_evidence_mcp.jobs import JobStore, RedisJobQueue
from video_evidence_mcp.logging_utils import configure_logging
from video_evidence_mcp.pipeline.runner import PipelineError, run_pipeline
from video_evidence_mcp.schemas import ErrorCode

LOGGER = logging.getLogger(__name__)


def process_job(job_id: str) -> None:
    settings = Settings()
    store = JobStore(settings.database_path)
    store.initialize()
    record = store.claim(job_id)
    if record is None:
        return
    cache = EvidenceCache(
        settings.cache_dir, store, settings.cache_ttl_days, settings.max_cache_bytes
    )
    warnings: list[str] = []

    def progress(value: float, stage: str, current_warnings: list[str] | None) -> None:
        if current_warnings is not None:
            warnings[:] = current_warnings
        store.update_progress(job_id, value, stage, warnings)

    try:
        analysis_id = run_pipeline(record.payload, settings, cache, progress)
        store.complete(job_id, analysis_id, warnings)
    except PipelineError as exc:
        store.fail(job_id, exc.code.value, str(exc), warnings)
    except Exception as exc:
        LOGGER.exception("analysis job failed job_id=%s", job_id)
        store.fail(
            job_id,
            ErrorCode.PROCESSING_FAILED.value,
            f"{type(exc).__name__}: {exc}",
            warnings,
        )


@dataclass(slots=True)
class ActiveJob:
    process: Any
    started_at: float


def run_worker(settings: Settings) -> None:
    store = JobStore(settings.database_path)
    store.initialize()
    interrupted = store.recover_interrupted()
    if interrupted:
        LOGGER.warning("marked %d interrupted jobs failed", interrupted)
    queue = RedisJobQueue(settings.redis_url)
    queue.ensure_queued(store.queued_ids())
    cache = EvidenceCache(
        settings.cache_dir, store, settings.cache_ttl_days, settings.max_cache_bytes
    )
    cache.cleanup()

    stopped = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    context = mp.get_context("spawn")
    active: dict[str, ActiveJob] = {}
    while not stopped:
        for job_id, current in list(active.items()):
            if not current.process.is_alive():
                current.process.join(timeout=1)
                active.pop(job_id)
                continue
            if time.monotonic() - current.started_at > settings.job_timeout_seconds:
                current.process.terminate()
                current.process.join(timeout=10)
                store.fail(
                    job_id,
                    ErrorCode.RESOURCE_LIMIT.value,
                    "analysis exceeded JOB_TIMEOUT_SECONDS",
                    [],
                )
                active.pop(job_id)
        if len(active) >= settings.analysis_concurrency:
            time.sleep(0.25)
            continue
        dequeued_job_id = queue.dequeue(timeout=2)
        if dequeued_job_id is None:
            continue
        process = context.Process(
            target=process_job,
            args=(dequeued_job_id,),
            name=f"analysis-{dequeued_job_id[:8]}",
        )
        process.start()
        active[dequeued_job_id] = ActiveJob(process=process, started_at=time.monotonic())
    for job_id, current in active.items():
        current.process.terminate()
        current.process.join(timeout=10)
        store.fail(job_id, ErrorCode.PROCESSING_FAILED.value, "worker stopped", [])


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    run_worker(settings)


if __name__ == "__main__":
    main()
