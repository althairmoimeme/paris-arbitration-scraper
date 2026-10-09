"""Swiss Arbitration Summit scraper.

The Summit uses WordPress with a /speakers/ listing page that embeds
every speaker as a ``[class*='speaker-card']`` block linking to an
individual ``/speaker/<slug>/`` profile. There is NO per-speaker email
or phone (the only contact info on each profile is the Swiss Arbitration
Association's generic footer — ``events@swissarbitration.org`` /
``+41 22 310 37 31``). We capture :

  - Full name
  - Combined "title + firm" string (from ``.speaker-card__job``)
  - Image URL
  - Speaker profile URL
  - External personal website (when present and non-social)

The real BD value here is cross-matching against PAW speakers (via
canonical dedup key) — "active on Paris ANDS Zurich" is a strong signal
for commodities arbitration since Geneva/Zurich = global commodity
trading hub (Glencore, Trafigura, Vitol, Mercuria).
"""
from __future__ import annotations

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


BASE = "https://swissarbitrationsummit.org"
LISTING = f"{BASE}/speakers/"
SOURCE = "swiss_summit"
# The Summit's canonical annual edition — hard-coded until we see
# multiple historical editions on-site.
DEFAULT_YEAR = 2026
EDITION_SLUG = "swiss-summit-2026"


@dataclass
class SwissSpeaker:
    full_name: str
    title_firm_raw: Optional[str] = None   # the ``.speaker-card__job`` text
    title: Optional[str] = None            # heuristic split of title_firm_raw
    firm_name: Optional[str] = None
    profile_url: Optional[str] = None
    image_url: Optional[str] = None
    external_website: Optional[str] = None


def _split_title_firm(raw: str) -> tuple[Optional[str], Optional[str]]:
    """"Partner, LALIVE & Vice-President, ICC Swiss ..." → ("Partner",
    "LALIVE"). The pattern is "<title>, <firm>" with an optional second
    role at the same firm or ICC committee. Pick the first segment as
    role and the second as firm."""
    if not raw:
        return None, None
    # Normalize whitespace / &amp;
    cleaned = raw.replace("&amp;", "&").strip()
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    if len(parts) == 1:
        # Single segment — treat as a title with no firm
        return parts[0], None
    # Walk forward until we find a token that looks like a firm name
    # (uppercase word, "LLP", "SA", "Associates", etc.) — fall back to parts[1]
    firm_hint = re.compile(
        r"(LLP|LLC|SA|SAS|SARL|Chambers|Associates|Advokat|Partners|Group|"
        r"Institute|Commission|Centre|Center|Court|University|School|"
        r"Attorneys|Avocats|&|Co\.|Ltd|GmbH|AG|KG|Inc)",
        flags=re.I,
    )
    for i in range(1, len(parts)):
        if firm_hint.search(parts[i]) or parts[i].isupper():
            return parts[0], parts[i]
    return parts[0], parts[1]


def parse_speakers_listing(html: str) -> list[SwissSpeaker]:
    tree = LexborHTMLParser(html)
    out: list[SwissSpeaker] = []
    seen: set[str] = set()
    for card in tree.css("a.speaker-card, a[class*='speaker-card']"):
        href = card.attributes.get("href") or ""
        name_el = card.css_first("h4") or card.css_first(".speaker-card__names")
        job_el = card.css_first(".speaker-card__job") or card.css_first("p")
        img_el = card.css_first("img")
        if not name_el:
            continue
        name = (name_el.text() or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        job_firm = (job_el.text() or "").strip() if job_el else None
        title, firm = _split_title_firm(job_firm or "")
        out.append(SwissSpeaker(
            full_name=name,
            title_firm_raw=job_firm,
            title=title,
            firm_name=firm,
            profile_url=href if href.startswith("http") else f"{BASE}{href}",
            image_url=(img_el.attributes.get("src") if img_el else None),
        ))
    return out


def parse_speaker_profile(html: str) -> dict:
    """Extract whatever extras exist on an individual speaker page.

    The useful signal is ``external_website`` (sometimes present, often
    not) — all contact fields (mailto / tel / linkedin) only ever carry
    the Swiss Arbitration Association's generic footer values.
    """
    tree = LexborHTMLParser(html)
    external_website = None
    for a in tree.css("a[href^='http']"):
        href = a.attributes.get("href") or ""
        if any(skip in href for skip in (
            "swissarbitrationsummit.org", "swissarbitration.org",
            "linkedin.com", "twitter.com", "x.com", "facebook.com",
            "instagram.com", "youtube.com", "eur.cvent.me", "gmpg.org",
            "trivialmass.ch",
        )):
            continue
        external_website = href
        break
    return {"external_website": external_website}


def _upsert_firm(session, name: str | None, slug_hint: str | None = None) -> Firm | None:
    if not name:
        return None
    slug = (slug_hint or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))[:255]
    existing = session.scalar(
        select(Firm).where(Firm.source == SOURCE, Firm.name == name)
    )
    if existing:
        return existing
    firm = Firm(source=SOURCE, slug=slug, name=name)
    session.add(firm)
    session.flush()
    return firm


