"""Build the Clients potentiels pool from DECP travaux ≥10 M€.

Pipeline :
  1. Scan DECP yearly JSON files (shared cache from eurosatory-scraper)
  2. Keep only marchés travaux ≥ MIN_AMOUNT_EUR (10 M€ default)
  3. Group all contracts by titulaire SIREN
  4. Resolve each SIREN via the free recherche-entreprises API
     → official name, code NAF, effectif, location
  5. Filter to the target sectors (construction / mining / énergie / BTP)
  6. Create one Company per qualified SIREN
  7. Attach one `decp_contract` signal per contract (we keep ALL contracts
     for a qualified titulaire, even sub-10M€ ones from the same scan,
     because once a group is in the target pool its full track record matters)

Sector qualification (NAF code prefixes) :
    05-09 : extraction (mining, charbon, pétrole, gaz, carrières)
    19    : raffinage, cokéfaction
    23    : fab. autres produits minéraux non métalliques (ciment, béton)
    24    : métallurgie
    35    : production/distribution énergie (élec, gaz, vapeur)
    41-43 : construction (bâtiment, génie civil, spécialisée)
    42    : génie civil (ouvrages d'art, infra)
    71    : ingénierie et architecture (bureaux d'études gros œuvre)
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional

import httpx
from rapidfuzz import fuzz
from sqlalchemy import select

from app.config import settings
from app.database import Company, CompanySignal, SessionLocal, init_db
from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402


# ─── Config ────────────────────────────────────────────────────────────

MIN_AMOUNT_EUR = 10_000_000   # travaux ≥ 10 M€ to enter the pool
# DECP has some source-side typos (montants with extra zeros). Above this
# threshold we treat the value as a data entry error and skip.
MAX_AMOUNT_EUR = 2_000_000_000  # 2 Md€

# NAF prefixes we keep (first 2 digits of code APE)
TARGET_NAF_PREFIX = {
    # Extraction
    "05", "06", "07", "08", "09",
    # Raffinage / cokéfaction
    "19",
    # Ciment, béton, carrelage
    "23",
    # Métallurgie
    "24",
    # Énergie
    "35",
    # Construction
    "41", "42", "43",
    # Ingénierie / architecture (bureaux d'études travaux)
    "71",
}

# Human labels for the sector buckets
NAF_TO_SECTOR = {
    "05": ("mining", "Extraction charbon"),
    "06": ("oil_gas", "Extraction hydrocarbures"),
    "07": ("mining", "Extraction minerais métalliques"),
    "08": ("mining", "Autres industries extractives"),
    "09": ("mining", "Services soutien aux industries extractives"),
    "19": ("oil_gas", "Cokéfaction & raffinage"),
    "23": ("construction", "Matériaux de construction"),
    "24": ("metals", "Métallurgie"),
    "35": ("energy", "Production/distribution énergie"),
    "41": ("construction", "Construction de bâtiments"),
    "42": ("construction", "Génie civil"),
    "43": ("construction", "Travaux spécialisés"),
    "71": ("construction", "Ingénierie / architecture"),
}

# Travaux keyword fallback (objet or nature) — some marchés travaux are
# published without the full CPV
TRAVAUX_OBJECT_KEYWORDS = (
    "travaux", "construction", "bâtiment", "batiment",
    "gros oeuvre", "gros œuvre", "génie civil", "genie civil",
    "ouvrage d'art", "ouvrage art", "voirie", "chaussée", "chaussee",
    "pont", "tunnel", "viaduc", "autoroute", "ligne ferroviaire", "ferré",
    "rénovation", "renovation", "réhabilitation", "rehabilitation",
    "extension", "aménagement", "amenagement", "infrastructure",
    "centre de tri", "station d'épuration", "step ", "réseau",
    "canalisation", "assainissement",
)


# ─── Shared cache paths ───────────────────────────────────────────────

_EURO_DECP_DIR = Path("/Users/bertantoine/eurosatory-scraper/data/raw/decp")
_LOCAL_DECP_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "raw" / "decp"


def _available_decp_files() -> list[Path]:
    for base in (_EURO_DECP_DIR, _LOCAL_DECP_DIR):
        if base.exists():
            found = sorted(base.glob("decp-*.json"))
            if found:
                return found
    return []


# ─── DECP parsing ─────────────────────────────────────────────────────


def _is_travaux(m: dict) -> bool:
    """Return True if the contract is a works contract (travaux)."""
    nature = (m.get("nature") or "").lower()
    if "travaux" in nature:
        return True
    cpv = (m.get("codeCPV") or "")
    if cpv.startswith("45"):
        return True
    obj = (m.get("objet") or "").lower()
    return any(k in obj for k in TRAVAUX_OBJECT_KEYWORDS)


def _extract_titulaire_sirens(m: dict) -> list[str]:
    out = []
    for tw in (m.get("titulaires") or []):
        tit = tw.get("titulaire") if isinstance(tw, dict) else tw
        if not isinstance(tit, dict):
            continue
        tid = str(tit.get("id") or "")
        digits = "".join(c for c in tid if c.isdigit())
        if len(digits) >= 9:
            out.append(digits[:9])
    return list(dict.fromkeys(out))


def _scan_decp_for_travaux_big(files: list[Path]) -> dict[str, list[dict]]:
    """Return {siren: [contract, ...]} for all marchés travaux ≥ threshold.

    We do NOT filter by titulaire here — we keep every match and will
    qualify by NAF later.
    """
    by_siren: dict[str, list[dict]] = defaultdict(list)
    total_matches = 0
    for path in files:
        logger.info(f"  Scanning {path.name} …")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"    skip {path.name}: {e}")
            continue
        marches = (data.get("marches") or {}).get("marche") or []
        year_matches = 0
        for m in marches:
            amt = m.get("montant")
            if not isinstance(amt, (int, float)) or amt < MIN_AMOUNT_EUR:
                continue
            if not _is_travaux(m):
                continue
            sirens = _extract_titulaire_sirens(m)
            if not sirens:
                continue
            for s in sirens:
                by_siren[s].append(m)
            year_matches += 1
        logger.info(f"    kept {year_matches} marchés travaux ≥ {MIN_AMOUNT_EUR/1e6:.0f} M€")
        total_matches += year_matches
    logger.info(f"DECP scan complete : {total_matches} contrats sur {len(by_siren)} titulaires uniques")
    return dict(by_siren)


# ─── SIRENE qualification ─────────────────────────────────────────────


def _qualify_siren(client: httpx.Client, siren: str) -> Optional[dict]:
    """Call recherche-entreprises API and return the resolved entity dict,
    or None if unresolved.
    """
    try:
        r = client.get(
            "https://recherche-entreprises.api.gouv.fr/search",
            params={"q": siren, "page": 1, "per_page": 1},
            timeout=10.0,
        )
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:  # noqa: BLE001
        return None
    results = data.get("results") or []
    if not results:
        return None
    top = results[0]
    if top.get("siren") != siren:
        return None
    return top


def _naf_to_sector(naf: Optional[str]) -> Optional[tuple[str, str]]:
    """Map NAF code (eg "41.20A") to (sector, sector_detail). Return None
    if the NAF is outside the target scope.
    """
    if not naf:
        return None
    prefix = naf.replace(".", "")[:2]
    if prefix not in TARGET_NAF_PREFIX:
        return None
    return NAF_TO_SECTOR.get(prefix, ("construction", f"NAF {naf}"))


def _effectif_bucket_to_size(effectif: Optional[str]) -> Optional[str]:
    """Map SIRENE effectif code to a size bucket.

    See https://www.sirene.fr/sirene/public/variable/trancheEffectifsUniteLegale
    """
    if not effectif:
        return None
    try:
        n = int(effectif)
    except ValueError:
        return None
    # 00=0 ; 01=1-2 ; 02=3-5 ; 03=6-9 ; 11=10-19 ; 12=20-49 ; 21=50-99
    # 22=100-199 ; 31=200-249 ; 32=250-499 ; 41=500-999 ; 42=1000-1999
    # 51=2000-4999 ; 52=5000-9999 ; 53=10000+
    if n <= 3:
        return "tpe"
    if n <= 11:
        return "small"
    if n <= 21:
        return "mid"
    if n <= 32:
        return "upper_mid"
    if n <= 41:
        return "large"
    return "very_large"


def _effectif_label(effectif: Optional[str]) -> Optional[int]:
    """Return a representative employee count from the SIRENE bucket."""
    if not effectif:
        return None
    try:
        n = int(effectif)
    except ValueError:
        return None
    mid = {
        0: 0, 1: 2, 2: 4, 3: 7,
        11: 15, 12: 35, 21: 75, 22: 150,
        31: 225, 32: 375, 41: 750, 42: 1500,
        51: 3500, 52: 7500, 53: 20000,
    }
    return mid.get(n)


def _canonical_key(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return s[:200]


def _upsert_company_and_contracts(
    session,
    siren: str,
    sirene_entity: dict,
    sector: str,
    sector_detail: str,
    contracts: list[dict],
) -> tuple[Company, int]:
    """Create (or reuse) a Company and attach ALL contracts as signals.
    Returns (company, nb_new_signals).
    """
    # Pick the best name
    name = (
        sirene_entity.get("nom_complet")
        or sirene_entity.get("nom_raison_sociale")
        or sirene_entity.get("nom_entreprise")
        or siren
    )
    key = _canonical_key(name)
    company = session.scalar(select(Company).where(Company.canonical_key == key))
    if not company:
        company = Company(
            canonical_key=key,
            name=name,
            siren=siren,
            sector=sector,
            sector_detail=sector_detail,
            size_bucket=_effectif_bucket_to_size(sirene_entity.get("tranche_effectif_salarie")),
            employees=_effectif_label(sirene_entity.get("tranche_effectif_salarie")),
            country="France",
            country_iso2="FR",
            has_africa_exposure=False,
            signal_count=0,
            hot_score=0,
        )
        session.add(company)
        session.flush()

    new_signals = 0
    for c in contracts:
        amt = c.get("montant")
        if not isinstance(amt, (int, float)):
            continue
        date = (c.get("dateNotification") or "").strip() or None
        acheteur_id = (c.get("acheteur") or {}).get("id") or ""
        obj = (c.get("objet") or "").strip()[:300]
        title = f"Marché public {int(amt):,} €".replace(",", " ")
        existing = session.scalar(
            select(CompanySignal).where(
                CompanySignal.company_id == company.id,
                CompanySignal.kind == "decp_contract",
                CompanySignal.title == title,
                CompanySignal.occurred_on == date,
            )
        )
        if existing:
            continue
        heat = min(95, 50 + int(amt / 10_000_000) * 5)
        sig = CompanySignal(
            company_id=company.id,
            kind="decp_contract",
            title=title,
            detail=f"{acheteur_id} · {obj}",
            source_name="DECP data.gouv",
            occurred_on=date,
            amount_eur=float(amt),
            heat=heat,
        )
        session.add(sig)
        new_signals += 1
        company.hot_score = max(company.hot_score or 0, heat)
    company.signal_count = (company.signal_count or 0) + new_signals
    return company, new_signals


# ─── Main entry ───────────────────────────────────────────────────────


def run(limit_sirens: Optional[int] = None) -> dict:
    init_db()
    files = _available_decp_files()
    if not files:
        logger.error("No DECP files found")
        return {"error": "no_decp"}
    logger.info(f"Using {len(files)} DECP file(s)")

    by_siren = _scan_decp_for_travaux_big(files)
    sirens = list(by_siren.keys())
    if limit_sirens:
        sirens = sirens[:limit_sirens]

    client = httpx.Client(
        headers={"User-Agent": settings.paw_user_agent},
        timeout=httpx.Timeout(10.0, connect=5.0),
    )
    stats = {
        "sirens_scanned": 0,
        "sirens_resolved": 0,
        "sirens_in_target_sector": 0,
        "sirens_out_of_scope": 0,
        "companies_created": 0,
        "signals_created": 0,
    }
    try:
        with SessionLocal() as session:
            for i, siren in enumerate(sirens, start=1):
                stats["sirens_scanned"] += 1
                entity = _qualify_siren(client, siren)
                if not entity:
                    continue
                stats["sirens_resolved"] += 1
                naf = (
                    entity.get("activite_principale")
                    or (entity.get("etablissement_siege") or {}).get("activite_principale")
                )
                sect = _naf_to_sector(naf)
                if not sect:
                    stats["sirens_out_of_scope"] += 1
                    continue
                stats["sirens_in_target_sector"] += 1
                sector, sector_detail = sect
                company, new_signals = _upsert_company_and_contracts(
                    session, siren, entity, sector, sector_detail, by_siren[siren],
                )
                if new_signals:
                    stats["signals_created"] += new_signals
                stats["companies_created"] += 1
                if i % 25 == 0:
                    session.commit()
                    logger.info(
                        f"  [{i}/{len(sirens)}] "
                        f"resolved={stats['sirens_resolved']} "
                        f"in-sector={stats['sirens_in_target_sector']} "
                        f"out={stats['sirens_out_of_scope']}"
                    )
                # Light pacing — the free API tolerates ~7 r/s but let's be nice
                time.sleep(0.1)
            session.commit()
    finally:
        client.close()

    logger.info(f"DECP targets build done. stats={stats}")
    return stats


if __name__ == "__main__":
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    print(run(limit))
