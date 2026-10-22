
import json
import os
import re
from ollama import Client


# ============================================================
# OLLAMA CONFIGURATION
# ============================================================

OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")

if not OLLAMA_API_KEY:
    raise Exception("OLLAMA_API_KEY environment variable is not set")

client = Client(
    host="https://ollama.com",
    headers={
        "Authorization": f"Bearer {OLLAMA_API_KEY}"
    }
)

GENERATION_MODEL = "gpt-oss:20b"

MAX_GENERATION_ATTEMPTS = 3
MAX_EVALUATION_ATTEMPTS = 3
MAX_REPLACEMENT_ATTEMPTS = 5


# ============================================================
# DIFFICULTY HELPERS
# ============================================================

def normalize_difficulty(value):
    """
    Normalize difficulty values to:
    Easy / Medium / Hard
    """

    if value is None:
        return ""

    value = str(value).strip().lower()

    if value == "easy":
        return "Easy"

    if value == "medium":
        return "Medium"

    if value == "hard":
        return "Hard"

    return ""


# ============================================================
# JSON CLEANING
# ============================================================

def clean_json_content(content):
    """
    Clean AI response before JSON parsing.

    Handles:
    - Markdown code fences
    - JSON arrays
    - JSON objects containing a questions key
    """

    if not content:
        return ""

    content = str(content).strip()

    # Remove markdown code fences
    content = re.sub(
        r"^```json\s*",
        "",
        content,
        flags=re.IGNORECASE
    )

    content = re.sub(
        r"^```\s*",
        "",
        content
    )

    content = re.sub(
        r"\s*```$",
        "",
        content
    )

    content = content.strip()

    # --------------------------------------------------------
    # Prefer JSON array if present
    # --------------------------------------------------------

    array_start = content.find("[")
    array_end = content.rfind("]")

    if (
        array_start != -1
        and array_end != -1
        and array_end > array_start
    ):
        return content[array_start:array_end + 1].strip()

    # --------------------------------------------------------
    # Otherwise try JSON object
    # --------------------------------------------------------

    object_start = content.find("{")
    object_end = content.rfind("}")

    if (
        object_start != -1
        and object_end != -1
        and object_end > object_start
    ):
        return content[object_start:object_end + 1].strip()

    return content


# ============================================================
# EXTRACT QUESTIONS FROM AI RESPONSE
# ============================================================

def extract_questions_from_response(content):
    """
    Extract questions from either:

    [
        {...}
    ]

    OR

    {
        "questions": [
            {...}
        ]
    }
    """

    cleaned = clean_json_content(content)

    if not cleaned:
        raise ValueError("AI returned empty JSON content")

    data = json.loads(cleaned)

    # Direct list
    if isinstance(data, list):
        return data

    # Object containing questions
    if isinstance(data, dict):

        questions = data.get("questions")

        if isinstance(questions, list):
            return questions

    raise ValueError(
        "AI response does not contain a valid questions list"
    )


# ============================================================
# BASIC QUESTION VALIDATION
# ============================================================

