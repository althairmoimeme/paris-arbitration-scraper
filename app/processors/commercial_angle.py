"""LLM-generated commercial angle for each prospect company.

Takes everything we know about a company (sector, signals, firmographie,
ICSID history, contacts) and asks Claude to produce ONE short actionable
pitch angle — the "💡 ANGLE COMMERCIAL" line on each card.

Requires ``ANTHROPIC_API_KEY`` in ``.env``. Uses prompt caching to keep
cost low across the whole 500-company run.
"""
from __future__ import annotations

import os
from typing import Optional

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


MODEL = "claude-haiku-4-5-20251001"
SYSTEM_PROMPT = """You are a BD assistant for a Paris-based commercial arbitrator \
specialised in commodities (mining, oil & gas, construction) with a strong \
Africa focus. You read structured intel about a prospect company and produce \
ONE sharp, actionable pitch angle in French (2-3 sentences, max 250 characters). \
The angle must :
- Reference something concrete and specific from the intel (sector, ICSID case, country, size).
- Explain WHY the arbitrator is a credible counsel for THIS specific company.
- Suggest a conversation opener (what to pitch, not generic flattery).
- Avoid buzzwords ("accompanement", "expertise", "savoir-faire").
- Avoid directly soliciting pending cases (déonto)."""


def _build_user_prompt(
    company: Company,
    icsid_cases: list[ICSIDCase],
    signals: list[CompanySignal],
) -> str:
    lines = [
        f"COMPANY : {company.name}",
        f"SECTOR : {company.sector or 'n/c'} ({company.sector_detail or 'n/c'})",
        f"COUNTRY : {company.country or 'n/c'}",
    ]
    if company.has_africa_exposure:
        lines.append("AFRICA EXPOSURE : yes")
    if company.revenue_eur:
        yr = f" ({company.revenue_year})" if company.revenue_year else ""
        lines.append(f"REVENUE{yr} : {company.revenue_eur:,.0f} €")
    if company.employees:
        lines.append(f"EMPLOYEES : {company.employees:,}")
    if company.size_bucket:
        lines.append(f"SIZE : {company.size_bucket}")
    if icsid_cases:
        lines.append("\nICSID CASES :")
        for c in icsid_cases[:5]:
            lines.append(
                f"  - {c.case_number} vs {c.respondent_state or 'n/c'} · "
                f"{c.economic_sector or 'n/c'} · subject: {(c.subject or 'n/c')[:120]} · "
                f"status: {c.status or 'n/c'} · registered: {c.registered_date or 'n/c'}"
            )
    other_signals = [s for s in signals if s.kind != "icsid"]
    if other_signals:
        lines.append("\nOTHER SIGNALS :")
        for s in other_signals[:5]:
            lines.append(f"  - {s.kind} · {s.title} · {(s.detail or '')[:100]}")
    lines.append("\nProduce the commercial angle (French, 2-3 sentences, max 250 chars).")
    return "\n".join(lines)


def generate_angle_for_company(client, company: Company, icsid_cases, signals) -> Optional[str]:
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=200,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=[
                {
                    "role": "user",
                    "content": _build_user_prompt(company, icsid_cases, signals),
                }
            ],
        )
        if resp.content and resp.content[0].type == "text":
            return resp.content[0].text.strip()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"  angle FAIL for {company.name}: {e}")
    return None


def run_angle_generation(limit: int | None = None) -> dict:
    init_db()
    api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
    if not api_key:
        logger.error("No ANTHROPIC_API_KEY — set it in .env and retry")
        return {"error": "no_api_key"}
    try:
        import anthropic
    except ImportError:
        logger.error("Anthropic SDK not installed")
        return {"error": "no_sdk"}

    client = anthropic.Anthropic(api_key=api_key)
    stats = {"total": 0, "generated": 0, "skipped": 0}
    with SessionLocal() as session:
        companies = session.scalars(
            select(Company).where(Company.commercial_angle.is_(None))
        ).all()
        if limit:
            companies = companies[:limit]
        logger.info(f"Generating commercial angles for {len(companies)} companies…")
        for c in companies:
            stats["total"] += 1
            # Fetch related ICSID + signals
            icsid_cases = session.scalars(
                select(ICSIDCase).where(ICSIDCase.company_id == c.id)
            ).all()
            signals = session.scalars(
                select(CompanySignal).where(CompanySignal.company_id == c.id)
            ).all()
            if not icsid_cases and not signals:
                stats["skipped"] += 1
                continue
            angle = generate_angle_for_company(client, c, icsid_cases, signals)
            if angle:
                c.commercial_angle = angle
                stats["generated"] += 1
            if stats["total"] % 20 == 0:
                session.commit()
                logger.info(
                    f"  [{stats['total']}/{len(companies)}] "
                    f"generated={stats['generated']} skipped={stats['skipped']}"
                )
        session.commit()
    logger.info(f"Angles done. stats={stats}")
    return stats
