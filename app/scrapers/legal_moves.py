"""Scrape new in-house General Counsel / Directeur Juridique appointments.

Fresh appointments (<6 months) are the softest signal available for a
litigation / arbitration lawyer : a new GC is still shaping her roster of
external counsel.

Sources queried :
  1. Google News RSS (multiple query variants)
  2. (planned) Decideurs Juridiques — rubric Nominations
  3. (planned) Les Échos Executives — rubric Carrières
  4. (planned) Google site:linkedin.com for public LinkedIn profiles
  5. (planned) Press-release pages of top-100 pool companies

For now we focus on Google News which is zero-friction and covers 60-70 %
of pertinent moves. The other sources will be added once this one is
battle-tested.
"""
from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

import httpx
from rapidfuzz import fuzz, process
from sqlalchemy import select

from app.config import settings
from app.database import Company, LegalMove, SessionLocal, init_db
from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402


# ─── Google News queries ──────────────────────────────────────────────

GN_RSS = "https://news.google.com/rss/search?q={q}&hl=fr&gl=FR&ceid=FR:fr"

QUERIES = [
    # Direct French appointments (variants)
    '"nommé directeur juridique" OR "nommée directrice juridique"',
    '"nouveau directeur juridique" OR "nouvelle directrice juridique"',
    '"directeur juridique" nomination',
    '"directrice juridique" nomination',
    '"nouvelle direction juridique"',
    '"nouveau directeur juridique groupe"',
    '"directrice juridique groupe" nomination',
    # Chief Legal Officer / Group General Counsel (variantes anglaises)
    '"chief legal officer" nomination',
    '"group general counsel" nomination',
    '"general counsel" nomination',
    '"nommé general counsel" OR "nommée general counsel"',
    '"nouveau general counsel"',
    '"head of legal" nomination',
    '"head of litigation" nomination',
    # Compliance / Group Compliance Officer / Secrétaire Général
    '"chief compliance officer" nomination',
    '"group compliance officer" nomination',
    '"directeur de la conformité" nomination',
    '"responsable contentieux" nomination',
    '"responsable juridique" nomination',
    '"secrétaire général" nomination groupe',
    '"vice-président juridique" nomination',
    # Sector-targeted (France)
    '"directeur juridique" construction nomination',
    '"directeur juridique" énergie nomination',
    '"directeur juridique" minier OR mines nomination',
    '"directeur juridique" BTP nomination',
    '"directeur juridique" aérospatial OR défense nomination',
    '"directrice juridique" infrastructure nomination',
    # Spin-off: nouvelle organisation, promotions internes
    '"promu directeur juridique" OR "promue directrice juridique"',
    '"prend la direction juridique"',
    '"prend ses fonctions" juridique',
    # Linkedin / specialist press surfacing
    '"appointed general counsel" France',
    '"joins as general counsel" France',
    '"nommée au poste" juridique groupe',
]


# ─── Parsing helpers ─────────────────────────────────────────────────


_PERSON_RE = re.compile(
    r"(?:nomm[ée]e?)\s+(?:au poste de\s+)?(?:directeur|directrice)\s+juridique"
    r"|(?:au poste de\s+)?general\s+counsel"
    r"|head of (?:litigation|legal)",
    re.IGNORECASE,
)


def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip()


_ROLE_PATTERNS = [
    (r"chief legal officer", "Chief Legal Officer"),
    (r"group general counsel", "Group General Counsel"),
    (r"general counsel", "General Counsel"),
    (r"head of legal", "Head of Legal"),
    (r"head of litigation", "Head of Litigation"),
    (r"chief compliance officer", "Chief Compliance Officer"),
    (r"group compliance officer", "Group Compliance Officer"),
    (r"vice[- ]pr[ée]sident(?:e)?\s+juridique", "Vice-Président Juridique"),
    (r"directeur\s+de\s+la\s+conformit[ée]", "Directeur de la Conformité"),
    (r"directrice\s+juridique", "Directrice juridique"),
    (r"directeur\s+juridique", "Directeur juridique"),
    (r"responsable\s+(?:du\s+)?contentieux", "Responsable Contentieux"),
    (r"responsable\s+juridique", "Responsable Juridique"),
    (r"secr[ée]taire\s+g[ée]n[ée]ral(?:e)?", "Secrétaire Général"),
    (r"direction\s+juridique", "Direction Juridique"),
]


def _detect_role(t: str) -> Optional[str]:
    low = t.lower()
    for pat, lbl in _ROLE_PATTERNS:
        if re.search(pat, low):
            return lbl
    return None