def validate_basic_question_structure(questions, total):
    """
    Validate basic structure of generated questions.
    """

    if not isinstance(questions, list):
        return False

    if len(questions) != total:
        return False

    required_fields = [
        "question",
        "option_a",
        "option_b",
        "option_c",
        "option_d",
        "correct_option",
        "difficulty",
        "explanation"
    ]

    seen_questions = set()

    for question in questions:

        if not isinstance(question, dict):
            return False

        # ----------------------------------------------------
        # Required fields
        # ----------------------------------------------------

        for field in required_fields:

            if field not in question:
                return False

            value = question.get(field)

            if value is None:
                return False

            if not str(value).strip():
                return False

        # ----------------------------------------------------
        # Normalize question text
        # ----------------------------------------------------

        question["question"] = str(
            question["question"]
        ).strip()

        # ----------------------------------------------------
        # Duplicate question check
        # ----------------------------------------------------

        question_text = question["question"].lower()

        if question_text in seen_questions:
            return False

        seen_questions.add(question_text)

        # ----------------------------------------------------
        # Correct option
        # ----------------------------------------------------

        correct_option = str(
            question["correct_option"]
        ).strip().upper()

        if correct_option not in ["A", "B", "C", "D"]:
            return False

        question["correct_option"] = correct_option

        # ----------------------------------------------------
        # Difficulty
        # ----------------------------------------------------

        difficulty = normalize_difficulty(
            question.get("difficulty")
        )

        if not difficulty:
            return False

        question["difficulty"] = difficulty

        # ----------------------------------------------------
        # Normalize options
        # ----------------------------------------------------

        question["option_a"] = str(
            question["option_a"]
        ).strip()

        question["option_b"] = str(
            question["option_b"]
        ).strip()

        question["option_c"] = str(
            question["option_c"]
        ).strip()

        question["option_d"] = str(
            question["option_d"]
        ).strip()

        # ----------------------------------------------------
        # Options should not be duplicates
        # ----------------------------------------------------

        options = [
            question["option_a"].lower(),
            question["option_b"].lower(),
            question["option_c"].lower(),
            question["option_d"].lower()
        ]

        if len(set(options)) != 4:
            return False

        # ----------------------------------------------------
        # Explanation
        # ----------------------------------------------------

        question["explanation"] = str(
            question["explanation"]
        ).strip()

    return True


# ============================================================
# TRIM EXTRA QUESTIONS
# ============================================================

def trim_extra_questions(
    questions,
    easy,
    medium,
    hard
):
    """
    Keep only the requested number of questions
    for each difficulty.
    """

    buckets = {
        "Easy": [],
        "Medium": [],
        "Hard": []
    }

    for question in questions:

        if not isinstance(question, dict):
            continue

        difficulty = normalize_difficulty(
            question.get("difficulty")
        )

        if difficulty in buckets:
            buckets[difficulty].append(question)

    selected = (
        buckets["Easy"][:easy]
        + buckets["Medium"][:medium]
        + buckets["Hard"][:hard]
    )

    return selected


# ============================================================
# CHECK GENERATED DISTRIBUTION
# ============================================================

def check_difficulty_distribution(
    questions,
    easy,
    medium,
    hard
):
    """
    Verify generated difficulty distribution exactly.
    """

    counts = {
        "Easy": 0,
        "Medium": 0,
        "Hard": 0
    }

    for question in questions:

        difficulty = normalize_difficulty(
            question.get("difficulty")
        )

        if difficulty not in counts:
            return False

        counts[difficulty] += 1

    return (
        counts["Easy"] == easy
        and
        counts["Medium"] == medium
        and
        counts["Hard"] == hard
    )


# ============================================================
# AI DIFFICULTY EVALUATION
# ============================================================

