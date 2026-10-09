"""Loguru configuration — single entry point so scripts stay import-light."""
from __future__ import annotations

import sys

from loguru import logger


_CONFIGURED = False


def setup(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:HH:mm:ss}</green> "
            "<level>{level:<7}</level> "
            "<cyan>{name}:{function}:{line}</cyan>  "
            "<level>{message}</level>"
        ),
        colorize=True,
    )
    _CONFIGURED = True
