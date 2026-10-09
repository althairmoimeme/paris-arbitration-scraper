"""Enrich Companies with Pappers data : revenue, employees, dirigeants."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.logging_setup import setup as _setup_logging
from app.scrapers.pappers import enrich_from_pappers

_setup_logging()
from loguru import logger  # noqa: E402


def main() -> int:
    stats = enrich_from_pappers()
    logger.info(f"Done : {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
