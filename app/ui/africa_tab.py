"""Africa major projects — H-J's differentiating positioning angle.

Projects curated across 11 countries (Mauritanie, Sénégal, Côte d'Ivoire,
Nigeria, Ghana, Mali, Burkina Faso, Niger, Mozambique, Angola, RDC,
Gabon), classified by sector (oil & gas / mining / construction /
energy) and scored by their current litigation / arbitration propensity.
"""
from __future__ import annotations

import html
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "data" / "paw.db"


_SECTOR_ICON = {
    "oil_gas": "🛢",
    "mining": "⛏",
    "construction": "🏗",
    "energy": "⚡",
}

_SECTOR_LABEL = {
    "oil_gas": "Oil &amp; Gas",
    "mining": "Mines",
    "construction": "Construction / Infrastructure",
    "energy": "Énergie",
}


def _esc(x) -> str:
    return html.escape(str(x) if x is not None else "")


@st.cache_data(ttl=120)
def _load_projects() -> pd.DataFrame:
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        """
        SELECT id, name, country, sector, sub_sector, operators,
               local_partners, french_exposure, phase, start_year,
               capex_eur, risk_signals, hj_angle, sources,
               actors_json, hot_score
        FROM africa_projects
        ORDER BY hot_score DESC, name ASC
        """,
        con,
    )
    con.close()
    return df


def _fmt_capex(n) -> str:
    if n is None or n == 0 or (isinstance(n, float) and pd.isna(n)):
        return "n/c"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f} Md$"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f} M$"
    return f"{n:.0f} $"


def _split(s, sep: str = ";") -> list[str]:
    if not s or pd.isna(s):
        return []
    return [p.strip() for p in str(s).split(sep) if p.strip()]


