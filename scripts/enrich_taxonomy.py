"""Run the full taxonomy enrichment on firms + speakers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.logging_setup import setup as _setup_logging
from app.processors.enrich import run_full_enrichment

_setup_logging()
from loguru import logger  # noqa: E402


def main() -> int:
    stats = run_full_enrichment()
    logger.info(f"Enrichment done : {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
