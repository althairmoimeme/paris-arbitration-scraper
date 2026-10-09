"""Scrape African Energy Week speakers (buyer-side signal for a commodities
arbitrator) and merge with existing speaker data via dedup."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.logging_setup import setup as _setup_logging
from app.scrapers.aec_week import run_aec_week

_setup_logging()
from loguru import logger  # noqa: E402


def main() -> int:
    stats = run_aec_week(years=(2026,))
    logger.info(f"Done : {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