def evaluate_question_difficulties(topic, questions):
    """
    Independently evaluate the actual difficulty of every question.

    The evaluator is NOT shown the original/generated
    difficulty label.

    Returns:
        [
            {
                "index": 1,
                "difficulty": "Easy",
                "confidence": 0.95,
                "reason": "..."
            }
        ]

    Returns None if evaluation completely fails.
    """

    if not questions:
        return []

    evaluation_questions = []

    for index, question in enumerate(
        questions,
        start=1
    ):

        evaluation_questions.append({
            "index": index,
            "question": question.get("question"),
            "option_a": question.get("option_a"),
            "option_b": question.get("option_b"),
            "option_c": question.get("option_c"),
            "option_d": question.get("option_d"),
            "correct_option": question.get("correct_option"),
            "explanation": question.get("explanation")
        })

    prompt = f"""
You are an expert educational assessment reviewer.

Topic:
{topic}

Your task is to independently determine the ACTUAL difficulty
of every question below.

IMPORTANT:
- Do NOT use any original/generated difficulty label.
- Judge only the question itself.
- Consider conceptual depth, reasoning required, ambiguity,
  number of steps, prerequisite knowledge and expected learner level.
- Use exactly one of:
  Easy
  Medium
  Hard
- Confidence must be between 0 and 1.
- Give a short reason.
- Return ONLY valid JSON.
- Do not use markdown.

Required JSON format:

[
  {{
    "index": 1,
    "difficulty": "Easy",
    "confidence": 0.95,
    "reason": "Short reason"
  }}
]

Questions:

{json.dumps(
    evaluation_questions,
    ensure_ascii=False,
    indent=2
)}
"""

    for attempt in range(MAX_EVALUATION_ATTEMPTS):

        try:

            response = client.chat(
                model=GENERATION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                options={
                    "temperature": 0.1
                },
                format="json"
            )

            content = response["message"]["content"]

            cleaned = clean_json_content(content)

            evaluations = json.loads(cleaned)

            if not isinstance(evaluations, list):
                raise ValueError(
                    "Evaluation response is not a list"
                )

            if len(evaluations) != len(questions):
                raise ValueError(
                    "Evaluation count does not match question count"
                )

            validated = []

            for expected_index, evaluation in enumerate(
                evaluations,
                start=1
            ):

                if not isinstance(evaluation, dict):
                    raise ValueError(
                        "Invalid evaluation object"
                    )

                index = evaluation.get("index")

                difficulty = normalize_difficulty(
                    evaluation.get("difficulty")
                )

                confidence = evaluation.get("confidence")

                reason = str(
                    evaluation.get("reason", "")
                ).strip()

                # AI sometimes returns "1.0" or "1"
                try:
                    index = int(index)
                except (TypeError, ValueError):
                    raise ValueError(
                        "Invalid evaluation index"
                    )

                if index != expected_index:
                    raise ValueError(
                        "Evaluation indexes are incorrect"
                    )

                if not difficulty:
                    raise ValueError(
                        "Invalid evaluated difficulty"
                    )

                try:
                    confidence = float(confidence)
                except (TypeError, ValueError):
                    raise ValueError(
                        "Invalid confidence value"
                    )

                confidence = max(
                    0.0,
                    min(1.0, confidence)
                )

                if not reason:
                    reason = (
                        "AI difficulty assessment completed."
                    )

                validated.append({
                    "index": expected_index,
                    "difficulty": difficulty,
                    "confidence": round(
                        confidence,
                        2
                    ),
                    "reason": reason[:300]
                })

            return validated

        except Exception as error:

            print(
                f"[AI DIFFICULTY EVALUATION] "
                f"Attempt {attempt + 1} failed: {error}"
            )

    print(
        "[AI DIFFICULTY EVALUATION] "
        "All evaluation attempts failed."
    )

    return None


# ============================================================
# VALIDATE ACTUAL DIFFICULTY
# ============================================================

def validate_actual_difficulty(
    questions,
    evaluations
):
    """
    Compare generated difficulty with independently
    evaluated difficulty.
    """

    if not evaluations:
        return [], []

    passed = []
    failed = []

    evaluation_map = {
        item["index"]: item
        for item in evaluations
    }

    for index, question in enumerate(
        questions,
        start=1
    ):

        evaluation = evaluation_map.get(index)

        if not evaluation:

            failed.append({
                "index": index,
                "question": question,
                "evaluation": None
            })

            continue

        generated_difficulty = normalize_difficulty(
            question.get("difficulty")
        )

        evaluated_difficulty = normalize_difficulty(
            evaluation.get("difficulty")
        )

        if generated_difficulty == evaluated_difficulty:

            passed.append(index)

        else:

            failed.append({
                "index": index,
                "question": question,
                "evaluation": evaluation
            })

    return passed, failed


# ============================================================
# REPLACEMENT QUESTION GENERATION
# ============================================================

