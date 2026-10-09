"""ICSID case database scraper.

Two-pass approach :

  Pass 1 — fetch ``/api/all/cases`` once : returns 1 159 cases with
  core fields (case number, claimant, respondent, status, dates). This
  is the entire ICSID catalog since 1972.

  Pass 2 — for each case, fetch its HTML detail page to extract :
    - Economic Sector (15 buckets ; we filter to the 5 relevant to the
      commodities arbitrator : Mining / Oil & Gas / Oil, Gas & Mining /
      Construction / Electric Power & Other Energy)
    - Subject Matter (free text, e.g. "Mining concession")
    - Status + Status date
    - Named claimant / respondent counsel when disclosed

For every target-sector case, we create (or reuse) a ``Company`` row for
the Claimant and attach the case as :
  - A dedicated ``ICSIDCase`` record for structured querying
  - A generic ``CompanySignal`` (kind='icsid') for the Clients tab UI

Idempotent : re-running updates existing rows without duplicating.
"""
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional

import httpx
from selectolax.lexbor import LexborHTMLParser
from sqlalchemy import select

from app.config import settings
from app.database import (
    Company,
    CompanySignal,
    ICSIDCase,
    SessionLocal,
    ScrapingRun,
    init_db,
)
from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402


ICSID_API = "https://icsid.worldbank.org/api/all/cases"
ICSID_DETAIL = "https://icsid.worldbank.org/cases/case-database/case-detail"

# The 5 sectors matching the commodities arbitrator's ICP. Case ids
# are strings in ICSID's taxonomy.
TARGET_SECTORS = {
    "Mining": "mining",
    "Oil & Gas": "oil_gas",
    "Oil, Gas & Mining": "oil_gas",
    "Construction": "construction",
    "Electric Power & Other Energy": "energy",
}

# African / OHADA + emerging-market respondents that bias toward the
# arbitrator's focus (Afrique / minier / construction / énergie).
AFRICA_HINTS = {
    "nigeria", "egypt", "south africa", "morocco", "algeria", "tunisia",
    "kenya", "ethiopia", "ghana", "angola", "mozambique", "zambia",
    "zimbabwe", "congo", "drc", "senegal", "cameroon", "cote d'ivoire",
    "ivoire", "mali", "burkina", "niger", "chad", "sudan", "libya",
    "guinea", "madagascar", "tanzania", "uganda", "benin", "togo",
    "mauritania", "gabon", "sierra leone", "botswana", "namibia",
    "rwanda", "liberia", "lesotho", "malawi", "eritrea",
}


# ─── Normalisation helpers ─────────────────────────────────────────────


