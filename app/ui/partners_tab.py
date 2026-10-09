"""Render the "Partenaires & acteurs du secteur" tab as qualified cards.

Each practitioner is rendered as a dark card showing :
  - Name · current title · firm (+ firm type)
  - Big commodity score on the right
  - A "Qualification" block that synthesizes what they actually do
    (practice areas + specializations + role_category + geo focus)
  - Chips : role category, geographic focus, sources
  - Contacts row : email, phone, LinkedIn, profile
  - Meta : years present at PAW, events count, firm move marker

Mirrors the Eurosatory card layout — every data point should be scannable
without needing to click or scroll inside the row.
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


# ─── Data loader ───────────────────────────────────────────────────────


@st.cache_data(ttl=120)
def _load_partners() -> pd.DataFrame:
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
            s.country, s.member_type,
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


# ─── Helpers ───────────────────────────────────────────────────────────


def _esc(x) -> str:
    return html.escape(str(x) if x is not None else "")


_FIRM_TYPE_LABEL = {
    "biglaw": "BigLaw",
    "boutique": "Boutique arbitrage",
    "boutique_arb": "Boutique arbitrage",
    "arbitration_center": "Centre d'arbitrage",
    "arbitration_institution": "Institution",
    "chambers": "Chambers",
    "academic": "Académique",
    "university": "Académique",
    "expert": "Expert",
    "in_house": "In-house",
    "corporate": "In-house",
    "litfunder": "Litigation Funder",
    "litigation_funder": "Litigation Funder",
    "government": "Gouvernement",
    "law_firm": "Cabinet",
}

_ROLE_LABEL = {
    "Partner": "Partner",
    "Counsel / Of Counsel": "Counsel",
    "Associate": "Associate",
    "Arbitrator / independent": "Arbitre",
    "Academic": "Académique",
    "In-house": "In-house",
    "Judge / former judge": "Juge",
    "Government / regulator": "Régulateur",
    "Barrister (QC/KC)": "Barrister QC/KC",
    "Expert witness": "Expert",
    "Other / to review": "Rôle à qualifier",
}

_SOURCE_LABEL = {
    "paw": "PAW",
    "asa_profiles": "ASA",
    "swiss_summit": "Swiss Summit",
    "aec_week": "AEC Week",
}


def _firm_type_label(ft: Optional[str]) -> Optional[str]:
    if not ft:
        return None
    s = str(ft).strip().lower()
    if s in ("unknown", "n/c", "none", "nan", ""):
        return None
    return _FIRM_TYPE_LABEL.get(s, s.replace("_", " ").title())


def _clean_str(v) -> str:
    """Return v as str, mapping NaN / 'nan' / 'None' / float NaN to ''."""
    if v is None:
        return ""
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return ""
    except Exception:
        pass
    s = str(v).strip()
    if s.lower() in ("nan", "none", "n/c", "<na>"):
        return ""
    return s


def _role_label(role: Optional[str]) -> str:
    if not role:
        return "Rôle à qualifier"
    return _ROLE_LABEL.get(role, role)


def _split_list(s, sep: str = ";") -> list[str]:
    """Robust split : handles NaN, None, numbers, strings."""
    if s is None:
        return []
    try:
        import math
        if isinstance(s, float) and math.isnan(s):
            return []
    except Exception:
        pass
    s = str(s).strip()
    if not s or s.lower() in ("nan", "none", "<na>"):
        return []
    return [p.strip() for p in s.split(sep) if p.strip()]


_HJ_PRACTICE_KEYWORDS = (
    "commercial arbitration",
    "construction",
    "energy & natural resources",
    "commodities",
    "investment arbitration",
    "infrastructure",
    "oil & gas",
    "mining",
)


def _hj_affinity_topics(row: pd.Series) -> list[str]:
    """Return the H-J-aligned practice areas found on this profile."""
    pracs = _split_list(row.get("practice_areas")) + _split_list(row.get("specializations"))
    matches = []
    seen = set()
    for p in pracs:
        pl = p.lower()
        for kw in _HJ_PRACTICE_KEYWORDS:
            if kw in pl and pl not in seen:
                matches.append(p)
                seen.add(pl)
                break
    return matches


def _criteria_check(row: pd.Series) -> list[tuple[bool, str]]:
    """Return the list of (passes, label) for the 3 selection criteria."""
    last_year = row.get("last_year") or 0
    sources = int(row.get("sources_count") or 0)
    hj_topics = _hj_affinity_topics(row)
    return [
        (last_year >= 2024, f"Actif sur le circuit en {int(last_year)}" if last_year else "Pas d'activité récente"),
        (bool(hj_topics), f"Spécialisation en {', '.join(hj_topics[:3])}" if hj_topics else "Spécialisation hors cœur H-J"),
        (sources >= 2, f"Multi-circuit ({sources} sources)" if sources >= 2 else f"Un seul circuit ({sources})"),
    ]


def _action_suggested(row: pd.Series) -> tuple[str, str]:
    """Return (icon, text) for the suggested action on this partner."""
    role = (row.get("role") or "").lower()
    firm = _clean_str(row.get("firm"))
    firm_type = (row.get("firm_type") or "").lower()
    if row.get("has_moved"):
        return "🔁", "Firm move détecté — reconnecter maintenant, fenêtre de recommandation fraîche."
    if "arbitrator" in role or "arbitrateur" in role or "arbitre" in role:
        return "🎙", "Inviter sur un panel d'un événement cabinet — nomination arbitre à cultiver."
    if "academic" in role or "académique" in role or "academic" in firm_type:
        return "🎓", "Inviter à intervenir sur un événement cabinet / co-publication."
    if "in-house" in role or "in_house" in firm_type:
        return "🎯", "Lead direct potentiel — approche feutrée, pas de pitch frontal."
    if "judge" in role or "juge" in role:
        return "⚖", "Entretenir la relation (ex-magistrat / arbitre récurrent)."
    if "partner" in role or "counsel" in role:
        return "🤝", "Inviter au prochain déjeuner cabinet (apporteur / co-counsel)."
    return "✉️", "Inclure dans la newsletter cabinet et suivre LinkedIn."


def _qualification_text(row: pd.Series) -> str:
    """Build the one-paragraph qualification shown in the highlighted box."""
    bits = []
    role = row.get("role")
    role_lbl = _role_label(role)
    title = row.get("title")
    firm = row.get("firm")

    opener = ""
    if title:
        opener = f"<b>{_esc(title)}</b>"
    elif role_lbl:
        opener = f"<b>{_esc(role_lbl)}</b>"
    if firm:
        opener += f" chez <b>{_esc(firm)}</b>" if opener else f"Praticien chez <b>{_esc(firm)}</b>"
    if opener:
        bits.append(opener + ".")

    hj_topics = _hj_affinity_topics(row)
    if hj_topics:
        topics = ", ".join(_esc(t) for t in hj_topics[:4])
        bits.append(f"Angle H-J : {topics}.")

    geo = _clean_str(row.get("geographic_focus"))
    if geo:
        geo_pretty = " · ".join([g for g in (p.strip() for p in geo.split(";")) if g])
        bits.append(f"Focus géographique : {_esc(geo_pretty)}.")

    nb_events = int(row.get("events_count") or 0)
    sources_present = row.get("sources_present") or ""
    if sources_present:
        src_labels = [_SOURCE_LABEL.get(s, s) for s in sources_present.split(",") if s]
        sources_txt = ", ".join(src_labels)
    else:
        src_labels = []
        sources_txt = ""
    presence = []
    if nb_events:
        presence.append(f"{nb_events} intervention{'s' if nb_events > 1 else ''}")
    first_year = row.get("first_year")
    last_year = row.get("last_year")
    if first_year and last_year and first_year != last_year:
        presence.append(f"{int(first_year)}–{int(last_year)}")
    elif first_year:
        presence.append(str(int(first_year)))
    if sources_txt:
        presence.append(f"source{'s' if len(src_labels) > 1 else ''} : {_esc(sources_txt)}")
    if presence:
        bits.append("Visibilité : " + " · ".join(presence) + ".")

    if row.get("has_moved"):
        bits.append("<span class='brand-accent'>🔁 Firm move détecté</span> — fenêtre de reconnexion fraîche.")

    return " ".join(bits)


# ─── Card render ───────────────────────────────────────────────────────


def render_partner_card(row: pd.Series) -> None:
    name = _esc(row["name"])
    title = _esc(_clean_str(row.get("title")))
    firm = _esc(_clean_str(row.get("firm")))
    firm_type_lbl = _firm_type_label(row.get("firm_type"))

    role_lbl = _role_label(row.get("role"))
    role_chip = f'<span class="chip chip--role">{_esc(role_lbl)}</span>'

    geo = _clean_str(row.get("geographic_focus"))
    # Pretty-print the geo list : "France;UK;Europe (rest);Africa" → "France · UK · Europe (rest) · Africa"
    geo_pretty = " · ".join([g for g in (p.strip() for p in geo.split(";")) if g]) if geo else ""
    # Collapse if too long
    if len(geo_pretty) > 60:
        geo_pretty = geo_pretty[:57] + "…"
    geo_chip = (
        f'<span class="chip chip--geo">🌍 {_esc(geo_pretty)}</span>'
        if geo_pretty else ""
    )

    sources_present = row.get("sources_present") or ""
    src_chips = []
    for s in sources_present.split(","):
        s = s.strip()
        if s:
            src_chips.append(f'<span class="chip">{_esc(_SOURCE_LABEL.get(s, s))}</span>')
    sources_html = "".join(src_chips)

    specs_chips = []
    specs = _split_list(row.get("specializations"))
    for sp in specs[:3]:
        specs_chips.append(f'<span class="chip">{_esc(sp)}</span>')

    chips_html = f'<div class="pcard__chips">{role_chip}{geo_chip}{"".join(specs_chips)}{sources_html}</div>'

    # Score
    score = int(row.get("commodity") or 0)
    score_html = (
        f'<div class="pcard__score">'
        f'<div class="pcard__score-value">{score}</div>'
        f'<div class="pcard__score-label">Score commodities</div>'
        f'</div>'
    )

    # Firm line
    firm_line = ""
    if firm:
        ft_html = f'<span class="pcard__firm-type">{_esc(firm_type_lbl)}</span>' if firm_type_lbl else ""
        firm_line = f'<div class="pcard__firm">{firm}{ft_html}</div>'
    title_line = f'<div class="pcard__title">{title}</div>' if title else ""

    # Contacts
    contact_bits = []
    email = _clean_str(row.get("email"))
    email_source = _clean_str(row.get("email_source"))
    if email:
        source_hint = ""
        if email_source == "asa":
            source_hint = ' <span class="brand-accent" style="font-size:0.65rem;">· VÉRIFIÉ ASA</span>'
        elif email_source == "guessed_mx_ok":
            source_hint = ' <span class="brand-dim" style="font-size:0.65rem;">· MX ok</span>'
        contact_bits.append(
            f'<a href="mailto:{_esc(email)}" class="link-chip">📧 {_esc(email)}{source_hint}</a>'
        )
    phone = _clean_str(row.get("phone"))
    if phone:
        contact_bits.append(f'<span class="link-chip">📞 {_esc(phone)}</span>')
    linkedin = _clean_str(row.get("linkedin_url"))
    if linkedin:
        contact_bits.append(
            f'<a href="{_esc(linkedin)}" target="_blank" class="link-chip">🔗 LinkedIn</a>'
        )
    profile_url = _clean_str(row.get("profile_url"))
    if profile_url:
        contact_bits.append(
            f'<a href="{_esc(profile_url)}" target="_blank" class="link-chip">📄 Profil</a>'
        )
    # Fallback : Google search by name + firm
    search_q = row["name"] + (f" {firm}" if firm else "")
    contact_bits.append(
        f'<a href="https://www.google.com/search?q={quote_plus(search_q + " arbitration")}" '
        f'target="_blank" class="link-chip">🔎 Google</a>'
    )
    contacts_html = f'<div class="pcard__contacts">{"".join(contact_bits)}</div>'

    qual_html = f'<div class="pcard__qual">{_qualification_text(row)}</div>'

    # "Pourquoi lui" : the 3 criteria check
    checks = _criteria_check(row)
    check_lines = "".join(
        f'<li style="color:{"#F2F2F2" if ok else "#6B6B6B"};">'
        f'{"✅" if ok else "·"} {_esc(label)}</li>'
        for ok, label in checks
    )
    why_html = (
        f'<div style="margin-top:0.6rem; padding:0.5rem 0.75rem; '
        f'background:#141414; border:1px dashed #2A2A2A;">'
        f'<div style="color:#9A9A9A; font-weight:600; letter-spacing:0.14em; '
        f'font-size:0.68rem; margin-bottom:0.2rem;">POURQUOI LUI</div>'
        f'<ul style="margin:0.2rem 0 0 0; padding:0; font-size:0.82rem; '
        f'list-style:none;">{check_lines}</ul>'
        f'</div>'
    )

    # Action suggérée
    act_icon, act_text = _action_suggested(row)
    action_html = (
        f'<div style="margin-top:0.5rem; padding:0.5rem 0.75rem; '
        f'background:rgba(230,51,35,0.08); border-left:3px solid #E63323; '
        f'color:#F2F2F2; font-size:0.85rem;">'
        f'<span style="color:#E63323; font-weight:600; letter-spacing:0.08em; '
        f'font-size:0.7rem;">ACTION SUGGÉRÉE</span><br/>'
        f'{act_icon} {_esc(act_text)}'
        f'</div>'
    )

    card_html = (
        f'<div class="pcard">'
        f'<div class="pcard__top">'
        f'<div class="pcard__identity">'
        f'<div class="pcard__name">{name}</div>'
        f'{title_line}'
        f'{firm_line}'
        f'</div>'
        f'{score_html}'
        f'</div>'
        f'{qual_html}'
        f'{chips_html}'
        f'{why_html}'
        f'{action_html}'
        f'{contacts_html}'
        f'</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)


# ─── Tab render ────────────────────────────────────────────────────────


def _is_in_top_network(row: pd.Series) -> bool:
    """3 criteria: active 2024+ · H-J practice affinity · multi-circuit."""
    last_year = row.get("last_year") or 0
    sources = int(row.get("sources_count") or 0)
    pracs = (str(row.get("practice_areas") or "") + " " + str(row.get("specializations") or "")).lower()
    has_hj = any(kw in pracs for kw in _HJ_PRACTICE_KEYWORDS)
    return last_year >= 2024 and sources >= 2 and has_hj


def render(by_source: dict) -> None:
    df = _load_partners()
    df_full = df.copy()
    top_mask = df.apply(_is_in_top_network, axis=1)
    df_top = df[top_mask].copy()

    # ── Méthodologie en haut ──────────────────────────────────────────
    st.markdown(
        f"""
        <div style="background:#141414; border:1px solid #2A2A2A;
                    padding:1.1rem 1.25rem; margin:0.8rem 0 1.2rem 0;">
          <div style="color:#E63323; font-size:0.72rem; font-weight:600;
                      letter-spacing:0.14em; margin-bottom:0.5rem;">
            MÉTHODE DE SÉLECTION
          </div>
          <div style="color:#F2F2F2; font-size:1.5rem; font-family:'Crimson Pro',Georgia,serif;
                      font-weight:600; margin-bottom:0.5rem;">
            {len(df_top)} praticiens à cultiver — pile dans l'écosystème H-J
          </div>
          <div style="color:#CFCFCF; font-size:0.9rem; line-height:1.5; max-width:72ch;">
            Sur <b>{len(df_full):,}</b> praticiens arbitralistes mondiaux cartographiés,
            on retient ceux qui remplissent les <b class="brand-accent">3 critères simultanés</b> :
            <ul style="margin:0.5rem 0 0.3rem 1.2rem; padding:0;">
              <li><b>Actifs 2024-2026</b> — vus sur au moins un événement du circuit récent (pas inactif)</li>
              <li><b>Angle H-J</b> — spécialisation en commercial arbitration, construction, énergie, commodities ou investment arbitration</li>
              <li><b>Multi-circuit</b> — présents sur ≥ 2 événements (PAW, ASA Profiles, Swiss Summit, AEC Week) = acteurs centraux</li>
            </ul>
            <div style="color:#9A9A9A; font-size:0.82rem; margin-top:0.5rem;">
              Chaque carte précise <b>pourquoi lui</b> et propose <b>une action concrète</b>
              (inviter au déjeuner cabinet, panel événement, ou lead direct).
            </div>
          </div>
        </div>
        """.replace("{len(df_full):,}", f"{len(df_full):,}".replace(",", " ")),
        unsafe_allow_html=True,
    )

    # ── Filters sidebar ───────────────────────────────────────────────
    with st.sidebar:
        st.markdown("### 🤝 Filtrer le réseau")
        apply_top_filter = st.checkbox(
            "Top réseau pile cible uniquement", value=True, key="partners_top",
            help="Les 3 critères simultanés : actif + angle H-J + multi-circuit.",
        )
        only_with_email = st.checkbox(
            "Avec email valide", value=True, key="partners_email",
        )
        only_moved = st.checkbox(
            "🔁 Firm move < 18 mois", value=False, key="partners_moved",
            help="Fenêtre de reconnexion fraîche.",
        )
        all_sources = sorted(by_source.keys())
        sel_sources = st.multiselect(
            "Sources (événements)", all_sources, default=all_sources,
            key="partners_sources",
            help=(
                "paw = Paris Arbitration Week  ·  asa_profiles = ASA Profiles  ·  "
                "swiss_summit = Swiss Arbitration Summit  ·  aec_week = African Energy Week"
            ),
        )

    f = df_top if apply_top_filter else df_full
    if sel_sources:
        f = f[f["sources_present"].fillna("").apply(
            lambda s: any(src in s for src in sel_sources)
        )]
    if only_with_email:
        f = f[f["email"].fillna("").astype(bool)]
    if only_moved:
        f = f[f["has_moved"] == 1]

    role_opts = sorted({r for r in f["role"].dropna().unique() if r})
    cols_top = st.columns([1, 2])
    with cols_top[0]:
        role_sel = st.multiselect(
            "Rôle", role_opts, placeholder="Tous rôles", key="partners_role",
            format_func=_role_label,
        )
    if role_sel:
        f = f[f["role"].isin(role_sel)]
    with cols_top[1]:
        search = st.text_input(
            "🔎 Recherche (nom / cabinet / spécialisation)",
            placeholder="e.g. Freshfields, Noah Rubins, Oil and Gas, Linklaters",
            key="partners_search",
        )
    if search:
        s = search.lower()
        mask = (
            f["name"].fillna("").str.lower().str.contains(s, na=False)
            | f["firm"].fillna("").str.lower().str.contains(s, na=False)
            | f["title"].fillna("").str.lower().str.contains(s, na=False)
            | f["practice_areas"].fillna("").str.lower().str.contains(s, na=False)
            | f["specializations"].fillna("").str.lower().str.contains(s, na=False)
            | f["geographic_focus"].fillna("").str.lower().str.contains(s, na=False)
        )
        f = f[mask]

    f = f.sort_values(["events_count", "sources_count"], ascending=[False, False])

    st.markdown(
        f"<p class='brand-dim'><b>{len(f):,} praticiens</b> matchent les filtres.</p>".replace(",", " "),
        unsafe_allow_html=True,
    )

    PAGE_SIZE = 15
    total = len(f)
    if total > PAGE_SIZE:
        page = st.number_input(
            "Page", min_value=1,
            max_value=(total + PAGE_SIZE - 1) // PAGE_SIZE,
            value=1, step=1, key="partners_page",
        )
        start = (int(page) - 1) * PAGE_SIZE
        page_df = f.iloc[start:start + PAGE_SIZE]
        st.caption(f"Praticiens {start + 1}–{min(start + PAGE_SIZE, total)} / {total}")
    else:
        page_df = f

    for _, row in page_df.iterrows():
        render_partner_card(row)