def generate_replacement_questions(
    topic,
    difficulty,
    count
):
    """
    Generate replacement questions for questions whose
    generated difficulty does not match AI evaluation.

    This function is intentionally more tolerant than the
    initial generation pipeline because only a small number
    of replacement questions may be required.
    """

    if count <= 0:
        return []

    difficulty = normalize_difficulty(difficulty)

    if not difficulty:
        raise ValueError(
            "Invalid replacement difficulty."
        )

    # --------------------------------------------------------
    # Difficulty-specific instructions
    # --------------------------------------------------------

    if difficulty == "Easy":

        difficulty_rules = """
Easy questions MUST:
- Test basic definitions or fundamental concepts.
- Require little or no multi-step reasoning.
- Be answerable by a beginner who understands the basics.
- Avoid tricky wording and advanced edge cases.
"""

    elif difficulty == "Medium":

        difficulty_rules = """
Medium questions MUST:
- Test understanding and application.
- Require at least some reasoning.
- Be more than simple direct recall.
- Be appropriate for an intermediate learner.
"""

    else:

        difficulty_rules = """
Hard questions MUST:
- Require genuine reasoning or deeper conceptual understanding.
- Preferably combine two or more related concepts.
- Require analysis, comparison, prediction, tracing,
  calculation, or multi-step reasoning where appropriate.
- NOT be simple definition/recall questions.
- NOT be made artificially difficult only by confusing wording.
- Be appropriate for an advanced learner.
"""

    prompt = f"""
Generate exactly {count} replacement multiple-choice question(s).

Topic:
{topic}

REQUIRED DIFFICULTY:
{difficulty}

{difficulty_rules}

VERY IMPORTANT:

1. Every question MUST genuinely be {difficulty}.
2. The difficulty field MUST be exactly "{difficulty}".
3. Generate exactly {count} questions.
4. Each question must have four different options.
5. Exactly one option must be correct.
6. Do not duplicate questions.
7. Do not leave any field empty.
8. Keep explanations concise.
9. Return ONLY JSON.
10. Do not use markdown.
11. Do not add commentary before or after JSON.

Return this exact JSON structure:

[
  {{
    "question": "Question text?",
    "option_a": "Option A",
    "option_b": "Option B",
    "option_c": "Option C",
    "option_d": "Option D",
    "correct_option": "A",
    "difficulty": "{difficulty}",
    "explanation": "Short explanation."
  }}
]

Generate exactly {count} question(s).
"""

    for attempt in range(
        MAX_REPLACEMENT_ATTEMPTS
    ):

        try:

            print(
                f"[AI REPLACEMENT] "
                f"Generating {count} {difficulty} "
                f"replacement question(s) "
                f"- attempt {attempt + 1}/"
                f"{MAX_REPLACEMENT_ATTEMPTS}"
            )

            response = client.chat(
                model=GENERATION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                options={
                    "temperature": 0.15
                },
                format="json"
            )

            content = response["message"]["content"]

            if not content:
                raise ValueError(
                    "AI returned empty response"
                )

            replacements = extract_questions_from_response(
                content
            )

            # ------------------------------------------------
            # If AI accidentally generated extra questions,
            # safely keep only the required difficulty.
            # ------------------------------------------------

            if len(replacements) > count:

                replacements = [
                    question
                    for question in replacements
                    if isinstance(question, dict)
                    and normalize_difficulty(
                        question.get("difficulty")
                    ) == difficulty
                ][:count]

            # ------------------------------------------------
            # Exact count
            # ------------------------------------------------

            if len(replacements) != count:

                raise ValueError(
                    f"Replacement count mismatch: "
                    f"expected {count}, "
                    f"received {len(replacements)}"
                )

            # ------------------------------------------------
            # Basic validation
            # ------------------------------------------------

            if not validate_basic_question_structure(
                replacements,
                count
            ):

                raise ValueError(
                    "Invalid replacement question structure"
                )

            # ------------------------------------------------
            # Required difficulty validation
            # ------------------------------------------------

            for question in replacements:

                actual_difficulty = normalize_difficulty(
                    question.get("difficulty")
                )

                if actual_difficulty != difficulty:

                    raise ValueError(
                        "Replacement question has incorrect "
                        f"difficulty: {actual_difficulty}"
                    )

            print(
                f"[AI REPLACEMENT] "
                f"Successfully generated {count} "
                f"{difficulty} replacement question(s)."
            )

            return replacements

        except Exception as error:

            print(
                f"[AI REPLACEMENT] "
                f"{difficulty} attempt "
                f"{attempt + 1} failed: {error}"
            )

    print(
        f"[AI REPLACEMENT] "
        f"Unable to generate {count} "
        f"{difficulty} replacement question(s) "
        f"after {MAX_REPLACEMENT_ATTEMPTS} attempts."
    )

    return []


# ============================================================
# MAIN QUESTION GENERATOR
# ============================================================

