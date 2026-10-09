"""Render the "Clients potentiels" tab : companies with active signals.

Each company card presents, in order :
  1. Header   — name, sector, country, exposure badges, external links
                (Pappers, corporate site, LinkedIn)
  2. Firmo    — CA, employees, size bucket, SIREN
  3. ICSID    — list of arbitration cases (one row per case)
  4. Marchés  — public contracts ≥ 5 M€ (one row per contract)
  5. Autres   — remaining signals (press, BODACC, M&A…)
  6. Contact  — key contact when known
  7. Angle    — one-liner commercial angle

Below 5 M€ public contracts are filtered out upstream — the arbitrator
targets 20-200 M€ litigation, so small-ticket procurement is noise.
"""
from __future__ import annotations

import html
import sqlite3
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "data" / "paw.db"


# ─── Data loaders ──────────────────────────────────────────────────────


@st.cache_data(ttl=120)
def _load_companies() -> pd.DataFrame:
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        """
        SELECT
            c.id, c.name, c.aliases, c.sector, c.sector_detail,
            c.country, c.country_iso2, c.website, c.linkedin_url,
            c.revenue_eur, c.revenue_year, c.employees, c.siren, c.size_bucket,
            c.has_africa_exposure, c.signal_count, c.hot_score,
            c.commercial_angle
        FROM companies c
        ORDER BY c.hot_score DESC, c.signal_count DESC, c.name ASC
        """,
        con,
    )
    con.close()
    return df


@st.cache_data(ttl=120)
def _load_icsid_by_company() -> dict[int, list[dict]]:
    con = sqlite3.connect(DB)
    rows = con.execute(
        """
        SELECT company_id, case_number, case_url, respondent_state,
               economic_sector, status, registered_date, status_date,
               sector_normalized
        FROM icsid_cases
        WHERE company_id IS NOT NULL
        ORDER BY registered_date DESC
        """
    ).fetchall()
    con.close()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r[0], []).append({
            "case_number": r[1], "case_url": r[2],
            "respondent_state": r[3], "economic_sector": r[4],
            "status": r[5], "registered_date": r[6], "status_date": r[7],
            "sector_normalized": r[8],
        })
    return out


@st.cache_data(ttl=120)
def _load_signals_by_company() -> dict[int, list[dict]]:
    con = sqlite3.connect(DB)
    rows = con.execute(
        """
        SELECT company_id, kind, title, detail, source_url, source_name,
               occurred_on, amount_eur, heat
        FROM company_signals
        ORDER BY occurred_on DESC, heat DESC
        """
    ).fetchall()
    con.close()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r[0], []).append({
            "kind": r[1], "title": r[2], "detail": r[3],
            "source_url": r[4], "source_name": r[5],
            "occurred_on": r[6], "amount_eur": r[7], "heat": r[8],
        })
    return out


@st.cache_data(ttl=120)
def _load_contacts_by_company() -> dict[int, list[dict]]:
    con = sqlite3.connect(DB)
    try:
        rows = con.execute(
            """SELECT company_id, full_name, role, role_category, email,
                      phone, linkedin_url, is_primary
               FROM company_contacts ORDER BY is_primary DESC"""
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    con.close()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r[0], []).append({
            "full_name": r[1], "role": r[2], "role_category": r[3],
            "email": r[4], "phone": r[5], "linkedin_url": r[6],
            "is_primary": r[7],
        })
    return out


# ─── Helpers ───────────────────────────────────────────────────────────


_SECTOR_ICON = {
    "mining": "⛏",
    "oil_gas": "🛢",
    "construction": "🏗",
    "energy": "⚡",
    "metals": "⚙",
    "commodities": "🌾",
    "other": "◆",
}

_SECTOR_LABEL = {
    "mining": "Mining",
    "oil_gas": "Oil &amp; Gas",
    "construction": "Construction",
    "energy": "Energy",
    "metals": "Metals",
    "commodities": "Commodities",
    "other": "Secteur à qualifier",
}


def _esc(x) -> str:
    return html.escape(str(x) if x is not None else "")


