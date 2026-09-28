"""Tests for the database layer. Run with:  pytest -q"""

import sqlite3
from datetime import datetime, timedelta

import pytest

import db

OPTIONS = {"A": "Energy", "B": "Colour", "C": "Smell", "D": "Weight"}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Every test gets its own empty database file."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(db, "PASSWORD_ITERATIONS", 1_000)  # fast hashing for tests only
    db.init_db()


@pytest.fixture
def sara():
    return db.create_user("sara", "correct horse")


def make_card(user_id, topic="Photosynthesis", question="Why do plants need sunlight?"):
    return db.save_generated_card(
        user_id, {"topic": topic, "question": question, "options": OPTIONS, "correct_answer": "A"}, "notes"
    )


# --- Accounts -------------------------------------------------------------------

def test_login_with_correct_and_wrong_password(sara):
    assert db.authenticate("sara", "correct horse") == {"id": sara, "username": "sara"}
    assert db.authenticate("sara", "wrong horse") is None
    assert db.authenticate("nobody", "correct horse") is None


def test_usernames_are_case_insensitive(sara):
    assert db.authenticate("SARA", "correct horse")["id"] == sara
    with pytest.raises(ValueError, match="taken"):
        db.create_user("Sara", "another password")


def test_passwords_are_never_stored_in_plain_text(sara):
    conn = db.get_connection()
    stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    assert "correct horse" not in stored
    assert stored.startswith("pbkdf2_sha256$")


def test_same_password_gives_different_hashes():
    assert db.hash_password("same password") != db.hash_password("same password")  # salted


@pytest.mark.parametrize(
    "username,password",
    [("ab", "long enough"), ("has space", "long enough"), ("x" * 31, "long enough"), ("valid_name", "short")],
)
def test_invalid_signups_are_rejected(username, password):
    with pytest.raises(ValueError):
        db.create_user(username, password)


def test_sql_injection_in_login_is_harmless(sara):
    assert db.authenticate("sara' OR '1'='1", "anything") is None
    assert db.authenticate("sara", "correct horse") is not None  # table still intact


# --- Data isolation -----------------------------------------------------------

def test_users_only_see_their_own_cards(sara):
    alex = db.create_user("alex", "another password")
    make_card(sara, question="Sara's question")
    make_card(alex, question="Alex's question")
    assert [c["question"] for c in db.get_due_cards(sara)] == ["Sara's question"]
    assert [c["question"] for c in db.get_due_cards(alex)] == ["Alex's question"]


def test_same_topic_name_is_separate_per_user(sara):
    alex = db.create_user("alex", "another password")
    assert db.get_or_create_topic(sara, "Bayes", "") != db.get_or_create_topic(alex, "Bayes", "")
    assert db.get_or_create_topic(sara, "Bayes", "") == db.get_or_create_topic(sara, "Bayes", "")


def test_cannot_review_another_users_card(sara):
    alex = db.create_user("alex", "another password")
    card = make_card(sara)
    with pytest.raises(LookupError):
        db.update_card_after_review(alex, card, quality=5)
    assert db.get_review_history(sara) == []  # nothing was written


def test_deleting_an_account_deletes_its_data(sara):
    card = make_card(sara)
    db.update_card_after_review(sara, card, 4)
    db.delete_user(sara)
    conn = db.get_connection()
    for table in ("users", "topics", "cards", "review_log"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


# --- SM-2 scheduling ------------------------------------------------------------

def test_sm2_intervals_grow_on_correct_answers(sara):
    card = make_card(sara)
    intervals = [db.update_card_after_review(sara, card, 5)["interval_days"] for _ in range(4)]
    assert intervals[:2] == [1, 6]
    assert intervals[2] > 6 and intervals[3] > intervals[2]


def test_sm2_wrong_answer_resets_the_card(sara):
    card = make_card(sara)
    for _ in range(3):
        db.update_card_after_review(sara, card, 5)
    result = db.update_card_after_review(sara, card, 1)
    assert (result["repetitions"], result["interval_days"]) == (0, 1)
    assert result["ease_factor"] >= 1.3


def test_reviewed_card_is_no_longer_due(sara):
    card = make_card(sara)
    assert db.count_due_cards(sara) == 1
    db.update_card_after_review(sara, card, 4)
    assert db.count_due_cards(sara) == 0


def test_due_cards_come_back_in_the_app_format(sara):
    make_card(sara)
    card = db.get_due_cards(sara)[0]
    assert set(card) == {"card_id", "topic", "question", "options", "correct_answer", "notes"}
    assert card["options"] == OPTIONS and card["notes"] == "notes"


def test_mastery_and_history(sara):
    card = make_card(sara)
    for q in (5, 5, 5):
        db.update_card_after_review(sara, card, q)
    mastery = db.get_topic_mastery(sara)
    assert mastery[0]["name"] == "Photosynthesis" and mastery[0]["mastered_count"] == 1
    assert len(db.get_review_history(sara)) == 3


# --- Migration from the pre-accounts schema ------------------------------------

OLD_SCHEMA = """
CREATE TABLE topics (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL,
    source_notes TEXT, created_at TEXT);
CREATE TABLE cards (id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id INTEGER NOT NULL, question TEXT NOT NULL,
    options TEXT NOT NULL, correct_answer TEXT NOT NULL, repetitions INTEGER DEFAULT 0,
    ease_factor REAL DEFAULT 2.5, interval_days REAL DEFAULT 0, next_review_date TEXT,
    last_reviewed_at TEXT, created_at TEXT, FOREIGN KEY (topic_id) REFERENCES topics (id));
CREATE TABLE review_log (id INTEGER PRIMARY KEY AUTOINCREMENT, card_id INTEGER NOT NULL,
    was_correct INTEGER NOT NULL, quality INTEGER NOT NULL, reviewed_at TEXT,
    FOREIGN KEY (card_id) REFERENCES cards (id));
INSERT INTO topics VALUES (1, 'Logic', 'old notes', '2026-03-01');
INSERT INTO cards (id, topic_id, question, options, correct_answer, next_review_date)
    VALUES (1, 1, 'Old question', '{"A": "x", "B": "y"}', 'A', '2026-01-01');
INSERT INTO review_log VALUES (1, 1, 1, 4, '2026-03-02');
"""


def test_old_database_is_upgraded_without_losing_data(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(OLD_SCHEMA)
    old.close()
    monkeypatch.setattr(db, "DB_PATH", str(path))

    db.init_db()
    db.init_db()  # running again must be a no-op

    conn = db.get_connection()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 1

    # The first account claims the old data; the next one starts empty.
    first = db.create_user("sara", "correct horse")
    second = db.create_user("alex", "another password")
    assert [c["question"] for c in db.get_due_cards(first)] == ["Old question"]
    assert db.get_due_cards(second) == []
    assert len(db.get_review_history(first)) == 1
