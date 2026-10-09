"""Enrich existing Companies with BODACC procédures collectives signals.

For each Company in the pool we query BODACC data.gouv by SIREN and keep
only "procédures collectives" family annonces :
  - sauvegarde
  - redressement judiciaire
  - liquidation judiciaire
  - mandat ad hoc
  - conciliation
  - plan de cession

Those signals are the strongest soft-indicators of a pending or imminent
litigation : a titulaire in sauvegarde = buyer will have a dispute, a
sub-contractor in liquidation = cascade of claims.

API : https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/
      datasets/annonces-commerciales/records
"""
from __future__ import annotations

import time
from typing import Optional

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import Company, CompanySignal, SessionLocal, init_db
from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402


BODACC_API = (
    "https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/"
    "datasets/annonces-commerciales/records"
)

# BODACC family codes
HOT_FAMILIES = {"A", "B"}  # A = procédures collectives, B = modifications stat.

HOT_TYPES = (
    "sauvegarde", "redressement", "liquidation",
    "mandat ad hoc", "conciliation", "plan de cession",
    "resolution plan", "ouverture", "jugement d'ouverture",
)


def _looks_hot(label: Optional[str], jsonc: dict) -> Optional[str]:
    """Return the matching keyword if the annonce is a hot signal."""
    txt = " ".join([
        str(label or ""),
        str(jsonc.get("familleavis", "")),
        str(jsonc.get("familleavis_lib", "")),
        str(jsonc.get("typeannonce_lib", "")),
    ]).lower()
    for kw in HOT_TYPES:
        if kw in txt:
            return kw
    return None


def _fetch_for_siren(client: httpx.Client, siren: str) -> list[dict]:
    """Query BODACC by SIREN. The `registre` column is an array containing
    the SIREN in both formatted and raw forms, so an ODSQL `IN` lookup works.
    We also narrow to the hot familles (procédures collectives).
    """
    try:
        r = client.get(
            BODACC_API,
            params={
                "limit": 50,
                "where": (
                    f'registre LIKE "{siren}" '
                    f'AND dateparution >= "2023-01-01" '
                    f'AND familleavis IN ("jo","annonces_jo","jm","col")'
                ),
                "order_by": "dateparution DESC",
                "select": (
                    "id,familleavis,familleavis_lib,typeavis_lib,"
                    "dateparution,ville,tribunal,commercant,publicationavis,"
                    "url_complete,jugement,acte"
                ),
            },
            timeout=20.0,
        )
        if r.status_code != 200:
            return []
        return (r.json() or {}).get("results") or []
    except Exception:
        return []


def run(limit: Optional[int] = None) -> dict:
    init_db()
    stats = {
        "companies_queried": 0,
        "annonces_scanned": 0,
        "hot_signals": 0,
    }
    with SessionLocal() as session:
        companies = session.scalars(
            select(Company).where(Company.siren.isnot(None))
        ).all()
        if limit:
            companies = companies[:limit]
        logger.info(f"BODACC query for {len(companies)} companies with SIREN")

        # BODACC API refuses requests missing Referer/Origin (403).
        client = httpx.Client(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
                "Referer": "https://www.bodacc.fr/",
                "Origin": "https://www.bodacc.fr",
            },
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=True,
        )
        try:
            for i, c in enumerate(companies, start=1):
                stats["companies_queried"] += 1
                annonces = _fetch_for_siren(client, c.siren)
                stats["annonces_scanned"] += len(annonces)
                for a in annonces:
                    kw = _looks_hot(a.get("typeannonce_lib"), a)
                    if not kw:
                        continue
                    stats["hot_signals"] += 1
                    date = a.get("dateparution") or None
                    kind = "bodacc_procedure"
                    title = f"BODACC · {a.get('typeannonce_lib') or kw} ({a.get('tribunal') or 'tribunal n/c'})"
                    detail = f"{a.get('familleavis_lib') or ''} · {a.get('ville') or ''}".strip(" ·")
                    existing = session.scalar(
                        select(CompanySignal).where(
                            CompanySignal.company_id == c.id,
                            CompanySignal.kind == kind,
                            CompanySignal.title == title,
                            CompanySignal.occurred_on == date,
                        )
                    )
                    if existing:
                        continue
                    heat = 85 if "liquidation" in kw else 80 if "redressement" in kw else 75
                    sig = CompanySignal(
                        company_id=c.id,
                        kind=kind,
                        title=title,
                        detail=detail,
                        source_url=a.get("url_complete") or a.get("publicationavis") or "",
                        source_name="BODACC data.gouv",
                        occurred_on=date,
                        heat=heat,
                    )
                    session.add(sig)
                    c.signal_count = (c.signal_count or 0) + 1
                    c.hot_score = max(c.hot_score or 0, heat)
                if i % 20 == 0:
                    session.commit()
                    logger.info(f"  [{i}/{len(companies)}] hot_signals={stats['hot_signals']}")
                time.sleep(0.05)
            session.commit()
        finally:
            client.close()

    logger.info(f"BODACC done. stats={stats}")
    return stats


if __name__ == "__main__":
    import sys
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    print(run(lim))