def _fmt_amount(n: Optional[float]) -> str:
    if n is None or n == 0:
        return "n/c"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f} Md€"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} M€"
    return f"{n:,.0f} €".replace(",", " ")


def _fmt_employees(n: Optional[int]) -> str:
    if n is None or n == 0:
        return "n/c"
    if n >= 1_000:
        return f"{n:,}".replace(",", " ")
    return str(n)


def _clean_str(v) -> str:
    """Return v as str, mapping NaN / None / 'nan' to ''."""
    if v is None:
        return ""
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return ""
    except Exception:
        pass
    s = str(v).strip()
    if s.lower() in ("nan", "none", "<na>"):
        return ""
    return s


def _pappers_url(siren, name: str) -> str:
    """Return a direct Pappers link when SIREN is known, else a search URL."""
    s = _clean_str(siren)
    if s and len(s) >= 9:
        return f"https://www.pappers.fr/entreprise/{s}"
    return f"https://www.pappers.fr/recherche?q={quote_plus(name)}"


def _sector_badge(sector: Optional[str], detail: Optional[str]) -> str:
    if not sector:
        return "◆ Secteur à qualifier"
    icon = _SECTOR_ICON.get(sector, "◆")
    label = _SECTOR_LABEL.get(sector, sector.title())
    if detail:
        return f"{icon} {label} · {_esc(detail[:60])}"
    return f"{icon} {label}"


# ─── Hot signal detection + narration ──────────────────────────────────


