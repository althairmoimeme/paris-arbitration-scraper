"""Enrich the Company pool with DECP avenants (contract modifications).

Each DECP marché can have one or more `modifications` entries — these are
contractual avenants that resize the initial amount. We extract them from
the same source files and emit one `decp_avenant` signal per avenant on
a titulaire already present in our pool.

The heat score depends on the % increase :
  - +10-30% : warm (50)
  - +30-100% : hot (75)
  - +100%+ : very hot (90)

An avenant doubling a marché travaux's amount is a near-guaranteed
contentieux trigger (surcoût contesté, délais contestés, résiliation).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from app.database import Company, CompanySignal, SessionLocal, init_db
from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402


_EURO_DECP_DIR = Path("/Users/bertantoine/eurosatory-scraper/data/raw/decp")
_LOCAL_DECP_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "raw" / "decp"

MIN_DELTA_PCT = 10          # Minimum % change to emit a signal
MIN_INITIAL_AMOUNT = 10_000_000   # Marché initial amount floor : ≥10 M€
# Below this threshold the ticket is too small for H-J's 20-200 M€
# litigation sweet spot — a +300 % avenant on a 1 M€ marché ends up at
# 4 M€, below the sweet spot.


def _files() -> list[Path]:
    for base in (_EURO_DECP_DIR, _LOCAL_DECP_DIR):
        if base.exists():
            found = sorted(base.glob("decp-*.json"))
            if found:
                return found
    return []


def _siren_from_titulaire(tw: dict) -> Optional[str]:
    tit = tw.get("titulaire") if isinstance(tw, dict) else tw
    if not isinstance(tit, dict):
        return None
    tid = str(tit.get("id") or "")
    digits = "".join(c for c in tid if c.isdigit())
    if len(digits) >= 9:
        return digits[:9]
    return None


def _heat_from_delta(delta_pct: float) -> int:
    if delta_pct >= 100:
        return 90
    if delta_pct >= 30:
        return 75
    return 50


def run() -> dict:
    init_db()
    files = _files()
    if not files:
        logger.error("No DECP files found")
        return {"error": "no_decp"}

    with SessionLocal() as session:
        pool = session.scalars(
            select(Company).where(Company.siren.isnot(None))
        ).all()
        siren_to_company = {c.siren: c.id for c in pool}
        logger.info(f"Pool: {len(siren_to_company)} companies with SIREN")

        stats = {
            "contracts_scanned": 0,
            "contracts_with_mods": 0,
            "pool_hits": 0,
            "avenants_signals": 0,
        }

        for path in files:
            logger.info(f"  Scanning {path.name} for avenants …")
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"    skip {path.name}: {e}")
                continue
            marches = (data.get("marches") or {}).get("marche") or []
            for m in marches:
                stats["contracts_scanned"] += 1
                mods = m.get("modifications") or []
                if not mods:
                    continue
                stats["contracts_with_mods"] += 1
                initial = m.get("montant")
                if not isinstance(initial, (int, float)) or initial < MIN_INITIAL_AMOUNT:
                    continue
                sirens = [s for s in (_siren_from_titulaire(tw) for tw in (m.get("titulaires") or [])) if s]
                hits = [s for s in sirens if s in siren_to_company]
                if not hits:
                    continue
                stats["pool_hits"] += len(hits)
                # Process each modification
                for mod_wrap in mods:
                    mod = mod_wrap.get("modification") if isinstance(mod_wrap, dict) else mod_wrap
                    if not isinstance(mod, dict):
                        continue
                    new_amount = mod.get("montant")
                    date = (mod.get("dateNotificationModification") or "").strip() or None
                    objet_mod = (mod.get("objetModification") or "").strip()[:280]
                    if not isinstance(new_amount, (int, float)) or new_amount <= 0:
                        continue
                    delta_pct = ((new_amount - initial) / initial) * 100
                    if abs(delta_pct) < MIN_DELTA_PCT:
                        continue
                    direction = "+" if delta_pct >= 0 else ""
                    heat = _heat_from_delta(abs(delta_pct))
                    original_objet = (m.get("objet") or "")[:120]
                    buyer_id = (m.get("acheteur") or {}).get("id") or ""
                    def _fmt_m(x: float) -> str:
                        """Format in M€ with 1 decimal, dropping the decimal
                        if the value is already round."""
                        v = x / 1_000_000
                        if v >= 100:
                            return f"{v:.0f} M€"
                        if v == int(v):
                            return f"{int(v)} M€"
                        return f"{v:.1f} M€".replace(".", ",")

                    for siren in hits:
                        cid = siren_to_company[siren]
                        title = (
                            f"Avenant {direction}{delta_pct:.0f}% · "
                            f"{_fmt_m(initial)} → {_fmt_m(new_amount)}"
                        )
                        detail = f"{buyer_id} · marché initial : {original_objet} · motif : {objet_mod}"
                        existing = session.scalar(
                            select(CompanySignal).where(
                                CompanySignal.company_id == cid,
                                CompanySignal.kind == "decp_avenant",
                                CompanySignal.title == title,
                                CompanySignal.occurred_on == date,
                            )
                        )
                        if existing:
                            continue
                        sig = CompanySignal(
                            company_id=cid,
                            kind="decp_avenant",
                            title=title,
                            detail=detail,
                            source_name="DECP data.gouv (modifications)",
                            occurred_on=date,
                            amount_eur=float(new_amount - initial),
                            heat=heat,
                        )
                        session.add(sig)
                        company = session.get(Company, cid)
                        if company:
                            company.signal_count = (company.signal_count or 0) + 1
                            company.hot_score = max(company.hot_score or 0, heat)
                        stats["avenants_signals"] += 1
                if stats["avenants_signals"] and stats["avenants_signals"] % 200 == 0:
                    session.commit()
                    logger.info(
                        f"    avenants={stats['avenants_signals']} pool_hits={stats['pool_hits']}"
                    )
            session.commit()
            logger.info(f"    done {path.name} — avenants cumulés : {stats['avenants_signals']}")

    logger.info(f"DECP avenants done. stats={stats}")
    return stats


if __name__ == "__main__":
    print(run())
