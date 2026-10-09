"""ASA Profiles scraper (profiles.swissarbitration.org).

**The** gold mine for a commodities arbitrator : 1006 ASA / SCCM members
with full contact details (email + phone + LinkedIn + website + full
postal address), career history, specializations (41 fields incl.
Oil and Gas / Metals and Mining / Power & Energy / Investment Dispute)
and categories (Arbitrator / Counsel / Mediator / Legal expert / Technical
expert).

The Laravel + Inertia.js stack embeds the entire users array as a
JSON blob in the ``data-page`` attribute of ``<div id="app">`` on every
plain-HTML GET. We iterate ``/?page=1`` → ``/?page=126`` (8 users per
page) and extract everything server-side — no custom headers needed,
no JS rendering, no rate-limit on anonymous reads.
"""
from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from selectolax.lexbor import LexborHTMLParser
from sqlalchemy import select

from app.config import settings
from app.database import (
    Edition,
    Event,
    EventSpeaker,
    Firm,
    SessionLocal,
    Speaker,
    init_db,
)
from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402


BASE = "https://profiles.swissarbitration.org"
SOURCE = "asa_profiles"
DEFAULT_YEAR = 2026  # ASA is a persistent directory, not an edition
EDITION_SLUG = "asa-profiles"


@dataclass
class ASAProfile:
    id: int
    name: str
    company: Optional[str] = None
    position: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    linkedin_url: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None            # ISO2
    country_name: Optional[str] = None
    nationalities: Optional[str] = None       # "DE, FR" joined
    languages: Optional[str] = None
    birth_year: Optional[int] = None
    member_type: Optional[str] = None        # asa / sccm
    categories: list[str] = field(default_factory=list)   # Arbitrator / Counsel / ...
    specializations: list[str] = field(default_factory=list)
    legal_backgrounds: list[str] = field(default_factory=list)
    positions_history: Optional[str] = None
    slug: Optional[str] = None
    profile_url: Optional[str] = None
    address: Optional[str] = None
    post_code: Optional[str] = None


def _parse_data_page(html_text: str) -> dict:
    tree = LexborHTMLParser(html_text)
    app = tree.css_first("#app")
    if not app:
        raise RuntimeError("No #app element — the site changed")
    raw = app.attributes.get("data-page") or ""
    if not raw:
        raise RuntimeError("No data-page attribute — the site changed")
    decoded = html.unescape(raw)
    return json.loads(decoded)


def _profile_from_user(u: dict) -> ASAProfile:
    cats = [sc.get("name") for sc in (u.get("specialization_categories") or []) if sc.get("name")]
    return ASAProfile(
        id=int(u["id"]),
        name=u.get("name") or "",
        company=u.get("company"),
        position=u.get("position"),
        email=u.get("email"),
        phone=u.get("phone"),
        website=u.get("website"),
        linkedin_url=u.get("linkedin_url"),
        city=u.get("city"),
        country=u.get("country"),
        country_name=u.get("country_name"),
        nationalities=u.get("nationalities"),
        languages=u.get("languages"),
        birth_year=int(u["birth_year"]) if u.get("birth_year") and str(u["birth_year"]).isdigit() else None,
        member_type=u.get("member_type"),
        categories=cats,
        specializations=list(u.get("specializations") or []),
        legal_backgrounds=list(u.get("legal_backgrounds") or []),
        positions_history=u.get("positions"),
        slug=u.get("slug"),
        profile_url=f"{BASE}/profile/{u.get('slug')}" if u.get("slug") else None,
        address=u.get("address"),
        post_code=u.get("post_code"),
    )


def fetch_page(client: httpx.Client, page: int) -> tuple[list[ASAProfile], dict]:
    r = client.get(f"{BASE}/?page={page}")
    r.raise_for_status()
    data = _parse_data_page(r.text)
    users_block = data["props"]["users"]
    users_raw = users_block.get("data", [])
    profiles = [_profile_from_user(u) for u in users_raw]
    return profiles, users_block.get("meta", {})


def _normalize_name(name: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    for noise in (" kc ", " qc ", " kcmg ", " dr ", " dr. ", " prof ",
                  " prof. ", " phd ", " ph.d. ", " ph.d "):
        s = s.replace(noise, " ")
    s = re.sub(r"[^a-z0-9 ]+", " ", f" {s} ")
    return re.sub(r"\s+", " ", s).strip()


def _upsert_firm(session, name: str | None, website: str | None = None) -> Firm | None:
    if not name:
        return None
    existing = session.scalar(
        select(Firm).where(Firm.source == SOURCE, Firm.name == name)
    )
    if existing:
        if website and not existing.website:
            existing.website = website
        return existing
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:255]
    firm = Firm(source=SOURCE, slug=slug, name=name, website=website)
    session.add(firm)
    session.flush()
    return firm


