"""SQLite persistence for Study Buddy: user accounts and SM-2 spaced repetition.

Schema (version 2):

    users ─< topics ─< cards ─< review_log

Every topic belongs to a user, so every card and review does too. All
functions that read or change study data take a user_id and only ever touch
that user's rows.

The schema version is stored in SQLite's built-in PRAGMA user_version, so an
existing single-user database (version 0/1) is upgraded in place the first
time the app starts (see _migrate_to_v2).
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.getenv("STUDY_BUDDY_DB", "study_buddy.db")
SCHEMA_VERSION = 2

# PBKDF2-HMAC-SHA256, the iteration count OWASP recommends. Each login costs a
# fraction of a second on purpose: it makes guessing passwords from a stolen
# database very slow. The count is stored with every hash, so it can be raised
# later without breaking existing accounts.
PASSWORD_ITERATIONS = 600_000
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
MIN_PASSWORD_LENGTH = 8


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite ignores FOREIGN KEY rules unless this is on
    return conn


# ---------------------------------------------------------------------------
# Schema and migrations
# ---------------------------------------------------------------------------

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,   -- "Sara" and "sara" are the same account
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER REFERENCES users (id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    source_notes TEXT,
    created_at   TEXT,
    UNIQUE (user_id, name)                 -- topic names are unique per user, not globally
);

CREATE TABLE IF NOT EXISTS cards (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id         INTEGER NOT NULL REFERENCES topics (id) ON DELETE CASCADE,
    question         TEXT NOT NULL,
    options          TEXT NOT NULL,         -- JSON: {"A": "...", "B": "...", ...}
    correct_answer   TEXT NOT NULL,
    -- SM-2 spaced repetition fields
    repetitions      INTEGER DEFAULT 0,
    ease_factor      REAL DEFAULT 2.5,
    interval_days    REAL DEFAULT 0,
    next_review_date TEXT,
    last_reviewed_at TEXT,
    created_at       TEXT
);

CREATE TABLE IF NOT EXISTS review_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id     INTEGER NOT NULL REFERENCES cards (id) ON DELETE CASCADE,
    was_correct INTEGER NOT NULL,
    quality     INTEGER NOT NULL CHECK (quality BETWEEN 0 AND 5),
    reviewed_at TEXT
);

CREATE INDEX IF NOT EXISTS topics_user_idx   ON topics (user_id);
CREATE INDEX IF NOT EXISTS cards_topic_idx   ON cards (topic_id);
CREATE INDEX IF NOT EXISTS cards_due_idx     ON cards (next_review_date);
CREATE INDEX IF NOT EXISTS review_card_idx   ON review_log (card_id);
"""


def init_db():
    """Create the schema, or upgrade an older database. Safe to call on every start."""
    conn = get_connection()
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(topics)")}
            if columns and "user_id" not in columns:
                _migrate_to_v2(conn)
            else:
                conn.executescript(SCHEMA_V2)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
    finally:
        conn.close()


