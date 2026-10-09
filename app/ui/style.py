"""Custom CSS injected into Streamlit for the branded dark theme.

Palette :
  - Background noir ``#0A0A0A``
  - Surface sombre ``#141414`` / ``#1C1C1C``
  - Accent rouge ``#E63323`` (chiffres clés, signaux chauds, boutons)
  - Texte principal ``#F2F2F2`` · secondaire ``#9A9A9A``
  - Borders ``#2A2A2A``

Typo : Google Fonts "Crimson Pro" (serif, titres) + "Inter" (sans-serif
corps). Both widely available, no restrictive license.

ZERO reference to any specific law firm — purely a color palette +
typography pairing. Non-brandable colors per standard trademark doctrine.
"""
from __future__ import annotations


BRAND_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Crimson+Pro:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="st-"], [class*="StApp"] {
    font-family: 'Inter', system-ui, -apple-system, sans-serif !important;
}

h1, h2, h3, .brand-title {
    font-family: 'Crimson Pro', Georgia, serif !important;
    font-weight: 600 !important;
    letter-spacing: -0.01em;
}

/* Primary accent — red used for titles, numbers, highlighted actions */
.brand-accent        { color: #E63323; }
.brand-accent-bg     { background: #E63323; }
.brand-dim           { color: #9A9A9A; }
.brand-muted         { color: #6B6B6B; }
.brand-surface       { background: #141414; border: 1px solid #2A2A2A; }

/* ────── Streamlit metrics ────── */
[data-testid="stMetricValue"] {
    font-family: 'Crimson Pro', Georgia, serif !important;
    color: #E63323 !important;
    font-weight: 700 !important;
    font-size: 1.65rem !important;
    white-space: nowrap !important;
    overflow: visible !important;
    text-overflow: unset !important;
    min-width: 0 !important;
}
[data-testid="stMetric"] {
    overflow: visible !important;
    min-width: 0 !important;
}
[data-testid="stMetric"] > div {
    overflow: visible !important;
    min-width: 0 !important;
}
[data-testid="stMetricLabel"] {
    color: #9A9A9A !important;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 0.7rem !important;
}

/* ────── Streamlit tabs ────── */
.stTabs [role="tablist"] {
    border-bottom: 1px solid #2A2A2A;
    gap: 0.5rem;
}
.stTabs [role="tab"] {
    font-family: 'Inter', sans-serif !important;
    font-size: 0.95rem;
    color: #9A9A9A;
    padding: 0.75rem 1.25rem;
    border-radius: 0;
}
.stTabs [role="tab"][aria-selected="true"] {
    color: #F2F2F2 !important;
    border-bottom: 2px solid #E63323 !important;
    font-weight: 600;
}

/* ────── Sidebar ────── */
[data-testid="stSidebar"] {
    background: #0A0A0A;
    border-right: 1px solid #2A2A2A;
}

/* ────── Buttons ────── */
.stButton button {
    background: transparent !important;
    color: #F2F2F2 !important;
    border: 1px solid #E63323 !important;
    border-radius: 2px !important;
    font-weight: 500 !important;
    transition: all 0.15s;
}
.stButton button:hover {
    background: #E63323 !important;
    color: #0A0A0A !important;
}

/* ────── Data editor / dataframe ────── */
/* Only paint the OUTER container — never descendants, otherwise the
   glide-data-grid canvas is masked and the grid appears as a black box. */
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
    background: #141414;
    border: 1px solid #2A2A2A;
    border-radius: 2px;
}
/* Force the grid's own CSS variables to render on a dark surface */
[data-testid="stDataFrame"] .glideDataEditor,
[data-testid="stDataEditor"] .glideDataEditor {
    --gdg-bg-cell: #141414;
    --gdg-bg-cell-medium: #1A1A1A;
    --gdg-bg-header: #0F0F0F;
    --gdg-bg-header-has-focus: #1C1C1C;
    --gdg-bg-header-hovered: #1C1C1C;
    --gdg-text-dark: #F2F2F2;
    --gdg-text-medium: #CFCFCF;
    --gdg-text-light: #9A9A9A;
    --gdg-text-header: #F2F2F2;
    --gdg-text-group-header: #E63323;
    --gdg-border-color: #2A2A2A;
    --gdg-horizontal-border-color: #2A2A2A;
    --gdg-accent-color: #E63323;
    --gdg-accent-light: rgba(230, 51, 35, 0.15);
    --gdg-link-color: #E63323;
}

/* ────── Custom card component ────── */
.card {
    background: #141414;
    border: 1px solid #2A2A2A;
    border-radius: 2px;
    padding: 1.5rem;
    margin-bottom: 1rem;
    transition: border-color 0.15s;
}
.card:hover {
    border-color: #E63323;
}
.card__sector {
    display: inline-block;
    color: #E63323;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    font-size: 0.72rem;
    font-weight: 600;
    margin-bottom: 0.5rem;
}
.card__sector::after {
    content: " · ";
    color: #6B6B6B;
    margin: 0 0.4rem;
}
.card__country {
    color: #9A9A9A;
    font-size: 0.78rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
.card__name {
    font-family: 'Crimson Pro', Georgia, serif;
    font-size: 1.9rem;
    font-weight: 600;
    color: #F2F2F2;
    margin: 0.2rem 0 0.35rem 0;
    line-height: 1.1;
}
.card__subline {
    color: #9A9A9A;
    font-size: 0.88rem;
    margin-bottom: 1rem;
}
.card__stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 1rem;
    margin: 1.1rem 0;
}
.card__stat {
    border-left: 2px solid #E63323;
    padding-left: 0.7rem;
}
.card__stat-label {
    color: #6B6B6B;
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    font-weight: 500;
    margin-bottom: 0.15rem;
}
.card__stat-value {
    font-family: 'Crimson Pro', Georgia, serif;
    font-size: 1.4rem;
    color: #F2F2F2;
    font-weight: 600;
}
.card__divider {
    height: 1px;
    background: #2A2A2A;
    margin: 1.1rem 0;
}
.card__section-title {
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: #9A9A9A;
    font-size: 0.72rem;
    font-weight: 600;
    margin-bottom: 0.75rem;
}
.signal {
    border-left: 3px solid #E63323;
    background: #1C1C1C;
    padding: 0.65rem 0.85rem;
    margin-bottom: 0.5rem;
    border-radius: 0 2px 2px 0;
}
.signal--news {
    border-left-color: #6B6B6B;
    background: #141414;
}
.signal__kind {
    color: #E63323;
    font-weight: 600;
    font-size: 0.82rem;
    letter-spacing: 0.03em;
}
.signal--news .signal__kind {
    color: #9A9A9A;
}
.signal__detail {
    color: #CFCFCF;
    font-size: 0.86rem;
    margin-top: 0.15rem;
    line-height: 1.35;
}
.signal__meta {
    color: #6B6B6B;
    font-size: 0.72rem;
    margin-top: 0.25rem;
    letter-spacing: 0.03em;
}
.card__contact {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    color: #CFCFCF;
    font-size: 0.9rem;
}
.card__contact a {
    color: #E63323;
    text-decoration: none;
}
.card__contact a:hover {
    text-decoration: underline;
}
.card__angle {
    background: #1C1C1C;
    border-top: 2px solid #E63323;
    padding: 1rem;
    color: #CFCFCF;
    font-size: 0.9rem;
    font-style: italic;
    line-height: 1.45;
}
.card__angle::before {
    content: "💡 ANGLE COMMERCIAL";
    display: block;
    font-style: normal;
    font-weight: 600;
    color: #E63323;
    letter-spacing: 0.14em;
    font-size: 0.68rem;
    margin-bottom: 0.4rem;
}
.badge {
    display: inline-block;
    background: #E63323;
    color: #0A0A0A;
    font-size: 0.68rem;
    font-weight: 700;
    padding: 0.2rem 0.5rem;
    border-radius: 1px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
.badge--grey {
    background: #2A2A2A;
    color: #CFCFCF;
}

/* ────── Card head + external links ────── */
.card__head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.3rem;
    margin-bottom: 0.3rem;
}
.card__links {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin: 0.3rem 0 1rem 0;
}
.link-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.25rem 0.6rem;
    background: #1C1C1C;
    border: 1px solid #2A2A2A;
    color: #CFCFCF !important;
    font-size: 0.78rem;
    letter-spacing: 0.02em;
    text-decoration: none !important;
    border-radius: 2px;
    transition: all 0.15s;
}
.link-chip:hover {
    border-color: #E63323;
    color: #FFFFFF !important;
}
.link-chip--pappers {
    border-color: #E63323;
    color: #FFFFFF !important;
}
.card__empty {
    color: #6B6B6B;
    font-style: italic;
    font-size: 0.85rem;
    padding: 0.6rem 0;
}

/* ────── Row lists inside a card (ICSID, DECP) ────── */
.rows {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    margin-bottom: 0.4rem;
}
.row-case {
    background: #1C1C1C;
    border-left: 3px solid #E63323;
    padding: 0.55rem 0.75rem;
    border-radius: 0 2px 2px 0;
}
.row-case__head {
    color: #F2F2F2;
    font-size: 0.9rem;
    letter-spacing: 0.02em;
}
.row-case__head b {
    color: #E63323;
    font-weight: 600;
}
.row-case__head a {
    color: #E63323;
    text-decoration: none;
}
.row-case__meta {
    color: #9A9A9A;
    font-size: 0.78rem;
    margin-top: 0.15rem;
    letter-spacing: 0.02em;
}
.row-decp {
    display: flex;
    gap: 0.9rem;
    background: #1C1C1C;
    border-left: 3px solid #E63323;
    padding: 0.55rem 0.75rem;
    border-radius: 0 2px 2px 0;
    align-items: flex-start;
}
.row-decp__amt {
    font-family: 'Crimson Pro', Georgia, serif;
    font-size: 1.3rem;
    color: #E63323;
    font-weight: 600;
    min-width: 110px;
    text-align: right;
    line-height: 1.1;
}
.row-decp__body {
    flex: 1;
    min-width: 0;
}
.row-decp__object {
    color: #F2F2F2;
    font-size: 0.86rem;
    line-height: 1.3;
}
.row-decp__meta {
    color: #9A9A9A;
    font-size: 0.75rem;
    margin-top: 0.2rem;
}

/* ────── Partner card (onglet 1) ────── */
.pcard {
    background: #141414;
    border: 1px solid #2A2A2A;
    border-radius: 2px;
    padding: 1.1rem 1.25rem;
    margin-bottom: 0.9rem;
    transition: border-color 0.15s;
}
.pcard:hover {
    border-color: #E63323;
}
.pcard__top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 1rem;
    margin-bottom: 0.4rem;
}
.pcard__identity {
    flex: 1;
    min-width: 0;
}
.pcard__name {
    font-family: 'Crimson Pro', Georgia, serif;
    font-size: 1.35rem;
    color: #F2F2F2;
    font-weight: 600;
    line-height: 1.15;
    margin: 0;
}
.pcard__title {
    color: #CFCFCF;
    font-size: 0.9rem;
    margin-top: 0.15rem;
}
.pcard__firm {
    color: #E63323;
    font-size: 0.88rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    margin-top: 0.1rem;
}
.pcard__firm-type {
    color: #9A9A9A;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-left: 0.3rem;
}
.pcard__score {
    text-align: right;
    min-width: 100px;
}
.pcard__score-value {
    font-family: 'Crimson Pro', Georgia, serif;
    font-size: 1.8rem;
    color: #E63323;
    font-weight: 700;
    line-height: 1;
}
.pcard__score-label {
    color: #9A9A9A;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-top: 0.15rem;
}
.pcard__qual {
    background: #1C1C1C;
    border-left: 2px solid #E63323;
    padding: 0.5rem 0.75rem;
    margin: 0.6rem 0;
    color: #CFCFCF;
    font-size: 0.85rem;
    line-height: 1.4;
}
.pcard__qual b {
    color: #F2F2F2;
    font-weight: 600;
}
.pcard__chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
    margin: 0.5rem 0;
}
.chip {
    display: inline-block;
    background: #1C1C1C;
    border: 1px solid #2A2A2A;
    color: #CFCFCF;
    font-size: 0.72rem;
    padding: 0.15rem 0.5rem;
    border-radius: 2px;
    letter-spacing: 0.02em;
}
.chip--role {
    border-color: #E63323;
    color: #E63323;
}
.chip--geo {
    border-color: #9A9A9A;
}
.pcard__contacts {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin-top: 0.6rem;
    padding-top: 0.6rem;
    border-top: 1px dashed #2A2A2A;
}
.pcard__contacts a {
    color: #CFCFCF !important;
    text-decoration: none;
}
.pcard__contacts a:hover {
    color: #E63323 !important;
}
.pcard__meta {
    color: #6B6B6B;
    font-size: 0.72rem;
    margin-top: 0.5rem;
    letter-spacing: 0.02em;
}

/* ────── Hero banner ────── */
.hero {
    padding: 1.6rem 0 0.6rem 0;
    border-bottom: 1px solid #2A2A2A;
    margin-bottom: 1.6rem;
}
.hero__eyebrow {
    color: #E63323;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    font-size: 0.72rem;
    font-weight: 600;
}
.hero__title {
    font-family: 'Crimson Pro', Georgia, serif;
    font-size: 2.4rem;
    color: #F2F2F2;
    font-weight: 600;
    margin: 0.3rem 0 0.4rem 0;
    letter-spacing: -0.01em;
}
.hero__subtitle {
    color: #9A9A9A;
    font-size: 0.95rem;
    max-width: 72ch;
}
</style>
"""


def inject() -> None:
    """Call at the top of every Streamlit page."""
    import streamlit as st
    st.markdown(BRAND_CSS, unsafe_allow_html=True)
