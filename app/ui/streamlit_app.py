"""Branded dashboard — Arbitration Intelligence.

Two tabs :
  1. "🤝 Réseau à cultiver" — the arbitration ecosystem seen as referral
     partners, peers, and invitation targets. Narrow default view : the
     127 practitioners who are simultaneously active in 2024-2026, carry
     H-J's practice-area affinity (commercial arbitration / construction
     & infrastructure / energy / commodities), and multi-circuit (seen
     at ≥ 2 events).

  2. "🎯 Leads sociétés" — companies carrying a hot signal (avenant >30%
     on a travaux marché OR new travaux marché ≥50 M€). Narrated : each
     card explains why it is a hot lead and which H-J angle to use.

Both tabs lead with a Méthode block that spells out the selection
criteria. The site is branded (noir / rouge / gris palette) but
intentionally generic — no mention of any specific law firm.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from app.ui.style import inject as _inject_brand_css


ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "data" / "paw.db"


st.set_page_config(
    page_title="Arbitration Intelligence — pour Henri-Joseph Trémollet de Villers",
    page_icon="⚖",
    layout="wide",
)
_inject_brand_css()


# ─── Data loaders ──────────────────────────────────────────────────────


@st.cache_data(ttl=120)
def load_partners() -> pd.DataFrame:
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        """
        SELECT
            s.id,
            s.display_name AS name,
            s.current_title AS title,
            s.role_category AS role,
            f.name AS firm,
            f.firm_type AS firm_type,
            s.email, s.email_source, s.phone, s.linkedin_url,
            s.country, s.birth_year, s.member_type,
            s.practice_areas,
            s.specializations,
            s.geographic_focus,
            s.commodity_relevance AS commodity,
            s.commodity_reasoning,
            s.years_present,
            s.first_year,
            s.last_year,
            s.events_count,
            s.has_moved,
            s.firm_history,
            s.sources_present,
            s.sources_count,
            s.profile_url
        FROM speakers s
        LEFT JOIN firms f ON f.id = s.firm_id
        """,
        con,
    )
    con.close()
    return df


@st.cache_data(ttl=120)
def load_counts() -> dict:
    con = sqlite3.connect(DB)
    counts = {
        "events": con.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        "speakers": con.execute("SELECT COUNT(*) FROM speakers").fetchone()[0],
        "firms": con.execute("SELECT COUNT(*) FROM firms").fetchone()[0],
        "firm_moves": con.execute("SELECT COUNT(*) FROM speakers WHERE has_moved=1").fetchone()[0],
        "multi_source": con.execute("SELECT COUNT(*) FROM speakers WHERE sources_count>=2").fetchone()[0],
        "emails": con.execute("SELECT COUNT(*) FROM speakers WHERE email IS NOT NULL").fetchone()[0],
    }
    # Praticiens matchant les 3 critères H-J
    counts["top_network"] = con.execute(
        """
        SELECT COUNT(*) FROM speakers
        WHERE last_year >= 2024
          AND sources_count >= 2
          AND (practice_areas LIKE '%Commercial arbitration%'
               OR practice_areas LIKE '%Construction%'
               OR practice_areas LIKE '%Energy & natural resources%'
               OR practice_areas LIKE '%Commodities%'
               OR practice_areas LIKE '%Investment arbitration%')
        """
    ).fetchone()[0]
    by_source = {}
    for src, n in con.execute("SELECT source, COUNT(*) FROM events GROUP BY source"):
        by_source[src] = {"events": n}
    for src, n in con.execute(
        "SELECT e.source, COUNT(DISTINCT es.speaker_id) FROM event_speakers es "
        "JOIN events e ON e.id=es.event_id GROUP BY e.source"
    ):
        by_source.setdefault(src, {})["speakers"] = n
    try:
        counts["companies"] = con.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        counts["decp_contracts"] = con.execute(
            "SELECT COUNT(*) FROM company_signals WHERE kind='decp_contract'"
        ).fetchone()[0]
        counts["avenants"] = con.execute(
            "SELECT COUNT(*) FROM company_signals WHERE kind='decp_avenant'"
        ).fetchone()[0]
        # Sociétés avec signal chaud
        counts["top_leads"] = con.execute(
            """
            SELECT COUNT(DISTINCT company_id) FROM company_signals
            WHERE (kind='decp_avenant' AND heat >= 75)
               OR (kind='decp_contract' AND amount_eur >= 50000000)
            """
        ).fetchone()[0]
        try:
            counts["africa_projects"] = con.execute(
                "SELECT COUNT(*) FROM africa_projects"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            counts["africa_projects"] = 0
        try:
            counts["legal_moves"] = con.execute(
                "SELECT COUNT(*) FROM legal_moves"
            ).fetchone()[0]
            counts["legal_moves_fresh"] = con.execute(
                "SELECT COUNT(*) FROM legal_moves "
                "WHERE announced_on IS NOT NULL "
                "AND julianday('now') - julianday(announced_on) <= 180"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            counts["legal_moves"] = 0
            counts["legal_moves_fresh"] = 0
    except sqlite3.OperationalError:
        counts["companies"] = 0
        counts["decp_contracts"] = 0
        counts["avenants"] = 0
        counts["top_leads"] = 0
        counts["africa_projects"] = 0
    con.close()
    return counts, by_source


counts, by_source = load_counts()


# ─── Hero header ───────────────────────────────────────────────────────


st.markdown(
    f"""
    <div class="hero">
      <div class="hero__eyebrow">OUTIL D'APPORT D'AFFAIRES · CONTENTIEUX COMMERCIAL &amp; ARBITRAGE INTERNATIONAL</div>
      <div class="hero__title">4 blocs, 4 actions concrètes</div>
      <div class="hero__subtitle">
        <b class="brand-accent">{counts['top_network']} praticiens</b> à cultiver (déjeuners, invitations)
        · <b class="brand-accent">{counts['top_leads']} sociétés françaises</b> BTP/énergie
        avec signal de pré-contentieux chaud ·
        <b class="brand-accent">{counts.get('africa_projects', 0)} projets Afrique</b>
        (gaz, pétrole, mines, construction) pour l'angle différenciant H-J ·
        <b class="brand-accent">{counts.get('legal_moves_fresh', 0)} nouveaux DJ</b>
        (< 6 mois) — fenêtre maximale pour entrer avant la constitution du roster.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ─── Top KPIs ──────────────────────────────────────────────────────────


