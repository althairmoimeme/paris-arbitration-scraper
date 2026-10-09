"""Recompute a composite hot_score for each company.

The simple "max heat across signals" rank is too flat — a +300 % avenant
on a 1 M€ contract scores the same as a +30 % avenant on 500 M€.
This module replaces hot_score with a 0-100 composite that reflects
what actually matters for a BD lead on contentieux commercial 20-200 M€ :

  surcout_cum  (up to 40 pts) : cumulated € overrun from all avenants
  recency      (up to 25 pts) : whether we have an avenant in the last 6
                                months (full), 12 months (half), older (0)
  repetition   (up to 20 pts) : 5 × nb of avenants with heat ≥ 75 (serial
                                avenant pattern = project drifting)
  recent_xxl   (up to 15 pts) : one of the recent (12 months) marchés
                                is ≥ 50 M€

Each component is stored in a JSON blob on the Company row so the UI
can show the decomposition "Score 92 = 40 + 25 + 15 + 12".
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy import select

from app.database import Company, CompanySignal, SessionLocal, init_db
from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402


# How far "recent" goes
RECENT_6M = timedelta(days=180)
RECENT_12M = timedelta(days=365)
HOT_HEAT = 75           # heat threshold for an avenant to count as hot
BIG_MARCHE = 50_000_000

# Component caps
CAP_SURCOUT = 40
CAP_RECENCY = 25
CAP_REPETITION = 20
CAP_RECENT_XXL = 15


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def _surcout_cumule(signals: list[dict]) -> float:
    """Return the cumulated € overrun from hot avenants on this company.
    For a decp_avenant signal, amount_eur stores (new - initial).
    """
    total = 0.0
    for s in signals:
        if s["kind"] != "decp_avenant":
            continue
        amt = s.get("amount_eur") or 0
        if amt and amt > 0:
            total += amt
    return total


def _best_recency_days(signals: list[dict], today: date) -> int | None:
    """Return the days since the most recent avenant, or None."""
    best = None
    for s in signals:
        if s["kind"] != "decp_avenant":
            continue
        d = _parse_date(s.get("occurred_on"))
        if not d:
            continue
        delta = (today - d).days
        if best is None or delta < best:
            best = delta
    return best


def _nb_hot_avenants(signals: list[dict]) -> int:
    return sum(1 for s in signals if s["kind"] == "decp_avenant" and (s.get("heat") or 0) >= HOT_HEAT)


def _has_recent_xxl_marche(signals: list[dict], today: date) -> bool:
    for s in signals:
        if s["kind"] != "decp_contract":
            continue
        amt = s.get("amount_eur") or 0
        if amt < BIG_MARCHE:
            continue
        d = _parse_date(s.get("occurred_on"))
        if d and (today - d) < RECENT_12M:
            return True
    return False


def _biggest_recent_xxl(signals: list[dict], today: date) -> float:
    best = 0.0
    for s in signals:
        if s["kind"] != "decp_contract":
            continue
        amt = s.get("amount_eur") or 0
        if amt < BIG_MARCHE:
            continue
        d = _parse_date(s.get("occurred_on"))
        if d and (today - d) < RECENT_12M:
            if amt > best:
                best = amt
    return best


def composite_score(signals: list[dict], today: date | None = None) -> tuple[int, dict]:
    """Return (score_0_100, components_dict)."""
    today = today or date.today()
    surcout = _surcout_cumule(signals)
    recency_days = _best_recency_days(signals, today)
    nb_hot = _nb_hot_avenants(signals)
    recent_xxl_amt = _biggest_recent_xxl(signals, today)

    # Component scores
    # surcout : 1 pt per 1 M€ of cumulated overrun, capped at CAP_SURCOUT
    pts_surcout = min(CAP_SURCOUT, int(surcout / 1_000_000))
    # recency : full if <6 mois, half if <12 mois, none older
    if recency_days is None:
        pts_recency = 0
    elif recency_days <= RECENT_6M.days:
        pts_recency = CAP_RECENCY
    elif recency_days <= RECENT_12M.days:
        pts_recency = CAP_RECENCY // 2
    else:
        pts_recency = 0
    # repetition : 5 pts per hot avenant, capped
    pts_repetition = min(CAP_REPETITION, 5 * nb_hot)
    # recent xxl : capped at CAP_RECENT_XXL (full score if any >=50M€ recent)
    if recent_xxl_amt >= 200_000_000:
        pts_recent_xxl = CAP_RECENT_XXL
    elif recent_xxl_amt >= 100_000_000:
        pts_recent_xxl = int(CAP_RECENT_XXL * 2 / 3)
    elif recent_xxl_amt >= 50_000_000:
        pts_recent_xxl = CAP_RECENT_XXL // 2
    else:
        pts_recent_xxl = 0

    total = pts_surcout + pts_recency + pts_repetition + pts_recent_xxl
    components = {
        "surcout_cum_eur": surcout,
        "surcout_pts": pts_surcout,
        "recency_days": recency_days,
        "recency_pts": pts_recency,
        "nb_hot_avenants": nb_hot,
        "repetition_pts": pts_repetition,
        "recent_xxl_amt_eur": recent_xxl_amt,
        "recent_xxl_pts": pts_recent_xxl,
        "total": total,
    }
    return min(100, total), components


def run(reference_date: date | None = None) -> dict:
    """Recompute hot_score + hot_components for every company."""
    init_db()
    today = reference_date or date.today()
    stats = {"companies": 0, "updated": 0}
    with SessionLocal() as session:
        companies = session.scalars(select(Company)).all()
        for i, c in enumerate(companies, start=1):
            stats["companies"] += 1
            sigs = session.execute(
                select(
                    CompanySignal.kind,
                    CompanySignal.amount_eur,
                    CompanySignal.heat,
                    CompanySignal.occurred_on,
                ).where(CompanySignal.company_id == c.id)
            ).all()
            sig_dicts = [
                {"kind": r[0], "amount_eur": r[1], "heat": r[2], "occurred_on": r[3]}
                for r in sigs
            ]
            score, comps = composite_score(sig_dicts, today)
            if c.hot_score != score or (c.commercial_angle or "").startswith("{") is False:
                c.hot_score = score
                # Stash the components in commercial_angle as a JSON blob
                # (we're not using the LLM angle for now so this field is free)
                c.commercial_angle = json.dumps(comps, default=str)
                stats["updated"] += 1
            if i % 500 == 0:
                session.commit()
                logger.info(f"  [{i}/{len(companies)}] updated={stats['updated']}")
        session.commit()
    logger.info(f"Hot score recomputed. stats={stats}")
    return stats


if __name__ == "__main__":
    print(run())
