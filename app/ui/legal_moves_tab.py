"""New in-house Legal Director / General Counsel appointments.

A newly-appointed GC has 6-12 months of carte blanche to reset the
roster of external counsel she consults. For a BD-oriented lawyer this
is the single highest-signal moment — much more actionable than a cold
list of 2 000 companies.
"""
from __future__ import annotations

import html
import sqlite3
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "data" / "paw.db"


def _esc(x) -> str:
    return html.escape(str(x) if x is not None else "")


@st.cache_data(ttl=120)
def _load_moves() -> pd.DataFrame:
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        """
        SELECT m.id, m.full_name, m.role_title, m.company_name,
               m.company_sector, m.pool_company_id, m.announced_on,
               m.start_date, m.linkedin_url, m.email, m.previous_role,
               m.notes, m.source_name, m.source_url, m.confidence,
               c.sector AS pool_sector, c.size_bucket AS pool_size,
               c.siren AS pool_siren
        FROM legal_moves m
        LEFT JOIN companies c ON c.id = m.pool_company_id
        ORDER BY m.announced_on DESC NULLS LAST
        """,
        con,
    )
    con.close()
    return df


def _days_since(d: str) -> int | None:
    if not d:
        return None
    try:
        dd = datetime.strptime(d[:10], "%Y-%m-%d").date()
        return (date.today() - dd).days
    except Exception:
        return None


def _window_badge(days: int | None) -> str:
    if days is None:
        return '<span class="chip">Date inconnue</span>'
    if days <= 90:
        return (
            '<span style="display:inline-block; background:#E63323; color:#0A0A0A; '
            'font-weight:700; font-size:0.72rem; padding:0.2rem 0.5rem; '
            'border-radius:2px; letter-spacing:0.1em; text-transform:uppercase;">'
            '🔥 Fenêtre ouverte &lt; 3 mois</span>'
        )
    if days <= 180:
        return (
            '<span style="display:inline-block; background:#E63323; color:#F2F2F2; '
            'font-weight:700; font-size:0.72rem; padding:0.2rem 0.5rem; '
            'border-radius:2px; letter-spacing:0.1em; text-transform:uppercase;">'
            '🎯 Fenêtre ouverte &lt; 6 mois</span>'
        )
    if days <= 365:
        return (
            '<span style="display:inline-block; background:#2A2A2A; color:#CFCFCF; '
            'font-weight:600; font-size:0.72rem; padding:0.2rem 0.5rem; '
            'border-radius:2px; letter-spacing:0.1em;">'
            '< 1 an</span>'
        )
    return '<span class="chip">Plus ancien</span>'


