
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

def generate_questions(
    topic,
    easy,
    medium,
    hard
):
    """
    Generate questions with requested difficulty distribution.

    Returns:
        questions, final_evaluations
    """

    easy = int(easy)
    medium = int(medium)
    hard = int(hard)

    total = easy + medium + hard

    if total <= 0:
        raise ValueError(
            "At least one question is required."
        )

    prompt = f"""
Generate exactly {total} multiple-choice questions.

Topic:
{topic}

Required difficulty distribution:

Easy: {easy}
Medium: {medium}
Hard: {hard}

IMPORTANT:

- Generate EXACTLY the requested number of questions.
- Each question must have exactly four options.
- Only one option should be correct.
- Do not create duplicate questions.
- Do not create duplicate options within a question.
- Difficulty must be exactly one of:
  Easy, Medium, Hard.
- Difficulty labels must match the actual difficulty.

Difficulty definitions:

Easy:
Basic concepts, direct recall, simple understanding.

Medium:
Application of concepts, moderate reasoning, multiple steps.

Hard:
Advanced reasoning, deeper conceptual understanding,
multiple concepts or non-trivial analysis.

Return ONLY valid JSON.

Format:

[
  {{
    "question": "...",
    "option_a": "...",
    "option_b": "...",
    "option_c": "...",
    "option_d": "...",
    "correct_option": "A",
    "difficulty": "Easy",
    "explanation": "..."
  }}
]
"""

    questions = None

    # ========================================================
    # INITIAL GENERATION
    # ========================================================

    for attempt in range(
        MAX_GENERATION_ATTEMPTS
    ):

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

            generated = extract_questions_from_response(
                content
            )

            # ------------------------------------------------
            # Handle extra questions before exact validation
            # ------------------------------------------------

            if len(generated) > total:

                print(
                    "[AI GENERATION] "
                    "AI generated extra questions. "
                    "Attempting safe trimming."
                )

                generated = trim_extra_questions(
                    generated,
                    easy,
                    medium,
                    hard
                )

            # ------------------------------------------------
            # Basic structure validation
            # ------------------------------------------------

            if not validate_basic_question_structure(
                generated,
                total
            ):

                raise ValueError(
                    "Generated question structure is invalid"
                )

            # ------------------------------------------------
            # Difficulty distribution
            # ------------------------------------------------

            if not check_difficulty_distribution(
                generated,
                easy,
                medium,
                hard
            ):

                raise ValueError(
                    "Difficulty distribution mismatch"
                )

            questions = generated

            break

        except Exception as error:

            print(
                f"[AI GENERATION] "
                f"Attempt {attempt + 1} failed: {error}"
            )

    if questions is None:

        raise Exception(
            "AI failed to generate valid questions "
            "after multiple attempts."
        )

    # ========================================================
    # FIRST AI DIFFICULTY EVALUATION
    # ========================================================

    evaluations = evaluate_question_difficulties(
        topic,
        questions
    )

    # ========================================================
    # IF EVALUATION FAILS
    # ========================================================

    if evaluations is None:

        print(
            "[AI DIFFICULTY EVALUATION] "
            "Evaluation unavailable. "
            "Returning generated questions."
        )

        return questions, []

    # ========================================================
    # FIND DIFFICULTY MISMATCHES
    # ========================================================

    passed, failed = validate_actual_difficulty(
        questions,
        evaluations
    )

    print(
        f"[AI DIFFICULTY CHECK] "
        f"Passed: {len(passed)}, "
        f"Failed: {len(failed)}"
    )

    # ========================================================
    # REPLACE MISMATCHED QUESTIONS
    # ========================================================

    if failed:

        replacement_by_difficulty = {
            "Easy": [],
            "Medium": [],
            "Hard": []
        }

        for item in failed:

            generated_difficulty = normalize_difficulty(
                item["question"].get("difficulty")
            )

            if generated_difficulty in replacement_by_difficulty:

                replacement_by_difficulty[
                    generated_difficulty
                ].append(item["index"])

        replacement_questions = {}

        for difficulty, indexes in (
            replacement_by_difficulty.items()
        ):

            count = len(indexes)

            if count <= 0:
                continue

            replacements = generate_replacement_questions(
                topic,
                difficulty,
                count
            )

            if len(replacements) != count:

                # ------------------------------------------------
                # IMPORTANT:
                # Instead of silently keeping a known mismatch,
                # fail clearly so teacher does not receive a
                # misleading "verified" quiz.
                # ------------------------------------------------

                raise Exception(
                    f"Failed to generate replacement "
                    f"questions for {difficulty}."
                )

            for index, replacement in zip(
                indexes,
                replacements
            ):

                replacement_questions[index] = replacement

        # ====================================================
        # APPLY REPLACEMENTS
        # ====================================================

        for index, replacement in (
            replacement_questions.items()
        ):

            questions[index - 1] = replacement

        # ====================================================
        # FINAL BASIC VALIDATION
        # ====================================================

        if not validate_basic_question_structure(
            questions,
            total
        ):

            raise Exception(
                "Questions became invalid after replacement."
            )

        # ====================================================
        # FINAL DISTRIBUTION VALIDATION
        # ====================================================

        if not check_difficulty_distribution(
            questions,
            easy,
            medium,
            hard
        ):

            raise Exception(
                "Difficulty distribution changed "
                "after replacement."
            )

        # ====================================================
        # FINAL AI EVALUATION
        # ====================================================

        final_evaluations = evaluate_question_difficulties(
            topic,
            questions
        )

        if final_evaluations is None:

            print(
                "[AI DIFFICULTY EVALUATION] "
                "Final evaluation failed."
            )

            return questions, []

        final_passed, final_failed = (
            validate_actual_difficulty(
                questions,
                final_evaluations
            )
        )

        print(
            f"[AI FINAL DIFFICULTY CHECK] "
            f"Passed: {len(final_passed)}, "
            f"Failed: {len(final_failed)}"
        )

        # ----------------------------------------------------
        # IMPORTANT:
        # Return FINAL evaluations.
        # ----------------------------------------------------

        return questions, final_evaluations

    # ========================================================
    # NO MISMATCHES
    # ========================================================

    return questions, evaluations

