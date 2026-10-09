"""Finalize the demo build after the ICSID scrape completes.

Runs in sequence :
  1. Backfill companies rollups (signal_count, hot_score, africa_exposure)
  2. Resolve French-ish SIRENs via free API (lightweight)
  3. Match DECP contracts (if DECP files are available)
  4. Generate rule-based commercial angles (fallback when no Anthropic key)
  5. If ANTHROPIC_API_KEY is set, upgrade the rule-based angles to LLM
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402


DB = ROOT / "data" / "paw.db"


def step_1_backfill_rollups() -> None:
    logger.info("[1/4] Backfilling companies rollups…")
    con = sqlite3.connect(DB)
    con.execute("""
        UPDATE companies
        SET signal_count = (SELECT COUNT(*) FROM company_signals WHERE company_id=companies.id),
            hot_score    = COALESCE((SELECT MAX(heat) FROM company_signals WHERE company_id=companies.id), 0)
    """)
    con.execute("""
        UPDATE companies SET has_africa_exposure=1
        WHERE id IN (
            SELECT DISTINCT company_id FROM icsid_cases
            WHERE LOWER(respondent_state) GLOB '*nigeria*'
               OR LOWER(respondent_state) GLOB '*egypt*'
               OR LOWER(respondent_state) GLOB '*south africa*'
               OR LOWER(respondent_state) GLOB '*morocco*'
               OR LOWER(respondent_state) GLOB '*algeria*'
               OR LOWER(respondent_state) GLOB '*tunisia*'
               OR LOWER(respondent_state) GLOB '*kenya*'
               OR LOWER(respondent_state) GLOB '*ethiopia*'
               OR LOWER(respondent_state) GLOB '*ghana*'
               OR LOWER(respondent_state) GLOB '*angola*'
               OR LOWER(respondent_state) GLOB '*mozambique*'
               OR LOWER(respondent_state) GLOB '*zambia*'
               OR LOWER(respondent_state) GLOB '*zimbabwe*'
               OR LOWER(respondent_state) GLOB '*congo*'
               OR LOWER(respondent_state) GLOB '*senegal*'
               OR LOWER(respondent_state) GLOB '*cameroon*'
               OR LOWER(respondent_state) GLOB '*ivoire*'
               OR LOWER(respondent_state) GLOB '*cote d*'
               OR LOWER(respondent_state) GLOB '*mali*'
               OR LOWER(respondent_state) GLOB '*burkina*'
               OR LOWER(respondent_state) GLOB '*niger*'
               OR LOWER(respondent_state) GLOB '*chad*'
               OR LOWER(respondent_state) GLOB '*sudan*'
               OR LOWER(respondent_state) GLOB '*libya*'
               OR LOWER(respondent_state) GLOB '*guinea*'
               OR LOWER(respondent_state) GLOB '*tanzania*'
               OR LOWER(respondent_state) GLOB '*uganda*'
               OR LOWER(respondent_state) GLOB '*gabon*'
               OR LOWER(respondent_state) GLOB '*benin*'
               OR LOWER(respondent_state) GLOB '*togo*'
               OR LOWER(respondent_state) GLOB '*rwanda*'
               OR LOWER(respondent_state) GLOB '*namibia*'
               OR LOWER(respondent_state) GLOB '*mauritania*'
        )
    """)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM companies WHERE has_africa_exposure=1").fetchone()[0]
    nsigs = con.execute("SELECT SUM(signal_count) FROM companies").fetchone()[0] or 0
    logger.info(f"   Companies updated · {n} with Africa exposure · {nsigs} signals total")
    con.close()


def step_2_decp() -> None:
    logger.info("[2/4] Matching DECP contracts (free API SIREN resolution)…")
    try:
        from app.scrapers.decp import run_decp
    except Exception as e:
        logger.error(f"   DECP module import failed: {e}")
        return
    try:
        stats = run_decp(limit_companies=400)
        logger.info(f"   DECP stats: {stats}")
    except Exception as e:
        logger.error(f"   DECP crashed: {e}")


def step_3_rule_based_angles() -> None:
    logger.info("[3/4] Generating rule-based commercial angles…")
    try:
        from app.processors.angle_rules import run_rule_based_angles
        stats = run_rule_based_angles()
        logger.info(f"   Angles stats: {stats}")
    except Exception as e:
        logger.error(f"   Angles crashed: {e}")


def step_4_llm_angles() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.info("[4/4] No ANTHROPIC_API_KEY — skipping LLM upgrade (rule-based already in place).")
        return
    logger.info("[4/4] Upgrading angles with LLM pass…")
    try:
        from app.processors.commercial_angle import run_angle_generation
        # Reset existing rule-based angles so LLM can replace them
        con = sqlite3.connect(DB)
        con.execute("UPDATE companies SET commercial_angle=NULL")
        con.commit()
        con.close()
        stats = run_angle_generation()
        logger.info(f"   LLM stats: {stats}")
    except Exception as e:
        logger.error(f"   LLM crashed: {e}")


def main() -> int:
    step_1_backfill_rollups()
    step_2_decp()
    step_3_rule_based_angles()
    step_4_llm_angles()
    logger.info("✓ Demo finalisation complete")
    # Final stats
    con = sqlite3.connect(DB)
    print()
    print("=== FINAL STATE ===")
    for t in ("companies", "icsid_cases", "company_signals", "company_contacts"):
        print(f"  {t:25s} {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
    print(f"  companies w/ angle        {con.execute('SELECT COUNT(*) FROM companies WHERE commercial_angle IS NOT NULL').fetchone()[0]}")
    print(f"  companies w/ Africa exp.  {con.execute('SELECT COUNT(*) FROM companies WHERE has_africa_exposure=1').fetchone()[0]}")
    print()
    print("Top-5 companies by hot score:")
    for r in con.execute("""
        SELECT name, sector, signal_count, hot_score, has_africa_exposure
        FROM companies ORDER BY hot_score DESC, signal_count DESC LIMIT 5
    """):
        print(f"  {r[0][:40]:40s} | {r[1] or 'n/c':12s} sigs={r[2]} hot={r[3]} africa={bool(r[4])}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
