"""Pappers API enrichment for Companies with a known SIREN.

Pulls : revenue (CA), employee count, main director (dirigeant principal),
NAF/APE code (secteur official), domiciliation.

Requires ``PAPPERS_API_KEY`` in ``.env`` or the environment. Free until
your account's monthly quota is exhausted.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import (
    Company,
    CompanyContact,
    SessionLocal,
    init_db,
)
from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402


PAPPERS_URL = "https://api.pappers.fr/v2/entreprise"


def _get_api_key() -> Optional[str]:
    """Fetch the Pappers API key from env (reads .env via pydantic-settings
    on import, so a key in .env is already in os.environ)."""
    return os.environ.get("PAPPERS_API_KEY") or getattr(settings, "pappers_api_key", None)


def _size_bucket(revenue_eur: Optional[float], employees: Optional[int]) -> Optional[str]:
    if revenue_eur is None and employees is None:
        return None
    rev = revenue_eur or 0
    emp = employees or 0
    if rev >= 1_500_000_000 or emp >= 5000:
        return "large_cap"
    if rev >= 50_000_000 or emp >= 250:
        return "mid_cap"
    if rev >= 2_000_000 or emp >= 10:
        return "small"
    return "very_small"


def fetch_pappers(client: httpx.Client, siren: str, api_token: str) -> Optional[dict]:
    try:
        r = client.get(
            PAPPERS_URL,
            params={"api_token": api_token, "siren": siren},
            timeout=10.0,
        )
    except Exception:  # noqa: BLE001
        return None
    if r.status_code == 401:
        logger.warning("Pappers 401 (crédits épuisés ou clef invalide)")
        return "quota_exceeded"  # type: ignore[return-value]
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return None


def _save_contact_from_pappers(session, company: Company, data: dict) -> None:
    for dirigeant in (data.get("representants") or [])[:3]:
        full_name = (
            (dirigeant.get("prenom") or "")
            + " "
            + (dirigeant.get("nom") or "")
        ).strip()
        if not full_name:
            continue
        role = dirigeant.get("qualite") or dirigeant.get("fonction") or ""
        role_cat = None
        rl = role.lower()
        if "président" in rl or "directeur général" in rl or "pdg" in rl:
            role_cat = "ceo"
        elif "financier" in rl or "daf" in rl or "cfo" in rl:
            role_cat = "cfo"
        elif "juridique" in rl or "legal" in rl:
            role_cat = "general_counsel"
        existing = session.scalar(
            select(CompanyContact).where(
                CompanyContact.company_id == company.id,
                CompanyContact.full_name == full_name,
            )
        )
        if existing:
            if role and not existing.role:
                existing.role = role
            if role_cat and not existing.role_category:
                existing.role_category = role_cat
        else:
            session.add(CompanyContact(
                company_id=company.id,
                full_name=full_name,
                role=role,
                role_category=role_cat,
                source="pappers",
                is_primary=(role_cat == "ceo"),
            ))


def enrich_from_pappers(limit: int | None = None) -> dict:
    init_db()
    api_token = _get_api_key()
    if not api_token:
        logger.error("No PAPPERS_API_KEY — set it in .env and retry")
        return {"error": "no_api_key"}

    client = httpx.Client(
        headers={"User-Agent": settings.paw_user_agent},
        timeout=httpx.Timeout(10.0, connect=5.0),
    )
    stats = {"companies_checked": 0, "enriched": 0, "quota_hit": False}
    try:
        with SessionLocal() as session:
            companies = session.scalars(
                select(Company).where(Company.siren.is_not(None))
            ).all()
            if limit:
                companies = companies[:limit]
            logger.info(f"Enriching {len(companies)} companies with Pappers…")
            for c in companies:
                if not c.siren or len(c.siren) < 9:
                    continue
                stats["companies_checked"] += 1
                data = fetch_pappers(client, c.siren, api_token)
                if data == "quota_exceeded":
                    stats["quota_hit"] = True
                    logger.warning("Pappers quota hit — stopping")
                    break
                if not data or not isinstance(data, dict):
                    continue
                # Firmographie
                finance = data.get("finances") or []
                last_fin = finance[0] if finance else {}
                rev = last_fin.get("chiffre_affaires") or data.get("chiffre_affaires")
                if isinstance(rev, (int, float)):
                    c.revenue_eur = float(rev)
                    yr = last_fin.get("annee") or data.get("annee_chiffre_affaires")
                    if yr: c.revenue_year = int(yr)
                emp = data.get("effectif") or last_fin.get("effectif")
                if emp:
                    try: c.employees = int(emp)
                    except (ValueError, TypeError): pass
                if not c.website and data.get("site_internet"):
                    c.website = data["site_internet"]
                c.size_bucket = _size_bucket(c.revenue_eur, c.employees)
                # Contacts (dirigeants)
                _save_contact_from_pappers(session, c, data)
                stats["enriched"] += 1
                if stats["companies_checked"] % 25 == 0:
                    session.commit()
                    logger.info(
                        f"  [{stats['companies_checked']}/{len(companies)}] "
                        f"enriched={stats['enriched']}"
                    )
            session.commit()
    finally:
        client.close()
    logger.info(f"Pappers done. stats={stats}")
    return stats
