"""Catalog orchestrator — iterates sitemap URLs, parses each page, upserts
into the DB. Shared infrastructure for the Yoast-powered sites (PAW,
HKIAC). Rate-limited, resumable, and logs every failure.
"""
from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import (
    AttendanceSignal,  # noqa: F401 (used indirectly)
    Edition,
    Event,
    EventSpeaker,
    Firm,
    ScrapingRun,
    SessionLocal,
    Speaker,
    init_db,
)
from app.logging_setup import setup as _setup_logging
from app.scrapers.paw_event import EventData, fetch_and_parse_event
from app.scrapers.paw_partner import PartnerData, fetch_and_parse_partner
from app.scrapers.sitemap import fetch_all_sub_sitemaps

_setup_logging()
from loguru import logger  # noqa: E402


# ─── Helpers ────────────────────────────────────────────────────────────


def _normalize_name(name: str) -> str:
    """Lowercase + ASCII fold + strip common titles for the dedup key."""
    import re
    import unicodedata

    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower()
    # Strip common honorifics and post-nominal titles
    noise = (
        " kcmg ", " kc ", " qc ", " ll.m. ", " ll.m ", " llm ", " mba ",
        " dr. ", " dr ", " prof. ", " prof ", " prof.dr. ",
        " cfa ", " faciarb ", " faarb ", " ph.d. ", " ph.d ", " phd ",
    )
    s_padded = f" {s} "
    for n in noise:
        s_padded = s_padded.replace(n, " ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s_padded)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _upsert_firm(session, source: str, slug: str | None, name: str) -> Firm | None:
    """Get-or-create a firm by (source, slug) or by name when slug missing."""
    if not name and not slug:
        return None
    if slug:
        existing = session.scalar(
            select(Firm).where(Firm.source == source, Firm.slug == slug)
        )
        if existing:
            return existing
    if name:
        existing = session.scalar(
            select(Firm).where(Firm.source == source, Firm.name == name)
        )
        if existing:
            return existing
    firm = Firm(
        source=source,
        slug=slug or (name.lower().replace(" ", "-")[:255] if name else ""),
        partner_url=None,
        name=name or (slug or "unknown"),
    )
    session.add(firm)
    session.flush()
    return firm


def _upsert_speaker(session, name: str, display_name: str | None = None) -> Speaker:
    canonical = _normalize_name(name)
    existing = session.scalar(
        select(Speaker).where(Speaker.canonical_key == canonical)
    )
    if existing:
        if display_name and not existing.display_name:
            existing.display_name = display_name
        return existing
    speaker = Speaker(
        canonical_key=canonical,
        full_name=name,
        display_name=display_name or name,
    )
    session.add(speaker)
    session.flush()
    return speaker


def _upsert_edition(session, source: str, year: int) -> None:
    """Create (source, year) if missing; stats are updated at the end."""
    existing = session.scalar(
        select(Edition).where(Edition.source == source, Edition.year == year)
    )
    if existing:
        return
    session.add(Edition(
        source=source,
        year=year,
        slug=str(year),
        url=f"{settings.paw_base_url}/edition/{year}/",
    ))
    session.flush()


# ─── Save one event into DB ─────────────────────────────────────────────


def save_event(session, d: EventData) -> Event:
    # Edition (idempotent)
    if d.edition_year:
        _upsert_edition(session, d.source, d.edition_year)

    # Host firm
    host_firm = None
    if d.host_firm_name:
        host_firm = _upsert_firm(session, d.source, d.host_firm_slug, d.host_firm_name)

    # Event upsert on (source, slug)
    existing = session.scalar(
        select(Event).where(Event.source == d.source, Event.slug == d.slug)
    )
    if existing:
        event = existing
    else:
        event = Event(source=d.source, slug=d.slug, event_url=d.event_url,
                      edition_year=d.edition_year or 0)
        session.add(event)
    event.title = d.title or event.title or d.slug
    event.edition_year = d.edition_year or event.edition_year or 0
    event.host_firm_id = host_firm.id if host_firm else None
    event.date = d.date
    event.time_start = d.time_start
    event.time_end = d.time_end
    event.format = d.format
    event.location = d.location
    event.description = d.description
    event.program = d.program
    event.themes = ";".join(d.themes) if d.themes else None
    event.regions = ";".join(d.regions) if d.regions else None
    session.flush()

    # Speakers
    # Clear existing event-speaker joins for a clean re-scrape
    session.query(EventSpeaker).filter(EventSpeaker.event_id == event.id).delete()
    for spk in d.speakers:
        if not spk.full_name:
            continue
        speaker = _upsert_speaker(session, spk.full_name, spk.full_name)
        # Attach LinkedIn / profile from the event page if we don't have it yet
        if spk.profile_url and not speaker.profile_url:
            speaker.profile_url = spk.profile_url
        # Attach the firm as "firm at this event" (not necessarily the current firm)
        at_event_firm = _upsert_firm(session, d.source, None, spk.firm_name_at_event) if spk.firm_name_at_event else None
        session.add(EventSpeaker(
            event_id=event.id,
            speaker_id=speaker.id,
            firm_name_at_event=spk.firm_name_at_event,
            role=spk.role or "panelist",
            title_at_event=spk.title_at_event,
        ))
        # Default current firm = most recently seen (last event in iteration wins ;
        # fine for first pass, we recompute later from event_speakers to be rigorous).
        if at_event_firm is not None and speaker.firm_id is None:
            speaker.firm_id = at_event_firm.id
            speaker.current_title = spk.title_at_event
    session.flush()
    return event


def save_partner(session, d: PartnerData) -> Firm:
    firm = session.scalar(
        select(Firm).where(Firm.source == d.source, Firm.slug == d.slug)
    )
    if firm is None:
        firm = Firm(source=d.source, slug=d.slug, name=d.name or d.slug)
        session.add(firm)
    firm.partner_url = d.partner_url
    firm.name = d.name or firm.name
    firm.description = d.description or firm.description
    firm.website = d.website or firm.website
    firm.country = d.country or firm.country
    firm.hq_address = d.hq_address or firm.hq_address
    firm.practice_areas_raw = d.practice_areas_raw or firm.practice_areas_raw
    firm.events_hosted_count = len(d.event_slugs)
    firm.speakers_count = len(d.speakers)
    if d.linkedin_url and not firm.linkedin_url:
        firm.linkedin_url = d.linkedin_url
    if d.twitter_url and not firm.twitter_url:
        firm.twitter_url = d.twitter_url
    # Years present : will be refined after event scrape (match by slug)
    if d.years_present:
        firm.years_present = ";".join(str(y) for y in d.years_present)
    session.flush()
    return firm


# ─── Run functions ──────────────────────────────────────────────────────


def _make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": settings.paw_user_agent},
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
        http2=False,
    )


