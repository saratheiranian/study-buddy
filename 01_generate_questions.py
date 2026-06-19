import os
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

NOTES = """
Paste your actual lecture notes or textbook chapter here for testing.
For example: a paragraph about photosynthesis, or your AI module notes.
"""

QUESTION_PROMPT = """You are a strict but helpful tutor. Based on the following study material, generate exactly 3 quiz questions that test real understanding, not just recall of definitions. Mix in some "why" or "explain" questions, not just "what is X."

Return your response as a numbered list, one question per line, nothing else.

STUDY MATERIAL:
{notes}
"""

def generate_questions(notes):
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": QUESTION_PROMPT.format(notes=notes)
        }]
    )
    return message.content[0].text.strip()

questions = generate_questions(NOTES)
print("=== GENERATED QUESTIONS ===\n")
print(questions)
