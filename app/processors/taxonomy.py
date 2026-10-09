"""Closed taxonomies for the arbitration domain + commodity-focus scoring.

Keyword-driven classifiers (fast, deterministic, readable). The lists are
tuned to the client's priority — commodities arbitration — so expect them
to flag firms/speakers in oil & gas, mining, trading, metals, LNG, shipping,
agricultural commodities and sports arbitration (which overlaps with the
commodities ecosystem via FIFA transfer disputes and horse-racing matters,
left for the client to decide).
"""
from __future__ import annotations

from dataclasses import dataclass


# ─── Firm type taxonomy ────────────────────────────────────────────────

FIRM_TYPE_PATTERNS: dict[str, tuple[str, ...]] = {
    "Arbitration institution": (
        "icc international court of arbitration", "icc court", "lcia",
        "hkiac", "scc arbitration", "stockholm chamber", "siac",
        "cietac", "cas sport", "court of arbitration for sport",
        "chamber of arbitration", "arbitration centre", "arbitration center",
        "arbitral institute", "jams", "aaa icdr", "wipo arbitration",
    ),
    "Barrister chambers": (
        "chambers", "barristers", "essex court chambers", "fountain court",
        "temple chambers", "20 essex", "4 new square", "serle court",
        "atkin chambers",
    ),
    "Academic": (
        "university", "école de droit", "sciences po", "dauphine",
        "faculty of law", "institute of law", "centre de recherche",
        "max planck", "queen mary", "sorbonne",
    ),
    "LitFunder": (
        "burford capital", "omni bridgeway", "therium", "nivalion",
        "litigation funding", "litfunder", "funder", "harbour litigation",
        "woodsford",
    ),
    "Expert / consultant": (
        "forensic", "fti consulting", "secretariat economists", "kpmg forensic",
        "pwc forensic", "charles river associates", "expert witness",
        "quantum expert", "berkeley research group", "brg", "compass lexecon",
        "versant partners",
    ),
    "Government": (
        "ministère", "ministry of", "attorney general", "procureur",
        "european commission", "eu commission", "state attorney",
    ),
    "In-house": (
        "general counsel", "legal director", "chief legal officer",
    ),
    "Media": (
        "gar ", "global arbitration review", "mondaq", "lexology",
        "jus mundi", "kluwer arbitration",
    ),
}

# Fallback — anything else matching lawyer-speak is a "Law firm".
LAWYER_SIGNALS = (
    "llp", "sarl", "avocats", "law firm", "lawyers", "attorneys",
    "cabinet d'avocats", "sas d'avocats", "ltd", "pllc", "p.c.",
    "studio legale", "advocaten", "rechtsanwälte",
)


def classify_firm_type(firm_name: str, description: str = "") -> str:
    """Pick the FIRST matching bucket. Falls back to 'Law firm' when the
    name/description reads like a lawyer-speak corporate entity, else
    'Unknown'.
    """
    hay = f" {(firm_name or '').lower()} {(description or '').lower()} "
    for bucket, keywords in FIRM_TYPE_PATTERNS.items():
        for kw in keywords:
            if kw in hay:
                return bucket
    for sig in LAWYER_SIGNALS:
        if sig in hay:
            return "Law firm"
    # If the firm name contains "arbitration" or "disputes" without being
    # an institution, assume a boutique law firm
    if any(w in hay for w in (" arbitration ", " disputes ", " dispute resolution ")):
        return "Law firm (boutique)"
    return "Unknown"


# ─── Role taxonomy ─────────────────────────────────────────────────────

ROLE_PATTERNS: dict[str, tuple[str, ...]] = {
    "Arbitrator / independent": (
        "independent arbitrator", "international arbitrator",
        "sole arbitrator", "arbitrator", "arbitre",
        "chartered arbitrator",
    ),
    "Barrister (QC/KC)": (
        " kc", " qc", " kcmg", "king's counsel", "queen's counsel",
    ),
    "Partner": (
        "senior partner", "managing partner", "equity partner", "partner",
        "associé ", "associée ",
    ),
    "Counsel / Of Counsel": (
        "counsel", "of counsel",
    ),
    "Associate": (
        "senior associate", "associate", "collaborateur", "collaboratrice",
    ),
    "Academic": (
        "professor", "professeur", "lecturer", "doyen", "dean", "chair of ",
        "research fellow", "chercheur",
    ),
    "Judge / former judge": (
        "judge", "juge", "justice", "president of the", "former president",
    ),
    "Expert witness": (
        "expert witness", "quantum expert", "testifying expert",
        "forensic accountant", "damages expert",
    ),
    "In-house": (
        "general counsel", "chief legal officer", "legal director",
        "head of legal", "in-house counsel",
    ),
    "Government / regulator": (
        "member of the legal service", "state attorney", "ministry",
    ),
}


def classify_role(title: str) -> str:
    t = f" {(title or '').lower()} "
    for bucket, keywords in ROLE_PATTERNS.items():
        for kw in keywords:
            if kw in t:
                return bucket
    return "Other / to review"


# ─── Practice-area taxonomy ────────────────────────────────────────────

