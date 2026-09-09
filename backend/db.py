"""SQLite 접근 계층.

SPEC.md 4번(데이터 모델), 6번(최근 피드백 3개), 10번 체크리스트를 따른다.
- 타임스탬프는 SQLite datetime('now') 기본값에 의존하지 않고, Python에서
  datetime.utcnow().isoformat()으로 만든 'T' 구분자 ISO 8601 문자열을 INSERT에 직접 넣는다.
- feasible 은 0/1 로 저장하고, 응답 변환은 호출부(app.py)에서 bool()로 처리한다.
"""

import os
import sqlite3
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", "fridge.db")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now_iso():
    # 'T' 구분자 ISO 8601 문자열 (SPEC.md 4번 타임스탬프 규칙)
    return datetime.utcnow().isoformat()


def init_db():
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS fridge_items (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                name     TEXT NOT NULL,
                amount   TEXT NOT NULL,
                added_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS recipes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_name TEXT,
                servings    INTEGER,
                feasible    INTEGER,
                note        TEXT,
                steps       TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS recipe_feedback (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id  INTEGER,
                rating     TEXT,
                comment    TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            """
        )
        conn.commit()

        # 기존 fridge.db 에는 아래 컬럼들이 없을 수 있으므로 마이그레이션.
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(fridge_items)")}
        if "is_main" not in existing_cols:
            conn.execute(
                "ALTER TABLE fridge_items ADD COLUMN is_main INTEGER NOT NULL DEFAULT 0"
            )
            conn.commit()
        if "category" not in existing_cols:
            conn.execute(
                "ALTER TABLE fridge_items ADD COLUMN category TEXT NOT NULL DEFAULT '기타'"
            )
            conn.commit()
        if "expiry_date" not in existing_cols:
            conn.execute("ALTER TABLE fridge_items ADD COLUMN expiry_date TEXT")
            conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# fridge_items
# ---------------------------------------------------------------------------

_ITEM_COLUMNS = "id, name, amount, added_at, is_main, category, expiry_date"


def _row_to_item(row) -> dict:
    item = dict(row)
    item["is_main"] = bool(item["is_main"])
    return item


def add_item(name: str, amount: str, category: str = "기타", expiry_date: str = None) -> dict:
    added_at = _now_iso()
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO fridge_items (name, amount, added_at, category, expiry_date) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, amount, added_at, category, expiry_date),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "name": name,
            "amount": amount,
            "added_at": added_at,
            "is_main": False,
            "category": category,
            "expiry_date": expiry_date,
        }
    finally:
        conn.close()


def list_items() -> list:
    conn = _connect()
    try:
        rows = conn.execute(
            f"SELECT {_ITEM_COLUMNS} FROM fridge_items ORDER BY added_at ASC"
        ).fetchall()
        return [_row_to_item(r) for r in rows]
    finally:
        conn.close()


def get_item(item_id: int):
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT {_ITEM_COLUMNS} FROM fridge_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        return _row_to_item(row) if row else None
    finally:
        conn.close()


def delete_item(item_id: int) -> bool:
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM fridge_items WHERE id = ?", (item_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def count_main_items() -> int:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM fridge_items WHERE is_main = 1"
        ).fetchone()
        return row["c"]
    finally:
        conn.close()


def set_item_main(item_id: int, is_main: bool):
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE fridge_items SET is_main = ? WHERE id = ?",
            (1 if is_main else 0, item_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            f"SELECT {_ITEM_COLUMNS} FROM fridge_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        return _row_to_item(row)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# recipes
# ---------------------------------------------------------------------------

def add_recipe(recipe_name: str, servings: int, feasible: bool, note: str, steps: str) -> int:
    created_at = _now_iso()
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO recipes (recipe_name, servings, feasible, note, steps, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (recipe_name, servings, 1 if feasible else 0, note, steps, created_at),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_recipe(recipe_id: int):
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, recipe_name, servings, feasible, note, steps, created_at "
            "FROM recipes WHERE id = ?",
            (recipe_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# recipe_feedback
# ---------------------------------------------------------------------------

def add_feedback(recipe_id: int, rating: str, comment):
    created_at = _now_iso()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO recipe_feedback (recipe_id, rating, comment, created_at) "
            "VALUES (?, ?, ?, ?)",
            (recipe_id, rating, comment, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def recent_feedback(limit: int = 3) -> list:
    """SPEC.md 6번 SQL 그대로: 최근 피드백 N개 (recipe_name 조인)."""
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT r.recipe_name AS recipe_name, f.rating AS rating, f.comment AS comment
            FROM recipe_feedback f
            JOIN recipes r ON f.recipe_id = r.id
            ORDER BY f.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
