import os
import json
from groq import Groq
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

SYSTEM_PROMPT = """You are analyzing a student's incorrect solution to a data structures and algorithms problem. Your job is not to just say what's wrong with the code, but to diagnose the underlying THINKING gap that caused the mistake, the misconception or missed insight that led to this specific wrong approach.

Respond with ONLY a JSON object, no other text, in this exact shape:
{
  "category": one of ["off_by_one", "wrong_data_structure", "incomplete_edge_case_handling", "misunderstood_problem", "wrong_algorithmic_approach", "complexity_misjudgment", "state_management_error", "other"],
  "note": a 2-3 sentence explanation of the specific reasoning gap, written directly to the student, plain and specific rather than generic,
  "topic_tags": an array of 1-4 short lowercase topic strings relevant to this problem, e.g. ["sliding-window", "hash-map"]
}

Be specific to THIS code and THIS mistake. Avoid generic advice like "be more careful with edge cases" — name the exact edge case they missed and why their code's structure caused them to miss it."""

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _call_groq(user_message: str) -> str:
    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        max_tokens=500,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )
    return response.choices[0].message.content

def analyze_gap(problem_title: str, problem_statement: str, wrong_code: str, correct_code: str | None) -> dict:
    if correct_code:
        ground_truth_note = f"The student has since found the correct solution:\n{correct_code}\nUse this as ground truth."
    else:
        ground_truth_note = "Derive the correct approach yourself to compare against."

    user_message = f"""Problem: {problem_title}
Problem statement: {problem_statement}

Student's submitted code:
{wrong_code}

{ground_truth_note}

Analyze the gap."""

    raw_text = _call_groq(user_message)
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return {"category": "other", "note": "Analysis parsing failed, raw response logged.", "topic_tags": []}