import streamlit as st
import os
import json
from dotenv import load_dotenv
from anthropic import Anthropic
from pypdf import PdfReader
import docx
import io

load_dotenv()

def get_api_key():
    try:
        return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        return os.getenv("ANTHROPIC_API_KEY")

client = Anthropic(api_key=get_api_key())

QUESTION_PROMPT = """You are a strict but helpful tutor. Based on the following study material, generate exactly ONE multiple-choice quiz question that tests real understanding, not just recall of a definition. Prefer questions that require reasoning ("why" or "what would happen if") over simple "what is X" questions.

Create 4 answer options. Exactly one must be correct. The other 3 should be plausible but clearly wrong to someone who understands the material - not silly or obviously fake.
{avoid_clause}
Respond with ONLY valid JSON in this exact format, nothing else, no markdown code fences:
{{
  "question": "the question text",
  "options": {{"A": "option text", "B": "option text", "C": "option text", "D": "option text"}},
  "correct_answer": "A"
}}

STUDY MATERIAL:
{notes}
"""

GRADING_PROMPT = """You are a strict but supportive tutor giving feedback on a multiple-choice answer.

STUDY MATERIAL (ground truth):
{notes}

QUESTION:
{question}

OPTIONS:
{options}

CORRECT ANSWER: {correct_answer}
STUDENT CHOSE: {student_choice}

Write a 2-3 sentence explanation. If the student got it right, briefly reinforce why that answer is correct. If they got it wrong, explain clearly why their choice was wrong and why the correct answer is right, in a supportive tone.
"""

REEXPLAIN_PROMPT = """The student got this question wrong:

QUESTION: {question}
CORRECT ANSWER: {correct_answer}
STUDENT CHOSE: {student_choice}

Using ONLY the study material below, re-explain the underlying concept in a DIFFERENT way than a textbook definition would - use an analogy or a simpler framing. Keep it to 3-4 sentences.

STUDY MATERIAL:
{notes}
"""

def extract_text_from_pdf(file_bytes):
    reader = PdfReader(io.BytesIO(file_bytes))
    text = ""
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text += page_text + "\n"
    return text.strip()

def extract_text_from_docx(file_bytes):
    doc = docx.Document(io.BytesIO(file_bytes))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

def extract_text_from_upload(uploaded_file):
    file_bytes = uploaded_file.read()
    name = uploaded_file.name.lower()
    if name.endswith(".pdf"):
        text = extract_text_from_pdf(file_bytes)
        if not text:
            return None, "This PDF appears to be scanned (no extractable text). Try a text-based PDF, or paste the notes manually."
        return text, None
    elif name.endswith(".docx"):
        return extract_text_from_docx(file_bytes), None
    elif name.endswith(".txt"):
        return file_bytes.decode("utf-8", errors="ignore"), None
    else:
        return None, "Unsupported file type. Please upload a PDF, DOCX, or TXT file."

def parse_json_response(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())

def generate_question(notes, avoid_topics=None):
    avoid_clause = ""
    if avoid_topics:
        avoid_clause = f"\nDo not repeat these already-asked questions: {avoid_topics}\n"
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": QUESTION_PROMPT.format(notes=notes, avoid_clause=avoid_clause)}]
    )
    return parse_json_response(message.content[0].text)

def get_feedback(notes, question, options, correct_answer, student_choice):
    options_text = "\n".join(f"{k}: {v}" for k, v in options.items())
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": GRADING_PROMPT.format(
            notes=notes, question=question, options=options_text,
            correct_answer=correct_answer, student_choice=student_choice
        )}]
    )
    return message.content[0].text.strip()

def reexplain(notes, question, correct_answer, student_choice):
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=250,
        messages=[{"role": "user", "content": REEXPLAIN_PROMPT.format(
            notes=notes, question=question, correct_answer=correct_answer, student_choice=student_choice
        )}]
    )
    return message.content[0].text.strip()

st.set_page_config(page_title="Study Buddy", page_icon="📚", layout="centered")
st.title("📚 Study Buddy")
st.caption("Upload your notes (or paste them), get quizzed, and get re-taught anything you miss.")

defaults = {
    "notes": "", "current_q": None, "asked_questions": [],
    "mode": "setup", "feedback_text": "", "was_correct": False, "reexplanation": "",
    "student_choice": None
}
for key, val in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = val