def run_paw_events(limit: int | None = None, resume: bool = True) -> dict:
    """Scrape every PAW event in sitemap. Resumable : skips events already
    in DB unless ``resume=False`` (then re-scrape to refresh)."""
    init_db()
    base = settings.paw_base_url
    client = _make_client()
    try:
        maps = fetch_all_sub_sitemaps(base, want=("event", "edition", "partner"), client=client)
    finally:
        pass  # keep client open for the real scrape below

    # The sitemap sometimes includes the /event/ root listing and other
    # non-event pages — keep only URLs with an actual slug after /event/.
    import re as _re
    event_urls = [
        u.loc for u in maps.get("event", [])
        if _re.search(r"/event/[^/]+/?$", u.loc)
        and not u.loc.rstrip("/").endswith("/event")
        and not u.loc.rstrip("/").endswith("/calendar")
    ]
    if limit:
        event_urls = event_urls[:limit]

    logger.info(f"PAW event scrape : {len(event_urls)} URLs")

    run = ScrapingRun(kind="paw_events", urls_total=len(event_urls))
    with SessionLocal() as session:
        session.add(run)
        session.commit()
        run_id = run.id

    stats = {"ok": 0, "fail": 0, "skipped": 0, "speakers_added": 0}
    with SessionLocal() as session:
        if resume:
            existing_slugs = {
                row[0] for row in session.execute(
                    select(Event.slug).where(Event.source == "paw")
                ).all()
            }
        else:
            existing_slugs = set()

        for i, url in enumerate(event_urls, 1):
            import re
            slug_m = re.search(r"/event/([^/?#]+)/?", url)
            slug = slug_m.group(1) if slug_m else ""
            if resume and slug in existing_slugs:
                stats["skipped"] += 1
                continue
            try:
                d = fetch_and_parse_event(url, client=client, source="paw")
                evt = save_event(session, d)
                stats["ok"] += 1
                stats["speakers_added"] += len(d.speakers)
                if i % 25 == 0:
                    session.commit()
                    logger.info(
                        f"  [{i:>3}/{len(event_urls)}] ok={stats['ok']} fail={stats['fail']}"
                        f" skip={stats['skipped']} speakers={stats['speakers_added']}"
                    )
            except Exception as e:
                stats["fail"] += 1
                logger.warning(f"  FAIL {url} :: {e}")
            time.sleep(settings.paw_rate_limit_sleep)

        session.commit()
        # Close out the run record
        run = session.get(ScrapingRun, run_id)
        if run is not None:
            run.finished_at = datetime.utcnow()
            run.urls_ok = stats["ok"]
            run.urls_failed = stats["fail"]
            run.notes = f"skipped={stats['skipped']} speakers={stats['speakers_added']}"
            session.commit()
    client.close()

    logger.info(
        f"PAW events done. ok={stats['ok']} fail={stats['fail']} skip={stats['skipped']}"
        f" speakers_added={stats['speakers_added']}"
    )
    return stats


