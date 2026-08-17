from __future__ import annotations

import argparse
import json

from video_evidence_mcp.cache import EvidenceCache
from video_evidence_mcp.config import get_settings
from video_evidence_mcp.jobs import JobStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Video Evidence cache and disk maintenance")
    subparsers = parser.add_subparsers(dest="command", required=True)
    cleanup = subparsers.add_parser("cleanup", help="remove expired/over-limit evidence")
    cleanup.add_argument("--dry-run", action="store_true")
    subparsers.add_parser("disk-check", help="print cache and filesystem usage")
    args = parser.parse_args()
    settings = get_settings()
    store = JobStore(settings.database_path)
    store.initialize()
    cache = EvidenceCache(
        settings.cache_dir, store, settings.cache_ttl_days, settings.max_cache_bytes
    )
    result = (
        cache.cleanup(dry_run=args.dry_run) if args.command == "cleanup" else cache.disk_report()
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
