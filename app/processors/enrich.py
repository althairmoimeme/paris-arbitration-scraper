"""Post-scrape enrichment : speaker firm-move detection, taxonomy
classification, and commodity relevance scoring.

Run after ``scrape_paw.py`` to populate the qualification columns on
Firm and Speaker — the XLSX exporter reads those columns verbatim.
"""
from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy import func, select

from app.database import (
    Edition,
    Event,
    EventSpeaker,
    Firm,
    SessionLocal,
    Speaker,
)
from app.logging_setup import setup as _setup_logging
from app.processors.taxonomy import (
    classify_firm_type,
    classify_role,
    score_commodity_relevance,
    tag_geo_focus,
    tag_practice_areas,
)

_setup_logging()
from loguru import logger  # noqa: E402


# ─── Firm enrichment ───────────────────────────────────────────────────


def enrich_firms() -> int:
    """Compute firm_type, practice_areas, geo focus and commodity score
    for every firm in the DB."""
    n = 0
    with SessionLocal() as session:
        firms = session.scalars(select(Firm)).all()
        for firm in firms:
            desc = firm.description or ""
            prac = firm.practice_areas_raw or ""
            blob_type = f"{firm.name} {desc} {prac}"
            firm.firm_type = classify_firm_type(firm.name, blob_type)

            # Aggregate the firm's event themes (practice-area hints)
            theme_blob_parts: list[str] = []
            for e in session.scalars(
                select(Event).where(Event.host_firm_id == firm.id)
            ).all():
                theme_blob_parts.extend(filter(None, [
                    e.title, e.description, e.themes, e.regions, e.program,
                ]))
            theme_blob = " ".join(theme_blob_parts)

            full_blob = f"{blob_type} {theme_blob}"
            firm.practice_areas = ";".join(tag_practice_areas(full_blob)) or None
            firm.geographic_focus = ";".join(tag_geo_focus(full_blob)) or None

            cscore = score_commodity_relevance(blob_type, theme_blob)
            firm.commodity_relevance = cscore.score
            firm.commodity_reasoning = cscore.reasoning

            n += 1
        session.commit()
    logger.info(f"Enriched {n} firms")
    return n


# ─── Speaker enrichment ────────────────────────────────────────────────


def _speaker_firm_history(
    session, speaker_id: int
) -> list[dict]:
    """Return the chronological list of {year, firm_name, title} the
    speaker has appeared under. Groups appearances by (year, firm).
    """
    rows = session.execute(
        select(
            Event.edition_year,
            EventSpeaker.firm_name_at_event,
            EventSpeaker.title_at_event,
        )
        .join(Event, Event.id == EventSpeaker.event_id)
        .where(EventSpeaker.speaker_id == speaker_id)
        .order_by(Event.edition_year.asc())
    ).all()
    history: dict[tuple[int, str], str | None] = {}
    for year, firm_name, title in rows:
        if year is None:
            continue
        key = (int(year), firm_name or "unknown")
        if key not in history or (title and not history[key]):
            history[key] = title
    return [
        {"year": year, "firm_name": firm, "title": title}
        for (year, firm), title in sorted(history.items())
    ]


