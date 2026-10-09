"""XLSX deliverables for the commodities arbitrator BD campaign.

Produces four files in ``data/exports/`` :

  1. ``speakers_all.xlsx``       — every qualified speaker across all sources
  2. ``firms_all.xlsx``          — every qualified firm
  3. ``priority_targets.xlsx``   — curated top N (commodity_relevance >= 50,
                                   senior role, Paris-friendly geo)
  4. ``attendance_signals.xlsx`` — OSINT-detected practitioners (filled by
                                   the attendance scrapers later)

Every sheet is auto-filterable, has color-coded commodity relevance and
freeze-pane on row 1 for comfortable browsing in Excel.
"""
from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import func, select

from app.config import EXPORT_DIR
from app.database import (
    AttendanceSignal,
    Event,
    EventSpeaker,
    Firm,
    SessionLocal,
    Speaker,
)
from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402


HEADER_FILL = PatternFill(start_color="0F1B2D", end_color="0F1B2D", fill_type="solid")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
BORDER = Border(
    left=Side(style="thin", color="DDDDDD"),
    right=Side(style="thin", color="DDDDDD"),
    top=Side(style="thin", color="DDDDDD"),
    bottom=Side(style="thin", color="DDDDDD"),
)
# Commodity relevance scale
FILL_GREEN = PatternFill(start_color="C8F7C5", end_color="C8F7C5", fill_type="solid")
FILL_YELLOW = PatternFill(start_color="FFF4C2", end_color="FFF4C2", fill_type="solid")
FILL_RED = PatternFill(start_color="FFD6CC", end_color="FFD6CC", fill_type="solid")
FILL_GRAY = PatternFill(start_color="F0F0F0", end_color="F0F0F0", fill_type="solid")


def _write_header(ws, columns: list[tuple[str, int]]) -> None:
    for i, (name, width) in enumerate(columns, start=1):
        c = ws.cell(row=1, column=i, value=name)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[1].height = 28
    ws.freeze_panes = "A2"


def _fill_for_score(score: int) -> PatternFill:
    if score >= 70:
        return FILL_GREEN
    if score >= 40:
        return FILL_YELLOW
    if score >= 15:
        return FILL_GRAY
    return FILL_RED


def _enable_autofilter(ws, n_rows: int, n_cols: int) -> None:
    last_col = get_column_letter(n_cols)
    ws.auto_filter.ref = f"A1:{last_col}{n_rows + 1}"


# ─── Speakers XLSX ─────────────────────────────────────────────────────


SPEAKER_COLUMNS = [
    ("Full name", 28), ("Current title", 30), ("Current firm", 28),
    ("Firm type", 22), ("Role category", 22),
    ("Email", 32), ("Email source", 14),
    ("Phone", 20), ("LinkedIn URL", 36),
    ("Country", 14), ("Birth year", 8), ("Member type", 10),
    ("Practice areas", 36), ("Specializations (ASA)", 50),
    ("Geographic focus", 24),
    ("Commodity score", 10), ("Commodity reasoning", 60),
    ("Years present", 16), ("First year", 10), ("Last year", 10),
    ("Events count", 10),
    ("Has moved", 10), ("Firm history (JSON)", 50),
    ("Sources", 20), ("Sources count", 7), ("Profile URL", 40),
]