def _migrate_to_v2(conn):
    """Upgrade a pre-accounts database without losing data.

    SQLite can't add a foreign key or change a UNIQUE constraint on an existing
    table, so each table is rebuilt: create the new version, copy the rows
    across, drop the old one, rename. It all runs in one transaction, so a
    failure halfway leaves the original database untouched.

    Existing topics get user_id = NULL; the first account created claims them
    (see create_user), so a single-user install keeps its history.
    """
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")  # must be off while tables are swapped
    try:
        conn.executescript(
            """
            BEGIN;
            CREATE TABLE users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL
            );
            ALTER TABLE topics     RENAME TO topics_old;
            ALTER TABLE cards      RENAME TO cards_old;
            ALTER TABLE review_log RENAME TO review_log_old;
            """
            + SCHEMA_V2
            + """
            INSERT INTO topics (id, user_id, name, source_notes, created_at)
                SELECT id, NULL, name, source_notes, created_at FROM topics_old;
            INSERT INTO cards SELECT id, topic_id, question, options, correct_answer, repetitions,
                ease_factor, interval_days, next_review_date, last_reviewed_at, created_at FROM cards_old;
            INSERT INTO review_log SELECT id, card_id, was_correct, quality, reviewed_at FROM review_log_old;
            DROP TABLE review_log_old;
            DROP TABLE cards_old;
            DROP TABLE topics_old;
            COMMIT;
            """
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    problems = conn.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise RuntimeError(f"migration left {len(problems)} broken references")


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

def hash_password(password, iterations=None):
    iterations = iterations or PASSWORD_ITERATIONS
    salt = secrets.token_bytes(16)  # unique per user: identical passwords get different hashes
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    # compare_digest takes the same time whether the first byte or the last one differs,
    # so response timing reveals nothing about the stored hash.
    return hmac.compare_digest(digest.hex(), digest_hex)


# A real hash to check against when the username doesn't exist, so a failed login
# takes the same time either way and can't reveal which usernames are registered.
_DUMMY_HASH = None


def create_user(username, password):
    """Create an account and return its id. Raises ValueError with a message
    that is safe to show the user."""
    username = (username or "").strip()
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError("Usernames are 3-30 characters: letters, numbers, dots, dashes and underscores.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Passwords need at least {MIN_PASSWORD_LENGTH} characters.")

    conn = get_connection()
    try:
        with conn:
            is_first_user = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            try:
                cur = conn.execute(
                    "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                    (username, hash_password(password), datetime.now().isoformat()),
                )
            except sqlite3.IntegrityError:
                raise ValueError("That username is taken.") from None
            user_id = cur.lastrowid
            if is_first_user:
                # Hand topics from before accounts existed to the first account.
                conn.execute("UPDATE topics SET user_id = ? WHERE user_id IS NULL", (user_id,))
        return user_id
    finally:
        conn.close()


def authenticate(username, password):
    """Return {"id", "username"} if the credentials are right, else None."""
    global _DUMMY_HASH
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?", ((username or "").strip(),)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        _DUMMY_HASH = _DUMMY_HASH or hash_password("not-a-real-password")
        verify_password(password or "", _DUMMY_HASH)  # same work as a real check
        return None
    if not verify_password(password or "", row["password_hash"]):
        return None
    return {"id": row["id"], "username": row["username"]}


def delete_user(user_id):
    """Delete an account and, through ON DELETE CASCADE, all of its study data."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Topics and cards
# ---------------------------------------------------------------------------

def get_or_create_topic(user_id, name, source_notes):
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                "INSERT INTO topics (user_id, name, source_notes, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (user_id, name) DO NOTHING",
                (user_id, name, source_notes, datetime.now().isoformat()),
            )
        return conn.execute(
            "SELECT id FROM topics WHERE user_id = ? AND name = ?", (user_id, name)
        ).fetchone()["id"]
    finally:
        conn.close()


def add_card(topic_id, question, options, correct_answer):
    conn = get_connection()
    try:
        now = datetime.now().isoformat()
        with conn:
            cur = conn.execute(
                """
                INSERT INTO cards (topic_id, question, options, correct_answer, next_review_date, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (topic_id, question, json.dumps(options), correct_answer, now, now),
            )
        return cur.lastrowid
    finally:
        conn.close()


def save_generated_card(user_id, card, notes):
    """Store a question produced by the app ({topic, question, options, correct_answer})."""
    topic_id = get_or_create_topic(user_id, card["topic"], notes)
    return add_card(topic_id, card["question"], card["options"], card["correct_answer"])


def _card_for_app(row):
    """Turn a database row into the dict shape the Streamlit app uses."""
    return {
        "card_id": row["id"],
        "topic": row["topic_name"],
        "question": row["question"],
        "options": json.loads(row["options"]),
        "correct_answer": row["correct_answer"],
        "notes": row["source_notes"],  # grading and re-explanations need the original notes
    }


def get_due_cards(user_id, limit=20):
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT cards.*, topics.name AS topic_name, topics.source_notes
            FROM cards
            JOIN topics ON cards.topic_id = topics.id
            WHERE topics.user_id = ? AND cards.next_review_date <= ?
            ORDER BY cards.next_review_date ASC
            LIMIT ?
            """,
            (user_id, datetime.now().isoformat(), limit),
        ).fetchall()
        return [_card_for_app(r) for r in rows]
    finally:
        conn.close()


def count_due_cards(user_id):
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT COUNT(*) FROM cards JOIN topics ON cards.topic_id = topics.id
            WHERE topics.user_id = ? AND cards.next_review_date <= ?
            """,
            (user_id, datetime.now().isoformat()),
        ).fetchone()[0]
    finally:
        conn.close()


def update_card_after_review(user_id, card_id, quality):
    """
    SM-2 algorithm. quality is 0-5:
    0-2 = wrong/hard, 3 = correct but hard, 4 = correct, 5 = correct and easy

    The card is looked up through its topic's owner, so a user can only ever
    update their own cards, even if a card id from another account is passed in.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT cards.* FROM cards JOIN topics ON cards.topic_id = topics.id
            WHERE cards.id = ? AND topics.user_id = ?
            """,
            (card_id, user_id),
        ).fetchone()
        if row is None:
            raise LookupError(f"card {card_id} not found for this user")
        card = dict(row)

        ease_factor = card["ease_factor"]
        repetitions = card["repetitions"]
        interval = card["interval_days"]

        if quality < 3:
            repetitions = 0
            interval = 1
        else:
            if repetitions == 0:
                interval = 1
            elif repetitions == 1:
                interval = 6
            else:
                interval = round(interval * ease_factor)
            repetitions += 1

        ease_factor = max(1.3, ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))

        now = datetime.now()
        next_review = (now + timedelta(days=interval)).isoformat()

        with conn:  # both writes succeed together or not at all
            conn.execute(
                """
                UPDATE cards
                SET repetitions = ?, ease_factor = ?, interval_days = ?,
                    next_review_date = ?, last_reviewed_at = ?
                WHERE id = ?
                """,
                (repetitions, ease_factor, interval, next_review, now.isoformat(), card_id),
            )
            conn.execute(
                "INSERT INTO review_log (card_id, was_correct, quality, reviewed_at) VALUES (?, ?, ?, ?)",
                (card_id, 1 if quality >= 3 else 0, quality, now.isoformat()),
            )
        return {"repetitions": repetitions, "ease_factor": ease_factor, "interval_days": interval}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Progress reports
# ---------------------------------------------------------------------------

def get_topic_mastery(user_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT topics.name,
                   COUNT(cards.id) AS total_cards,
                   AVG(cards.repetitions) AS avg_repetitions,
                   AVG(cards.ease_factor) AS avg_ease,
                   SUM(CASE WHEN cards.repetitions >= 3 THEN 1 ELSE 0 END) AS mastered_count
            FROM topics
            LEFT JOIN cards ON cards.topic_id = topics.id
            WHERE topics.user_id = ?
            GROUP BY topics.id
            ORDER BY topics.name
            """,
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_review_history(user_id, limit=50):
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT cards.question, topics.name AS topic_name, review_log.was_correct, review_log.reviewed_at
            FROM review_log
            JOIN cards ON review_log.card_id = cards.id
            JOIN topics ON cards.topic_id = topics.id
            WHERE topics.user_id = ?
            ORDER BY review_log.reviewed_at DESC, review_log.id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
