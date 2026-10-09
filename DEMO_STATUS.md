# État de la démo — paris-arbitration-scraper

Date : 2026-10-06

## 🎯 Pour la démo

**Dashboard live** : http://localhost:8502
*(si Streamlit redémarré : `.venv/bin/streamlit run app/ui/streamlit_app.py --server.port 8502` depuis la racine du projet)*

## Deux onglets prêts

### 🤝 Onglet 1 — Partenaires & acteurs du secteur
**3 330 praticiens arbitrage** cartographiés (ASA Profiles 997 + PAW 2163 + Swiss 122 + AEC 250, dédupliqués).
- 1 616 emails directs (997 ASA vérifiés + 619 guessed MX-OK)
- 197 firm moves détectés
- 187 praticiens multi-circuits (pan-européens)
- Rôle : apporteurs d'affaires potentiels, référents, networking

### 🎯 Onglet 2 — Clients potentiels (nouveau)
**263 sociétés qualifiées** mining / oil & gas / construction / énergie.
- 182 ICSID cases attachés
- 10 marchés publics DECP matchés
- 39 avec exposure Afrique
- 263 avec angle commercial auto-généré (rule-based)
- Cards branded noir/rouge/gris, signaux visuels empilés

## Top clients à montrer en démo

1. **ArcelorMittal S.A.** — Energy · 11 signaux (ICSID vs Italie + 7 marchés publics DECP matériaux métallerie SIGP + angle commercial)
2. **Klesch Group Holdings** — Oil & Gas · 3 ICSID vs UE / Allemagne (gas / mining)
3. **China Railway 18th Bureau** — Construction · ICSID vs État africain
4. **Orano Mining SAS** — Oil & Gas 🌍 Afrique · uranium → nationalisations récentes
5. **PanAfrican Energy Tanzania** — Oil & Gas 🌍 Afrique

## Livrables XLSX (data/exports/)

- `clients_potentiels.xlsx` — **263 sociétés** avec ICSID, DECP, angle commercial
- `speakers_all.xlsx` — **3 330 praticiens** avec emails + specializations ASA
- `priority_targets.xlsx` — sous-ensemble top commodities
- `firms_all.xlsx` — 2 546 cabinets
- `attendance_signals.xlsx` — placeholder (Phase 4 OSINT pas lancée)

## Fait vs À faire

**Fait** ✅
- Branding charte noir/rouge/gris + typo Crimson Pro + Inter
- Onglet 1 "Partenaires" avec filtres + emails ASA + guesses MX-validés
- Onglet 2 "Clients potentiels" avec cards branded, signaux ICSID + DECP, angle commercial
- Pipeline ICSID API → sector filter → company dedup → signal attachment
- Pipeline DECP SIREN resolver → contract matching
- Rule-based commercial angle generator (fallback LLM)

**Pas fait / limites** ⚠️
- **ICSID scrape incomplet** : 182/1159 cas scrapés (le site a été lent/stuck). Reprise possible : `.venv/bin/python scripts/scrape_icsid.py` (resume automatique).
- **CCJA / OHADA** : pas d'API publique accessible, skippé pour cette démo. Alternative : accès manuel via le portail juridique.
- **Pappers enrichment** : clef API en rupture de crédits (confirmé par test). Si tu top up, lance `.venv/bin/python scripts/enrich_pappers.py` → ajoute CA, employés, dirigeants.
- **LLM angles upgrade** : rule-based en place. Si tu me donnes une clef Anthropic (`ANTHROPIC_API_KEY=...` dans .env) → lance `.venv/bin/python scripts/generate_angles.py` pour upgrade qualité angles.
- **Press RSS mining/energy** : non lancé (ROI faible pour la démo).
- **2 individus dans le top 10** (Argüello x2) : vrais claimants ICSID du Nicaragua, techniquement légitimes mais à filtrer si tu veux uniquement des sociétés.

## Scripts utiles

```bash
cd /Users/bertantoine/paris-arbitration-scraper
.venv/bin/streamlit run app/ui/streamlit_app.py --server.port 8502
.venv/bin/python scripts/scrape_icsid.py            # reprise ICSID
.venv/bin/python scripts/scrape_decp.py             # match DECP
.venv/bin/python scripts/finalize_demo.py           # pipeline complète
.venv/bin/python -c "from app.exports.companies_xlsx import export_companies_xlsx; export_companies_xlsx()"
```
