"""Project-wide pytest config.

Local runs write a full pytest log to ``logs/pytest/<UTC-timestamp>.log``
so the latest run is always reviewable from disk. CI runs skip the file
write — GitHub Actions captures the same output in its UI and an extra
artefact would be noise.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path


def pytest_configure(config):
    """Write a timestamped log file when running outside CI."""
    if os.environ.get("CI"):
        return

    repo_root = Path(__file__).resolve().parent.parent
    log_dir = repo_root / "logs" / "pytest"
    log_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    log_path = log_dir / f"{ts}.log"

    config.option.log_file = str(log_path)
    config.option.log_file_level = "INFO"
    config.option.log_file_format = (
        "%(asctime)s %(levelname)-8s %(name)s :: %(message)s"
    )
