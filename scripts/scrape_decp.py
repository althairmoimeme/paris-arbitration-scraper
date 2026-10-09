"""Match the Companies in the DB against the DECP public procurement
dataset (shared cache with eurosatory-scraper)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.logging_setup import setup as _setup_logging
from app.scrapers.decp import run_decp

_setup_logging()
from loguru import logger  # noqa: E402


def main() -> int:
    stats = run_decp()
    logger.info(f"Done : {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