def _hot_signals(signals: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (hot_avenants, big_contracts) — the signals that make this
    company a lead for H-J.
    """
    hot_avenants = [
        s for s in signals
        if s["kind"] == "decp_avenant" and (s.get("heat") or 0) >= 75
    ]
    big_contracts = [
        s for s in signals
        if s["kind"] == "decp_contract" and (s.get("amount_eur") or 0) >= 50_000_000
    ]
    return hot_avenants, big_contracts


def _why_lead(company: pd.Series, hot_avenants: list[dict], big_contracts: list[dict]) -> str:
    """Build the 'Pourquoi c'est un lead chaud' narration."""
    bits = []
    if hot_avenants:
        a = hot_avenants[0]
        # extract the % from the title "Avenant +180% ..."
        title = a.get("title", "")
        bits.append(
            f"**{title}** sur un marché travaux — surcoût contesté typique "
            f"d'une renégociation ou résiliation qui dégénère."
        )
        if len(hot_avenants) > 1:
            bits.append(
                f"{len(hot_avenants)} autres avenants élevés sur la période 2024-2026 — "
                f"profil répétitif de litige travaux."
            )
    if big_contracts:
        b_sorted = sorted(big_contracts, key=lambda s: s.get("amount_eur") or 0, reverse=True)
        top = b_sorted[0]
        amt = top.get("amount_eur") or 0
        bits.append(
            f"Marché public travaux récent de **{_fmt_amount(amt)}** — "
            f"terrain à litige élevé (surcoûts, délais, résiliations)."
        )
    if not bits:
        bits.append("Groupe industriel actif sur marchés publics travaux — surveillance long-cours.")
    sector = (company.get("sector") or "").lower()
    if "mining" in sector or "oil_gas" in sector:
        bits.append("Exposition commodités / énergie — angle arbitrage international.")
    return " ".join(bits)


def _tremollet_angle(company: pd.Series, hot_avenants: list[dict], big_contracts: list[dict]) -> str:
    """Return the H-J positioning angle best suited to this lead's signals."""
    sector = (company.get("sector") or "").lower()
    emp = company.get("employees") or 0
    if hot_avenants:
        # Fort signal pré-contentieux → amiable + contentieux commercial
        top = hot_avenants[0]
        return (
            "🎯 **Pré-contentieux / négociation amiable** + **contentieux commercial travaux 20-200 M€**. "
            "Entrer avant que l'avenant dégénère en procédure — positionnement de résolution négociée "
            "quand c'est encore possible, bascule contentieux maîtrisée si ça casse."
        )
    if sector in ("oil_gas", "mining") or "africa" in (str(company.get("country") or "")).lower():
        return (
            "🌍 **Arbitrage international (ICC, CCJA, OHADA)** + **commodities Afrique**. "
            "Angle de niche d'H-J — groupes avec exposition africaine ou matières premières."
        )
    if big_contracts and (big_contracts[0].get("amount_eur") or 0) >= 100_000_000:
        return (
            "⚖ **Contentieux commercial 20-200 M€** + **arbitrage international**. "
            "Groupe habitué aux gros tickets — angle positionnement cabinet reconnu BigLaw-compétitif."
        )
    return (
        "🎯 **Contentieux commercial travaux 20-200 M€**. "
        "Positionnement direct sur le cœur de métier d'H-J : litige commercial industriel mid-cap."
    )


# ─── Card component ────────────────────────────────────────────────────


def render_company_card(
    company: pd.Series,
    icsid_cases: list[dict],
    signals: list[dict],
    contacts: list[dict],
) -> None:
    sector_html = _sector_badge(_clean_str(company.get("sector")) or None, _clean_str(company.get("sector_detail")) or None)
    country = _esc(_clean_str(company.get("country")))
    name = _esc(str(company["name"]))

    # Badges
    badges = []
    if company.get("has_africa_exposure"):
        badges.append('<span class="badge" style="margin-left:0.6rem;">🌍 EXPOSURE AFRIQUE</span>')
    if (company.get("signal_count") or 0) >= 2:
        badges.append(
            f'<span class="badge badge--grey" style="margin-left:0.4rem;">🔥 {int(company["signal_count"])} SIGNAUX</span>'
        )
    badges_html = "".join(badges)

    # External links row
    siren_clean = _clean_str(company.get("siren"))
    pappers_href = _pappers_url(siren_clean, str(company["name"]))
    pappers_label = "Pappers (direct)" if siren_clean else "Pappers (recherche)"
    link_bits = [
        f'<a href="{_esc(pappers_href)}" target="_blank" class="link-chip link-chip--pappers">📊 {pappers_label}</a>'
    ]
    website = _clean_str(company.get("website"))
    if website:
        link_bits.append(
            f'<a href="{_esc(website)}" target="_blank" class="link-chip">🌐 Site corporate</a>'
        )
    li_url = _clean_str(company.get("linkedin_url"))
    if li_url:
        link_bits.append(
            f'<a href="{_esc(li_url)}" target="_blank" class="link-chip">🔗 LinkedIn</a>'
        )
    link_bits.append(
        f'<a href="https://www.google.com/search?q={quote_plus(str(company["name"]) + " arbitration dispute")}" '
        f'target="_blank" class="link-chip">🔎 Google (dispute)</a>'
    )
    links_html = f'<div class="card__links">{"".join(link_bits)}</div>'

    # Firmographie
    stats_bits = []
    rev = company.get("revenue_eur")
    try:
        import math
        if isinstance(rev, float) and math.isnan(rev):
            rev = None
    except Exception:
        pass
    emp = company.get("employees")
    try:
        if isinstance(emp, float) and math.isnan(emp):
            emp = None
    except Exception:
        pass
    size = _clean_str(company.get("size_bucket"))
    if rev:
        yr = _clean_str(company.get("revenue_year"))
        yr_txt = f" ({yr})" if yr else ""
        stats_bits.append(
            f'<div class="card__stat"><div class="card__stat-label">CA{yr_txt}</div>'
            f'<div class="card__stat-value">{_fmt_amount(rev)}</div></div>'
        )
    if emp:
        stats_bits.append(
            f'<div class="card__stat"><div class="card__stat-label">Employés</div>'
            f'<div class="card__stat-value">{_fmt_employees(int(emp))}</div></div>'
        )
    if size:
        stats_bits.append(
            f'<div class="card__stat"><div class="card__stat-label">Taille</div>'
            f'<div class="card__stat-value">{_esc(size.replace("_", " ").title())}</div></div>'
        )
    if siren_clean:
        stats_bits.append(
            f'<div class="card__stat"><div class="card__stat-label">SIREN</div>'
            f'<div class="card__stat-value" style="font-size:1.1rem;">{_esc(siren_clean)}</div></div>'
        )
    stats_html = (
        f'<div class="card__stats">{"".join(stats_bits)}</div>'
        if stats_bits else
        '<div class="card__empty">Firmographie à enrichir (Pappers requis — crédits épuisés)</div>'
    )

    # ICSID cases — list every case
    icsid_html = ""
    if icsid_cases:
        rows = []
        for c in icsid_cases:
            cn = _esc(c["case_number"])
            rs = _esc(c.get("respondent_state") or "n/c")
            es = _esc(c.get("economic_sector") or "")
            status = _esc(c.get("status") or "n/c")
            reg = _esc(c.get("registered_date") or "")
            link = ""
            if c.get("case_url"):
                link = f' · <a href="{_esc(c["case_url"])}" target="_blank">↗</a>'
            sector_line = f' · {es}' if es else ''
            rows.append(
                f'<div class="row-case">'
                f'<div class="row-case__head"><b>{cn}</b> vs <b>{rs}</b>{link}</div>'
                f'<div class="row-case__meta">{status} · enregistré {reg}{sector_line}</div>'
                f'</div>'
            )
        icsid_html = (
            f'<div class="card__section-title">⚖ Arbitrages ICSID ({len(icsid_cases)})</div>'
            f'<div class="rows">{"".join(rows)}</div>'
            f'<div class="card__divider"></div>'
        )

    # Marchés publics ≥10M€ (top 8 par montant, résumé si plus — reste
    # déployable via <details><summary>)
    decp_signals = [s for s in signals if s["kind"] == "decp_contract"]
    decp_html = ""
    if decp_signals:
        decp_total = sum((s.get("amount_eur") or 0) for s in decp_signals)
        sorted_decp = sorted(decp_signals, key=lambda s: s.get("amount_eur") or 0, reverse=True)

        def _row(s):
            amt = s.get("amount_eur") or 0
            buyer_object = _esc((s.get("detail") or "").replace("·", " · "))
            date = _esc(s.get("occurred_on") or "n/c")
            return (
                f'<div class="row-decp">'
                f'<div class="row-decp__amt">{_fmt_amount(amt)}</div>'
                f'<div class="row-decp__body">'
                f'<div class="row-decp__object">{buyer_object}</div>'
                f'<div class="row-decp__meta">Notifié {date}</div>'
                f'</div></div>'
            )

        top_rows = "".join(_row(s) for s in sorted_decp[:8])
        more_html = ""
        if len(sorted_decp) > 8:
            extra_rows = "".join(_row(s) for s in sorted_decp[8:])
            n_more = len(sorted_decp) - 8
            more_html = (
                f'<details style="margin-top:0.5rem;">'
                f'<summary style="cursor:pointer; color:#E63323; font-weight:600; '
                f'letter-spacing:0.06em; font-size:0.82rem; padding:0.4rem 0; '
                f'list-style:none; user-select:none;">'
                f'▸ Voir les {n_more} marchés additionnels'
                f'</summary>'
                f'<div class="rows" style="margin-top:0.4rem;">{extra_rows}</div>'
                f'</details>'
            )
        decp_html = (
            f'<div class="card__section-title">📜 Marchés publics travaux ≥ 10 M€ '
            f'· {len(decp_signals)} contrats · cumulé {_fmt_amount(decp_total)}</div>'
            f'<div class="rows">{top_rows}</div>'
            f'{more_html}'
            f'<div class="card__divider"></div>'
        )

    # Pré-contentieux : avenants, BOAMP, BODACC, Judilibre (hot signals)
    precontentieux = [
        s for s in signals
        if s["kind"] in ("decp_avenant", "boamp_modification", "boamp_annulation",
                          "boamp_resultat", "boamp_other",
                          "bodacc_procedure", "judilibre_appeal")
    ]
    pre_html = ""
    if precontentieux:
        _KINDS = {
            "decp_avenant": "🔥 Avenant DECP",
            "boamp_modification": "📝 BOAMP — Avenant / modification",
            "boamp_annulation": "🚫 BOAMP — Annulation",
            "boamp_resultat": "📑 BOAMP — Résultat",
            "boamp_other": "📜 BOAMP",
            "bodacc_procedure": "⚠️ BODACC — Procédure collective",
            "judilibre_appeal": "⚖ Jurisprudence appel",
        }

        def _signal_row(s):
            kind_label = _KINDS.get(s["kind"], s["kind"].upper())
            link = ""
            if s.get("source_url"):
                link = f' · <a href="{_esc(s["source_url"])}" target="_blank">↗</a>'
            date = _esc(s.get("occurred_on") or "")
            date_html = f' <span class="brand-dim" style="font-size:0.72rem;">· {date}</span>' if date else ""
            return (
                f'<div class="signal">'
                f'<div class="signal__kind">{kind_label}{date_html}</div>'
                f'<div class="signal__detail">{_esc(s["title"])}{link}</div>'
                f'<div class="signal__meta">{_esc((s.get("detail") or "")[:220])}</div>'
                f'</div>'
            )

        top_rows = "".join(_signal_row(s) for s in precontentieux[:10])
        more_pre = ""
        if len(precontentieux) > 10:
            extra_rows = "".join(_signal_row(s) for s in precontentieux[10:])
            n_more = len(precontentieux) - 10
            more_pre = (
                f'<details style="margin-top:0.5rem;">'
                f'<summary style="cursor:pointer; color:#E63323; font-weight:600; '
                f'letter-spacing:0.06em; font-size:0.82rem; padding:0.4rem 0; '
                f'list-style:none; user-select:none;">'
                f'▸ Voir les {n_more} signaux additionnels'
                f'</summary>'
                f'<div style="margin-top:0.4rem;">{extra_rows}</div>'
                f'</details>'
            )
        pre_html = (
            f'<div class="card__section-title">🚨 Pré-contentieux ({len(precontentieux)})</div>'
            f'{top_rows}{more_pre}'
            f'<div class="card__divider"></div>'
        )

    # Autres signaux (press / M&A / nominations)
    other_signals = [
        s for s in signals
        if s["kind"] not in (
            "decp_contract", "icsid", "decp_avenant",
            "boamp_modification", "boamp_annulation", "boamp_resultat",
            "boamp_other", "bodacc_procedure", "judilibre_appeal",
        )
    ]
    other_html = ""
    if other_signals:
        rows = []
        for s in other_signals[:5]:
            kind_label = {
                "press": "📰 Presse",
                "job_change": "👤 Nomination",
                "ma_announcement": "🤝 M&amp;A",
                "ccja_ohada": "🏛 CCJA / OHADA",
            }.get(s["kind"], s["kind"].upper())
            link = ""
            if s.get("source_url"):
                link = f' · <a href="{_esc(s["source_url"])}" target="_blank">↗</a>'
            rows.append(
                f'<div class="signal signal--news">'
                f'<div class="signal__kind">{kind_label}</div>'
                f'<div class="signal__detail">{_esc(s["title"])}{link}</div>'
                f'</div>'
            )
        other_html = (
            f'<div class="card__section-title">Autres signaux ({len(other_signals)})</div>'
            f'{"".join(rows)}'
            f'<div class="card__divider"></div>'
        )

    # Contact
    contact_html = ""
    primary = next((c for c in contacts if c.get("is_primary")), None) or (contacts[0] if contacts else None)
    if primary:
        parts = [f'<div style="font-weight:600;">{_esc(primary["full_name"])}</div>']
        if primary.get("role"):
            parts.append(f'<div class="brand-dim" style="font-size:0.85rem;">{_esc(primary["role"])}</div>')
        if primary.get("email"):
            parts.append(f'<div>📧 <a href="mailto:{_esc(primary["email"])}">{_esc(primary["email"])}</a></div>')
        if primary.get("phone"):
            parts.append(f'<div>📞 {_esc(primary["phone"])}</div>')
        if primary.get("linkedin_url"):
            parts.append(f'<div>🔗 <a href="{_esc(primary["linkedin_url"])}" target="_blank">LinkedIn</a></div>')
        contact_html = (
            f'<div class="card__section-title">Contact clé</div>'
            f'<div class="card__contact">{"".join(parts)}</div>'
            f'<div class="card__divider"></div>'
        )

    # Composite hot_score with decomposition (stored as JSON in
    # commercial_angle during the hot_score processor pass).
    components = None
    raw_angle = company.get("commercial_angle") or ""
    if raw_angle.startswith("{"):
        try:
            import json as _json
            components = _json.loads(raw_angle)
        except Exception:
            components = None
    score_decomp_html = ""
    if components:
        bits = []
        if components.get("surcout_pts"):
            amt = components.get("surcout_cum_eur") or 0
            bits.append(f"**{components['surcout_pts']} pts** surcoût cumulé ({_fmt_amount(amt)})")
        if components.get("recency_pts"):
            days = components.get("recency_days") or 0
            bits.append(f"**{components['recency_pts']} pts** récence (dernier avenant il y a {days} j)")
        if components.get("repetition_pts"):
            nb = components.get("nb_hot_avenants") or 0
            bits.append(f"**{components['repetition_pts']} pts** répétition ({nb} avenants ≥30%)")
        if components.get("recent_xxl_pts"):
            amt = components.get("recent_xxl_amt_eur") or 0
            bits.append(f"**{components['recent_xxl_pts']} pts** marché récent {_fmt_amount(amt)}")
        if bits:
            import re as _re_s
            decomp_txt = " · ".join(bits)
            decomp_txt = _re_s.sub(r"\*\*(.+?)\*\*", r'<b class="brand-accent">\1</b>', decomp_txt)
            score_decomp_html = (
                f'<div style="margin:0.4rem 0 0.9rem 0; padding:0.5rem 0.75rem; '
                f'background:#0F0F0F; border-left:3px solid #E63323; '
                f'color:#CFCFCF; font-size:0.78rem; line-height:1.5;">'
                f'<span style="color:#E63323; font-weight:700; letter-spacing:0.1em; '
                f'font-size:0.7rem; margin-right:0.5rem;">SCORE {int(company.get("hot_score") or 0)}/100</span>'
                f'{decomp_txt}'
                f'</div>'
            )

    # "Pourquoi c'est un lead chaud" + "Angle H-J"
    hot_avenants, big_contracts = _hot_signals(signals)
    why_text = _why_lead(company, hot_avenants, big_contracts)
    import re as _re
    why_text_html = _re.sub(r"\*\*(.+?)\*\*", r'<b style="color:#F2F2F2;">\1</b>', _esc(why_text).replace("**", "||MD||"))
    # Re-apply the bold since _esc stripped the markdown — rebuild differently
    why_safe = (
        _esc(why_text)
    )
    # Simple approach : escape then re-wrap **
    _esc_text = _esc(why_text)
    _esc_text = _re.sub(r"\*\*(.+?)\*\*", r'<b style="color:#F2F2F2;">\1</b>', _esc_text)
    why_html_block = (
        f'<div style="margin:0.9rem 0; padding:0.75rem 0.9rem; '
        f'background:#141414; border:1px dashed #E63323;">'
        f'<div style="color:#E63323; font-weight:600; letter-spacing:0.12em; '
        f'font-size:0.72rem; margin-bottom:0.35rem;">🚨 POURQUOI C\'EST UN LEAD CHAUD</div>'
        f'<div style="color:#CFCFCF; font-size:0.88rem; line-height:1.5;">{_esc_text}</div>'
        f'</div>'
    )
    angle_text = _tremollet_angle(company, hot_avenants, big_contracts)
    _angle_esc = _esc(angle_text)
    _angle_esc = _re.sub(r"\*\*(.+?)\*\*", r'<b>\1</b>', _angle_esc)
    angle_html_block = (
        f'<div style="margin:0.6rem 0 0 0; padding:0.75rem 0.9rem; '
        f'background:rgba(230,51,35,0.1); border-left:3px solid #E63323;">'
        f'<div style="color:#E63323; font-weight:600; letter-spacing:0.12em; '
        f'font-size:0.72rem; margin-bottom:0.35rem;">💼 ANGLE H-J À METTRE EN AVANT</div>'
        f'<div style="color:#F2F2F2; font-size:0.88rem; line-height:1.5;">{_angle_esc}</div>'
        f'</div>'
    )

    card_html = (
        f'<div class="card">'
        f'<div class="card__head">'
        f'<span class="card__sector">{sector_html}</span>'
        f'<span class="card__country">{country}</span>'
        f'{badges_html}'
        f'</div>'
        f'<div class="card__name">{name}</div>'
        f'{links_html}'
        f'{stats_html}'
        f'<div class="card__divider"></div>'
        f'{score_decomp_html}'
        f'{why_html_block}'
        f'{angle_html_block}'
        f'<div class="card__divider"></div>'
        f'{icsid_html}'
        f'{decp_html}'
        f'{pre_html}'
        f'{other_html}'
        f'{contact_html}'
        f'</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)


# ─── Tab render ────────────────────────────────────────────────────────


def render() -> None:
    df = _load_companies()
    icsid_by_company = _load_icsid_by_company()
    signals = _load_signals_by_company()
    contacts = _load_contacts_by_company()

    if df.empty:
        st.markdown(
            """
            <div class="card" style="max-width: 720px;">
              <div class="card__sector">🎯 Clients potentiels</div>
              <div class="card__name" style="margin-top: 0.6rem;">
                Base en cours de construction
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    # Pool with at least 1 hot signal
    hot_company_ids = {
        cid for cid, sigs in signals.items()
        if any(
            (s["kind"] == "decp_avenant" and (s.get("heat") or 0) >= 75)
            or (s["kind"] == "decp_contract" and (s.get("amount_eur") or 0) >= 50_000_000)
            for s in sigs
        )
    }

    # Count companies by score band
    nb_elite = int((df["hot_score"].fillna(0) >= 90).sum())
    nb_hot = int(((df["hot_score"].fillna(0) >= 70) & (df["hot_score"].fillna(0) < 90)).sum())
    nb_warm = int(((df["hot_score"].fillna(0) >= 50) & (df["hot_score"].fillna(0) < 70)).sum())

    # Méthode block
    st.markdown(
        f"""
        <div style="background:#141414; border:1px solid #2A2A2A;
                    padding:1.1rem 1.25rem; margin:0.8rem 0 1.2rem 0;">
          <div style="color:#E63323; font-size:0.72rem; font-weight:600;
                      letter-spacing:0.14em; margin-bottom:0.5rem;">
            MÉTHODE DE SÉLECTION — SCORE COMPOSITE 0-100
          </div>
          <div style="color:#F2F2F2; font-size:1.5rem; font-family:'Crimson Pro',Georgia,serif;
                      font-weight:600; margin-bottom:0.5rem;">
            {nb_elite} élite (90+) · {nb_hot} très chaudes (70-89) · {nb_warm} chaudes (50-69)
          </div>
          <div style="color:#CFCFCF; font-size:0.9rem; line-height:1.5; max-width:76ch;">
            Sur <b>{len(df):,}</b> sociétés françaises qualifiées (code NAF construction,
            génie civil, énergie, matières premières, titulaires d'au moins un marché
            travaux ≥ 10 M€), chaque société reçoit un <b class="brand-accent">score composite 0-100</b>
            pondéré sur 4 composantes :
            <ul style="margin:0.5rem 0 0.3rem 1.2rem; padding:0;">
              <li><b>Surcoût cumulé</b> (40 pts max) — somme des avenants € sur 24 mois,
                1 pt par M€ de surcoût. <i>Ticket pile 20-200 M€ pour H-J.</i></li>
              <li><b>Récence</b> (25 pts max) — avenant &lt; 6 mois = 25 pts,
                &lt; 12 mois = 12 pts. <i>Un avenant frais déclenche encore un contentieux.</i></li>
              <li><b>Répétition</b> (20 pts max) — 5 pts par avenant ≥30%
                sur un marché initial ≥ <b>10 M€</b>, plafonné à 4 avenants.
                <i>Serial avenant sur gros projet = terrain à litige.</i></li>
              <li><b>Marché récent XXL</b> (15 pts max) — 50 M€ = 7 pts, 100 M€ = 10 pts,
                200 M€ = 15 pts. <i>Taille absolue du terrain contentieux.</i></li>
            </ul>
            <div style="color:#9A9A9A; font-size:0.82rem; margin-top:0.5rem;">
              Classement par score composite — les cibles pile 20-200 M€ H-J remontent naturellement.
              Chaque carte montre la <b>décomposition</b> et l'<b>angle H-J</b> à mettre en avant.
            </div>
          </div>
        </div>
        """.replace("{len(df):,}", f"{len(df):,}".replace(",", " ")),
        unsafe_allow_html=True,
    )

    # ── Filters ───────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("### 🎯 Filtrer les leads")
        min_score = st.slider(
            "Score composite min.", 0, 100, 50, step=5, key="clients_score",
            help="Score 0-100 pondéré : surcoût cumulé + récence + répétition + marché XXL récent. 70+ = élite.",
        )
        apply_hot_filter = st.checkbox(
            "Signal chaud uniquement", value=False, key="clients_hot",
            help="Avenant ≥30 % OU marché travaux ≥50 M€ récent (le score composite couvre déjà ça).",
        )
        sector_opts = sorted({s for s in df["sector"].dropna().unique() if s})
        sector_sel = st.multiselect(
            "Secteur", sector_opts,
            default=sector_opts, key="clients_sector",
        )
        size_opts = sorted({s for s in df["size_bucket"].dropna().unique() if s})
        default_sizes = [s for s in size_opts if s in ("upper_mid", "large", "very_large")] or size_opts
        size_sel = st.multiselect(
            "Taille société", size_opts, default=default_sizes,
            key="clients_size",
            help="upper_mid (100-499 emp), large (500-999), very_large (1000+). On exclut TPE/PME par défaut.",
        )
        search = st.text_input(
            "🔎 Recherche société", placeholder="Colas, Vinci, Eiffage, Bouygues...",
            key="clients_search",
        )

    f = df.copy()
    f = f[f["hot_score"].fillna(0) >= min_score]
    if apply_hot_filter:
        f = f[f["id"].isin(hot_company_ids)]
    if sector_sel:
        f = f[f["sector"].isin(sector_sel)]
    if size_sel:
        f = f[f["size_bucket"].isin(size_sel) | f["size_bucket"].isna()]
    if search:
        s = search.lower()
        f = f[
            f["name"].fillna("").str.lower().str.contains(s, na=False)
            | f["aliases"].fillna("").str.lower().str.contains(s, na=False)
        ]

    st.markdown(
        f'<p class="brand-dim"><b>{len(f)}</b> leads affichés · triés par chaleur signal.</p>',
        unsafe_allow_html=True,
    )

    # Render cards (max 50 per page for performance)
    PAGE_SIZE = 50
    if len(f) > PAGE_SIZE:
        page = st.number_input(
            "Page", min_value=1,
            max_value=(len(f) + PAGE_SIZE - 1) // PAGE_SIZE,
            value=1, step=1, key="clients_page",
        )
        start = (int(page) - 1) * PAGE_SIZE
        page_df = f.iloc[start:start + PAGE_SIZE]
        st.caption(f"Sociétés {start + 1}–{min(start + PAGE_SIZE, len(f))} / {len(f)}")
    else:
        page_df = f

    for _, company in page_df.iterrows():
        cid = int(company["id"])
        render_company_card(
            company,
            icsid_by_company.get(cid, []),
            signals.get(cid, []),
            contacts.get(cid, []),
        )
