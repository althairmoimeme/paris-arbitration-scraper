"""Create every table in the paris-arbitration-scraper SQLite DB."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DB_PATH
from app.database import init_db
from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402


def main() -> int:
    logger.info(f"Initializing DB at {DB_PATH}")
    init_db()
    logger.info("✓ Tables created (or already present)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