PRACTICE_PATTERNS: dict[str, tuple[str, ...]] = {
    "Commercial arbitration": (
        "commercial arbitration", "commercial disputes",
    ),
    "Investment arbitration / ISDS": (
        "investment arbitration", "isds", "investor-state", "investor state",
        "bit arbitration", "icsid", "treaty arbitration",
    ),
    "Construction & infrastructure": (
        "construction", "infrastructure", "epc", "engineering disputes",
        "fidic",
    ),
    "Energy & natural resources": (
        "energy", "oil & gas", "oil and gas", "mining", "upstream",
        "downstream", "lng", "liquefied natural gas", "renewables",
        "offshore", "petroleum", "pipeline", "concession",
    ),
    "Commodities & trading": (
        "commodity", "commodities", "trading", "gafta", "ffa", "lme",
        "matières premières", "trafigura", "glencore", "vitol", "mercuria",
        "cargill", "louis dreyfus", "charbon", "coal",
    ),
    "M&A disputes": (
        "post-m&a", "post-m&a disputes", "shareholder disputes", "sha",
        "spa dispute",
    ),
    "Sports arbitration (CAS)": (
        "sports arbitration", "cas", "court of arbitration for sport",
    ),
    "IP / tech arbitration": (
        "intellectual property", "ip arbitration", "patent", "tech arbitration",
    ),
    "Finance / banking disputes": (
        "banking", "financial services", "finance disputes",
    ),
    "Public international law": (
        "public international law", "pil ", "state responsibility",
        "boundary dispute",
    ),
    "Enforcement & setting aside": (
        "enforcement", "setting aside", "set-aside", "exequatur",
        "new york convention",
    ),
    "Interim measures / provisional": (
        "interim measures", "provisional measures", "emergency arbitrator",
    ),
}


def tag_practice_areas(text: str) -> list[str]:
    hay = f" {(text or '').lower()} "
    out: list[str] = []
    for bucket, kws in PRACTICE_PATTERNS.items():
        if any(kw in hay for kw in kws):
            out.append(bucket)
    return out


# ─── Commodity-relevance scorer (THE priority axis) ────────────────────

# Weighted keywords — each hit adds its weight to the raw score (then
# clipped to 0-100). We split them in strong / medium / weak buckets so
# occasional mentions don't inflate the score.

COMMODITY_STRONG = (
    "commodity", "commodities", "matières premières", "matieres premieres",
    "oil and gas", "oil & gas", "pétrolier", "pétrolière", "petroleum",
    "mining", "minier", "minière", "lng", "liquefied natural gas",
    "coal", "charbon", "metals", "métaux", "steel", "acier",
    "upstream", "downstream", "exploration & production",
    "gafta", "ffa ", "lme ", "trafigura", "glencore", "vitol", "mercuria",
    "cargill", "louis dreyfus", "cobalt", "nickel", "copper", "aluminium",
    "iron ore",
    # Energy sector — nearly always commodity-adjacent in the arbitration
    # context (power concessions, oil & gas, mining megaprojects …)
    " energy ", "energy sector", "energy disputes", "energy arbitration",
    "energy transition", "natural resources", " oil ", " gas ",
    "ore ", " ore", "concession",
)
COMMODITY_MEDIUM = (
    "pipeline", "refinery", "offshore",
    "shipping", "charterparty", "maritime", "agricultural",
    "cocoa", "sugar", "wheat", "corn", "soy",
    "power project", "ipp ", "power purchase",
    "epc ", "fidic", " fpso",
)
COMMODITY_WEAK = (
    "trade", "trading", "export", "import", "sanctions", "ohada",
    "africa", "latin america", "emerging markets",
    "construction", "infrastructure", "investor-state",
)


@dataclass
class CommodityScore:
    score: int
    reasoning: str


def score_commodity_relevance(*text_blobs: str) -> CommodityScore:
    """Combine every text source (firm desc, practice areas, event titles,
    speaker title) and return a 0-100 score + a reasoning string.
    """
    hay = " ".join((b or "").lower() for b in text_blobs if b)
    hits: list[str] = []
    score = 0
    for kw in COMMODITY_STRONG:
        if kw in hay:
            hits.append(f"strong:{kw.strip()}")
            score += 25
    for kw in COMMODITY_MEDIUM:
        if kw in hay:
            hits.append(f"medium:{kw.strip()}")
            score += 10
    for kw in COMMODITY_WEAK:
        if kw in hay:
            hits.append(f"weak:{kw.strip()}")
            score += 3
    score = min(100, score)
    reasoning = ", ".join(hits[:12]) if hits else "no commodity-related keywords detected"
    return CommodityScore(score=score, reasoning=reasoning)


# ─── Geographic focus (simple keyword tagging) ─────────────────────────

GEO_PATTERNS: dict[str, tuple[str, ...]] = {
    "France": ("france", "paris", "lyon", "marseille"),
    "UK": ("united kingdom", "uk ", "london", "english law"),
    "Europe (rest)": ("europe", "european", "brussels", "zurich",
                      "stockholm", "geneva", "milan", "madrid", "berlin",
                      "amsterdam", "vienna"),
    "North America": ("united states", "usa", "us ", "new york",
                      "washington", "canada", "california"),
    "Latin America": ("brazil", "mexico", "argentina", "chile", "colombia",
                      "latin america", "latam"),
    "Africa": ("africa", "nigeria", "egypt", "south africa", "morocco",
               "ohada", "cameroon", "ghana", "kenya"),
    "Middle East / MENA": ("mena", "uae", "dubai", "qatar", "saudi arabia",
                           "oman", "bahrain", "kuwait", "iran", "iraq"),
    "Asia (Pacific)": ("asia", "singapore", "hong kong", "china", "japan",
                       "korea", "india", "australia"),
}


def tag_geo_focus(text: str) -> list[str]:
    hay = f" {(text or '').lower()} "
    out: list[str] = []
    for bucket, kws in GEO_PATTERNS.items():
        if any(kw in hay for kw in kws):
            out.append(bucket)
    return out