def run_paw_partners(limit: int | None = None, resume: bool = True) -> dict:
    """Scrape every PAW partner firm page."""
    init_db()
    base = settings.paw_base_url
    client = _make_client()
    try:
        maps = fetch_all_sub_sitemaps(base, want=("partner",), client=client)
    finally:
        pass

    partner_urls = [u.loc for u in maps.get("partner", []) if "/partner/" in u.loc and not u.loc.rstrip("/").endswith("/partner")]
    if limit:
        partner_urls = partner_urls[:limit]
    logger.info(f"PAW partner scrape : {len(partner_urls)} URLs")

    run = ScrapingRun(kind="paw_partners", urls_total=len(partner_urls))
    with SessionLocal() as session:
        session.add(run)
        session.commit()
        run_id = run.id

    stats = {"ok": 0, "fail": 0, "skipped": 0}
    with SessionLocal() as session:
        if resume:
            existing_slugs = {
                row[0] for row in session.execute(
                    select(Firm.slug).where(Firm.source == "paw", Firm.partner_url.isnot(None))
                ).all()
            }
        else:
            existing_slugs = set()
        for i, url in enumerate(partner_urls, 1):
            import re
            slug_m = re.search(r"/partner/([^/?#]+)/?", url)
            slug = slug_m.group(1) if slug_m else ""
            if not slug:
                continue
            if resume and slug in existing_slugs:
                stats["skipped"] += 1
                continue
            try:
                d = fetch_and_parse_partner(url, client=client, source="paw")
                save_partner(session, d)
                stats["ok"] += 1
                if i % 25 == 0:
                    session.commit()
                    logger.info(
                        f"  [{i:>3}/{len(partner_urls)}] ok={stats['ok']} fail={stats['fail']} skip={stats['skipped']}"
                    )
            except Exception as e:
                stats["fail"] += 1
                logger.warning(f"  FAIL {url} :: {e}")
            time.sleep(settings.paw_rate_limit_sleep)

        session.commit()
        run = session.get(ScrapingRun, run_id)
        if run is not None:
            run.finished_at = datetime.utcnow()
            run.urls_ok = stats["ok"]
            run.urls_failed = stats["fail"]
            run.notes = f"skipped={stats['skipped']}"
            session.commit()
    client.close()

    logger.info(f"PAW partners done. ok={stats['ok']} fail={stats['fail']} skip={stats['skipped']}")
    return stats
