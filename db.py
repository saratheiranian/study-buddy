import sqlite3
from datetime import datetime, timedelta
import json

DB_PATH = "study_buddy.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            source_notes TEXT,
            created_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            options TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            -- SM-2 spaced repetition fields
            repetitions INTEGER DEFAULT 0,
            ease_factor REAL DEFAULT 2.5,
            interval_days REAL DEFAULT 0,
            next_review_date TEXT,
            last_reviewed_at TEXT,
            created_at TEXT,
            FOREIGN KEY (topic_id) REFERENCES topics (id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS review_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER NOT NULL,
            was_correct INTEGER NOT NULL,
            quality INTEGER NOT NULL,
            reviewed_at TEXT,
            FOREIGN KEY (card_id) REFERENCES cards (id)
        )
    """)

    conn.commit()
    conn.close()

def get_or_create_topic(name, source_notes):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM topics WHERE name = ?", (name,))
    row = c.fetchone()
    if row:
        topic_id = row["id"]
    else:
        c.execute(
            "INSERT INTO topics (name, source_notes, created_at) VALUES (?, ?, ?)",
            (name, source_notes, datetime.now().isoformat())
        )
        conn.commit()
        topic_id = c.lastrowid
    conn.close()
    return topic_id

def add_card(topic_id, question, options, correct_answer):
    conn = get_connection()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("""
        INSERT INTO cards (topic_id, question, options, correct_answer, next_review_date, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (topic_id, question, json.dumps(options), correct_answer, now, now))
    conn.commit()
    card_id = c.lastrowid
    conn.close()
    return card_id

def get_due_cards(limit=20):
    conn = get_connection()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("""
        SELECT cards.*, topics.name as topic_name
        FROM cards
        JOIN topics ON cards.topic_id = topics.id
        WHERE cards.next_review_date <= ?
        ORDER BY cards.next_review_date ASC
        LIMIT ?
    """, (now, limit))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def update_card_after_review(card_id, quality):
    """
    SM-2 algorithm. quality is 0-5:
    0-2 = wrong/hard, 3 = correct but hard, 4 = correct, 5 = correct and easy
    """
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM cards WHERE id = ?", (card_id,))
    card = dict(c.fetchone())

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

    next_review = (datetime.now() + timedelta(days=interval)).isoformat()

    c.execute("""
        UPDATE cards
        SET repetitions = ?, ease_factor = ?, interval_days = ?,
            next_review_date = ?, last_reviewed_at = ?
        WHERE id = ?
    """, (repetitions, ease_factor, interval, next_review, datetime.now().isoformat(), card_id))

    c.execute("""
        INSERT INTO review_log (card_id, was_correct, quality, reviewed_at)
        VALUES (?, ?, ?, ?)
    """, (card_id, 1 if quality >= 3 else 0, quality, datetime.now().isoformat()))

    conn.commit()
    conn.close()

def get_topic_mastery():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT topics.name,
               COUNT(cards.id) as total_cards,
               AVG(cards.repetitions) as avg_repetitions,
               AVG(cards.ease_factor) as avg_ease,
               SUM(CASE WHEN cards.repetitions >= 3 THEN 1 ELSE 0 END) as mastered_count
        FROM topics
        LEFT JOIN cards ON cards.topic_id = topics.id
        GROUP BY topics.id
    """)
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_review_history():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT cards.question, topics.name as topic_name, review_log.was_correct, review_log.reviewed_at
        FROM review_log
        JOIN cards ON review_log.card_id = cards.id
        JOIN topics ON cards.topic_id = topics.id
        ORDER BY review_log.reviewed_at DESC
        LIMIT 50
    """)
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]