# Patterns, ordered from most to least specific
_PATTERNS = [
    # "Company : Person nommé(e)/est nommé(e)/prend/rejoint/promu(e)..."
    r"^(?P<company>.{3,120}?)\s*[:,—\-·]\s*(?P<person>[A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜŸÇ][\wÀ-ÿ'’\- ]{2,60})\s+(?:est\s+)?(?:nomm[ée]e?|promu[e]?|prend|rejoint|intègre|arrive|appointed|joins)",

    # "Person est nommé(e) ... de/chez/à Company"
    r"^(?P<person>[A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜŸÇ][\wÀ-ÿ'’\- ]{2,60}?)\s+(?:est\s+)?nomm[ée]e?\s+.*?\s+(?:de|chez|à|au|pour|chez|at|of)\s+(?P<company>.{3,120}?)(?:\s*[-—\|–·].*)?$",

    # "Person, nouvelle/nouveau ... de/chez Company"
    r"^(?P<person>[A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜŸÇ][\wÀ-ÿ'’\- ]{2,60}?)[,\s]+(?:nouvelle|nouveau|new)\s+.*?\s+(?:de|chez|à|au|pour|at|of)\s+(?P<company>.{3,120}?)(?:\s*[-—\|–·].*)?$",

    # "Company nomme Person ..."
    r"^(?P<company>.{3,120}?)\s+(?:nomme|promeut|accueille|intègre|welcomes|appoints)\s+(?P<person>[A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜŸÇ][\wÀ-ÿ'’\- ]{2,60})",

    # "Person rejoint/joins Company"
    r"^(?P<person>[A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜŸÇ][\wÀ-ÿ'’\- ]{2,60}?)\s+(?:rejoint|intègre|joins|arrives\s+at)\s+(?P<company>.{3,120}?)(?:\s*[-—\|–·].*)?$",

    # "Who's Who / Nomination · Person, nouveau DJ de Company"
    r"[Nn]omination\s*[\.·:,\-]+\s*(?P<person>[A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜŸÇ][\wÀ-ÿ'’\- ]{2,60}?)[,\s]+.*?\s+(?:de|chez|à|au|pour|at|of)\s+(?P<company>.{3,120}?)(?:\s*[-—\|–·].*)?$",
]