st.divider()
st.subheader("Your study material")

tab1, tab2 = st.tabs(["Upload a file", "Paste text"])

uploaded_text = None
with tab1:
    uploaded_file = st.file_uploader("Upload notes (PDF, DOCX, or TXT)", type=["pdf", "docx", "txt"])
    if uploaded_file is not None:
        extracted, error = extract_text_from_upload(uploaded_file)
        if error:
            st.error(error)
        else:
            uploaded_text = extracted
            st.success(f"Extracted {len(extracted)} characters from {uploaded_file.name}")
            with st.expander("Preview extracted text"):
                st.text(extracted[:2000])

with tab2:
    pasted_text = st.text_area("Paste or update your notes here", value=st.session_state.notes, height=200,
                                 placeholder="Paste lecture notes, a textbook chapter, or any material you want to be quizzed on...")

notes_to_use = uploaded_text if uploaded_text else pasted_text

col1, col2 = st.columns(2)
with col1:
    if st.button("Start / Update quiz with these notes", use_container_width=True):
        if not notes_to_use or not notes_to_use.strip():
            st.warning("Please upload a file or paste some notes first.")
        else:
            with st.spinner("Generating your first question..."):
                try:
                    st.session_state.notes = notes_to_use
                    st.session_state.asked_questions = []
                    st.session_state.current_q = generate_question(notes_to_use)
                    st.session_state.asked_questions.append(st.session_state.current_q["question"])
                    st.session_state.mode = "quiz"
                except Exception as e:
                    st.error(f"Something went wrong generating the question: {e}")
                    st.stop()
            st.rerun()

with col2:
    if st.button("Reset everything", use_container_width=True):
        for key in defaults:
            del st.session_state[key]
        st.rerun()

st.divider()

if st.session_state.mode == "quiz" and st.session_state.current_q:
    q = st.session_state.current_q
    st.subheader("Question")
    st.write(q["question"])

    choice = st.radio(
        "Choose an answer:",
        options=list(q["options"].keys()),
        format_func=lambda k: f"{k}. {q['options'][k]}",
        key="mc_choice",
        index=None
    )

    if st.button("Submit answer", use_container_width=True):
        if choice is None:
            st.warning("Please select an answer.")
        else:
            is_correct = (choice == q["correct_answer"])
            st.session_state.was_correct = is_correct
            st.session_state.student_choice = choice
            st.session_state.feedback_text = get_feedback(
                st.session_state.notes, q["question"], q["options"], q["correct_answer"], choice
            )
            if not is_correct:
                st.session_state.reexplanation = reexplain(
                    st.session_state.notes, q["question"], q["correct_answer"], choice
                )
                st.session_state.mode = "reteach"
            else:
                st.session_state.mode = "graded"
            st.rerun()

if st.session_state.mode == "graded":
    q = st.session_state.current_q
    st.success(f"Correct! You chose {st.session_state.student_choice}: {q['options'][st.session_state.student_choice]}")
    st.write(st.session_state.feedback_text)
    if st.button("Next question", use_container_width=True):
        st.session_state.current_q = generate_question(st.session_state.notes, st.session_state.asked_questions)
        st.session_state.asked_questions.append(st.session_state.current_q["question"])
        st.session_state.mode = "quiz"
        st.rerun()

if st.session_state.mode == "reteach":
    q = st.session_state.current_q
    st.warning(f"Not quite. You chose {st.session_state.student_choice}: {q['options'][st.session_state.student_choice]}")
    st.write(f"Correct answer was {q['correct_answer']}: {q['options'][q['correct_answer']]}")
    st.write(st.session_state.feedback_text)
    st.info("Let's look at this differently:\n\n" + st.session_state.reexplanation)

    if st.button("Try a follow-up question on this same topic", use_container_width=True):
        followup_notes = st.session_state.notes + "\n\nFocus specifically on the concept just re-explained: " + st.session_state.reexplanation
        st.session_state.current_q = generate_question(followup_notes)
        st.session_state.asked_questions.append(st.session_state.current_q["question"])
        st.session_state.mode = "quiz"
        st.rerun()

st.divider()
st.caption("Built by Sara Ghassemi - Powered by Claude Anthropic")
