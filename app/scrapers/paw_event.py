"""Parse ONE PAW event page into a structured dict.

The page template is stable: fixed CSS classes (``.fullname`` / ``.job`` /
``.company``) wrap each speaker, taxonomy terms for themes/regions sit in
anchor tags with ``/theme/`` and ``/region/`` href prefixes, and the
event metadata (date, time, format, host firm) is rendered inline in the
header block.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import httpx
from selectolax.lexbor import LexborHTMLParser

from app.config import settings


@dataclass
class SpeakerRef:
    """One speaker as listed on an event page.

    ``firm_name_at_event`` is captured verbatim — the dedup pipeline uses
    it later to detect firm moves (same person seen at Firm A in 2023,
    Firm B in 2026).
    """

    full_name: str
    firm_name_at_event: Optional[str] = None
    title_at_event: Optional[str] = None
    profile_url: Optional[str] = None
    role: str = "panelist"
    image_url: Optional[str] = None


@dataclass
class EventData:
    source: str
    slug: str
    event_url: str
    edition_year: Optional[int] = None
    title: Optional[str] = None
    date: Optional[str] = None            # ISO YYYY-MM-DD if parseable
    date_text: Optional[str] = None       # raw string as rendered
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    format: Optional[str] = None          # in-person | hybrid | online
    location: Optional[str] = None
    host_firm_name: Optional[str] = None
    host_firm_slug: Optional[str] = None
    description: Optional[str] = None
    program: Optional[str] = None
    themes: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    speakers: list[SpeakerRef] = field(default_factory=list)


_SLUG_RE = re.compile(r"/event/([^/?#]+)/?")
_SPEAKER_SLUG_RE = re.compile(r"/speakers?/([^/?#]+)/?")
_PARTNER_SLUG_RE = re.compile(r"/partner/([^/?#]+)/?")
_THEME_SLUG_RE = re.compile(r"/theme/([^/?#]+)/?")
_REGION_SLUG_RE = re.compile(r"/region/([^/?#]+)/?")

_MONTHS_FR = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "août": 8, "aout": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}
_MONTHS_EN = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _parse_date_text(text: str) -> tuple[Optional[str], Optional[int]]:
    """Returns (iso_date, year). Tolerates FR / EN / short forms."""
    if not text:
        return None, None
    t = " ".join(text.strip().split())
    # Try ISO 2026-03-24 first
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return m.group(0), int(m.group(1))
    # "Tuesday, 24 March 2026" or "24 mars 2026"
    m = re.search(
        r"(\d{1,2})\s+([A-Za-zéèêûàâôîïç]+)\s+(\d{4})",
        t,
    )
    if m:
        day = int(m.group(1))
        month_token = m.group(2).lower()
        year = int(m.group(3))
        month = _MONTHS_FR.get(month_token) or _MONTHS_EN.get(month_token)
        if month:
            return f"{year:04d}-{month:02d}-{day:02d}", year
    # Fallback: just extract year
    m = re.search(r"\b(20\d{2})\b", t)
    if m:
        return None, int(m.group(1))
    return None, None


def _parse_time_range(text: str) -> tuple[Optional[str], Optional[str]]:
    """Extract start / end from a '08:30 – 10:30' style string."""
    if not text:
        return None, None
    m = re.search(r"(\d{1,2})[:h](\d{2})\s*(?:[–-]|to)\s*(\d{1,2})[:h](\d{2})", text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}", f"{int(m.group(3)):02d}:{m.group(4)}"
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}", None
    return None, None


def _detect_format(text_blob: str) -> Optional[str]:
    """Pick in-person / hybrid / online from any text block that mentions it."""
    t = text_blob.lower()
    for kw, label in (
        ("in-person", "in-person"),
        ("in person", "in-person"),
        ("présentiel", "in-person"),
        ("présentielle", "in-person"),
        ("hybrid", "hybrid"),
        ("hybride", "hybrid"),
        ("online", "online"),
        ("virtual", "online"),
        ("en ligne", "online"),
        ("remote", "online"),
    ):
        if kw in t:
            return label
    return None


def _norm_slug(url: str) -> Optional[str]:
    m = _SLUG_RE.search(url or "")
    return m.group(1) if m else None


def _extract_speakers(tree: LexborHTMLParser) -> list[SpeakerRef]:
    """Each speaker is wrapped in a div containing ``.fullname`` + ``.job`` +
    ``.company``. We walk by ``.fullname`` and look at nearby siblings to
    collect the trio consistently.
    """
    speakers: list[SpeakerRef] = []
    seen: set[str] = set()
    for fullname_el in tree.css(".fullname"):
        name = (fullname_el.text() or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        # Walk up to the common parent (two levels is enough for the PAW template)
        parent = fullname_el.parent
        if parent is None:
            continue
        job_text = None
        firm_text = None
        profile_url = None
        image_url = None
        # ``.job`` and ``.company`` are siblings of ``.fullname`` inside the parent
        for sib in parent.iter(include_text=False):
            cls = (sib.attributes.get("class") or "")
            if "job" in cls and job_text is None:
                job_text = (sib.text() or "").strip()
            elif "company" in cls and firm_text is None:
                firm_text = (sib.text() or "").strip()
        # Walk up one more level for the <a href="/speakers/.../"> + <img>
        grandparent = parent.parent
        if grandparent is not None:
            a = grandparent.css_first("a[href*='/speakers/']")
            if a:
                href = a.attributes.get("href") or ""
                if href:
                    profile_url = href if href.startswith("http") else f"{settings.paw_base_url}{href}"
            img = grandparent.css_first("img")
            if img:
                image_url = img.attributes.get("src")
        speakers.append(SpeakerRef(
            full_name=name,
            firm_name_at_event=firm_text or None,
            title_at_event=job_text or None,
            profile_url=profile_url,
            image_url=image_url,
            role="panelist",
        ))
    return speakers


def _extract_taxonomy(tree: LexborHTMLParser) -> tuple[list[str], list[str]]:
    themes: set[str] = set()
    regions: set[str] = set()
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        text = (a.text() or "").strip()
        if not text:
            continue
        if _THEME_SLUG_RE.search(href):
            themes.add(text)
        elif _REGION_SLUG_RE.search(href):
            regions.add(text)
    return sorted(themes), sorted(regions)


def _extract_host_firm(tree: LexborHTMLParser) -> tuple[Optional[str], Optional[str]]:
    """Primary ``/partner/<slug>/`` link in the page = the host firm.

    Skips the breadcrumb/menu link that goes to the ``/partner/`` listing
    page (no slug, text usually just "Partners"). Picks the first link
    whose href actually resolves to a specific firm slug.
    """
    for a in tree.css("a[href*='/partner/']"):
        href = a.attributes.get("href") or ""
        m = _PARTNER_SLUG_RE.search(href)
        if not m:
            # Breadcrumb-style /partner/ link — not a firm
            continue
        slug = m.group(1)
        # Guard against the "partner" category root sometimes slugged as "page"
        if slug in {"page", "category", "all"}:
            continue
        name = (a.text() or "").strip() or None
        return name, slug
    return None, None


def parse_event_html(html: str, event_url: str, *, source: str = "paw") -> EventData:
    tree = LexborHTMLParser(html)
    slug = _norm_slug(event_url) or ""

    # Title : prefer <h1>, then og:title fallback
    title = None
    h1 = tree.css_first("h1")
    if h1:
        title = (h1.text() or "").strip()
    if not title:
        og = tree.css_first("meta[property='og:title']")
        if og:
            title = (og.attributes.get("content") or "").strip()

    # Date : look for a block with class containing "date" first,
    # otherwise scan the whole header text blob
    date_text = None
    for sel in [".event-date", ".date", "[class*='date']", "time"]:
        el = tree.css_first(sel)
        if el and (el.text() or "").strip():
            date_text = (el.text() or "").strip()
            break
    if not date_text:
        header = tree.css_first(".event-header, header, .intro, .event-intro")
        if header:
            date_text = (header.text() or "").strip()
    iso_date, year = _parse_date_text(date_text or "")

    # Time
    time_text = None
    for sel in ["[class*='time']", ".hour"]:
        el = tree.css_first(sel)
        if el and (el.text() or "").strip():
            time_text = (el.text() or "").strip()
            break
    time_start, time_end = _parse_time_range(time_text or (date_text or ""))

    # Format : scan whole page body text
    body = tree.css_first("body")
    body_text = (body.text() or "")[:5000] if body else ""
    fmt = _detect_format(body_text)

    # Location : address or ".location"
    location = None
    for sel in [".location", "[class*='location']", ".address", "[class*='address']"]:
        el = tree.css_first(sel)
        if el and (el.text() or "").strip():
            location = (el.text() or "").strip()
            break

    # Description : meta og:description or ".description"
    description = None
    og = tree.css_first("meta[property='og:description']")
    if og:
        description = (og.attributes.get("content") or "").strip() or None
    if not description:
        for sel in [".description", "[class*='description']", ".content p"]:
            el = tree.css_first(sel)
            if el and (el.text() or "").strip():
                description = (el.text() or "").strip()
                break

    # Host firm
    host_name, host_slug = _extract_host_firm(tree)

    # Taxonomy
    themes, regions = _extract_taxonomy(tree)

    # Speakers
    speakers = _extract_speakers(tree)

    # Fallback: if we didn't catch the year from the date, pull from slug
    if year is None:
        m = re.search(r"\b(20\d{2})\b", slug)
        if m:
            year = int(m.group(1))

    return EventData(
        source=source,
        slug=slug,
        event_url=event_url,
        edition_year=year,
        title=title,
        date=iso_date,
        date_text=date_text,
        time_start=time_start,
        time_end=time_end,
        format=fmt,
        location=location,
        host_firm_name=host_name,
        host_firm_slug=host_slug,
        description=description,
        program=None,
        themes=themes,
        regions=regions,
        speakers=speakers,
    )


def fetch_and_parse_event(url: str, *, client: httpx.Client, source: str = "paw") -> EventData:
    r = client.get(url)
    r.raise_for_status()
    return parse_event_html(r.text, url, source=source)