def enrich_speakers() -> dict:
    """Compute role_category, commodity relevance, firm history and move
    detection for every speaker."""
    stats = {"total": 0, "moved": 0, "commodity_high": 0}
    with SessionLocal() as session:
        speakers = session.scalars(select(Speaker)).all()
        for s in speakers:
            stats["total"] += 1
            # ── Multi-source presence
            sources = {
                row[0] for row in session.execute(
                    select(Event.source)
                    .join(EventSpeaker, EventSpeaker.event_id == Event.id)
                    .where(EventSpeaker.speaker_id == s.id)
                ).all()
            }
            s.sources_present = ";".join(sorted(sources)) if sources else None
            s.sources_count = len(sources)
            # ── Firm history + move detection
            history = _speaker_firm_history(session, s.id)
            if history:
                s.firm_history = json.dumps(history, ensure_ascii=False)
                years = [h["year"] for h in history]
                s.first_year = min(years)
                s.last_year = max(years)
                s.years_present = ";".join(str(y) for y in sorted(set(years)))
                distinct_firms = {
                    h["firm_name"] for h in history
                    if h["firm_name"] and h["firm_name"] != "unknown"
                }
                s.has_moved = len(distinct_firms) >= 2
                if s.has_moved:
                    stats["moved"] += 1
                # Most-recent firm = current firm
                last = history[-1]
                if last.get("firm_name") and last["firm_name"] != "unknown":
                    firm = session.scalar(
                        select(Firm).where(Firm.name == last["firm_name"])
                    )
                    if firm:
                        s.firm_id = firm.id
                if last.get("title"):
                    s.current_title = last["title"]
                s.events_count = sum(1 for _ in history)

            # ── Role
            s.role_category = classify_role(s.current_title or "")

            # ── Practice areas from the events they panelled
            event_blob_parts: list[str] = []
            for es, e in session.execute(
                select(EventSpeaker, Event)
                .join(Event, Event.id == EventSpeaker.event_id)
                .where(EventSpeaker.speaker_id == s.id)
            ).all():
                event_blob_parts.extend(filter(None, [
                    e.title, e.description, e.themes, e.regions,
                ]))
            event_blob = " ".join(event_blob_parts)

            s.practice_areas = ";".join(tag_practice_areas(event_blob)) or None
            s.geographic_focus = ";".join(tag_geo_focus(event_blob)) or None

            # ── Commodity relevance : combine firm score + personal panels
            # + ASA specializations (closed-list, high signal)
            firm_score = 0
            if s.firm_id:
                firm = session.get(Firm, s.firm_id)
                if firm:
                    firm_score = firm.commodity_relevance or 0
            personal = score_commodity_relevance(
                s.current_title or "",
                event_blob,
            )
            # ASA specializations hit specific commodity buckets — strong signal
            asa_boost = 0
            asa_hits: list[str] = []
            if s.specializations:
                specs_lower = s.specializations.lower()
                for strong_spec in (
                    "oil and gas", "metals and mining", "power / energy",
                    "transport / infrastructure", "investment dispute",
                    "international trade law", "construction / engineering",
                ):
                    if strong_spec in specs_lower:
                        asa_boost += 25 if "oil and gas" in strong_spec or "mining" in strong_spec else 15
                        asa_hits.append(strong_spec)
                asa_boost = min(60, asa_boost)  # Cap ASA contribution
            # Legacy blend (firm + personal) — always applied as the floor
            legacy = 0.6 * firm_score + 0.4 * personal.score
            # ASA specs blend — only kicks in when the person has ASA
            # specializations; otherwise it would wrongly dilute the signal
            if asa_boost:
                combo = 0.4 * firm_score + 0.3 * personal.score + 0.3 * asa_boost
                blended = max(legacy, combo)
            else:
                blended = legacy
            s.commodity_relevance = min(100, int(round(blended)))
            reasoning_parts = [f"firm={firm_score}", f"personal={personal.score}"]
            if asa_boost:
                reasoning_parts.append(f"ASA_specs=[{','.join(asa_hits)}]={asa_boost}")
            reasoning_parts.append(f"({personal.reasoning[:150]})")
            s.commodity_reasoning = "; ".join(reasoning_parts)
            if s.commodity_relevance >= 50:
                stats["commodity_high"] += 1

        session.commit()

    # ── Edition totals (events + unique speakers per year)
    with SessionLocal() as session:
        editions = session.scalars(select(Edition)).all()
        for ed in editions:
            n_events = session.scalar(
                select(func.count(Event.id))
                .where(Event.source == ed.source, Event.edition_year == ed.year)
            ) or 0
            n_speakers = session.scalar(
                select(func.count(func.distinct(EventSpeaker.speaker_id)))
                .join(Event, Event.id == EventSpeaker.event_id)
                .where(Event.source == ed.source, Event.edition_year == ed.year)
            ) or 0
            ed.events_count = int(n_events)
            ed.speakers_count = int(n_speakers)
        session.commit()

    logger.info(
        f"Enriched {stats['total']} speakers · {stats['moved']} with firm moves · "
        f"{stats['commodity_high']} with commodity_relevance >= 50"
    )
    return stats


def run_full_enrichment() -> dict:
    firms_n = enrich_firms()
    speakers = enrich_speakers()
    return {"firms_enriched": firms_n, **speakers}