def _upsert_speaker(session, p: ASAProfile) -> tuple[Speaker, bool]:
    canonical = _normalize_name(p.name)
    existing = session.scalar(
        select(Speaker).where(Speaker.canonical_key == canonical)
    )
    if existing:
        # Enrich with the contact info ASA has (best source for emails)
        if p.email and not existing.email:
            existing.email = p.email
        if p.phone and not existing.phone:
            existing.phone = p.phone
        if p.linkedin_url and not existing.linkedin_url:
            existing.linkedin_url = p.linkedin_url
        if p.profile_url and not existing.profile_url:
            existing.profile_url = p.profile_url
        if p.country and not existing.country:
            existing.country = p.country_name or p.country
        if p.birth_year and not existing.birth_year:
            existing.birth_year = p.birth_year
        if p.specializations and not existing.specializations:
            existing.specializations = ";".join(p.specializations)
        if p.member_type and not existing.member_type:
            existing.member_type = p.member_type
        return existing, False
    speaker = Speaker(
        canonical_key=canonical,
        full_name=p.name,
        display_name=p.name,
        email=p.email,
        phone=p.phone,
        linkedin_url=p.linkedin_url,
        profile_url=p.profile_url,
        country=p.country_name or p.country,
        birth_year=p.birth_year,
        current_title=p.position,
        specializations=";".join(p.specializations) if p.specializations else None,
        member_type=p.member_type,
    )
    session.add(speaker)
    session.flush()
    return speaker, True


def _ensure_edition_and_event(session) -> Event:
    edition = session.scalar(
        select(Edition).where(Edition.source == SOURCE, Edition.year == DEFAULT_YEAR)
    )
    if not edition:
        edition = Edition(
            source=SOURCE, year=DEFAULT_YEAR,
            slug=EDITION_SLUG, url=BASE,
        )
        session.add(edition)
        session.flush()
    event = session.scalar(
        select(Event).where(Event.source == SOURCE, Event.slug == EDITION_SLUG)
    )
    if not event:
        event = Event(
            source=SOURCE,
            slug=EDITION_SLUG,
            event_url=BASE,
            edition_year=DEFAULT_YEAR,
            title="ASA Profiles — Swiss Arbitration Association members directory",
            format="online",
            themes="Arbitration directory",
            location="Switzerland + international",
        )
        session.add(event)
        session.flush()
    return event


def run_asa_profiles(
    max_pages: int | None = None,
    rate_limit: float | None = None,
) -> dict:
    """Scrape every ASA profile. Pagination handled via ``?page=N`` ; the
    first page's meta tells us the total page count."""
    init_db()
    sleep_s = rate_limit if rate_limit is not None else settings.paw_rate_limit_sleep
    client = httpx.Client(
        headers={"User-Agent": settings.paw_user_agent},
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
    )
    stats = {"profiles": 0, "new_speakers": 0, "matched_prior": 0,
             "with_email": 0, "with_phone": 0, "with_linkedin": 0,
             "firms_created": 0}
    try:
        with SessionLocal() as session:
            event = _ensure_edition_and_event(session)
            # Discover total page count via the first fetch
            first_profiles, meta = fetch_page(client, 1)
            last_page = meta.get("last_page", 1)
            total = meta.get("total", 0)
            if max_pages:
                last_page = min(last_page, max_pages)
            logger.info(f"ASA Profiles : {total} profiles across {meta.get('last_page')} pages — scraping {last_page}")

            def _save_batch(profiles: list[ASAProfile]) -> None:
                for p in profiles:
                    stats["profiles"] += 1
                    if p.email: stats["with_email"] += 1
                    if p.phone: stats["with_phone"] += 1
                    if p.linkedin_url: stats["with_linkedin"] += 1
                    speaker, is_new = _upsert_speaker(session, p)
                    if is_new: stats["new_speakers"] += 1
                    else: stats["matched_prior"] += 1
                    if p.company:
                        was_new_firm = session.scalar(
                            select(Firm).where(
                                Firm.source == SOURCE, Firm.name == p.company
                            )
                        ) is None
                        firm = _upsert_firm(session, p.company, p.website)
                        if was_new_firm: stats["firms_created"] += 1
                        if firm and not speaker.firm_id:
                            speaker.firm_id = firm.id
                    es = session.scalar(
                        select(EventSpeaker).where(
                            EventSpeaker.event_id == event.id,
                            EventSpeaker.speaker_id == speaker.id,
                            EventSpeaker.role == "panelist",
                        )
                    )
                    if es is None:
                        session.add(EventSpeaker(
                            event_id=event.id,
                            speaker_id=speaker.id,
                            firm_name_at_event=p.company,
                            role="panelist",
                            title_at_event=p.position,
                        ))

            _save_batch(first_profiles)
            session.commit()

            for page in range(2, last_page + 1):
                try:
                    profiles, _ = fetch_page(client, page)
                except Exception as e:
                    logger.warning(f"  page {page} FAIL : {e}")
                    continue
                _save_batch(profiles)
                if page % 20 == 0 or page == last_page:
                    session.commit()
                    logger.info(
                        f"  [{page:>3}/{last_page}] profiles={stats['profiles']}  "
                        f"email={stats['with_email']}  tel={stats['with_phone']}  "
                        f"li={stats['with_linkedin']}  matched_prior={stats['matched_prior']}"
                    )
                time.sleep(sleep_s)
            session.commit()
    finally:
        client.close()

    logger.info(
        f"ASA Profiles done. total={stats['profiles']}  new={stats['new_speakers']}  "
        f"matched_prior={stats['matched_prior']}  "
        f"with_email={stats['with_email']} ({stats['with_email']*100//max(1,stats['profiles'])}%)  "
        f"with_phone={stats['with_phone']}  with_linkedin={stats['with_linkedin']}  "
        f"firms_added={stats['firms_created']}"
    )
    return stats
