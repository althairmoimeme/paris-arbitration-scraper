"""Generate firstname.lastname@firm-domain guesses for every PAW speaker
without a sourced email. Saves MX-validated guesses to the DB."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.logging_setup import setup as _setup_logging
from app.processors.email_guess import run_email_guess

_setup_logging()
from loguru import logger  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-sources", action="store_true",
                    help="Guess for every source, not just PAW.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute stats without writing to the DB.")
    args = ap.parse_args()
    stat = run_email_guess(only_paw=not args.all_sources, dry_run=args.dry_run)
    logger.info(f"Stats : {stat}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
