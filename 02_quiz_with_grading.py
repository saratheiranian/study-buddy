import os
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

NOTES = """
Paste your real study material here. For example: a paragraph
explaining supervised vs unsupervised learning, or a section
from your AI coursework.
"""

QUESTION_PROMPT = """You are a strict but helpful tutor. Based on the following study material, generate exactly ONE quiz question that tests real understanding, not just recall of a definition. Prefer "why" or "explain" style questions over "what is X."

Return ONLY the question text, nothing else.

STUDY MATERIAL:
{notes}
"""

GRADING_PROMPT = """You are a strict but supportive tutor grading a student's answer.

STUDY MATERIAL (ground truth):
{notes}

QUESTION ASKED:
{question}

STUDENT'S ANSWER:
{answer}

Grade the answer on whether the REASONING is correct and complete, not just whether they used the right keywords.

Respond in EXACTLY this format:
VERDICT: CORRECT or INCORRECT or PARTIALLY_CORRECT
FEEDBACK: [2-3 sentences explaining what was right/wrong/missing, in a supportive tone]
"""

def generate_question(notes):
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{"role": "user", "content": QUESTION_PROMPT.format(notes=notes)}]
    )
    return message.content[0].text.strip()

def grade_answer(notes, question, answer):
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": GRADING_PROMPT.format(notes=notes, question=question, answer=answer)}]
    )
    return message.content[0].text.strip()

# Step 1: generate a question
question = generate_question(NOTES)
print("=== QUESTION ===")
print(question)

# Step 2: get the student's answer via terminal input
print("\nType your answer below and press Enter:")
student_answer = input("> ")

# Step 3: grade it
result = grade_answer(NOTES, question, student_answer)
print("\n=== GRADE ===")
print(result)
