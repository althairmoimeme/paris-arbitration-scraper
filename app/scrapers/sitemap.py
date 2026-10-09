"""Sitemap enumeration for Yoast-SEO-powered sites (PAW, HKIAC).

Yoast exposes ``/sitemap_index.xml`` which points to sub-sitemaps per
content type (events, partners, pages, …). We fetch the index, pick the
sub-sitemaps we care about, and return the list of URLs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from app.config import settings
from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402


@dataclass(frozen=True)
class SitemapURL:
    loc: str
    lastmod: str | None = None


def _parse_sitemap(xml: str) -> list[SitemapURL]:
    """Yoast sitemaps are tiny well-formed XML — a regex is enough and
    sidesteps the lxml dependency on dinner-plate-sized XMLs."""
    locs = re.findall(r"<loc>([^<]+)</loc>", xml)
    mods = re.findall(r"<lastmod>([^<]+)</lastmod>", xml)
    out = []
    for i, loc in enumerate(locs):
        lastmod = mods[i] if i < len(mods) else None
        out.append(SitemapURL(loc=loc.strip(), lastmod=lastmod))
    return out


def fetch_sitemap(url: str, *, client: httpx.Client | None = None) -> list[SitemapURL]:
    """Fetch a single sitemap or sitemap index. Returns ``SitemapURL`` list.
    If it's an index, the caller must recurse (see ``fetch_all_sub_sitemaps``).
    """
    close = False
    if client is None:
        client = httpx.Client(
            headers={"User-Agent": settings.paw_user_agent},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        )
        close = True
    try:
        r = client.get(url)
        r.raise_for_status()
        return _parse_sitemap(r.text)
    finally:
        if close:
            client.close()


def fetch_all_sub_sitemaps(
    base_url: str,
    *,
    want: Iterable[str] = ("event", "partner", "edition"),
    client: httpx.Client | None = None,
) -> dict[str, list[SitemapURL]]:
    """Fetch the Yoast sitemap index then every sub-sitemap whose basename
    (``event-sitemap``, ``partner-sitemap``, …) matches ``want``.

    Returns a dict keyed by sub-sitemap short name :
      {
        "event":   [SitemapURL, …],
        "partner": [SitemapURL, …],
        "edition": [SitemapURL, …],
      }
    """
    index_url = f"{base_url.rstrip('/')}/sitemap_index.xml"
    close = False
    if client is None:
        client = httpx.Client(
            headers={"User-Agent": settings.paw_user_agent},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        )
        close = True
    try:
        logger.info(f"Fetching sitemap index : {index_url}")
        subs = fetch_sitemap(index_url, client=client)
        want_set = set(want)
        out: dict[str, list[SitemapURL]] = {}
        for sub in subs:
            # URL looks like https://.../event-sitemap.xml → key = "event"
            match = re.search(r"/([a-z0-9_-]+)-sitemap\.xml$", sub.loc)
            if not match:
                continue
            key = match.group(1)
            if key in want_set:
                logger.info(f"  Fetching sub-sitemap : {key}  ({sub.loc})")
                out[key] = fetch_sitemap(sub.loc, client=client)
                logger.info(f"    → {len(out[key])} URLs")
        return out
    finally:
        if close:
            client.close()


# ─── Simple HTML fetch helper used by downstream scrapers ────────────────


def fetch_html(url: str, *, client: httpx.Client | None = None) -> HTMLParser:
    """GET a URL and return a parsed ``HTMLParser`` tree. Caller handles
    retries at a higher level — we deliberately keep this call shallow."""
    close = False
    if client is None:
        client = httpx.Client(
            headers={"User-Agent": settings.paw_user_agent},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        )
        close = True
    try:
        r = client.get(url)
        r.raise_for_status()
        return HTMLParser(r.text)
    finally:
        if close:
            client.close()