def render_move_card(row: pd.Series) -> None:
    name = _esc(row.get("full_name") or "")
    role = _esc(row.get("role_title") or "Direction juridique")
    company = _esc(row.get("company_name") or "")
    announced = row.get("announced_on") or ""
    days = _days_since(announced)
    badge = _window_badge(days)

    pool_match = pd.notna(row.get("pool_company_id"))
    pool_html = ""
    if pool_match:
        siren = _esc(row.get("pool_siren") or "")
        pool_sector = _esc((row.get("pool_sector") or "").replace("_", " ").title())
        pool_html = (
            '<div style="margin:0.5rem 0; padding:0.5rem 0.75rem; '
            'background:rgba(230,51,35,0.1); border-left:3px solid #E63323; '
            'color:#CFCFCF; font-size:0.82rem;">'
            '<span style="color:#E63323; font-weight:700; letter-spacing:0.1em; '
            'font-size:0.68rem; margin-right:0.5rem;">✓ MATCH POOL DECP</span>'
            f'Secteur {pool_sector} · SIREN {siren}'
            '</div>'
        )

    # External links
    search_q = f"{row.get('full_name', '')} {row.get('company_name', '')} LinkedIn"
    linkedin_href = f"https://www.google.com/search?q={quote_plus(search_q)}"
    link_bits = [
        f'<a href="{linkedin_href}" target="_blank" class="link-chip">🔎 Rechercher LinkedIn</a>',
        f'<a href="{_esc(row.get("source_url") or "#")}" target="_blank" class="link-chip">📰 Source</a>',
    ]
    if row.get("company_name"):
        pappers_q = quote_plus(str(row["company_name"]))
        link_bits.append(
            f'<a href="https://www.pappers.fr/recherche?q={pappers_q}" target="_blank" class="link-chip">📊 Pappers</a>'
        )
    links_html = f'<div class="card__links">{"".join(link_bits)}</div>'

    # Action suggested
    if days is not None and days <= 90:
        action_icon, action_txt = (
            "🚀",
            "Approche immédiate — DJ en période de reset de son roster. Entrer avant la constitution du carnet d'adresses.",
        )
    elif days is not None and days <= 180:
        action_icon, action_txt = (
            "🎯",
            "Fenêtre encore ouverte — demander un café de présentation, apporter un dossier (pré-contentieux ou arbitrage) comme introduction.",
        )
    elif days is not None and days <= 365:
        action_icon, action_txt = (
            "📬",
            "DJ installé — approche classique, référence commune ou événement cabinet nécessaire.",
        )
    else:
        action_icon, action_txt = "·", "Nomination ancienne — surveiller sans priorité."
    action_html = (
        '<div style="margin:0.6rem 0 0 0; padding:0.6rem 0.9rem; '
        'background:rgba(230,51,35,0.08); border-left:3px solid #E63323;">'
        '<div style="color:#E63323; font-weight:600; letter-spacing:0.12em; '
        'font-size:0.72rem; margin-bottom:0.35rem;">💼 ACTION SUGGÉRÉE</div>'
        f'<div style="color:#F2F2F2; font-size:0.88rem; line-height:1.5;">{action_icon} {_esc(action_txt)}</div>'
        '</div>'
    )

    notes = _esc(row.get("notes") or "")
    notes_html = (
        f'<div class="pcard__qual" style="margin-top:0.6rem;"><b>Contexte :</b> {notes}</div>'
        if notes else ""
    )

    confidence = int(row.get("confidence") or 0)
    confidence_html = (
        f'<span class="chip chip--role" style="margin-left:0.5rem;">Confiance {confidence} %</span>'
    )

    card_html = (
        f'<div class="pcard">'
        f'<div class="pcard__top">'
        f'<div class="pcard__identity">'
        f'<div class="pcard__name">{name}</div>'
        f'<div class="pcard__title">{role}</div>'
        f'<div class="pcard__firm">{company}{confidence_html}</div>'
        f'</div>'
        f'<div style="text-align:right; min-width:160px;">'
        f'{badge}'
        f'<div class="brand-dim" style="font-size:0.75rem; margin-top:0.3rem;">'
        f'Nomination : {_esc(announced or "n/c")}'
        f'</div>'
        f'</div>'
        f'</div>'
        f'{pool_html}'
        f'{notes_html}'
        f'{links_html}'
        f'{action_html}'
        f'</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)


def render() -> None:
    df = _load_moves()
    df["days_since"] = df["announced_on"].apply(_days_since)

    nb_fresh = int((df["days_since"].fillna(9999) <= 90).sum())
    nb_6m = int((df["days_since"].fillna(9999) <= 180).sum())
    nb_matched = int(df["pool_company_id"].notna().sum())

    st.markdown(
        f"""
        <div style="background:#141414; border:1px solid #2A2A2A;
                    padding:1.1rem 1.25rem; margin:0.8rem 0 1.2rem 0;">
          <div style="color:#E63323; font-size:0.72rem; font-weight:600;
                      letter-spacing:0.14em; margin-bottom:0.5rem;">
            SIGNAL FAIBLE PILE CIBLE — NOUVEAUX DÉCIDEURS JURIDIQUES
          </div>
          <div style="color:#F2F2F2; font-size:1.5rem; font-family:'Crimson Pro',Georgia,serif;
                      font-weight:600; margin-bottom:0.5rem;">
            {nb_fresh} fraîches (&lt; 3 mois) · {nb_6m} ouvertes (&lt; 6 mois) ·
            {nb_matched} dans le pool DECP
          </div>
          <div style="color:#CFCFCF; font-size:0.9rem; line-height:1.5; max-width:76ch;">
            Un DJ fraîchement nommé reset son <b>roster de conseils externes</b> sur les 6-12 premiers mois.
            C'est <b class="brand-accent">la fenêtre la plus courte et la plus efficace</b> pour un cabinet
            d'avocats qui cherche à se positionner : la décision d'adopter un nouveau conseil est
            faisable, l'étiquette d'avocat historique n'est pas encore collée.
            <ul style="margin:0.5rem 0 0.3rem 1.2rem; padding:0;">
              <li>Sources actuelles : <b>Google News RSS</b> (11 requêtes variantes FR + EN)</li>
              <li>Sources prévues : <b>Decideurs Juridiques</b>, <b>Les Échos Executives</b>, Google site:linkedin.com</li>
              <li>Chaque match au <b>pool DECP</b> est signalé (soc. du cœur de cible H-J)</li>
            </ul>
            <div style="color:#9A9A9A; font-size:0.82rem; margin-top:0.5rem;">
              💡 Prochaine itération : ajouter Decideurs + Les Échos augmentera la couverture ×3 à ×5
              (presse généraliste manque les nominations internes des groupes BTP/énergie non-cotés).
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Filters
    with st.sidebar:
        st.markdown("### 🆕 Filtrer les nominations")
        max_days = st.slider(
            "Fraîcheur max (jours)", 0, 720, 180, step=30, key="moves_fresh",
            help="Par défaut 6 mois = fenêtre d'opportunité maximale.",
        )
        pool_only = st.checkbox(
            "Dans le pool DECP uniquement", value=False, key="moves_pool",
        )
        role_opts = sorted({r for r in df["role_title"].dropna().unique() if r})
        role_sel = st.multiselect(
            "Rôle", role_opts, default=role_opts, key="moves_role",
        )
        search = st.text_input(
            "🔎 Recherche nom ou société", key="moves_search",
        )

    f = df.copy()
    f = f[f["days_since"].fillna(9999) <= max_days]
    if pool_only:
        f = f[f["pool_company_id"].notna()]
    if role_sel:
        f = f[f["role_title"].isin(role_sel)]
    if search:
        s = search.lower()
        f = f[
            f["full_name"].fillna("").str.lower().str.contains(s, na=False)
            | f["company_name"].fillna("").str.lower().str.contains(s, na=False)
        ]

    st.markdown(
        f'<p class="brand-dim"><b>{len(f)}</b> nominations · triées par date descendante.</p>',
        unsafe_allow_html=True,
    )

    if f.empty:
        st.info(
            "Aucune nomination ne matche. Pousse le slider plus loin, "
            "ou lance un re-scrape (`python -m app.scrapers.legal_moves 365`)."
        )
        return

    for _, row in f.iterrows():
        render_move_card(row)