def export_speakers_xlsx(path: Path | None = None, min_events: int = 1) -> Path:
    path = path or (EXPORT_DIR / "speakers_all.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = "Speakers"
    _write_header(ws, SPEAKER_COLUMNS)

    with SessionLocal() as session:
        rows = session.execute(
            select(Speaker, Firm)
            .outerjoin(Firm, Firm.id == Speaker.firm_id)
            .order_by(Speaker.commodity_relevance.desc(), Speaker.full_name.asc())
        ).all()

        r = 2
        for sp, firm in rows:
            if (sp.events_count or 0) < min_events:
                continue
            vals = [
                sp.display_name or sp.full_name,
                sp.current_title or "",
                firm.name if firm else "",
                firm.firm_type if firm else "",
                sp.role_category or "",
                sp.email or "", sp.email_source or "",
                sp.phone or "", sp.linkedin_url or "",
                sp.country or "",
                sp.birth_year, sp.member_type or "",
                sp.practice_areas or "",
                sp.specializations or "",
                sp.geographic_focus or "",
                sp.commodity_relevance or 0,
                sp.commodity_reasoning or "",
                sp.years_present or "",
                sp.first_year,
                sp.last_year,
                sp.events_count or 0,
                "YES" if sp.has_moved else "no",
                sp.firm_history or "",
                sp.sources_present or "",
                sp.sources_count or 0,
                sp.profile_url or "",
            ]
            for i, v in enumerate(vals, start=1):
                c = ws.cell(row=r, column=i, value=v)
                c.alignment = Alignment(vertical="top", wrap_text=True)
                c.border = BORDER
            # Color email cell by source : verified ASA = green, guessed = yellow
            if sp.email:
                color = FILL_GREEN if sp.email_source == "asa" else FILL_YELLOW
                ws.cell(row=r, column=6).fill = color
                ws.cell(row=r, column=6).font = Font(bold=True, color="0F1B2D")
            # Commodity score column index — now col 16
            score_cell = ws.cell(row=r, column=16)
            score_cell.fill = _fill_for_score(sp.commodity_relevance or 0)
            score_cell.alignment = Alignment(horizontal="center", vertical="center")
            score_cell.font = Font(bold=True)
            # Firm-move column shifted — now col 22
            if sp.has_moved:
                ws.cell(row=r, column=22).fill = FILL_YELLOW
                ws.cell(row=r, column=22).font = Font(bold=True)
            # Sources column shifted — now col 24
            if (sp.sources_count or 0) >= 2:
                ws.cell(row=r, column=24).fill = FILL_GREEN
                ws.cell(row=r, column=24).font = Font(bold=True)
            ws.row_dimensions[r].height = 42
            r += 1

    _enable_autofilter(ws, r - 2, len(SPEAKER_COLUMNS))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    logger.info(f"Wrote {path.name}  ({r - 2} speaker rows)")
    return path


# ─── Firms XLSX ────────────────────────────────────────────────────────


FIRM_COLUMNS = [
    ("Firm name", 30), ("Firm type", 22), ("Country", 14),
    ("Website", 28), ("LinkedIn", 32),
    ("Practice areas", 36), ("Geographic focus", 24),
    ("Commodity score", 10), ("Commodity reasoning", 50),
    ("Years present", 14), ("Events hosted", 10),
    ("Speakers linked", 10),
    ("Description", 60), ("Source", 10),
]


def export_firms_xlsx(path: Path | None = None) -> Path:
    path = path or (EXPORT_DIR / "firms_all.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = "Firms"
    _write_header(ws, FIRM_COLUMNS)

    with SessionLocal() as session:
        firms = session.scalars(
            select(Firm).order_by(Firm.commodity_relevance.desc(), Firm.name.asc())
        ).all()
        r = 2
        for firm in firms:
            vals = [
                firm.name,
                firm.firm_type or "",
                firm.country or "",
                firm.website or "",
                firm.linkedin_url or "",
                firm.practice_areas or "",
                firm.geographic_focus or "",
                firm.commodity_relevance or 0,
                firm.commodity_reasoning or "",
                firm.years_present or "",
                firm.events_hosted_count or 0,
                firm.speakers_count or 0,
                (firm.description or "")[:600],
                firm.source,
            ]
            for i, v in enumerate(vals, start=1):
                c = ws.cell(row=r, column=i, value=v)
                c.alignment = Alignment(vertical="top", wrap_text=True)
                c.border = BORDER
            score_cell = ws.cell(row=r, column=8)
            score_cell.fill = _fill_for_score(firm.commodity_relevance or 0)
            score_cell.alignment = Alignment(horizontal="center", vertical="center")
            score_cell.font = Font(bold=True)
            ws.row_dimensions[r].height = 42
            r += 1

    _enable_autofilter(ws, r - 2, len(FIRM_COLUMNS))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    logger.info(f"Wrote {path.name}  ({r - 2} firm rows)")
    return path


# ─── Priority targets XLSX ─────────────────────────────────────────────


def export_priority_targets_xlsx(
    path: Path | None = None,
    min_commodity: int = 20,
    min_events: int = 1,
    senior_only: bool = True,
) -> Path:
    """Curated subset : commodity-relevant seniors with ≥ 2 appearances.

    Senior = role is Partner / Counsel / Arbitrator / Barrister / Judge /
    Academic / In-house / Expert witness (anything that reads as a
    decision-maker).
    """
    path = path or (EXPORT_DIR / "priority_targets.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = "Priority targets"
    _write_header(ws, SPEAKER_COLUMNS)

    senior_roles = {
        "Partner", "Counsel / Of Counsel", "Arbitrator / independent",
        "Academic", "Judge / former judge", "Barrister (QC/KC)",
        "Expert witness", "In-house",
    }

    with SessionLocal() as session:
        rows = session.execute(
            select(Speaker, Firm)
            .outerjoin(Firm, Firm.id == Speaker.firm_id)
            .where(Speaker.commodity_relevance >= min_commodity)
            .where(Speaker.events_count >= min_events)
            .order_by(Speaker.commodity_relevance.desc(), Speaker.events_count.desc())
        ).all()

        r = 2
        for sp, firm in rows:
            if senior_only and sp.role_category not in senior_roles:
                continue
            vals = [
                sp.display_name or sp.full_name,
                sp.current_title or "",
                firm.name if firm else "",
                firm.firm_type if firm else "",
                sp.role_category or "",
                sp.email or "", sp.email_source or "",
                sp.phone or "", sp.linkedin_url or "",
                sp.country or "",
                sp.birth_year, sp.member_type or "",
                sp.practice_areas or "",
                sp.specializations or "",
                sp.geographic_focus or "",
                sp.commodity_relevance or 0,
                sp.commodity_reasoning or "",
                sp.years_present or "",
                sp.first_year,
                sp.last_year,
                sp.events_count or 0,
                "YES" if sp.has_moved else "no",
                sp.firm_history or "",
                sp.sources_present or "",
                sp.sources_count or 0,
                sp.profile_url or "",
            ]
            for i, v in enumerate(vals, start=1):
                c = ws.cell(row=r, column=i, value=v)
                c.alignment = Alignment(vertical="top", wrap_text=True)
                c.border = BORDER
            if sp.email:
                color = FILL_GREEN if sp.email_source == "asa" else FILL_YELLOW
                ws.cell(row=r, column=6).fill = color
                ws.cell(row=r, column=6).font = Font(bold=True, color="0F1B2D")
            score_cell = ws.cell(row=r, column=16)
            score_cell.fill = _fill_for_score(sp.commodity_relevance or 0)
            score_cell.alignment = Alignment(horizontal="center", vertical="center")
            score_cell.font = Font(bold=True)
            if sp.has_moved:
                ws.cell(row=r, column=22).fill = FILL_YELLOW
            if (sp.sources_count or 0) >= 2:
                ws.cell(row=r, column=24).fill = FILL_GREEN
                ws.cell(row=r, column=24).font = Font(bold=True)
            ws.row_dimensions[r].height = 42
            r += 1

    _enable_autofilter(ws, r - 2, len(SPEAKER_COLUMNS))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    logger.info(f"Wrote {path.name}  ({r - 2} priority targets)")
    return path


# ─── Attendance signals XLSX (fed by OSINT scrapers) ───────────────────


SIGNAL_COLUMNS = [
    ("Edition", 10), ("Source platform", 14), ("Hashtag", 14),
    ("Person name", 28), ("Role", 30), ("Firm", 28), ("Country", 14),
    ("Signal type", 20), ("Presence confidence", 12),
    ("Commodity score", 10),
    ("Source title", 60), ("Snippet", 80), ("Source URL", 50),
    ("Source", 10),
]


def export_attendance_signals_xlsx(path: Path | None = None) -> Path:
    path = path or (EXPORT_DIR / "attendance_signals.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = "Signals"
    _write_header(ws, SIGNAL_COLUMNS)

    with SessionLocal() as session:
        sigs = session.scalars(
            select(AttendanceSignal)
            .where(AttendanceSignal.is_duplicate == False)  # noqa: E712
            .order_by(
                AttendanceSignal.commodity_relevance.desc(),
                AttendanceSignal.edition_year.desc(),
            )
        ).all()
        r = 2
        for s in sigs:
            vals = [
                s.edition_year, s.source_platform, s.hashtag or "",
                s.person_name or "", s.person_role or "",
                s.firm_name or "", s.country or "",
                s.signal_type or "", s.presence_confidence or "",
                s.commodity_relevance or 0,
                (s.source_title or "")[:400], (s.source_snippet or "")[:800],
                s.source_url, s.source,
            ]
            for i, v in enumerate(vals, start=1):
                c = ws.cell(row=r, column=i, value=v)
                c.alignment = Alignment(vertical="top", wrap_text=True)
                c.border = BORDER
            score_cell = ws.cell(row=r, column=10)
            score_cell.fill = _fill_for_score(s.commodity_relevance or 0)
            score_cell.alignment = Alignment(horizontal="center")
            score_cell.font = Font(bold=True)
            ws.row_dimensions[r].height = 40
            r += 1

    _enable_autofilter(ws, r - 2, len(SIGNAL_COLUMNS))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    logger.info(f"Wrote {path.name}  ({r - 2} signals)")
    return path


# ─── Entry point ───────────────────────────────────────────────────────


def export_all() -> dict[str, Path]:
    out = {
        "speakers": export_speakers_xlsx(),
        "firms": export_firms_xlsx(),
        "priority_targets": export_priority_targets_xlsx(),
        "attendance_signals": export_attendance_signals_xlsx(),
    }
    return out
