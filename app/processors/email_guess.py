"""Guess emails for PAW speakers whose firm website is known.

Covers the ~619 speakers (30 % of PAW alumni) who are at firms with a
captured website but weren't in the ASA Profiles directory. The target
population is overwhelmingly BigLaw / mid-market arbitration firms,
where the ``firstname.lastname@domain`` convention is reliable.

Confidence :
  - ``guessed_mx_ok``  : the firm's domain has valid MX records (= email
                        servers accept mail at that domain) — this is
                        the honest cold-outreach floor
  - ``guessed_no_mx``  : no MX records — unlikely to deliver, kept so
                        the user can review

For stronger confidence (first-touch bounce protection) the user can
run the resulting list through Hunter.io / Clearout later. We never do
SMTP VRFY probes — they're brittle, often banned, and can get the
sender IP flagged.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import dns.resolver  # type: ignore
from sqlalchemy import or_, select

from app.database import Firm, SessionLocal, Speaker, Event, EventSpeaker
from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402


# ─── Name / domain helpers ─────────────────────────────────────────────


# Firm-specific overrides when the website domain is NOT the mail domain.
# Collected from publicly-known BigLaw email conventions.
_FIRM_DOMAIN_OVERRIDES: dict[str, str] = {
    "latham & watkins": "lw.com",
    "latham watkins": "lw.com",
    "allen & overy": "aoshearman.com",
    "a&o shearman": "aoshearman.com",
    "wordstone dispute resolution": "wordstone.com",
    "freshfields bruckhaus deringer": "freshfields.com",
    "cleary gottlieb steen & hamilton": "cgsh.com",
    "simpson thacher & bartlett": "stblaw.com",
    "sullivan & cromwell": "sullcrom.com",
    "paul hastings": "paulhastings.com",
    "jones day": "jonesday.com",
    "white & case": "whitecase.com",
    "wilmer cutler pickering hale and dorr": "wilmerhale.com",
    "shearman & sterling": "shearman.com",
    "weil gotshal & manges": "weil.com",
    "arnold & porter": "arnoldporter.com",
    "mayer brown": "mayerbrown.com",
    "hogan lovells": "hoganlovells.com",
    "wilmerhale": "wilmerhale.com",
}


def _domain_from_website(website: str, firm_name: str = "") -> Optional[str]:
    override = _FIRM_DOMAIN_OVERRIDES.get(firm_name.strip().lower())
    if override:
        return override
    try:
        p = urlparse(website if website.startswith("http") else f"https://{website}")
    except Exception:  # noqa: BLE001
        return None
    host = (p.netloc or p.path or "").lower()
    host = host.replace("www.", "").split("/")[0]
    # Strip any port
    host = host.split(":")[0]
    # Skip obvious non-mail domains
    if not host or "." not in host:
        return None
    for bad in (
        "linkedin.com", "twitter.com", "x.com", "facebook.com",
        "instagram.com", "youtube.com", "tiktok.com", "wikipedia.org",
    ):
        if host == bad or host.endswith(f".{bad}"):
            return None
    return host


def _normalize_name(name: str) -> tuple[str, str]:
    """Return (first, last) in lowercase ASCII, with hyphens kept and
    any post-nominal titles (KC/QC/LL.M/PhD/Prof.) stripped.

    "Diana PARAGUACUTO-MAHEO"  → ("diana", "paraguacuto-maheo")
    "Prof. Dr. Maxi SCHERER"   → ("maxi",  "scherer")
    "Noah RUBINS KC"           → ("noah",  "rubins")
    "H.E. Haitham Al Ghais"    → ("haitham", "al-ghais")
    """
    # Decode accents / cedillas to ASCII
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.strip()
    # Strip common honorifics (prefixes)
    for pre in ("h.e.", "h.e", "dr.", "dr ", "prof.", "prof ",
                "prof. dr.", "mr.", "mr ", "ms.", "ms ", "mrs.", "mrs ",
                "mme ", "m. "):
        if s.lower().startswith(pre):
            s = s[len(pre):].strip()
    # Strip post-nominals
    tokens = s.replace(",", " ").split()
    clean_tokens: list[str] = []
    POSTNOMINAL = {
        "kc", "qc", "kcmg", "ll.m.", "ll.m", "llm", "ph.d.", "ph.d", "phd",
        "mba", "cfa", "faciarb", "faarb",
    }
    for t in tokens:
        if t.lower().rstrip(".") in POSTNOMINAL:
            continue
        clean_tokens.append(t)
    if not clean_tokens:
        return "", ""
    # First token = given name, last token(s) = surname (keep hyphens)
    first = clean_tokens[0].lower()
    # Join remaining tokens with hyphens for multi-part surnames
    last = "-".join(t.lower() for t in clean_tokens[1:]) if len(clean_tokens) > 1 else ""
    # Replace any whitespace in individual tokens
    first = re.sub(r"[^a-z0-9'-]", "", first)
    last = re.sub(r"[^a-z0-9'-]+", "-", last).strip("-")
    return first, last


def _guess_email(first: str, last: str, domain: str) -> str:
    """Build the top-choice ``firstname.lastname@domain`` pattern.

    For single-token names (first-only), fall back to just the first name.
    """
    if not first:
        return ""
    local = f"{first}.{last}" if last else first
    local = local.replace("'", "")
    return f"{local}@{domain}"


# ─── MX cache + verifier ───────────────────────────────────────────────


_mx_cache: dict[str, bool] = {}
_resolver = dns.resolver.Resolver()
_resolver.timeout = 3.0
_resolver.lifetime = 5.0


def _has_mx(domain: str) -> bool:
    if domain in _mx_cache:
        return _mx_cache[domain]
    try:
        ans = _resolver.resolve(domain, "MX")
        ok = len(ans) > 0
    except Exception:  # noqa: BLE001 — DNS failures = no MX
        ok = False
    _mx_cache[domain] = ok
    return ok


# ─── Main pass ─────────────────────────────────────────────────────────


@dataclass
class GuessStat:
    speakers_total: int = 0
    skipped_already: int = 0
    skipped_no_firm: int = 0
    skipped_no_domain: int = 0
    guessed_mx_ok: int = 0
    guessed_no_mx: int = 0


def run_email_guess(only_paw: bool = True, dry_run: bool = False) -> GuessStat:
    """Walk every PAW speaker without an email and generate the top
    ``firstname.lastname@firm-domain`` guess. MX-validated guesses are
    saved to the DB with ``email_source='guessed_mx_ok'``.

    Pass ``dry_run=True`` to compute stats without writing to the DB.
    """
    stat = GuessStat()
    with SessionLocal() as session:
        q = (
            select(Speaker, Firm)
            .outerjoin(Firm, Firm.id == Speaker.firm_id)
            .where(or_(Speaker.email.is_(None), Speaker.email == ""))
        )
        if only_paw:
            paw_speaker_ids = {
                row[0] for row in session.execute(
                    select(EventSpeaker.speaker_id)
                    .join(Event, Event.id == EventSpeaker.event_id)
                    .where(Event.source == "paw")
                ).all()
            }
            all_rows = session.execute(q).all()
            rows = [(sp, firm) for sp, firm in all_rows if sp.id in paw_speaker_ids]
        else:
            rows = session.execute(q).all()

        logger.info(f"Email guessing pass : {len(rows)} candidates (PAW only={only_paw})")

        for sp, firm in rows:
            stat.speakers_total += 1
            if sp.email:
                stat.skipped_already += 1
                continue
            if firm is None or not firm.website:
                stat.skipped_no_firm += 1
                continue
            domain = _domain_from_website(firm.website, firm.name or "")
            if not domain:
                stat.skipped_no_domain += 1
                continue
            first, last = _normalize_name(sp.full_name)
            if not first:
                stat.skipped_no_domain += 1
                continue
            email = _guess_email(first, last, domain)
            if not email:
                stat.skipped_no_domain += 1
                continue
            mx_ok = _has_mx(domain)
            if mx_ok:
                stat.guessed_mx_ok += 1
                src_label = "guessed_mx_ok"
            else:
                stat.guessed_no_mx += 1
                src_label = "guessed_no_mx"
            if not dry_run:
                sp.email = email
                sp.email_source = src_label
            if stat.speakers_total % 50 == 0:
                logger.info(
                    f"  [{stat.speakers_total}/{len(rows)}] "
                    f"mx_ok={stat.guessed_mx_ok} no_mx={stat.guessed_no_mx} "
                    f"no_domain={stat.skipped_no_domain}"
                )
                if not dry_run:
                    session.commit()
        if not dry_run:
            session.commit()
    logger.info(
        f"Email guess done : {stat.guessed_mx_ok} MX-validated guesses "
        f"+ {stat.guessed_no_mx} invalid-MX (not saved as hot leads). "
        f"Skipped : no_firm={stat.skipped_no_firm}, no_domain={stat.skipped_no_domain}"
    )
    return stat
