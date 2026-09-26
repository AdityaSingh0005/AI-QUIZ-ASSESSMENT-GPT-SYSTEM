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
    Handles markdown code blocks and extra text.
    """

    if not content:
        return ""

    content = str(content).strip()

    # Remove markdown code fences
    content = re.sub(r"^```json\s*", "", content, flags=re.IGNORECASE)
    content = re.sub(r"^```\s*", "", content)
    content = re.sub(r"\s*```$", "", content)

    # Try to extract JSON array
    start = content.find("[")
    end = content.rfind("]")

    if start != -1 and end != -1 and end > start:
        content = content[start:end + 1]

    return content.strip()


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

        # Required fields
        for field in required_fields:
            if field not in question:
                return False

            value = question.get(field)

            if value is None:
                return False

            if not str(value).strip():
                return False

        # Question duplicate check
        question_text = str(question["question"]).strip().lower()

        if question_text in seen_questions:
            return False

        seen_questions.add(question_text)

        # Correct option
        correct_option = str(
            question["correct_option"]
        ).strip().upper()

        if correct_option not in ["A", "B", "C", "D"]:
            return False

        question["correct_option"] = correct_option

        # Difficulty
        difficulty = normalize_difficulty(
            question.get("difficulty")
        )

        if not difficulty:
            return False

        question["difficulty"] = difficulty

        # Options should not be duplicates
        options = [
            str(question["option_a"]).strip().lower(),
            str(question["option_b"]).strip().lower(),
            str(question["option_c"]).strip().lower(),
            str(question["option_d"]).strip().lower()
        ]

        if len(set(options)) != 4:
            return False

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

    IMPORTANT:
    The evaluator is NOT shown the original/generated
    difficulty label. This prevents simple confirmation bias.

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

    for index, question in enumerate(questions, start=1):

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

{json.dumps(evaluation_questions, ensure_ascii=False, indent=2)}
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
                ]
            )

            content = response["message"]["content"]

            cleaned = clean_json_content(content)

            evaluations = json.loads(cleaned)

            if not isinstance(evaluations, list):
                raise ValueError("Evaluation response is not a list")

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
                    raise ValueError("Invalid evaluation object")

                index = evaluation.get("index")

                difficulty = normalize_difficulty(
                    evaluation.get("difficulty")
                )

                confidence = evaluation.get("confidence")

                reason = str(
                    evaluation.get("reason", "")
                ).strip()

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

                # Keep confidence inside valid range
                confidence = max(
                    0.0,
                    min(1.0, confidence)
                )

                if not reason:
                    reason = "AI difficulty assessment completed."

                validated.append({
                    "index": expected_index,
                    "difficulty": difficulty,
                    "confidence": round(confidence, 2),
                    "reason": reason
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
    """

    if count <= 0:
        return []

    prompt = f"""
Generate exactly {count} multiple-choice questions.

Topic:
{topic}

Required difficulty:
{difficulty}

IMPORTANT:
Every generated question MUST genuinely match the
required difficulty level.

Difficulty definitions:

Easy:
- Basic concepts
- Direct recall or simple understanding
- Very little reasoning

Medium:
- Requires understanding and application
- May require multiple reasoning steps
- Not merely direct recall

Hard:
- Requires deeper reasoning
- Multiple concepts or non-trivial analysis
- Suitable for advanced learners

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
    "difficulty": "{difficulty}",
    "explanation": "..."
  }}
]
"""

    for attempt in range(MAX_GENERATION_ATTEMPTS):

        try:

            response = client.chat(
                model=GENERATION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )

            content = response["message"]["content"]

            cleaned = clean_json_content(content)

            replacements = json.loads(cleaned)

            if not isinstance(replacements, list):
                raise ValueError(
                    "Replacement response is not a list"
                )

            if len(replacements) != count:
                raise ValueError(
                    "Replacement count mismatch"
                )

            if not validate_basic_question_structure(
                replacements,
                count
            ):
                raise ValueError(
                    "Invalid replacement question structure"
                )

            # Force/verify required difficulty
            for question in replacements:

                if normalize_difficulty(
                    question.get("difficulty")
                ) != difficulty:
                    raise ValueError(
                        "Replacement has incorrect difficulty"
                    )

            return replacements

        except Exception as error:

            print(
                f"[AI REPLACEMENT] "
                f"{difficulty} attempt "
                f"{attempt + 1} failed: {error}"
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

    Example:
        questions, evaluations = generate_questions(...)
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

    # --------------------------------------------------------
    # INITIAL GENERATION
    # --------------------------------------------------------

    for attempt in range(MAX_GENERATION_ATTEMPTS):

        try:

            response = client.chat(
                model=GENERATION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )

            content = response["message"]["content"]

            cleaned = clean_json_content(content)

            generated = json.loads(cleaned)

            if not validate_basic_question_structure(
                generated,
                total
            ):
                raise ValueError(
                    "Generated question structure is invalid"
                )

            # Trim if AI somehow produced extra questions
            generated = trim_extra_questions(
                generated,
                easy,
                medium,
                hard
            )

            if len(generated) != total:
                raise ValueError(
                    "Could not obtain requested number "
                    "of questions."
                )

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

    # --------------------------------------------------------
    # FIRST AI DIFFICULTY EVALUATION
    # --------------------------------------------------------

    evaluations = evaluate_question_difficulties(
        topic,
        questions
    )

    # --------------------------------------------------------
    # IF EVALUATION FAILS
    # --------------------------------------------------------

    if evaluations is None:

        print(
            "[AI DIFFICULTY EVALUATION] "
            "Evaluation unavailable. "
            "Returning generated questions."
        )

        return questions, []

    # --------------------------------------------------------
    # FIND DIFFICULTY MISMATCHES
    # --------------------------------------------------------

    passed, failed = validate_actual_difficulty(
        questions,
        evaluations
    )

    print(
        f"[AI DIFFICULTY CHECK] "
        f"Passed: {len(passed)}, "
        f"Failed: {len(failed)}"
    )

    # --------------------------------------------------------
    # REPLACE MISMATCHED QUESTIONS
    # --------------------------------------------------------

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

                raise Exception(
                    f"Failed to generate replacement "
                    f"questions for {difficulty}."
                )

            for index, replacement in zip(
                indexes,
                replacements
            ):

                replacement_questions[index] = replacement

        # Apply replacements
        for index, replacement in replacement_questions.items():

            questions[index - 1] = replacement

        # ----------------------------------------------------
        # FINAL VALIDATION AFTER REPLACEMENTS
        # ----------------------------------------------------

        if not validate_basic_question_structure(
            questions,
            total
        ):
            raise Exception(
                "Questions became invalid after replacement."
            )

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

        # ----------------------------------------------------
        # FINAL AI EVALUATION
        # ----------------------------------------------------

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

        # IMPORTANT:
        # Return FINAL evaluations, not stale first evaluations.
        return questions, final_evaluations

    # --------------------------------------------------------
    # NO MISMATCHES
    # --------------------------------------------------------

    return questions, evaluations