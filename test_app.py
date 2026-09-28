"""End-to-end tests of the Streamlit app with Streamlit's AppTest.

The Claude API is replaced by a fake that returns fixed responses, so these
tests need no API key, cost nothing and always give the same result.
Run with:  pytest -q
"""

import json
from types import SimpleNamespace

import pytest
from anthropic.resources.messages import Messages
from streamlit.testing.v1 import AppTest

import db


def fake_create(self, *, messages, **kwargs):
    prompt = messages[0]["content"]
    if "Respond with ONLY valid JSON" in prompt:
        fake_create.count += 1
        text = json.dumps({
            "topic": "Light reactions",
            "question": f"Question {fake_create.count}: why do plants need sunlight?",
            "options": {"A": "Energy", "B": "Colour", "C": "Smell", "D": "Weight"},
            "correct_answer": "A",
        })
    else:
        text = "Feedback from the tutor."
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(Messages, "create", fake_create)
    fake_create.count = 0
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setattr(db, "PASSWORD_ITERATIONS", 1_000)
    db.init_db()


def start():
    return AppTest.from_file("app.py", default_timeout=30).run()


def click(at, label):
    next(b for b in at.button if b.label.startswith(label)).click()
    return at.run()


def sign_up(at, username="sara", password="correct horse"):
    at.text_input(key="signup_username").input(username)
    at.text_input(key="signup_password").input(password)
    at.text_input(key="signup_confirm").input(password)
    return click(at, "Create account")


def log_in(at, username, password):
    at.text_input(key="login_username").input(username)
    at.text_input(key="login_password").input(password)
    return click(at, "Log in")


def signed_in_as(at):
    return next((m.value for m in at.sidebar.markdown if m.value.startswith("Signed in as")), None)


def test_login_page_is_shown_first():
    at = start()
    assert not at.exception
    assert signed_in_as(at) is None
    assert not any(b.label == "Generate quiz" for b in at.button)  # the app is locked


def test_sign_up_generate_quiz_and_answer_saves_progress():
    at = sign_up(start())
    assert signed_in_as(at) == "Signed in as **sara**"

    at.text_area[0].input("Plants use sunlight to make glucose.")
    at.number_input[0].set_value(2)
    at = click(at, "Generate quiz")
    assert not at.exception

    user = db.authenticate("sara", "correct horse")
    assert db.count_due_cards(user["id"]) == 2  # both questions saved to the account

    at.radio(key="mc_choice_0").set_value("A")
    at = click(at, "Submit answer")
    assert not at.exception
    assert any("Correct!" in s.value for s in at.success)
    assert db.count_due_cards(user["id"]) == 1  # the answered card is now scheduled for later
    assert len(db.get_review_history(user["id"])) == 1
    assert any(d for d in at.dataframe)  # progress table is shown


def test_wrong_password_is_rejected():
    db.create_user("sara", "correct horse")
    at = log_in(start(), "sara", "wrong horse")
    assert any("Wrong username or password" in e.value for e in at.error)
    assert signed_in_as(at) is None


def test_mismatched_passwords_on_sign_up():
    at = start()
    at.text_input(key="signup_username").input("sara")
    at.text_input(key="signup_password").input("correct horse")
    at.text_input(key="signup_confirm").input("different horse")
    at = click(at, "Create account")
    assert any("don't match" in e.value for e in at.error)


def test_users_only_see_their_own_progress():
    alex = db.create_user("alex", "alex password")
    db.save_generated_card(alex, {"topic": "Alex's topic", "question": "Q", "options": {"A": "x"},
                                  "correct_answer": "A"}, "notes")
    db.create_user("sara", "correct horse")
    at = log_in(start(), "sara", "correct horse")
    assert any(b.label == "Review due cards (0)" for b in at.button)
    assert not at.dataframe  # sara has no topics yet; alex's are invisible to her


def test_review_due_cards_from_an_earlier_session():
    sara = db.create_user("sara", "correct horse")
    db.save_generated_card(sara, {"topic": "Logic", "question": "Old question", "options": {"A": "x", "B": "y"},
                                  "correct_answer": "B"}, "saved notes")
    at = log_in(start(), "sara", "correct horse")
    at = click(at, "Review due cards (1)")
    assert any("Old question" in m.value for m in at.markdown)
    at.radio(key="mc_choice_0").set_value("A")  # wrong on purpose
    at = click(at, "Submit answer")
    assert not at.exception
    assert any("Not quite" in w.value for w in at.warning)
    assert db.count_due_cards(sara) == 0  # rescheduled for tomorrow by SM-2


def test_log_out():
    at = sign_up(start())
    at = click(at, "Log out")
    assert signed_in_as(at) is None
