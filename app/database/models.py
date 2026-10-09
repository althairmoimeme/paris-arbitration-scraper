"""SQLAlchemy models for the PAW qualified actor database.

Core entities :
  - Edition  : one row per PAW year (2023 → 2026)
  - Firm     : one row per partner organization (law firm, institution, …)
  - Event    : one row per PAW event session
  - Speaker  : one row per unique person (dedup across years)
  - EventSpeaker : many-to-many (role = host / guest / moderator / panelist)

Qualification columns live on Firm / Speaker — stored flat for easy XLSX
export. Multi-value fields use ``;``-joined strings so one speaker /
firm = one row downstream, with no transposition logic needed in Excel.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)

from app.config import DB_PATH


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class Edition(Base, TimestampMixin):
    """One row per edition year, per source."""

    __tablename__ = "editions"
    __table_args__ = (UniqueConstraint("source", "year", name="uq_edition_source_year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(
        String(20), index=True, default="paw",
        doc="paw | hkiac | future sources — lets us mix catalogs in one DB.",
    )
    year: Mapped[int] = mapped_column(Integer, index=True)
    slug: Mapped[str] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(String(500))
    # Date range of the edition (first/last event date — filled after event scrape)
    start_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    end_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    # Totals (computed after scrape)
    events_count: Mapped[int] = mapped_column(Integer, default=0)
    speakers_count: Mapped[int] = mapped_column(Integer, default=0)

class Firm(Base, TimestampMixin):
    """One row per partner organization (law firm, institution, chambers, …)."""

    __tablename__ = "firms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(
        String(20), index=True, default="paw",
        doc="paw | hkiac | manual — provenance of the firm record.",
    )
    slug: Mapped[str] = mapped_column(String(255), index=True)
    partner_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Core fields scraped from the partner page
    name: Mapped[str] = mapped_column(String(400), index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    website: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    twitter_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    hq_address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    practice_areas_raw: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        doc="Multiline free text from the firm's own partner page.",
    )

    # ── Qualification (filled by processors/firm_classifier.py) ─────────
    firm_type: Mapped[Optional[str]] = mapped_column(
        String(60), nullable=True, index=True,
        doc=(
            "Closed taxonomy : BigLaw | Boutique | Regional law firm | "
            "Arbitration institution | Barrister chambers | Academic | "
            "Government | Expert / consultant | LitFunder | In-house | Media"
        ),
    )
    practice_areas: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        doc="';'-joined canonical practice areas (see processors/taxonomy.py).",
    )
    geographic_focus: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        doc="';'-joined canonical geo buckets.",
    )
    size_estimate: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True,
        doc="Rough bucket inferred from name + known registries.",
    )

    # ── Commodity-specific flags (THE priority axis for this client) ────
    commodity_relevance: Mapped[int] = mapped_column(
        Integer, default=0, index=True,
        doc="0 (none) → 100 (central practice). Set by processors/commodity_scorer.py",
    )
    commodity_reasoning: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        doc="Short rule-based or LLM-generated justification.",
    )

    # Totals (filled after scrape)
    events_hosted_count: Mapped[int] = mapped_column(Integer, default=0)
    speakers_count: Mapped[int] = mapped_column(Integer, default=0)
    years_present: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True,
        doc="e.g. '2023;2024;2025;2026' — telling if the firm is a regular.",
    )

    # Relationships
    events_hosted: Mapped[list["Event"]] = relationship(back_populates="host_firm")
    speakers: Mapped[list["Speaker"]] = relationship(back_populates="firm")


class Event(Base, TimestampMixin):
    """One row per PAW event session."""

    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("source", "slug", name="uq_event_source_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(
        String(20), index=True, default="paw",
        doc="paw | hkiac — same slug can exist on both sources, so dedup needs (source, slug).",
    )
    slug: Mapped[str] = mapped_column(String(255), index=True)
    event_url: Mapped[str] = mapped_column(String(500))
    edition_year: Mapped[int] = mapped_column(Integer, index=True)
    host_firm_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("firms.id"), nullable=True, index=True
    )

    title: Mapped[str] = mapped_column(String(600))
    date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    time_start: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    time_end: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    format: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True,
        doc="in-person | hybrid | online",
    )
    location: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    program: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Tags scraped from the event taxonomy terms (themes + regions)
    themes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    regions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Processed signal : does this event's topic touch commodities ?
    commodity_relevance: Mapped[int] = mapped_column(
        Integer, default=0, index=True,
        doc="0 → 100 — propagated to speakers of commodity-themed events.",
    )

    host_firm: Mapped[Optional["Firm"]] = relationship(back_populates="events_hosted")
    event_speakers: Mapped[list["EventSpeaker"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class Speaker(Base, TimestampMixin):
    """One row per unique person (dedup across years + firm moves)."""

    __tablename__ = "speakers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Normalized key for dedup : lowercase + ASCII fold + strip titles
    canonical_key: Mapped[str] = mapped_column(
        String(255), unique=True, index=True,
        doc="e.g. 'bernard hanotiau' — stable across years.",
    )

    full_name: Mapped[str] = mapped_column(String(255), index=True)
    display_name: Mapped[Optional[str]] = mapped_column(
        String(400), nullable=True,
        doc="Original casing from source, with titles kept (e.g. 'Tim EICKE KCMG KC').",
    )

    # Current firm (most recent event's firm)
    firm_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("firms.id"), nullable=True, index=True
    )
    current_title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # ── Firm move history ────────────────────────────────────────────────
    firm_history: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        doc="JSON array of {year, firm_name, title}; detect lateral moves.",
    )
    has_moved: Mapped[bool] = mapped_column(
        Boolean, default=False, index=True,
        doc="True if seen at ≥ 2 different firms across years.",
    )

    # Profile scraped from /speaker/<slug>/ when available
    profile_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Direct contact (ASA Profiles source — the only one that exposes these)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    email_source: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, index=True,
        doc="asa | guessed_mx_ok | guessed_no_mx | manual",
    )
    phone: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    # ASA-sourced extras
    birth_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    specializations: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        doc="';'-joined ASA closed-list specializations (41 values incl. Oil and Gas, Metals and Mining).",
    )
    member_type: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True,
        doc="asa | sccm (ASA = Swiss Arbitration Association, SCCM = Swiss Chamber of Commercial Mediation).",
    )

    # ── Qualification ────────────────────────────────────────────────────
    role_category: Mapped[Optional[str]] = mapped_column(
        String(60), nullable=True, index=True,
        doc=(
            "Closed taxonomy : Partner | Counsel | Associate | Arbitrator | "
            "Academic | Judge | Expert witness | Barrister (QC/KC) | "
            "In-house | Government | Student"
        ),
    )
    seniority: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    practice_areas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    geographic_focus: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Commodity axis
    commodity_relevance: Mapped[int] = mapped_column(
        Integer, default=0, index=True,
        doc="0 → 100 ; weighted combination of firm score + personal panels.",
    )
    commodity_reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Attendance stats (computed)
    events_count: Mapped[int] = mapped_column(Integer, default=0)
    years_present: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    first_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Multi-source presence : ';'-joined list of sources where the person
    # has shown up (paw / swiss_summit / hkiac / aec_week / ...).
    # Active on >= 2 sources is a strong BD signal.
    sources_present: Mapped[Optional[str]] = mapped_column(
        String(80), nullable=True, index=True
    )
    sources_count: Mapped[int] = mapped_column(Integer, default=0, index=True)

    firm: Mapped[Optional["Firm"]] = relationship(back_populates="speakers")
    event_speakers: Mapped[list["EventSpeaker"]] = relationship(
        back_populates="speaker", cascade="all, delete-orphan"
    )


class EventSpeaker(Base, TimestampMixin):
    """Many-to-many linking Events ↔ Speakers with contextual role info."""

    __tablename__ = "event_speakers"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "speaker_id", "role", name="uq_event_speaker_role"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id"), index=True
    )
    speaker_id: Mapped[int] = mapped_column(
        ForeignKey("speakers.id"), index=True
    )
    # Firm as stated for THIS event — may differ from Speaker.firm_id (firm move)
    firm_name_at_event: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    role: Mapped[Optional[str]] = mapped_column(
        String(40), index=True,
        doc="host | guest | moderator | panelist | keynote",
    )
    title_at_event: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    event: Mapped["Event"] = relationship(back_populates="event_speakers")
    speaker: Mapped["Speaker"] = relationship(back_populates="event_speakers")


class AttendanceSignal(Base, TimestampMixin):
    """OSINT signal of participation / interest around PAW.

    Mirrors the ``exhibitor_signals`` concept from the eurosatory project
    but adapted : speakers listed on an event already count as attendance ;
    this table captures NON-OBVIOUS attendees — people posting about PAW,
    firms press-releasing their presence, journalists reporting, etc.
    """

    __tablename__ = "attendance_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(
        String(20), index=True, default="paw",
        doc="paw | hkiac — which event this signal relates to.",
    )
    edition_year: Mapped[int] = mapped_column(Integer, index=True)

    person_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    canonical_person_key: Mapped[Optional[str]] = mapped_column(
        String(255), index=True, nullable=True
    )
    person_role: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    firm_name: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Source
    source_platform: Mapped[str] = mapped_column(
        String(40), index=True,
        doc="linkedin | press | firm_news | twitter | other",
    )
    source_url: Mapped[str] = mapped_column(String(900))
    source_title: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    source_snippet: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    hashtag: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)

    # Classification
    signal_type: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True,
        doc="personal_post | company_announcement | press_coverage | event_page",
    )
    presence_confidence: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True,
        doc="high | medium | low",
    )
    commodity_relevance: Mapped[int] = mapped_column(
        Integer, default=0, index=True,
    )
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    linked_speaker_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("speakers.id"), nullable=True
    )


# ─── CLIENT PROSPECT LAYER (Tab 2) ───────────────────────────────────
# Separate entity from Firm (which is the arbitration-ecosystem firm —
# law firms, chambers, etc.). A Company here is a prospect : mining /
# oil & gas / construction entity that could be a client.


class Company(Base, TimestampMixin):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("canonical_key", name="uq_company_canonical"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_key: Mapped[str] = mapped_column(String(400), index=True)
    name: Mapped[str] = mapped_column(String(400), index=True)
    aliases: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        doc="';'-joined alt names seen across sources.",
    )
    sector: Mapped[Optional[str]] = mapped_column(
        String(60), nullable=True, index=True,
        doc="mining | oil_gas | construction | metals | commodities | other",
    )
    sector_detail: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    country_iso2: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    website: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Firmographie (filled by Pappers / enrichment)
    revenue_eur: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    revenue_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    employees: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    siren: Mapped[Optional[str]] = mapped_column(String(14), nullable=True, index=True)
    size_bucket: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True,
        doc="small | mid_cap | large_cap — derived from revenue + employees.",
    )

    # Africa / commodity exposure signal
    has_africa_exposure: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # Processed signals (filled by processors)
    signal_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    hot_score: Mapped[int] = mapped_column(
        Integer, default=0, index=True,
        doc="0-100 — composite freshness + relevance score.",
    )
    commercial_angle: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        doc="LLM-generated or rule-based pitch summary.",
    )

    # Relationships
    icsid_cases: Mapped[list["ICSIDCase"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    signals: Mapped[list["CompanySignal"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    contacts: Mapped[list["CompanyContact"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class ICSIDCase(Base, TimestampMixin):
    __tablename__ = "icsid_cases"
    __table_args__ = (UniqueConstraint("case_number", name="uq_icsid_case"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_number: Mapped[str] = mapped_column(String(60), index=True)
    case_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Core
    claimant: Mapped[Optional[str]] = mapped_column(String(600), nullable=True)
    respondent_state: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    economic_sector: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    sector_normalized: Mapped[Optional[str]] = mapped_column(
        String(60), nullable=True, index=True,
        doc="mining | oil_gas | construction | metals | commodities | other",
    )
    subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Dates
    registered_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(
        String(60), nullable=True, index=True,
        doc="Pending | Discontinued | Awarded | Settled | …",
    )
    status_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Counsels (parties' lawyers, when disclosed)
    claimant_counsel: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    respondent_counsel: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Link to the company we extracted the Claimant into
    company_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True
    )
    company: Mapped[Optional["Company"]] = relationship(back_populates="icsid_cases")


class CompanySignal(Base, TimestampMixin):
    """Catch-all for any dated signal attached to a company : press
    mention, DECP contract, OHADA / CCJA case, M&A event, etc.
    """

    __tablename__ = "company_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"), index=True
    )
    kind: Mapped[str] = mapped_column(
        String(30), index=True,
        doc="icsid | ccja_ohada | decp_contract | press | bodacc | job_change | ma_announcement",
    )
    title: Mapped[str] = mapped_column(String(400))
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(900), nullable=True)
    source_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    occurred_on: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True,
        doc="ISO date YYYY-MM-DD when the signal happened (if dated).",
    )
    amount_eur: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    heat: Mapped[int] = mapped_column(
        Integer, default=50,
        doc="0 (cold) → 100 (urgent) — how actionable the signal is.",
    )

    company: Mapped["Company"] = relationship(back_populates="signals")


class CompanyContact(Base, TimestampMixin):
    """Named decision-makers linked to a company (GC, HOL, CEO, DAF, …)."""

    __tablename__ = "company_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"), index=True
    )
    full_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    role_category: Mapped[Optional[str]] = mapped_column(
        String(40), index=True, nullable=True,
        doc="general_counsel | legal_director | ceo | cfo | director_africa | other",
    )
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)

    company: Mapped["Company"] = relationship(back_populates="contacts")


class LegalMove(Base, TimestampMixin):
    """A newly-appointed (or recently moved) in-house Legal Director /
    General Counsel of a major industrial group. Freshly-appointed GC
    = short window of opportunity for BD approach.
    """

    __tablename__ = "legal_moves"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role_title: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    company_name: Mapped[str] = mapped_column(String(400), nullable=False, index=True)
    company_sector: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    pool_company_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True,
    )
    announced_on: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    start_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    previous_role: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(900), nullable=True)
    confidence: Mapped[int] = mapped_column(Integer, default=50, nullable=False)


class AfricaProject(Base, TimestampMixin):
    """Major Africa project (energy, mining, construction) tracked as a BD
    opportunity for H-J's Africa / commodities positioning.
    """

    __tablename__ = "africa_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(400), nullable=False)
    country: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    sector: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    sub_sector: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    operators: Mapped[Optional[str]] = mapped_column(Text, nullable=True)        # ;-joined
    local_partners: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # ;-joined
    french_exposure: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # named French groups involved
    phase: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    start_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    capex_eur: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    risk_signals: Mapped[Optional[str]] = mapped_column(Text, nullable=True)     # ;-joined bullet points
    hj_angle: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sources: Mapped[Optional[str]] = mapped_column(Text, nullable=True)          # ;-joined URLs
    actors_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)      # JSON blob with structured actors
    hot_score: Mapped[int] = mapped_column(Integer, default=50, nullable=False)


class ScrapingRun(Base, TimestampMixin):
    """Audit of each scrape execution."""

    __tablename__ = "scraping_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    urls_total: Mapped[int] = mapped_column(Integer, default=0)
    urls_ok: Mapped[int] = mapped_column(Integer, default=0)
    urls_failed: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


# ─── Engine & session ────────────────────────────────────────────────────

ENGINE = create_engine(f"sqlite:///{DB_PATH}", echo=False, future=True)
SessionLocal = sessionmaker(bind=ENGINE, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create every table that doesn't exist yet."""
    Base.metadata.create_all(ENGINE)
