"""Load the curated Africa projects seed into the database."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from app.database import AfricaProject, SessionLocal, init_db
from app.logging_setup import setup as _setup_logging

_setup_logging()
from loguru import logger  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
SEED_DIR = ROOT / "data" / "seeds"
ACTORS_SEED = SEED_DIR / "africa_actors.json"


def run() -> dict:
    init_db()
    stats = {"created": 0, "updated": 0, "files_loaded": 0, "actors_attached": 0}
    files = sorted(SEED_DIR.glob("africa_projects*.json"))

    # Pre-load actors mapping (project name → list[dict]) from all actor seeds
    actors_map: dict = {}
    for actors_file in sorted(SEED_DIR.glob("africa_actors*.json")):
        actors_map.update(json.loads(actors_file.read_text(encoding="utf-8")))

    with SessionLocal() as session:
        for seed_file in files:
            data = json.loads(seed_file.read_text(encoding="utf-8"))
            stats["files_loaded"] += 1
            for row in data:
                existing = session.scalar(
                    select(AfricaProject).where(AfricaProject.name == row["name"])
                )
                if existing:
                    for k, v in row.items():
                        setattr(existing, k, v)
                    stats["updated"] += 1
                    obj = existing
                else:
                    obj = AfricaProject(**row)
                    session.add(obj)
                    stats["created"] += 1
                # Attach actors JSON if a structured seed exists
                if row["name"] in actors_map:
                    obj.actors_json = json.dumps(actors_map[row["name"]], ensure_ascii=False)
                    stats["actors_attached"] += 1
        session.commit()
    logger.info(f"Africa projects loaded. stats={stats}")
    return stats


if __name__ == "__main__":
    print(run())