def _normalize_canonical(name: str) -> str:
    """Lowercase ASCII + strip legal suffixes to detect dupes across
    multi-claimant filings (e.g. "Trafigura Pte Ltd" vs "Trafigura
    PTE Ltd" vs "Trafigura Pte. Ltd.")."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    # Strip common entity suffixes
    for suf in (
        " ltd.", " ltd", " llc", " plc", " sa", " s.a.", " sas",
        " s.a.s.", " sarl", " s.a.r.l.", " inc", " inc.", " corp",
        " corp.", " corporation", " co.", " company", " gmbh", " ag",
        " bv", " b.v.", " nv", " n.v.", " pte", " pte.", " pty",
        " pty.", " lp", " llp",
    ):
        s = s.replace(suf, " ")
    # Collapse whitespace + drop punctuation
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _parse_claimants(raw: str) -> list[str]:
    """Split a multi-claimant string like 'Foo Ltd and Bar SA' into
    individual company names. Keeps individuals (returns their names)."""
    if not raw:
        return []
    # Common connectors : "and", "&", comma, semicolon
    text = raw.replace(" and ", "|").replace(" & ", "|")
    text = text.replace("; ", "|").replace(", ", "|")
    parts = [p.strip() for p in text.split("|") if p.strip()]
    return parts


def _is_target_sector(econsector: str) -> Optional[str]:
    if not econsector:
        return None
    for label, norm in TARGET_SECTORS.items():
        if label.lower() == econsector.lower():
            return norm
    return None


def _has_africa_exposure(respondent: str) -> bool:
    r = (respondent or "").lower()
    return any(hint in r for hint in AFRICA_HINTS)


# ─── Pass 1 : fetch all cases via API ──────────────────────────────────


def fetch_all_cases(client: httpx.Client) -> list[dict]:
    r = client.get(f"{ICSID_API}?dt={int(time.time() * 1000)}")
    r.raise_for_status()
    data = r.json()
    return data["data"]["GetAllCasesResult"]


# ─── Pass 2 : fetch per-case HTML details ──────────────────────────────


@dataclass
class ICSIDDetail:
    economic_sector: Optional[str] = None
    subject_matter: Optional[str] = None
    date_registered: Optional[str] = None
    status: Optional[str] = None
    status_date: Optional[str] = None
    claimant_counsel: Optional[str] = None
    respondent_counsel: Optional[str] = None


_DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})"
)
_MONTHS = {
    m: i + 1 for i, m in enumerate([
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    ])
}


def _iso_date(text: str) -> Optional[str]:
    if not text:
        return None
    m = _DATE_RE.search(text)
    if not m:
        return None
    mo = _MONTHS[m.group(1).lower()]
    return f"{int(m.group(3)):04d}-{mo:02d}-{int(m.group(2)):02d}"


_LABEL_KEYS = {
    "Economic Sector": "economic_sector",
    "Subject Matter": "subject_matter",
    "Date Registered": "date_registered",
    "Status": "status",
    "Status Date": "status_date",
    "Claimant(s) Counsel": "claimant_counsel",
    "Respondent(s) Counsel": "respondent_counsel",
}


def parse_case_detail(html: str) -> ICSIDDetail:
    tree = LexborHTMLParser(html)
    out = ICSIDDetail()
    for el in tree.css("div, span, strong, b, p"):
        label = (el.text() or "").strip().rstrip(":")
        key = _LABEL_KEYS.get(label)
        if not key:
            continue
        parent = el.parent
        if not parent:
            continue
        parent_text = (parent.text() or "").strip()
        value = parent_text.replace(label, "").lstrip(": \n\t").strip()
        if value and value != label:
            setattr(out, key, value)
    # Convert date fields to ISO when possible
    if out.date_registered:
        iso = _iso_date(out.date_registered)
        if iso:
            out.date_registered = iso
    if out.status_date:
        iso = _iso_date(out.status_date)
        if iso:
            out.status_date = iso
    return out


# ─── Save pipeline ─────────────────────────────────────────────────────


def _upsert_company(session, name: str) -> Company:
    canonical = _normalize_canonical(name)
    existing = session.scalar(
        select(Company).where(Company.canonical_key == canonical)
    )
    if existing:
        # Enrich alias list
        if name not in (existing.aliases or ""):
            existing.aliases = f"{existing.aliases or ''};{name}".strip(";")
        return existing
    company = Company(
        canonical_key=canonical,
        name=name,
        aliases=name,
    )
    session.add(company)
    session.flush()
    return company


def _upsert_icsid_case(
    session,
    api_case: dict,
    detail: ICSIDDetail,
    company: Company,
    sector_norm: str,
) -> ICSIDCase:
    case_no = api_case.get("caseno") or ""
    existing = session.scalar(
        select(ICSIDCase).where(ICSIDCase.case_number == case_no)
    )
    if existing:
        case = existing
    else:
        case = ICSIDCase(case_number=case_no)
        session.add(case)
    case.case_url = f"{ICSID_DETAIL}?CaseNo={case_no}"
    case.claimant = api_case.get("claimant") or ""
    case.respondent_state = api_case.get("respondent") or ""
    case.economic_sector = detail.economic_sector
    case.sector_normalized = sector_norm
    case.subject = detail.subject_matter
    case.registered_date = detail.date_registered
    case.status = (detail.status or api_case.get("status") or "").strip() or None
    case.status_date = detail.status_date
    case.claimant_counsel = detail.claimant_counsel
    case.respondent_counsel = detail.respondent_counsel
    case.company_id = company.id
    session.flush()
    return case


def _attach_signal(
    session,
    company: Company,
    case: ICSIDCase,
) -> None:
    # Keep one signal per ICSID case (idempotent on (company_id, kind, title))
    title = f"ICSID {case.case_number}"
    existing = session.scalar(
        select(CompanySignal).where(
            CompanySignal.company_id == company.id,
            CompanySignal.kind == "icsid",
            CompanySignal.title == title,
        )
    )
    detail = (
        f"vs {case.respondent_state or 'Respondent'} · "
        f"{case.economic_sector or 'Sector n/a'} · "
        f"{case.status or 'Status n/a'}"
    )
    heat = 85 if (case.status or "").lower() == "pending" else 55
    if existing:
        sig = existing
        is_new = False
    else:
        sig = CompanySignal(
            company_id=company.id,
            kind="icsid",
            title=title,
        )
        session.add(sig)
        is_new = True
    sig.detail = detail
    sig.source_url = case.case_url
    sig.source_name = "ICSID"
    sig.occurred_on = case.registered_date
    sig.heat = heat
    session.flush()
    # Incremental rollup : update signal_count + hot_score on each add
    # so the Clients tab shows data live during the scrape.
    if is_new:
        company.signal_count = (company.signal_count or 0) + 1
    company.hot_score = max(company.hot_score or 0, heat)
    if _has_africa_exposure(case.respondent_state or ""):
        company.has_africa_exposure = True


def _recompute_company_rollups(session, company: Company) -> None:
    """Update signal_count + hot_score + has_africa_exposure for a company."""
    signals = session.scalars(
        select(CompanySignal).where(CompanySignal.company_id == company.id)
    ).all()
    company.signal_count = len(signals)
    company.hot_score = max((s.heat for s in signals), default=0)
    # Africa exposure based on any ICSID respondent being African
    icsid_cases = session.scalars(
        select(ICSIDCase).where(ICSIDCase.company_id == company.id)
    ).all()
    company.has_africa_exposure = any(
        _has_africa_exposure(c.respondent_state or "") for c in icsid_cases
    )


# ─── Main runner ───────────────────────────────────────────────────────


def run_icsid_scrape(
    all_sectors: bool = False,
    rate_limit: float | None = None,
    skip_already: bool = True,
) -> dict:
    """Full scrape of the ICSID case database.

    ``all_sectors=False`` (default) keeps only cases in the 5 target
    sectors matching the commodities-arbitrator ICP. Set True for the
    entire 1 159-case corpus (useful for v2 if we broaden the ICP).

    ``skip_already=True`` (default) skips cases already saved — makes
    the scrape resumable after an interruption.
    """
    init_db()
    sleep_s = rate_limit if rate_limit is not None else max(
        settings.paw_rate_limit_sleep, 0.3
    )
    # Per-request timeout 15 s instead of 60 s — ICSID detail pages are
    # fast normally ; if one hangs, we skip it rather than block for a
    # full minute on each.
    client = httpx.Client(
        headers={"User-Agent": settings.paw_user_agent},
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=True,
    )
    stats = {
        "cases_total": 0, "cases_detail_fetched": 0, "cases_target_sector": 0,
        "companies_touched": 0, "signals_added": 0,
    }
    try:
        logger.info("Fetching ICSID case list (API)…")
        all_cases = fetch_all_cases(client)
        stats["cases_total"] = len(all_cases)
        logger.info(f"  {len(all_cases)} cases returned by API")

        with SessionLocal() as session:
            run = ScrapingRun(kind="icsid", urls_total=len(all_cases))
            session.add(run)
            session.commit()
            run_id = run.id
            companies_seen: set[int] = set()
            # Build set of already-scraped case numbers for resume
            if skip_already:
                already = {
                    row[0] for row in session.execute(
                        select(ICSIDCase.case_number)
                    ).all()
                }
                logger.info(f"  resume mode : {len(already)} cases already in DB (will skip)")
            else:
                already = set()

            for i, c in enumerate(all_cases, 1):
                case_no = (c.get("caseno") or "").strip()
                if not case_no:
                    continue
                if case_no in already:
                    continue
                # Fetch the detail HTML
                detail_url = f"{ICSID_DETAIL}?CaseNo={case_no}"
                try:
                    r = client.get(detail_url)
                    r.raise_for_status()
                    detail = parse_case_detail(r.text)
                    stats["cases_detail_fetched"] += 1
                except Exception as e:
                    logger.warning(f"  detail FAIL {case_no}: {e}")
                    continue
                sector_norm = _is_target_sector(detail.economic_sector or "")
                if not sector_norm and not all_sectors:
                    time.sleep(sleep_s)
                    continue
                stats["cases_target_sector"] += 1

                # Create one Company row per distinct claimant
                claimants = _parse_claimants(c.get("claimant") or "")
                for claimant_name in claimants:
                    company = _upsert_company(session, claimant_name)
                    # Set sector if blank
                    if not company.sector:
                        company.sector = sector_norm or "other"
                    if detail.subject_matter and not company.sector_detail:
                        company.sector_detail = detail.subject_matter
                    case = _upsert_icsid_case(
                        session, c, detail, company, sector_norm or "other",
                    )
                    _attach_signal(session, company, case)
                    stats["signals_added"] += 1
                    companies_seen.add(company.id)
                if i % 50 == 0 or i == len(all_cases):
                    session.commit()
                    logger.info(
                        f"  [{i:>4}/{len(all_cases)}] "
                        f"target={stats['cases_target_sector']} "
                        f"companies={len(companies_seen)} signals={stats['signals_added']}"
                    )
                time.sleep(sleep_s)

            # Rollups after everything in
            logger.info("Recomputing company rollups…")
            for cid in companies_seen:
                company = session.get(Company, cid)
                if company:
                    _recompute_company_rollups(session, company)
            stats["companies_touched"] = len(companies_seen)
            session.commit()
            run = session.get(ScrapingRun, run_id)
            if run:
                from datetime import datetime as _dt
                run.finished_at = _dt.utcnow()
                run.urls_ok = stats["cases_detail_fetched"]
                run.notes = (
                    f"target_sector={stats['cases_target_sector']} "
                    f"companies={stats['companies_touched']} "
                    f"signals={stats['signals_added']}"
                )
                session.commit()
    finally:
        client.close()
    logger.info(f"ICSID scrape done. stats={stats}")
    return stats
