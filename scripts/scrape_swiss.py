"""Scrape the Swiss Arbitration Summit speakers listing + merge with
existing PAW data (dedup by canonical name)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.logging_setup import setup as _setup_logging
from app.scrapers.swiss_summit import run_swiss_summit

_setup_logging()
from loguru import logger  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--enrich-profiles", action="store_true",
                    help="Also walk each speaker's /speaker/<slug>/ page for extras.")
    args = ap.parse_args()
    stats = run_swiss_summit(enrich_profiles=args.enrich_profiles)
    logger.info(f"Done : {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
