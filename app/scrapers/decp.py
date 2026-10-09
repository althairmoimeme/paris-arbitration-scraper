"""Match companies against the French DECP (public procurement) dataset.

Reuses the DECP yearly JSON files already cached in the sibling
``eurosatory-scraper/data/raw/decp/`` project. Produces one
``CompanySignal`` per matching contract :

  - kind = ``decp_contract``
  - title = ``Marché public {amount} €``
  - detail = ``{buyer} · {object}``
  - occurred_on = contract notification date
  - amount_eur = contract amount
  - source_url = data.gouv archived link (reused where applicable)

Matching is SIREN-based : we only tag contracts where one of the
Claimant companies has a known SIREN. SIREN resolution for
non-ASA / non-Pappers companies happens via the free
``recherche-entreprises.api.gouv.fr`` endpoint.
"""
from __future__ import annotations

import json
import re
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


# ─── Shared cache paths ───────────────────────────────────────────────

# Reuse the Eurosatory project's downloaded DECP files when present (they
# weigh ~1.5 GB and are identical for both projects).
_EURO_DECP_DIR = Path("/Users/bertantoine/eurosatory-scraper/data/raw/decp")
_LOCAL_DECP_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "raw" / "decp"


def _available_decp_files() -> list[Path]:
    for base in (_EURO_DECP_DIR, _LOCAL_DECP_DIR):
        if base.exists():
            found = sorted(base.glob("decp-*.json"))
            if found:
                return found
    return []


# ─── SIREN resolver (free API) ────────────────────────────────────────


def _normalize_for_api(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def resolve_siren(client: httpx.Client, name: str) -> tuple[Optional[str], int]:
    """Return (siren, confidence_0_100) using the free data.gouv API."""
    q = _normalize_for_api(name)
    if not q:
        return None, 0
    try:
        r = client.get(
            "https://recherche-entreprises.api.gouv.fr/search",
            params={"q": q, "page": 1, "per_page": 1},
            timeout=10.0,
        )
        if r.status_code != 200:
            return None, 0
        data = r.json()
    except Exception:  # noqa: BLE001
        return None, 0
    results = data.get("results") or []
    if not results:
        return None, 0
    top = results[0]
    nom = top.get("nom_complet") or ""
    conf = int(fuzz.token_set_ratio(q.lower(), nom.lower()))
    if conf < 80:
        return None, conf
    return top.get("siren"), conf


# ─── DECP processing ─────────────────────────────────────────────────


def _iter_contracts_for_sirens(files: Iterable[Path], target_sirens: set[str]) -> Iterable[dict]:
    """Stream through DECP JSON files, yield contracts whose titulaire
    SIREN is in ``target_sirens``.
    """
    for path in files:
        logger.info(f"  Scanning {path.name} …")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"    skip {path.name}: {e}")
            continue
        marches = (data.get("marches") or {}).get("marche") or []
        for m in marches:
            titulaires = m.get("titulaires") or []
            for tw in titulaires:
                tit = tw.get("titulaire") if isinstance(tw, dict) else tw
                if not isinstance(tit, dict):
                    continue
                tid = str(tit.get("id") or "")
                digits = "".join(c for c in tid if c.isdigit())
                if len(digits) < 9:
                    continue
                siren = digits[:9]
                if siren in target_sirens:
                    m["_matched_siren"] = siren
                    yield m
                    break


# Minimum contract amount to be worth tracking — anything below this
# will never translate into a 20-200 M€ litigation for the arbitrator.
DECP_MIN_AMOUNT_EUR = 5_000_000