def generate_questions(topic, easy, medium, hard):
    """Generate validated small batches to reduce invalid JSON and worker timeouts."""
    easy, medium, hard = int(easy), int(medium), int(hard)
    total = easy + medium + hard
    if min(easy, medium, hard) < 0:
        raise ValueError("Question counts cannot be negative.")
    if total <= 0:
        raise ValueError("At least one question is required.")
    if total > 150:
        raise ValueError("A quiz cannot contain more than 150 questions.")

    batch_size = 10
    requirements = [("Easy", easy), ("Medium", medium), ("Hard", hard)]
    all_questions = []
    seen_questions = set()

    for difficulty, requested_count in requirements:
        remaining = requested_count
        while remaining:
            batch_count = min(batch_size, remaining)
            previous_questions = "\n".join(
                "- " + question[:180]
                for question in list(seen_questions)[-20:]
            ) or "(none yet)"
            prompt = f"""
Create exactly {batch_count} multiple-choice questions about this topic:
{topic}

Do not repeat or paraphrase these previously generated questions:
{previous_questions}

Every question must have difficulty exactly: {difficulty}.
Return only a valid JSON array, no markdown or commentary. Each object must
contain these fields: question, option_a, option_b, option_c, option_d,
correct_option, difficulty, explanation.
correct_option must be A, B, C, or D. Use four distinct options and exactly
one correct answer. Questions must be distinct, clear, and factually correct.
The difficulty value for every object must be exactly "{difficulty}".
Example object: {{"question":"...","option_a":"...","option_b":"...","option_c":"...","option_d":"...","correct_option":"A","difficulty":"{difficulty}","explanation":"..."}}
"""
            batch = None
            last_error = None
            for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
                try:
                    print(f"[AI GENERATION] {difficulty} batch of {batch_count}; attempt {attempt}/{MAX_GENERATION_ATTEMPTS}")
                    response = client.chat(
                        model=GENERATION_MODEL,
                        messages=[{"role": "user", "content": prompt}],
                        options={"temperature": 0.1},
                        format="json",
                    )
                    content = response["message"]["content"]
                    generated = extract_questions_from_response(content)
                    if not validate_basic_question_structure(generated, batch_count):
                        raise ValueError("Generated question structure or count is invalid")
                    expected = (
                        batch_count if difficulty == "Easy" else 0,
                        batch_count if difficulty == "Medium" else 0,
                        batch_count if difficulty == "Hard" else 0,
                    )
                    if not check_difficulty_distribution(generated, *expected):
                        raise ValueError(f"Difficulty mismatch; expected {difficulty}")
                    normalized = [
                        re.sub(r"\s+", " ", str(item.get("question", "")).strip()).casefold()
                        for item in generated
                    ]
                    if len(set(normalized)) != len(normalized):
                        raise ValueError("Duplicate questions inside batch")
                    if any(question in seen_questions for question in normalized):
                        raise ValueError("Question duplicates an earlier batch")
                    batch = generated
                    break
                except Exception as exc:
                    last_error = exc
                    print(f"[AI GENERATION] {difficulty} attempt {attempt} failed: {exc}")
            if batch is None:
                raise RuntimeError(
                    f"Could not generate a valid batch of {batch_count} {difficulty} questions. "
                    f"Please retry or reduce the question count. Last error: {last_error}"
                )
            for item in batch:
                seen_questions.add(
                    re.sub(r"\s+", " ", str(item.get("question", "")).strip()).casefold()
                )
            all_questions.extend(batch)
            remaining -= batch_count

    if len(all_questions) != total:
        raise RuntimeError(f"Generated {len(all_questions)} questions, expected {total}.")
    if not validate_basic_question_structure(all_questions, total):
        raise RuntimeError("Final quiz failed question-structure validation.")
    if not check_difficulty_distribution(all_questions, easy, medium, hard):
        raise RuntimeError("Final quiz failed difficulty-distribution validation.")

    print(f"[AI GENERATION] Successfully generated {total} questions in small batches.")
    # The create_quiz route supports an empty evaluations list. Avoiding extra
    # full-quiz evaluation/replacement calls reduces synchronous request time.
    return all_questions, []
