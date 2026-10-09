"""Enrich existing Companies with BOAMP signals.

The BOAMP (Bulletin Officiel des Annonces de Marchés Publics) tracks all
French public-procurement notices : attribution, modification (= avenant),
rectificatif, résultat, annulation.

For a litigation BD lead-gen tool, the hot signals are :

  - MODIFICATION on a travaux marché where our company is the titulaire
    → avenants (+% or extended duration) frequently precede disputes
  - RESULTAT with non-attribution motive (abandonné, infructueux, etc.)
  - ANNULATION / rectificatif substantiel

The BOAMP open-data API is :
    https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/
    datasets/boamp/records

Titulaire is only published as free text (no SIREN) — we match by
rapidfuzz name similarity against our Company pool.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional

import httpx
from rapidfuzz import fuzz, process
from sqlalchemy import select

from app.config import settings
from app.database import Company, CompanySignal, SessionLocal, init_db
from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402


BOAMP_API = (
    "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/"
    "datasets/boamp/records"
)

HOT_NATURES = {
    "modification",     # avenants
    "modificatif",
    "rectificatif",     # often substantial changes
    "annulation",       # procedure cancelled
    "resultat",         # may hide infructueux
}


# ─── helpers ─────────────────────────────────────────────────────────


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.lower()).strip()


def _flatten_titulaire(x) -> str:
    """BOAMP returns titulaire as either a string, a list of strings, or
    a list of dicts. Flatten to a single joined string."""
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, list):
        parts = []
        for item in x:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                # Possible keys : "denomination", "name"
                for k in ("denomination", "name", "value", "titulaire"):
                    v = item.get(k)
                    if isinstance(v, str):
                        parts.append(v)
                        break
        return " ; ".join(parts)
    if isinstance(x, dict):
        for k in ("denomination", "name", "value", "titulaire"):
            v = x.get(k)
            if isinstance(v, str):
                return v
    return str(x)


def _best_titulaire_match(titulaire_text, name_index: dict[str, int]) -> Optional[tuple[int, int]]:
    """Return (company_id, score) if the free-text titulaire resembles one
    of our pool companies above threshold.
    """
    flat = _flatten_titulaire(titulaire_text)
    if not flat:
        return None
    norm = _norm(flat)
    if not norm or len(norm) < 3:
        return None
    choices = list(name_index.keys())
    hit = process.extractOne(norm, choices, scorer=fuzz.token_set_ratio)
    if not hit:
        return None
    choice, score, _ = hit
    if score < 88:
        return None
    return name_index[choice], int(score)


# ─── main ────────────────────────────────────────────────────────────


def run(limit_pages: int = 150) -> dict:
    init_db()
    stats = {
        "pages_fetched": 0,
        "records_scanned": 0,
        "hot_notices": 0,
        "matched_signals": 0,
    }
    with SessionLocal() as session:
        companies = session.scalars(select(Company)).all()
        name_index: dict[str, int] = {}
        for c in companies:
            name_index[_norm(c.name)] = c.id
            # Also index short name forms (remove suffixes like SA, SAS, SNC, EURL)
            short = re.sub(r"\b(sa|sas|sarl|snc|eurl|sasu|holding|france|frs?|international|group)\b\.?", "", _norm(c.name)).strip()
            if short and short not in name_index:
                name_index[short] = c.id
        if not name_index:
            logger.warning("No companies in pool — run decp_targets first")
            return stats
        logger.info(f"Pool: {len(name_index)} name variants from {len(companies)} companies")

        client = httpx.Client(
            headers={"User-Agent": settings.paw_user_agent},
            timeout=httpx.Timeout(20.0, connect=5.0),
        )
        offset = 0
        PAGE = 100
        where = (
            "dateparution >= '2024-01-01' "
            "AND (type_marche LIKE 'TRAVAUX' OR type_marche LIKE 'SERVICES')"
        )
        try:
            while True:
                if stats["pages_fetched"] >= limit_pages:
                    break
                try:
                    r = client.get(
                        BOAMP_API,
                        params={
                            "limit": PAGE,
                            "offset": offset,
                            "where": where,
                            "select": "idweb,objet,titulaire,nomacheteur,dateparution,nature_libelle,type_marche,url_avis",
                            "order_by": "dateparution DESC",
                        },
                    )
                    if r.status_code != 200:
                        logger.warning(f"BOAMP HTTP {r.status_code} — stopping")
                        break
                    payload = r.json()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"BOAMP fetch error: {e} — stopping")
                    break
                results = payload.get("results") or []
                if not results:
                    break
                stats["pages_fetched"] += 1
                stats["records_scanned"] += len(results)
                for rec in results:
                    nature = (rec.get("nature_libelle") or "").lower()
                    is_hot = any(h in nature for h in HOT_NATURES)
                    if not is_hot:
                        continue
                    stats["hot_notices"] += 1
                    titulaire = rec.get("titulaire") or ""
                    if not titulaire:
                        continue
                    match = _best_titulaire_match(titulaire, name_index)
                    if not match:
                        continue
                    cid, score = match
                    kind = "boamp_modification" if "modif" in nature else "boamp_other"
                    if "annul" in nature:
                        kind = "boamp_annulation"
                    if "resultat" in nature:
                        kind = "boamp_resultat"
                    date = rec.get("dateparution") or None
                    title = f"BOAMP {rec.get('nature_libelle')} — {rec.get('nomacheteur') or 'acheteur n/c'}"
                    detail = (rec.get("objet") or "")[:280]
                    url = rec.get("url_avis") or ""
                    existing = session.scalar(
                        select(CompanySignal).where(
                            CompanySignal.company_id == cid,
                            CompanySignal.kind == kind,
                            CompanySignal.title == title,
                            CompanySignal.occurred_on == date,
                        )
                    )
                    if existing:
                        continue
                    heat = 70 if kind == "boamp_modification" else 60
                    sig = CompanySignal(
                        company_id=cid,
                        kind=kind,
                        title=title,
                        detail=detail + f" · match score {score}",
                        source_url=url,
                        source_name="BOAMP data.gouv",
                        occurred_on=date,
                        heat=heat,
                    )
                    session.add(sig)
                    company = session.get(Company, cid)
                    if company:
                        company.signal_count = (company.signal_count or 0) + 1
                        company.hot_score = max(company.hot_score or 0, heat)
                    stats["matched_signals"] += 1
                offset += PAGE
                if stats["pages_fetched"] % 10 == 0:
                    session.commit()
                    logger.info(
                        f"  pages={stats['pages_fetched']} records={stats['records_scanned']} "
                        f"hot={stats['hot_notices']} matched={stats['matched_signals']}"
                    )
            session.commit()
        finally:
            client.close()

    logger.info(f"BOAMP done. stats={stats}")
    return stats


if __name__ == "__main__":
    import sys
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    print(run(pages))