k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Réseau à cultiver", f"{counts['top_network']:,}".replace(",", " "))
k2.metric("Praticiens tracés", f"{counts['speakers']:,}".replace(",", " "))
k3.metric("Emails valides", f"{counts['emails']:,}".replace(",", " "))
k4.metric("Leads chauds", f"{counts['top_leads']:,}".replace(",", " "))
k5.metric("Marchés tracés", f"{counts['decp_contracts']:,}".replace(",", " "))
k6.metric("Avenants 🔥", f"{counts['avenants']:,}".replace(",", " "))


st.markdown("<div style='height: 1.5rem'></div>", unsafe_allow_html=True)


# ─── Tabs ─────────────────────────────────────────────────────────────


tab_partners, tab_clients, tab_africa, tab_moves = st.tabs(
    [
        f"🤝 Réseau à cultiver · {counts['top_network']}",
        f"🎯 Leads sociétés · {counts['top_leads']}",
        f"🌍 Afrique — angle différenciant · {counts.get('africa_projects', 0)}",
        f"🆕 Nouveaux DJ · {counts.get('legal_moves_fresh', 0)}",
    ]
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   ONGLET 1 — RÉSEAU À CULTIVER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


with tab_partners:
    from app.ui.partners_tab import render as _render_partners_tab
    _render_partners_tab(by_source)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   ONGLET 2 — LEADS SOCIÉTÉS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


with tab_clients:
    from app.ui.clients_tab import render as _render_clients_tab
    _render_clients_tab()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   ONGLET 3 — AFRIQUE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


with tab_africa:
    from app.ui.africa_tab import render as _render_africa_tab
    _render_africa_tab()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   ONGLET 4 — NOUVEAUX DJ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


with tab_moves:
    from app.ui.legal_moves_tab import render as _render_moves_tab
    _render_moves_tab()
