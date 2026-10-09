"""Top-level entry point: scrape Paris Arbitration Week catalog
(events + partners) into the SQLite DB.

Usage :
    .venv/bin/python scripts/scrape_paw.py             # full run (547 + 555)
    .venv/bin/python scripts/scrape_paw.py --sample 5  # 5 events + 5 partners
    .venv/bin/python scripts/scrape_paw.py --no-resume # re-scrape everything
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.scrapers.catalog import run_paw_events, run_paw_partners
from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape PAW catalog")
    ap.add_argument("--sample", type=int, default=None,
                    help="Only scrape first N events + N partners.")
    ap.add_argument("--no-resume", action="store_true",
                    help="Re-scrape pages already in the DB.")
    ap.add_argument("--events-only", action="store_true")
    ap.add_argument("--partners-only", action="store_true")
    args = ap.parse_args()

    resume = not args.no_resume

    if not args.partners_only:
        logger.info("─── PAW events ───")
        run_paw_events(limit=args.sample, resume=resume)

    if not args.events_only:
        logger.info("─── PAW partners ───")
        run_paw_partners(limit=args.sample, resume=resume)

    return 0


if __name__ == "__main__":
    sys.exit(main())
