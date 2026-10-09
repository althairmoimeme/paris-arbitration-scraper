"""Parse ONE PAW partner (firm) page into a structured dict.

Partner pages contain: firm name (h1), description, website link, country,
LinkedIn URL, practice areas (list), associated events (we scrape years
from URLs), associated speakers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import httpx
from selectolax.lexbor import LexborHTMLParser

from app.config import settings


@dataclass
class PartnerData:
    source: str
    slug: str
    partner_url: str
    name: Optional[str] = None
    description: Optional[str] = None
    website: Optional[str] = None
    country: Optional[str] = None
    hq_address: Optional[str] = None
    practice_areas_raw: Optional[str] = None
    linkedin_url: Optional[str] = None
    twitter_url: Optional[str] = None
    event_slugs: list[str] = field(default_factory=list)
    years_present: list[int] = field(default_factory=list)
    speakers: list[dict] = field(default_factory=list)


_SLUG_RE = re.compile(r"/partner/([^/?#]+)/?")
_EVENT_SLUG_RE = re.compile(r"/event/([^/?#]+)/?")


def _norm_slug(url: str) -> Optional[str]:
    m = _SLUG_RE.search(url or "")
    return m.group(1) if m else None


def parse_partner_html(html: str, partner_url: str, *, source: str = "paw") -> PartnerData:
    tree = LexborHTMLParser(html)
    slug = _norm_slug(partner_url) or ""

    # Name : h1 fallback to og:title
    name = None
    h1 = tree.css_first("h1")
    if h1:
        name = (h1.text() or "").strip() or None
    if not name:
        og = tree.css_first("meta[property='og:title']")
        if og:
            name = (og.attributes.get("content") or "").strip() or None

    # Description : og:description + any ``.description`` block
    description = None
    og = tree.css_first("meta[property='og:description']")
    if og:
        description = (og.attributes.get("content") or "").strip() or None
    for sel in [".description", "[class*='description']", ".bio", ".firm-bio"]:
        el = tree.css_first(sel)
        if el and (el.text() or "").strip() and (description is None or len(description) < 80):
            description = (el.text() or "").strip()

    # Website : first external link that isn't PAW itself + isn't social
    website = None
    for a in tree.css("a[href^='http']"):
        href = a.attributes.get("href") or ""
        if "parisarbitrationweek.com" in href:
            continue
        if any(skip in href for skip in ("linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com")):
            continue
        website = href
        break

    # LinkedIn
    linkedin = None
    for a in tree.css("a[href*='linkedin.com']"):
        href = a.attributes.get("href") or ""
        if "/company/" in href or "/in/" in href:
            linkedin = href
            break

    # Twitter / X
    twitter = None
    for a in tree.css("a[href*='twitter.com'], a[href*='x.com/']"):
        href = a.attributes.get("href") or ""
        twitter = href
        break

    # Country / address — look for an ``.address`` or ``.country`` block,
    # fallback to body-wide keyword sniff
    country = None
    hq_address = None
    for sel in [".country", "[class*='country']"]:
        el = tree.css_first(sel)
        if el and (el.text() or "").strip():
            country = (el.text() or "").strip()
            break
    for sel in [".address", "[class*='address']", ".hq"]:
        el = tree.css_first(sel)
        if el and (el.text() or "").strip():
            hq_address = (el.text() or "").strip()
            break

    # Practice areas — look for an unordered list under a "practice" header
    practice_areas = None
    for h in tree.css("h2, h3, h4"):
        txt = (h.text() or "").strip().lower()
        if "practice" in txt or "domaine" in txt or "activit" in txt:
            # Walk to the next ul/ol
            nxt = h.next
            while nxt is not None:
                if nxt.tag in ("ul", "ol"):
                    items = [li.text().strip() for li in nxt.css("li") if (li.text() or "").strip()]
                    if items:
                        practice_areas = " ; ".join(items)
                    break
                nxt = nxt.next
            if practice_areas:
                break

    # Associated events — collect every /event/<slug>/ link + extract year from slug
    event_slugs: set[str] = set()
    years: set[int] = set()
    for a in tree.css("a[href*='/event/']"):
        href = a.attributes.get("href") or ""
        m = _EVENT_SLUG_RE.search(href)
        if m:
            es = m.group(1)
            event_slugs.add(es)
            ym = re.search(r"\b(20\d{2})\b", es)
            if ym:
                years.add(int(ym.group(1)))

    # Associated speakers — reuse the event-page selector logic
    speakers: list[dict] = []
    seen: set[str] = set()
    for fullname_el in tree.css(".fullname"):
        name_sp = (fullname_el.text() or "").strip()
        if not name_sp or name_sp in seen:
            continue
        seen.add(name_sp)
        parent = fullname_el.parent
        job_text = None
        for sib in (parent.iter(include_text=False) if parent else []):
            cls = (sib.attributes.get("class") or "")
            if "job" in cls:
                job_text = (sib.text() or "").strip()
                break
        speakers.append({"full_name": name_sp, "title_at_event": job_text or None})

    return PartnerData(
        source=source,
        slug=slug,
        partner_url=partner_url,
        name=name,
        description=description,
        website=website,
        country=country,
        hq_address=hq_address,
        practice_areas_raw=practice_areas,
        linkedin_url=linkedin,
        twitter_url=twitter,
        event_slugs=sorted(event_slugs),
        years_present=sorted(years),
        speakers=speakers,
    )


def fetch_and_parse_partner(url: str, *, client: httpx.Client, source: str = "paw") -> PartnerData:
    r = client.get(url)
    r.raise_for_status()
    return parse_partner_html(r.text, url, source=source)
