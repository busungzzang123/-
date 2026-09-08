"""SQLite 접근 계층.

- 스키마 생성/시드, fridge_items / recipes / feedback_events / user_preference CRUD
- 개인화(v1.5): user_preference 는 항상 id=1 단일 행. 유저 개념 없음(공유).
- 타임스탬프는 여기서 Python ISO 8601(`T` 구분자, 초 단위)로 만들어 INSERT 한다.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.getenv("DB_PATH", "fridge.db")

# 장르 8축 (제안서 4-4). JSON 키는 snake_case 영문, 표시용 한글 라벨은 프롬프트/프론트에서 사용.
GENRE_KEYS = [
    "soup", "one_plate", "stir_fry_grill", "braised_steamed",
    "noodle", "side_dish", "fried_jeon", "light_raw",
]
GENRE_LABELS = {
    "soup": "국물요리", "one_plate": "한접시요리", "stir_fry_grill": "볶음·구이",
    "braised_steamed": "조림·찜", "noodle": "면요리", "side_dish": "반찬·무침",
    "fried_jeon": "튀김·전", "light_raw": "가벼운·생식",
}

# 갱신 상수 (제안서 9-3)
LR = 0.2
K = 0.4
SPREAD = 2.0

_NEUTRAL_WEIGHTS = {k: 0.0 for k in GENRE_KEYS}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS fridge_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                amount TEXT NOT NULL,
                added_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_name TEXT NOT NULL,
                servings INTEGER NOT NULL,
                feasible INTEGER NOT NULL,
                note TEXT NOT NULL,
                steps TEXT NOT NULL,
                genre_dist TEXT NOT NULL,
                difficulty REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id INTEGER NOT NULL,
                verdict TEXT NOT NULL,
                reason TEXT,
                comment TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_preference (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                genre_weights TEXT NOT NULL,
                skill_level REAL NOT NULL,
                feedback_count INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        row = c.execute("SELECT 1 FROM user_preference WHERE id = 1").fetchone()
        if row is None:
            c.execute(
                "INSERT INTO user_preference (id, genre_weights, skill_level, feedback_count, updated_at) "
                "VALUES (1, ?, ?, ?, ?)",
                (json.dumps(_NEUTRAL_WEIGHTS), 2.0, 0, _now_iso()),
            )


# ---------- fridge_items ----------

def add_item(name: str, amount: str) -> dict:
    ts = _now_iso()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO fridge_items (name, amount, added_at) VALUES (?, ?, ?)",
            (name, amount, ts),
        )
        return {"id": cur.lastrowid, "name": name, "amount": amount, "added_at": ts}


def list_items() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, name, amount, added_at FROM fridge_items ORDER BY added_at ASC, id ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_item(item_id: int) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM fridge_items WHERE id = ?", (item_id,))
        return cur.rowcount > 0


# ---------- recipes ----------

def save_recipe(recipe: dict) -> int:
    ts = _now_iso()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO recipes (recipe_name, servings, feasible, note, steps, genre_dist, difficulty, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                recipe["recipe_name"], int(recipe["servings"]), 1 if recipe["feasible"] else 0,
                recipe["note"], recipe["steps"], json.dumps(recipe["genre_dist"]),
                float(recipe["difficulty"]), ts,
            ),
        )
        return cur.lastrowid


def get_recipe(recipe_id: int) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        return dict(r) if r else None


# ---------- feedback + preference ----------

def get_preference() -> dict:
    with _conn() as c:
        r = c.execute("SELECT genre_weights, skill_level, feedback_count FROM user_preference WHERE id = 1").fetchone()
    weights = {**_NEUTRAL_WEIGHTS, **json.loads(r["genre_weights"])}
    return {
        "genre_weights": {k: weights[k] for k in GENRE_KEYS},
        "skill_level": r["skill_level"],
        "feedback_count": r["feedback_count"],
    }


def recent_feedback(limit: int = 3) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT r.recipe_name, f.verdict, f.reason, f.comment "
            "FROM feedback_events f JOIN recipes r ON f.recipe_id = r.id "
            "ORDER BY f.created_at DESC, f.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def _update_skill(skill: float, difficulty: float, verdict: str, reason: str | None) -> float:
    if verdict == "좋았음":
        w = 1.0
    elif verdict == "별로였음" and reason == "난이도":
        w = 0.0
    else:
        return skill
    e = 1.0 / (1.0 + 10.0 ** ((difficulty - skill) / SPREAD))
    return _clamp(skill + K * (w - e), 1.0, 5.0)


def add_feedback(recipe_id: int, verdict: str, reason: str | None, comment: str | None,
                 genre_dist: dict, difficulty: float) -> dict:
    """feedback_events INSERT + user_preference 갱신을 한 트랜잭션으로. 갱신된 preference 반환."""
    ts = _now_iso()
    with _conn() as c:
        c.execute(
            "INSERT INTO feedback_events (recipe_id, verdict, reason, comment, created_at) VALUES (?, ?, ?, ?, ?)",
            (recipe_id, verdict, reason, comment, ts),
        )
        r = c.execute(
            "SELECT genre_weights, skill_level, feedback_count FROM user_preference WHERE id = 1"
        ).fetchone()
        weights = {**_NEUTRAL_WEIGHTS, **json.loads(r["genre_weights"])}
        skill = r["skill_level"]

        sign = 1.0 if verdict == "좋았음" else -1.0
        for g in GENRE_KEYS:
            new_w = round(_clamp(weights[g] + LR * sign * float(genre_dist.get(g, 0.0)), -1.0, 1.0), 4)
            weights[g] = 0.0 if new_w == 0 else new_w
        skill = round(_update_skill(skill, float(difficulty), verdict, reason), 4)
        count = r["feedback_count"] + 1

        c.execute(
            "UPDATE user_preference SET genre_weights = ?, skill_level = ?, feedback_count = ?, updated_at = ? WHERE id = 1",
            (json.dumps(weights), skill, count, ts),
        )
    return {
        "genre_weights": {k: weights[k] for k in GENRE_KEYS},
        "skill_level": skill,
        "feedback_count": count,
    }
