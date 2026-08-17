from __future__ import annotations

import os

os.environ.setdefault("DATA_DIR", "/tmp/video-evidence-tests")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/15")
os.environ.setdefault("HOST", "127.0.0.1")
os.environ.setdefault("AUTH_MODE", "none")
