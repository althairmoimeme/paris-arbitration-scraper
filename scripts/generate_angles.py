"""Generate LLM-powered commercial angles for every company with signals."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.logging_setup import setup as _setup_logging
from app.processors.commercial_angle import run_angle_generation

_setup_logging()
from loguru import logger  # noqa: E402


def main() -> int:
    stats = run_angle_generation()
    logger.info(f"Done : {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