def _save_decp_signal(session, company: Company, contract: dict) -> bool:
    date = (contract.get("dateNotification") or "").strip() or None
    object_ = (contract.get("objet") or "").strip()
    amount = contract.get("montant")
    buyer_id = (contract.get("acheteur") or {}).get("id") or ""
    duration = contract.get("dureeMois")

    # Threshold: skip contracts under 5 M€ (noise for a mid-cap litigation lawyer)
    if not isinstance(amount, (int, float)) or float(amount) < DECP_MIN_AMOUNT_EUR:
        return False

    title = f"Marché public {int(amount):,} €".replace(",", " ") if isinstance(amount, (int, float)) else "Marché public"
    detail = f"{buyer_id} · {object_[:200]}"

    # Idempotent on (company_id, kind, title, occurred_on)
    existing = session.scalar(
        select(CompanySignal).where(
            CompanySignal.company_id == company.id,
            CompanySignal.kind == "decp_contract",
            CompanySignal.title == title,
            CompanySignal.occurred_on == date,
        )
    )
    if existing:
        return False
    sig = CompanySignal(
        company_id=company.id,
        kind="decp_contract",
        title=title,
        detail=detail,
        source_name="DECP data.gouv",
        occurred_on=date,
        amount_eur=float(amount) if isinstance(amount, (int, float)) else None,
        heat=min(80, 40 + (int((amount or 0) / 1_000_000) * 2)),
    )
    session.add(sig)
    # Rollup
    company.signal_count = (company.signal_count or 0) + 1
    company.hot_score = max(company.hot_score or 0, sig.heat)
    return True


# ─── Main entry ───────────────────────────────────────────────────────


def run_decp(limit_companies: int | None = None) -> dict:
    init_db()
    decp_files = _available_decp_files()
    if not decp_files:
        logger.error("No DECP files found. Run eurosatory-scraper's downloader first.")
        return {"error": "no_decp"}
    logger.info(f"Found {len(decp_files)} DECP files")

    # 1. Resolve SIRENs for every French-ish Company (companies with a
    # website on a .fr domain or a country labelled France). Also do
    # best-effort resolution for all companies — the API is free.
    stats = {"companies_scanned": 0, "sirens_resolved": 0, "contracts_matched": 0}
    client = httpx.Client(
        headers={"User-Agent": settings.paw_user_agent},
        timeout=httpx.Timeout(10.0, connect=5.0),
    )
    try:
        with SessionLocal() as session:
            companies = session.scalars(select(Company)).all()
            if limit_companies:
                companies = companies[:limit_companies]
            logger.info(f"Resolving SIRENs for {len(companies)} companies…")
            siren_to_company: dict[str, int] = {}
            for c in companies:
                stats["companies_scanned"] += 1
                if c.siren:
                    siren_to_company[c.siren] = c.id
                    continue
                siren, conf = resolve_siren(client, c.name)
                if siren:
                    c.siren = siren
                    siren_to_company[siren] = c.id
                    stats["sirens_resolved"] += 1
                if stats["companies_scanned"] % 50 == 0:
                    session.commit()
                    logger.info(
                        f"  [{stats['companies_scanned']}/{len(companies)}] "
                        f"sirens_resolved={stats['sirens_resolved']}"
                    )
            session.commit()
            logger.info(
                f"SIREN resolution done : {stats['sirens_resolved']} new SIRENs "
                f"({len(siren_to_company)} total in-scope)."
            )

            if not siren_to_company:
                logger.info("No SIREN to match — stopping before DECP scan.")
                return stats

            # 2. Scan DECP files for matching titulaires
            for contract in _iter_contracts_for_sirens(decp_files, set(siren_to_company)):
                cid = siren_to_company.get(contract.get("_matched_siren") or "")
                if not cid:
                    continue
                company = session.get(Company, cid)
                if company and _save_decp_signal(session, company, contract):
                    stats["contracts_matched"] += 1
                if stats["contracts_matched"] and stats["contracts_matched"] % 25 == 0:
                    session.commit()
                    logger.info(f"  matched contracts: {stats['contracts_matched']}")
            session.commit()
    finally:
        client.close()
    logger.info(f"DECP done. stats={stats}")
    return stats
