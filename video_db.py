"""
Tracks used Pixabay video IDs to avoid repetition
across clips and across different projects.
"""

import json
from pathlib import Path

DB_PATH = Path("used_videos.json")


def load_db() -> dict:
    if DB_PATH.exists():
        return json.loads(DB_PATH.read_text(encoding="utf-8"))
    return {"used_ids": []}


def save_db(db: dict):
    DB_PATH.write_text(
        json.dumps(db, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def is_used(video_id: int) -> bool:
    return video_id in load_db()["used_ids"]


def mark_used(video_id: int):
    db = load_db()
    if video_id not in db["used_ids"]:
        db["used_ids"].append(video_id)
        save_db(db)


def get_used_count() -> int:
    return len(load_db()["used_ids"])
