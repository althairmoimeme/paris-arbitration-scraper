"""Scrape the ICSID case database and populate companies + icsid_cases
+ company_signals tables."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.logging_setup import setup as _setup_logging
from app.scrapers.icsid import run_icsid_scrape

_setup_logging()
from loguru import logger  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-sectors", action="store_true",
                    help="Keep all 1159 cases (default: only mining / oil & gas / construction / energy)")
    args = ap.parse_args()
    stats = run_icsid_scrape(all_sectors=args.all_sectors)
    logger.info(f"Done : {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
