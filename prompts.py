QUESTION_PROMPT = """You are a strict but helpful tutor. Based on the following study material, generate exactly ONE multiple-choice quiz question that tests real understanding, not just recall of a definition. Prefer questions that require reasoning over simple "what is X" questions.

Also assign this question a short topic label (2-4 words) representing the specific sub-topic it tests within the material, so questions on the same concept can be grouped together consistently. Use consistent topic naming - if you've used a topic name before for this material, reuse it exactly.

Create 4 answer options. Exactly one must be correct. The other 3 should be plausible but clearly wrong to someone who understands the material.
{avoid_clause}
Respond with ONLY valid JSON in this exact format, nothing else, no markdown code fences:
{{
  "topic": "short topic label",
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

Then on a new final line, write ONLY: QUALITY: followed by a number 0-5 representing how well this answer demonstrates understanding (0=completely wrong/guessed, 3=correct but seemed unsure, 5=correct and clearly confident/well understood). Base this on the answer choice alone since this is multiple choice - use 5 if correct, 1 if incorrect, unless context suggests otherwise.
"""

REEXPLAIN_PROMPT = """The student got this question wrong:

QUESTION: {question}
CORRECT ANSWER: {correct_answer}
STUDENT CHOSE: {student_choice}

Using ONLY the study material below, re-explain the underlying concept in a DIFFERENT way than a textbook definition would - use an analogy or a simpler framing. Keep it to 3-4 sentences.

STUDY MATERIAL:
{notes}
"""

SUMMARY_PROMPT = """A student just completed a study session. Here is their performance broken down by topic:

{topic_breakdown}

Write a short, encouraging 3-4 sentence summary highlighting which topics they've mastered and which need more review, and mention that weaker topics will be resurfaced sooner thanks to spaced repetition.
"""
