"""Scrape the ASA Profiles directory (~1006 arbitration specialists with
emails + phones + LinkedIn + specializations)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.logging_setup import setup as _setup_logging
from app.scrapers.asa_profiles import run_asa_profiles

_setup_logging()
from loguru import logger  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=None,
                    help="Limit the number of pages scraped (default: all 126).")
    args = ap.parse_args()
    stats = run_asa_profiles(max_pages=args.max_pages)
    logger.info(f"Done : {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