def _extract_person_and_company(title: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (person_name, company_name, role_title) parsed from a news title."""
    t = title or ""
    role = _detect_role(t)

    for pat in _PATTERNS:
        m = re.search(pat, t)
        if m:
            person_raw = m.group("person").strip()
            company_raw = m.group("company").strip()
            person = _clean_person(person_raw)
            company = _clean_company(company_raw)
            if person and company and _looks_like_person(person):
                return person, company, role

    return None, None, role


def _looks_like_person(s: str) -> bool:
    """Reject obviously non-person strings (e.g. role words captured by mistake)."""
    low = s.lower()
    bad = (
        "directeur", "directrice", "general counsel", "head of", "secrétaire",
        "responsable", "conseil", "president", "président", "manager", "nouvelle",
        "nouveau", "le groupe", "la direction", "intègre", "nomination",
    )
    if any(b in low for b in bad):
        return False
    # At least one uppercase + one space (first + last name)
    parts = s.split()
    if len(parts) < 2 or len(parts) > 5:
        return False
    return True


def _clean_person(s: str) -> Optional[str]:
    s = s.strip(" .,:;-—–\"'")
    # Strip leading role / title crumbs
    s = re.sub(r"^(?:M\.|Mme|Madame|Monsieur)\s+", "", s)
    if len(s) < 3 or len(s) > 80:
        return None
    if any(c.isdigit() for c in s):
        return None
    return s


def _clean_company(s: str) -> Optional[str]:
    s = s.strip(" .,:;-—–\"'")
    # Strip source domain suffix if any (e.g. " - Immoweek")
    s = re.sub(r"\s+[-—–]\s*[A-Z][\w\-]+(?:\.[a-z]{2,3})?$", "", s)
    if len(s) < 2 or len(s) > 200:
        return None
    return s


# ─── RSS fetch ────────────────────────────────────────────────────────


def _fetch_rss(client: httpx.Client, query: str) -> list[dict]:
    url = GN_RSS.format(q=quote(query))
    try:
        r = client.get(url, timeout=15.0)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"RSS fetch failed [{query[:40]}…]: {e}")
        return []
    items = []
    for it in root.iterfind(".//item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        src = it.find("source")
        source_name = (src.text if src is not None else "Google News").strip()
        items.append({
            "title": title, "link": link, "pub": pub, "source": source_name,
        })
    return items


def _parse_pub_date(pub: str) -> Optional[str]:
    """Return ISO date (YYYY-MM-DD) from an RSS pubDate like
    'Mon, 05 Oct 2026 07:28:00 GMT'."""
    try:
        dt = datetime.strptime(pub[:25], "%a, %d %b %Y %H:%M:%S")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


# ─── Pool matching ────────────────────────────────────────────────────


def _match_to_pool(company_name: str, pool_index: dict[str, tuple[int, str]]) -> Optional[tuple[int, str, int]]:
    """Return (company_id, sector, fuzz_score) if the name matches a company
    in our 2 400 pool above threshold.
    """
    if not company_name:
        return None
    norm = _normalize(company_name).lower()
    if not norm:
        return None
    hit = process.extractOne(norm, pool_index.keys(), scorer=fuzz.token_set_ratio)
    if not hit:
        return None
    choice, score, _ = hit
    if score < 85:
        return None
    cid, sector = pool_index[choice]
    return cid, sector, int(score)


# ─── Main ─────────────────────────────────────────────────────────────


def _targeted_queries_from_pool(session, top_n: int = 100) -> list[str]:
    """Build targeted Google News queries for the top-N companies of the pool."""
    rows = session.execute(
        select(Company.name).order_by(Company.hot_score.desc()).limit(top_n)
    ).all()
    queries = []
    for (name,) in rows:
        # Clean the name : drop legal suffix noise
        clean = re.sub(
            r"\b(SA|SAS|SARL|SNC|EURL|SASU|HOLDING|GROUPE|GROUP)\b\.?", "", name,
            flags=re.IGNORECASE,
        ).strip()
        if len(clean) < 3:
            continue
        queries.append(f'"{clean}" "directeur juridique" OR "directrice juridique"')
    return queries


def run(within_days: int = 365, include_targeted: bool = True) -> dict:
    init_db()
    cutoff = datetime.utcnow() - timedelta(days=within_days)
    stats = {"queries": 0, "items": 0, "parsed": 0, "saved": 0, "matched_pool": 0}

    pool_index: dict[str, tuple[int, str]] = {}
    with SessionLocal() as session:
        for c in session.scalars(select(Company)).all():
            key = _normalize(c.name).lower()
            if key:
                pool_index[key] = (c.id, c.sector or "")
            # also short form
            short = re.sub(r"\b(sa|sas|sarl|snc|eurl|sasu|holding|france|frs?|international|group)\b\.?",
                           "", key).strip()
            if short and short != key and short not in pool_index:
                pool_index[short] = (c.id, c.sector or "")
        targeted = _targeted_queries_from_pool(session, top_n=50) if include_targeted else []
    all_queries = QUERIES + targeted
    logger.info(f"Running {len(all_queries)} queries ({len(QUERIES)} generic + {len(targeted)} targeted)")

    client = httpx.Client(
        headers={
            "User-Agent": settings.paw_user_agent,
            "Accept": "application/rss+xml,application/xml,*/*",
        },
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=True,
    )
    seen_titles: set[str] = set()
    try:
        with SessionLocal() as session:
            # Pre-load existing moves to dedupe
            for mv in session.scalars(select(LegalMove)).all():
                sig = _normalize(f"{mv.full_name or ''} {mv.company_name or ''}").lower()
                if sig:
                    seen_titles.add(sig)

            for q in all_queries:
                stats["queries"] += 1
                items = _fetch_rss(client, q)
                stats["items"] += len(items)
                for it in items:
                    person, company, role = _extract_person_and_company(it["title"])
                    if not (person and company):
                        continue
                    sig = _normalize(f"{person} {company}").lower()
                    if sig in seen_titles:
                        continue
                    seen_titles.add(sig)
                    stats["parsed"] += 1

                    pub_date = _parse_pub_date(it["pub"])
                    if pub_date:
                        try:
                            if datetime.strptime(pub_date, "%Y-%m-%d") < cutoff:
                                continue
                        except Exception:
                            pass

                    # Match to pool
                    match = _match_to_pool(company, pool_index)
                    pool_id = sector = None
                    confidence = 60
                    if match:
                        pool_id, sector, score = match
                        confidence = min(95, 60 + (score - 85))
                        stats["matched_pool"] += 1

                    mv = LegalMove(
                        full_name=person,
                        role_title=role,
                        company_name=company,
                        company_sector=sector,
                        pool_company_id=pool_id,
                        announced_on=pub_date,
                        source_name=it["source"],
                        source_url=it["link"],
                        confidence=confidence,
                        notes=it["title"][:400],
                    )
                    session.add(mv)
                    stats["saved"] += 1
                session.commit()
    finally:
        client.close()

    logger.info(f"Legal moves scraped. stats={stats}")
    return stats


if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 365
    print(run(days))