def _normalize_name(name: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    for noise in (" kc ", " qc ", " kcmg ", " dr ", " dr. ", " prof ", " prof. "):
        s = s.replace(noise, " ")
    s = re.sub(r"[^a-z0-9 ]+", " ", f" {s} ")
    return re.sub(r"\s+", " ", s).strip()


def _upsert_speaker(session, s: SwissSpeaker) -> Speaker:
    """Dedupes against the global Speaker table (incl. PAW). Returns the
    existing or newly-created Speaker row."""
    canonical = _normalize_name(s.full_name)
    existing = session.scalar(
        select(Speaker).where(Speaker.canonical_key == canonical)
    )
    if existing:
        if s.full_name and not existing.display_name:
            existing.display_name = s.full_name
        if s.profile_url and not existing.profile_url:
            existing.profile_url = s.profile_url
        return existing
    speaker = Speaker(
        canonical_key=canonical,
        full_name=s.full_name,
        display_name=s.full_name,
        profile_url=s.profile_url,
    )
    session.add(speaker)
    session.flush()
    return speaker


def _ensure_edition_and_dummy_event(session) -> Event:
    """Swiss Summit publishes one annual edition, no per-event URLs.
    Create a single carrier Event so EventSpeaker rows link cleanly to
    the (source, year) tuple — reusing the same relational model as PAW.
    """
    edition = session.scalar(
        select(Edition).where(Edition.source == SOURCE, Edition.year == DEFAULT_YEAR)
    )
    if not edition:
        edition = Edition(
            source=SOURCE, year=DEFAULT_YEAR,
            slug=EDITION_SLUG, url=LISTING,
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
            event_url=LISTING,
            edition_year=DEFAULT_YEAR,
            title="Swiss Arbitration Summit 2026",
            format="in-person",
        )
        session.add(event)
        session.flush()
    return event


def run_swiss_summit(
    enrich_profiles: bool = False,
    rate_limit: float | None = None,
) -> dict:
    """Scrape the Swiss Summit speakers listing. When ``enrich_profiles``
    is True, we also walk each individual speaker page for the external
    website (adds ~2 min for 125 speakers)."""
    init_db()
    sleep_s = rate_limit if rate_limit is not None else settings.paw_rate_limit_sleep

    client = httpx.Client(
        headers={"User-Agent": settings.paw_user_agent},
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
    )
    try:
        logger.info(f"Fetching Swiss Summit listing : {LISTING}")
        r = client.get(LISTING)
        r.raise_for_status()
        speakers = parse_speakers_listing(r.text)
    finally:
        if not enrich_profiles:
            client.close()
    logger.info(f"Parsed {len(speakers)} unique speakers from the listing")

    # Optionally fetch each profile for extras (external_website)
    extras: dict[str, dict] = {}
    if enrich_profiles and speakers:
        for i, sp in enumerate(speakers, 1):
            if not sp.profile_url:
                continue
            try:
                r = client.get(sp.profile_url)
                r.raise_for_status()
                extras[sp.full_name] = parse_speaker_profile(r.text)
            except Exception as e:
                logger.warning(f"  profile fail {sp.full_name}: {e}")
            time.sleep(sleep_s)
            if i % 25 == 0:
                logger.info(f"  profiles fetched: {i}/{len(speakers)}")
        client.close()

    # ── Save
    stats = {"total": 0, "new_speakers": 0, "matched_paw": 0, "firms_created": 0}
    with SessionLocal() as session:
        event = _ensure_edition_and_dummy_event(session)
        for sp in speakers:
            stats["total"] += 1
            was_new = session.scalar(
                select(Speaker).where(
                    Speaker.canonical_key == _normalize_name(sp.full_name)
                )
            ) is None
            if not was_new:
                stats["matched_paw"] += 1
            else:
                stats["new_speakers"] += 1
            speaker = _upsert_speaker(session, sp)
            # Attach external website if we scraped it
            if enrich_profiles:
                ext = (extras.get(sp.full_name) or {}).get("external_website")
                if ext and not speaker.profile_url:
                    speaker.profile_url = ext
            # Firm (per-source, so we don't contaminate the PAW firms table)
            firm = None
            if sp.firm_name:
                was_new_firm = session.scalar(
                    select(Firm).where(
                        Firm.source == SOURCE, Firm.name == sp.firm_name
                    )
                ) is None
                firm = _upsert_firm(session, sp.firm_name)
                if was_new_firm:
                    stats["firms_created"] += 1
            # Link via EventSpeaker (idempotent — unique on event+speaker+role)
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
                    firm_name_at_event=sp.firm_name,
                    role="panelist",
                    title_at_event=sp.title,
                ))
        session.commit()

    logger.info(
        f"Swiss Summit done. speakers_total={stats['total']}  new={stats['new_speakers']}"
        f"  matched_PAW={stats['matched_paw']}  firms_added={stats['firms_created']}"
    )
    return stats
