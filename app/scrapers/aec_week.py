"""African Energy Week scraper.

AEC Week is NOT an arbitration conference — it's the top African energy
summit (oil, gas, mining, LNG, power). We scrape it because the
audience = commodity producers / traders / state energy co's / African
regulators : exactly the buyer side for a commodities arbitrator.

Structure : one page per edition at ``aecweek.com/speakers-<year>`` ;
each speaker is wrapped in an ``<article class="m-speakers-list__list__
items__item">`` carrying :

  - ``h2 > a``    : full name + profile URL (relative)
  - ``.meta``     : role + firm concatenated with whitespace runs
  - ``img``       : head-shot

The site is dynamic — ``/speakers-2024`` currently redirects to
``/speakers-2026``, so we can only grab the current year's speakers
reliably. Historical editions would need archive.org.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
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


BASE = "https://aecweek.com"
SOURCE = "aec_week"


@dataclass
class AECSpeaker:
    full_name: str
    role: Optional[str] = None
    firm_name: Optional[str] = None
    profile_url: Optional[str] = None
    image_url: Optional[str] = None


def _normalize_name(name: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    for noise in (" kc ", " qc ", " kcmg ", " dr ", " dr. ", " prof ",
                  " prof. ", " phd ", " ph.d. ", " ph.d ",
                  ", phd", ", ph.d.", ", ph.d"):
        s = s.replace(noise, " ")
    s = re.sub(r"[^a-z0-9 ]+", " ", f" {s} ")
    return re.sub(r"\s+", " ", s).strip()


def _parse_meta(text: str) -> tuple[Optional[str], Optional[str]]:
    """The ``.header__meta`` block renders ``role`` and ``firm`` as two
    chunks separated by whitespace runs of 3+ consecutive spaces/newlines.
    Split on that pattern and keep the first two non-empty chunks.
    """
    if not text:
        return None, None
    chunks = [c.strip() for c in re.split(r"\s{3,}", text.strip()) if c.strip()]
    if not chunks:
        return None, None
    if len(chunks) == 1:
        return chunks[0], None
    return chunks[0], chunks[1]


def parse_speakers_page(html: str) -> list[AECSpeaker]:
    tree = LexborHTMLParser(html)
    out: list[AECSpeaker] = []
    seen: set[str] = set()
    for art in tree.css("article.m-speakers-list__list__items__item, article[class*='speakers-list']"):
        link = art.css_first(".m-speakers-list__list__items__item__header__title a")
        if not link:
            link = art.css_first("h2 a")
        if not link:
            continue
        name = (link.text() or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        href = link.attributes.get("href") or ""
        if href and not href.startswith("http"):
            href = f"{BASE}/{href.lstrip('/')}"
        meta_el = art.css_first(".m-speakers-list__list__items__item__header__meta")
        role, firm = _parse_meta(meta_el.text() if meta_el else "")
        img = art.css_first("img")
        img_url = (img.attributes.get("src") or img.attributes.get("data-src") or None) if img else None
        out.append(AECSpeaker(
            full_name=name,
            role=role,
            firm_name=firm,
            profile_url=href or None,
            image_url=img_url,
        ))
    return out


def _upsert_firm(session, name: str | None) -> Firm | None:
    if not name:
        return None
    existing = session.scalar(
        select(Firm).where(Firm.source == SOURCE, Firm.name == name)
    )
    if existing:
        return existing
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:255]
    firm = Firm(source=SOURCE, slug=slug, name=name)
    session.add(firm)
    session.flush()
    return firm


def _upsert_speaker(session, s: AECSpeaker) -> tuple[Speaker, bool]:
    canonical = _normalize_name(s.full_name)
    existing = session.scalar(
        select(Speaker).where(Speaker.canonical_key == canonical)
    )
    if existing:
        if s.profile_url and not existing.profile_url:
            existing.profile_url = s.profile_url
        return existing, False
    speaker = Speaker(
        canonical_key=canonical,
        full_name=s.full_name,
        display_name=s.full_name,
        profile_url=s.profile_url,
    )
    session.add(speaker)
    session.flush()
    return speaker, True


def _ensure_edition_and_event(session, year: int) -> Event:
    edition = session.scalar(
        select(Edition).where(Edition.source == SOURCE, Edition.year == year)
    )
    if not edition:
        edition = Edition(
            source=SOURCE,
            year=year,
            slug=f"aec-week-{year}",
            url=f"{BASE}/speakers-{year}/",
        )
        session.add(edition)
        session.flush()
    slug = f"aec-week-{year}"
    event = session.scalar(
        select(Event).where(Event.source == SOURCE, Event.slug == slug)
    )
    if not event:
        event = Event(
            source=SOURCE,
            slug=slug,
            event_url=f"{BASE}/speakers-{year}/",
            edition_year=year,
            title=f"African Energy Week {year}",
            format="in-person",
            location="Cape Town, South Africa",
            themes="Energy & natural resources;Commodities & trading",
        )
        session.add(event)
        session.flush()
    return event


def run_aec_week(years: tuple[int, ...] = (2026,)) -> dict:
    """Scrape AEC Week speakers for each year. By default only the current
    edition is accessible on the live site — pass ``(2024, 2025, 2026)``
    if you point at archive.org snapshots."""
    init_db()
    client = httpx.Client(
        headers={"User-Agent": settings.paw_user_agent},
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
    )
    stats = {"total": 0, "new_speakers": 0, "matched_prior": 0,
             "firms_created": 0}
    try:
        with SessionLocal() as session:
            for y in years:
                url = f"{BASE}/speakers-{y}/"
                logger.info(f"Fetching AEC Week {y} : {url}")
                try:
                    r = client.get(url)
                    r.raise_for_status()
                except Exception as e:
                    logger.warning(f"  AEC {y} failed : {e}")
                    continue
                # The site redirects old years to the current one; detect that
                final_url = str(r.url)
                redirected = str(y) not in final_url
                if redirected:
                    logger.warning(f"  AEC {y} redirects to {final_url} — skipping")
                    continue
                event = _ensure_edition_and_event(session, y)
                speakers = parse_speakers_page(r.text)
                logger.info(f"  Parsed {len(speakers)} speakers")
                for sp in speakers:
                    stats["total"] += 1
                    speaker, was_new = _upsert_speaker(session, sp)
                    if was_new:
                        stats["new_speakers"] += 1
                    else:
                        stats["matched_prior"] += 1
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
                    # EventSpeaker link
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
                            title_at_event=sp.role,
                        ))
                time.sleep(settings.paw_rate_limit_sleep)
            session.commit()
    finally:
        client.close()

    logger.info(
        f"AEC Week done. total={stats['total']}  new={stats['new_speakers']}"
        f"  matched_prior={stats['matched_prior']}  firms_added={stats['firms_created']}"
    )
    return stats
