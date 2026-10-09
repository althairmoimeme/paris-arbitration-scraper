"""Run the full XLSX export pipeline."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.exports.xlsx import export_all
from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402


def main() -> int:
    out = export_all()
    for key, path in out.items():
        logger.info(f"  {key:22s} → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