def render_project_card(row: pd.Series) -> None:
    sector = row.get("sector") or ""
    icon = _SECTOR_ICON.get(sector, "◆")
    sector_lbl = _SECTOR_LABEL.get(sector, sector.title())
    sub = _esc(row.get("sub_sector") or "")
    country = _esc(row.get("country") or "")
    name = _esc(row.get("name") or "")
    score = int(row.get("hot_score") or 0)

    # Opérateurs + partenaires + exposition française
    ops = _split(row.get("operators"))
    partners = _split(row.get("local_partners"))
    french = row.get("french_exposure") or ""

    stats_bits = []
    capex = row.get("capex_eur")
    if capex and not pd.isna(capex):
        stats_bits.append(
            f'<div class="card__stat"><div class="card__stat-label">Capex total</div>'
            f'<div class="card__stat-value">{_fmt_capex(capex)}</div></div>'
        )
    phase = _esc(row.get("phase") or "")
    if phase:
        stats_bits.append(
            f'<div class="card__stat"><div class="card__stat-label">Phase</div>'
            f'<div class="card__stat-value" style="font-size:0.95rem;">{phase[:60]}</div></div>'
        )
    stats_html = f'<div class="card__stats">{"".join(stats_bits)}</div>' if stats_bits else ""

    # Opérateurs
    ops_html = ""
    if ops:
        chips = "".join(f'<span class="chip chip--role">{_esc(o)}</span>' for o in ops)
        ops_html = (
            f'<div style="margin:0.6rem 0;">'
            f'<div class="pcard__chips" style="margin:0;">'
            f'<span style="color:#9A9A9A; font-size:0.72rem; letter-spacing:0.08em; '
            f'margin-right:0.5rem;">OPÉRATEURS</span>'
            f'{chips}</div></div>'
        )
    partners_html = ""
    if partners:
        chips = "".join(f'<span class="chip">{_esc(p)}</span>' for p in partners)
        partners_html = (
            f'<div style="margin:0.3rem 0;">'
            f'<div class="pcard__chips" style="margin:0;">'
            f'<span style="color:#9A9A9A; font-size:0.72rem; letter-spacing:0.08em; '
            f'margin-right:0.5rem;">PARTENAIRES LOCAUX</span>'
            f'{chips}</div></div>'
        )

    # Exposition française
    french_html = ""
    if french and french.strip():
        french_html = (
            f'<div style="margin:0.5rem 0; padding:0.5rem 0.75rem; '
            f'background:rgba(0, 85, 164, 0.08); border-left:3px solid #0055A4; '
            f'color:#CFCFCF; font-size:0.82rem;">'
            f'<span style="color:#5090D0; font-weight:600; letter-spacing:0.1em; '
            f'font-size:0.68rem; margin-right:0.5rem;">🇫🇷 EXPOSITION FRANÇAISE</span>'
            f'{_esc(french)}</div>'
        )

    # Structured actors — "qui parle à qui"
    actors_html = ""
    actors_json = row.get("actors_json") or ""
    if actors_json:
        import json as _json
        try:
            actors = _json.loads(actors_json)
        except Exception:
            actors = []
        if actors:
            rows = []
            for a in actors:
                icon = a.get("icon", "·")
                role_lbl = _esc(a.get("role_label") or "")
                name_ = _esc(a.get("name") or "")
                country_ = _esc(a.get("country") or "")
                stake = a.get("stake_pct")
                stake_txt = f" · {stake} %" if stake is not None else ""
                function_ = _esc(a.get("function") or "")
                position_ = _esc(a.get("position") or "")
                # Red flag if position contains "🚨"
                is_hot = "🚨" in (a.get("position") or "") or "🚨" in (a.get("function") or "")
                left_border = "#E63323" if is_hot else "#2A2A2A"
                rows.append(
                    f'<div style="margin:0.4rem 0; padding:0.5rem 0.75rem; '
                    f'background:#141414; border-left:3px solid {left_border}; '
                    f'border-radius:0 2px 2px 0;">'
                    f'<div style="color:#9A9A9A; font-size:0.68rem; '
                    f'letter-spacing:0.1em; font-weight:600; margin-bottom:0.2rem;">'
                    f'{icon} {role_lbl.upper()}</div>'
                    f'<div style="color:#F2F2F2; font-size:0.9rem; font-weight:600;">'
                    f'{name_}<span style="color:#9A9A9A; font-weight:400; font-size:0.8rem;"> · {country_}{stake_txt}</span></div>'
                    f'<div style="color:#CFCFCF; font-size:0.78rem; margin-top:0.2rem;">{function_}</div>'
                    f'<div style="color:#9A9A9A; font-size:0.74rem; margin-top:0.15rem; font-style:italic;">'
                    f'En cas de litige : {position_}</div>'
                    f'</div>'
                )
            actors_html = (
                f'<div style="margin:0.9rem 0;">'
                f'<div style="color:#E63323; font-weight:600; letter-spacing:0.12em; '
                f'font-size:0.72rem; margin-bottom:0.5rem;">👥 ACTEURS DU PROJET — QUI PARLE À QUI</div>'
                f'{"".join(rows)}'
                f'</div>'
            )

    # Risk signals
    risks = _split(row.get("risk_signals"))
    risks_html = ""
    if risks:
        lis = "".join(
            f'<li style="margin-bottom:0.25rem; color:#CFCFCF; font-size:0.84rem; line-height:1.4;">{_esc(r)}</li>'
            for r in risks
        )
        risks_html = (
            f'<div style="margin:0.9rem 0; padding:0.75rem 0.9rem; '
            f'background:#141414; border:1px dashed #E63323;">'
            f'<div style="color:#E63323; font-weight:600; letter-spacing:0.12em; '
            f'font-size:0.72rem; margin-bottom:0.4rem;">🚨 SIGNAUX DE RISQUE / CONTENTIEUX</div>'
            f'<ul style="margin:0.2rem 0 0 1.1rem; padding:0;">{lis}</ul>'
            f'</div>'
        )

    # H-J angle
    angle = row.get("hj_angle") or ""
    angle_html = ""
    if angle:
        angle_html = (
            f'<div style="margin:0.6rem 0 0 0; padding:0.75rem 0.9rem; '
            f'background:rgba(230,51,35,0.1); border-left:3px solid #E63323;">'
            f'<div style="color:#E63323; font-weight:600; letter-spacing:0.12em; '
            f'font-size:0.72rem; margin-bottom:0.35rem;">💼 ANGLE H-J À METTRE EN AVANT</div>'
            f'<div style="color:#F2F2F2; font-size:0.88rem; line-height:1.5;">{_esc(angle)}</div>'
            f'</div>'
        )

    # Sources
    sources = _split(row.get("sources"))
    sources_html = ""
    if sources:
        chips = "".join(
            f'<a href="{_esc(s)}" target="_blank" class="link-chip">🔗 source</a>'
            for s in sources[:3]
        )
        sources_html = f'<div class="pcard__contacts">{chips}</div>'

    # Score display (right side)
    score_html = (
        f'<div class="pcard__score">'
        f'<div class="pcard__score-value">{score}</div>'
        f'<div class="pcard__score-label">Score contentieux</div>'
        f'</div>'
    )

    card_html = (
        f'<div class="card">'
        f'<div class="card__head">'
        f'<span class="card__sector">{icon} {sector_lbl}{" · " + sub if sub else ""}</span>'
        f'<span class="card__country">🌍 {country}</span>'
        f'</div>'
        f'<div class="pcard__top">'
        f'<div class="pcard__identity">'
        f'<div class="card__name" style="font-size:1.6rem;">{name}</div>'
        f'</div>'
        f'{score_html}'
        f'</div>'
        f'{stats_html}'
        f'{ops_html}'
        f'{partners_html}'
        f'{french_html}'
        f'<div class="card__divider"></div>'
        f'{actors_html}'
        f'{risks_html}'
        f'{angle_html}'
        f'{sources_html}'
        f'</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)


def render() -> None:
    df = _load_projects()

    # Méthode block
    nb_elite = int((df["hot_score"] >= 90).sum())
    nb_hot = int(((df["hot_score"] >= 70) & (df["hot_score"] < 90)).sum())
    countries_count = df["country"].nunique()

    st.markdown(
        f"""
        <div style="background:#141414; border:1px solid #2A2A2A;
                    padding:1.1rem 1.25rem; margin:0.8rem 0 1.2rem 0;">
          <div style="color:#E63323; font-size:0.72rem; font-weight:600;
                      letter-spacing:0.14em; margin-bottom:0.5rem;">
            ANGLE DIFFÉRENCIANT H-J — AFRIQUE
          </div>
          <div style="color:#F2F2F2; font-size:1.5rem; font-family:'Crimson Pro',Georgia,serif;
                      font-weight:600; margin-bottom:0.5rem;">
            {len(df)} projets en cours · {nb_elite} élite (90+) · {nb_hot} très chauds (70-89)
          </div>
          <div style="color:#CFCFCF; font-size:0.9rem; line-height:1.5; max-width:76ch;">
            Projets gaz / pétrole / mines / construction en cours sur <b>{countries_count} pays</b>
            — Mauritanie, Sénégal, Côte d'Ivoire, Nigeria, Mali, Niger, Mozambique, Angola, RDC
            notamment. Pour chaque projet : <b>opérateurs internationaux</b>, <b>partenaires locaux</b>,
            <b>exposition française</b>, <b>signaux de risque contentieux</b> et
            <b class="brand-accent">angle H-J à mettre en avant</b>.
            <div style="color:#9A9A9A; font-size:0.82rem; margin-top:0.6rem;">
              💡 L'expertise reconnue d'H-J sur la Mauritanie + commodities + arbitrage international
              CCJA/ICSID est différenciante. Projets scorés par propension au contentieux (expropriation en
              cours, dépassement budget, retards, partenariats multi-opérateurs).
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Filters
    with st.sidebar:
        st.markdown("### 🌍 Filtrer les projets Afrique")
        country_opts = sorted(df["country"].dropna().unique().tolist())
        country_sel = st.multiselect(
            "Pays", country_opts, default=country_opts, key="africa_country",
        )
        sector_opts = sorted(df["sector"].dropna().unique().tolist())
        sector_sel = st.multiselect(
            "Secteur", sector_opts, default=sector_opts, key="africa_sector",
            format_func=lambda s: _SECTOR_LABEL.get(s, s),
        )
        only_french = st.checkbox(
            "🇫🇷 Avec exposition française uniquement", value=False, key="africa_french",
            help="Projets où un groupe français est opérateur, partenaire ou sous-contractant.",
        )
        only_elite = st.checkbox(
            "Score ≥ 90 uniquement", value=False, key="africa_elite",
        )

    f = df.copy()
    if country_sel:
        f = f[f["country"].isin(country_sel)]
    if sector_sel:
        f = f[f["sector"].isin(sector_sel)]
    if only_french:
        f = f[f["french_exposure"].fillna("").str.strip().astype(bool) & ~f["french_exposure"].str.lower().str.startswith("nulle")]
    if only_elite:
        f = f[f["hot_score"] >= 90]

    st.markdown(
        f'<p class="brand-dim"><b>{len(f)}</b> projets affichés · triés par score contentieux.</p>',
        unsafe_allow_html=True,
    )

    for _, row in f.iterrows():
        render_project_card(row)
