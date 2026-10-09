# paris-arbitration-scraper

Qualified actor database around **Paris Arbitration Week** (PAW), built to
support the business development of a Paris-based international arbitrator
specialized in **commodities arbitration** (raw materials, oil, mining,
coal) + commercial consulting + commercial litigation.

## What it does

- Scrapes the full history of PAW 2023 → 2026 from `parisarbitrationweek.com`
  - 547 events (4 editions)
  - 555 partner firms (law firms, institutions, chambers, experts, academics)
  - All speakers (host / guest / moderator / panelist) with firm affiliation
- Qualifies each speaker and each firm against a closed arbitration taxonomy
  - Firm type · practice area · geographic focus · role · seniority
  - **Commodity-relevance flag + score** — the client's priority axis
  - **Firm-move detection** — same person spotted at different firms across years
- Collects **attendance signals** via OSINT (LinkedIn hashtags, press, firm news)
- Exports 4 ready-to-use XLSX files for direct outreach

## Project layout

```
app/
  scrapers/       — sitemap / event / partner HTTP scrapers
  database/       — SQLAlchemy models + session
  processors/     — taxonomy, qualification, firm-move detection
  attendance/     — OSINT signals (LinkedIn / press / firm news)
  exports/        — XLSX generators
scripts/          — top-level entry points (run_scrape, run_enrich, run_export)
data/
  raw/            — cached HTML (regenerable, gitignored)
  exports/        — XLSX deliverables
templates/        — reserved for future email / report templates
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add ANTHROPIC_API_KEY if you want LLM qualification
```

## Running

```bash
# 1. Scrape the public catalog (sitemap → events + firms)
python -m scripts.scrape_catalog

# 2. Qualify speakers + firms (rule-based, optional LLM refine)
python -m scripts.enrich_taxonomy

# 3. Collect OSINT attendance signals
python -m scripts.scrape_osint

# 4. Export the 4 XLSX deliverables
python -m scripts.export_xlsx
```

## Status

- ✅ Project structure
- ⏳ Scrapers (events + firms)
- ⏳ Qualification taxonomy
- ⏳ OSINT attendance signals
- ⏳ XLSX exports
