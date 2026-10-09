"""Rule-based commercial angle generator — fallback when no Anthropic
API key is available.

Produces a short pitch angle (French, 2-3 sentences) per company,
driven by hand-written templates triggered by sector + signal patterns.
Never as polished as an LLM pass but totally offline / free.
"""
from __future__ import annotations

from sqlalchemy import select

from app.database import (
    Company,
    CompanySignal,
    ICSIDCase,
    SessionLocal,
    init_db,
)
from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402


# ─── Angle templates ─────────────────────────────────────────────────


SECTOR_LABEL = {
    "mining": "minier",
    "oil_gas": "oil & gas",
    "construction": "construction / infra",
    "energy": "énergie / power",
    "other": "secteur à qualifier",
}


def _format_amount(n):
    if n is None or n == 0:
        return None
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f} Md€"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} M€"
    return f"{n:,.0f} €".replace(",", " ")


def generate_angle(
    company: Company,
    icsid_cases: list[ICSIDCase],
    signals: list[CompanySignal],
) -> str:
    sector = SECTOR_LABEL.get(company.sector or "", company.sector or "secteur à qualifier")
    name = company.name

    # Prefer a specific lead-with signal
    pending_icsid = [c for c in icsid_cases if (c.status or "").lower() == "pending"]
    concluded_icsid = [c for c in icsid_cases if c not in pending_icsid]

    decp = [s for s in signals if s.kind == "decp_contract"]
    big_decp = max(decp, key=lambda s: s.amount_eur or 0, default=None) if decp else None

    parts: list[str] = []

    # 1) Hook — lead with the hottest signal
    if pending_icsid:
        case = pending_icsid[0]
        resp = case.respondent_state or "un État"
        parts.append(
            f"{name} est actuellement partie à un arbitrage CIRDI contre "
            f"{resp} ({case.case_number}, {sector})."
        )
    elif concluded_icsid:
        case = concluded_icsid[0]
        resp = case.respondent_state or "un État"
        parts.append(
            f"{name} a déjà été partie à un arbitrage CIRDI contre "
            f"{resp} ({case.case_number}, {sector})."
        )
    elif big_decp:
        amount = _format_amount(big_decp.amount_eur) or "montant significatif"
        parts.append(
            f"{name} a récemment remporté un marché public de {amount} dans le "
            f"{sector}."
        )
    else:
        parts.append(
            f"{name} est actif dans le {sector}, un secteur fréquemment "
            f"concerné par des contentieux commerciaux et des arbitrages."
        )

    # 2) Positioning / why them
    if company.has_africa_exposure:
        parts.append(
            "Leur exposition Afrique les rend particulièrement sensibles aux "
            "contentieux CCJA / OHADA et aux arbitrages matières premières."
        )
    elif company.size_bucket in ("mid_cap", "large_cap"):
        parts.append(
            "Leur taille les place dans la bonne catégorie pour des contentieux "
            "20-200 M€."
        )
    else:
        parts.append(
            "Profile cohérent avec des contentieux commerciaux complexes et "
            "arbitrages internationaux."
        )

    # 3) Approach suggestion
    if pending_icsid or concluded_icsid:
        parts.append(
            "Angle : me positionner comme second counsel francophone / référent "
            "Paris pour leurs prochains dossiers d'arbitrage international."
        )
    elif big_decp:
        parts.append(
            "Angle : prise de contact pour couvrir leur dispositif juridique "
            "contentieux sur ce type de marché."
        )
    else:
        parts.append(
            "Angle : prise de contact préventive pour se positionner sur les "
            "prochains contentieux dans leur secteur."
        )

    return " ".join(parts)[:420]


def run_rule_based_angles(limit: int | None = None) -> dict:
    init_db()
    stats = {"generated": 0, "skipped": 0}
    with SessionLocal() as session:
        companies = session.scalars(
            select(Company).where(Company.commercial_angle.is_(None))
        ).all()
        if limit:
            companies = companies[:limit]
        logger.info(f"Generating rule-based angles for {len(companies)} companies…")
        for c in companies:
            icsid_cases = session.scalars(
                select(ICSIDCase).where(ICSIDCase.company_id == c.id)
            ).all()
            signals = session.scalars(
                select(CompanySignal).where(CompanySignal.company_id == c.id)
            ).all()
            if not icsid_cases and not signals:
                stats["skipped"] += 1
                continue
            c.commercial_angle = generate_angle(c, icsid_cases, signals)
            stats["generated"] += 1
        session.commit()
    logger.info(f"Rule-based angles done. stats={stats}")
    return stats
