import json
import os
import re
import time
import threading

from ollama import Client as OllamaClient


# ============================================================
# OLLAMA CLOUD CONFIGURATION (OLLAMA ONLY)
# ============================================================

OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")

ollama_client = None
if OLLAMA_API_KEY:
    ollama_client = OllamaClient(
        host="https://ollama.com",
        headers={"Authorization": f"Bearer {OLLAMA_API_KEY}"},
        timeout=300,
    )

MAX_OLLAMA_ATTEMPTS = 2

_GENERATION_LOCK = threading.Lock()

MAX_EVALUATION_ATTEMPTS = 2
MAX_REPLACEMENT_ATTEMPTS = 3


# ============================================================
# OLLAMA CLOUD REQUEST
# ============================================================

def _ollama_json_content(
    prompt,
    temperature=0.1
):

    if not ollama_client:
        raise RuntimeError(
            "OLLAMA_API_KEY is not configured."
        )

    response = ollama_client.chat(
        model=OLLAMA_MODEL,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
        options={
            "temperature": temperature
        },
        format="json",
    )

    def field(value, name, default=None):

        if isinstance(value, dict):
            return value.get(name, default)

        return getattr(
            value,
            name,
            default
        )

    message = field(
        response,
        "message",
        {}
    )

    content = field(
        message,
        "content",
        ""
    )

    if not isinstance(content, str) or not content.strip():

        thinking = field(
            message,
            "thinking",
            ""
        )

        done_reason = field(
            response,
            "done_reason",
            None
        )

        print(
            "[OLLAMA RESPONSE EMPTY] "
            f"thinking_chars="
            f"{len(thinking) if isinstance(thinking, str) else 0} "
            f"done_reason={done_reason!r}"
        )

        raise ValueError(
            "Ollama returned empty message.content."
        )

    return content


# ============================================================
# OLLAMA-ONLY REQUEST HANDLER
# ============================================================

def _request_with_ollama(
    prompt,
    validator,
    operation,
    response_format=None,
    max_completion_tokens=3000,
    temperature=0.1
):
    """
    Send the request only to Ollama Cloud.

    Every response is validated before acceptance. There is no other
    AI provider or fallback path. Previously validated batches remain in memory if a later
    batch fails.
    """

    if not ollama_client:
        raise RuntimeError(
            "OLLAMA_API_KEY is not configured. "
            "Add a valid Ollama Cloud API key to the environment."
        )

    errors = []

    for attempt in range(1, MAX_OLLAMA_ATTEMPTS + 1):
        try:
            print(
                f"[AI PROVIDER] START "
                f"provider=Ollama "
                f"operation={operation} "
                f"attempt={attempt}/{MAX_OLLAMA_ATTEMPTS} "
                f"model={OLLAMA_MODEL}"
            )

            content = _ollama_json_content(
                prompt,
                temperature=temperature
            )

            result = validator(content)

            print(
                f"[AI PROVIDER] SUCCESS "
                f"provider=Ollama operation={operation}"
            )

            return result, "Ollama"

        except Exception as error:
            errors.append(
                f"Ollama {type(error).__name__}: {str(error)[:400]}"
            )

            print(
                f"[AI PROVIDER] FAILED "
                f"provider=Ollama "
                f"operation={operation} "
                f"attempt={attempt} "
                f"error_type={type(error).__name__} "
                f"error={str(error)[:500]}"
            )

            if attempt < MAX_OLLAMA_ATTEMPTS:
                delay = min(2 ** attempt, 8)
                print(
                    f"[OLLAMA RETRY] "
                    f"Retrying the same request after {delay}s."
                )
                time.sleep(delay)

    detail = " | ".join(errors[-4:]) or "No Ollama response was received."

    raise RuntimeError(
        f"{operation} failed. "
        "Ollama did not return a validated response after "
        f"{MAX_OLLAMA_ATTEMPTS} attempts. "
        "Previously validated batches were preserved in memory. "
        f"Details: {detail}"
    )


# ============================================================
# STRICT JSON SCHEMA
# ============================================================

def build_quiz_batch_schema(
    batch_count,
    difficulty
):

    """Strict JSON Schema for one exact-difficulty batch."""

    question_schema = {

        "type": "object",

        "properties": {

            "question": {
                "type": "string"
            },

            "option_a": {
                "type": "string"
            },

            "option_b": {
                "type": "string"
            },

            "option_c": {
                "type": "string"
            },

            "option_d": {
                "type": "string"
            },

            "correct_option": {
                "type": "string",
                "enum": ["A", "B", "C", "D"]
            },

            "difficulty": {
                "type": "string",
                "enum": [difficulty]
            },

            "explanation": {
                "type": "string"
            },
        },

        "required": [
            "question",
            "option_a",
            "option_b",
            "option_c",
            "option_d",
            "correct_option",
            "difficulty",
            "explanation"
        ],

        "additionalProperties": False,
    }

    return {

        "type": "object",

        "properties": {

            "questions": {

                "type": "array",

                "items": question_schema,

                "minItems": batch_count,

                "maxItems": batch_count,
            }
        },

        "required": ["questions"],

        "additionalProperties": False,
    }


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
    Remove optional Markdown fences and locate a valid JSON
    value without blindly slicing at the first '[' or last ']'.
    """

    if content is None:
        return ""

    text = str(content).strip()

    text = re.sub(
        r"^\s*```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\s*```\s*$",
        "",
        text
    ).strip()

    if not text:
        return ""

    try:
        json.loads(text)
        return text

    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()

    for position, character in enumerate(text):

        if character not in "[{":
            continue

        try:

            _, end_position = decoder.raw_decode(
                text[position:]
            )

            return text[
                position:position + end_position
            ].strip()

        except json.JSONDecodeError:
            continue

    return text


# ============================================================
# EXTRACT QUESTIONS FROM AI RESPONSE
# ============================================================

def validate_basic_question_structure(
    questions,
    total
):

    """Validate exact count, required fields, difficulty, and distinct options."""

    def fail(reason):

        print(
            f"[AI VALIDATION FAILED] {reason}"
        )

        return False

    if not isinstance(questions, list):

        return fail(
            "AI response is not a list."
        )

    if len(questions) != total:

        return fail(
            f"Question count mismatch: "
            f"expected {total}, received {len(questions)}."
        )

    required_fields = (
        "question",
        "option_a",
        "option_b",
        "option_c",
        "option_d",
        "correct_option",
        "difficulty",
        "explanation",
    )

    seen_questions = set()

    for index, question in enumerate(
        questions,
        start=1
    ):

        if not isinstance(question, dict):

            return fail(
                f"Question {index} is not a valid JSON object."
            )

        for field in required_fields:

            value = question.get(field)

            if value is None or not str(value).strip():

                return fail(
                    f"Question {index}: "
                    f"field '{field}' is missing or empty."
                )

        question_text = re.sub(
            r"\s+",
            " ",
            str(question["question"]).strip()
        )

        normalized_question = question_text.casefold()

        if normalized_question in seen_questions:

            return fail(
                f"Question {index}: duplicate question detected."
            )

        seen_questions.add(normalized_question)

        question["question"] = question_text

        correct_option = str(
            question["correct_option"]
        ).strip().upper()

        if correct_option not in {
            "A", "B", "C", "D"
        }:

            return fail(
                f"Question {index}: "
                f"invalid correct_option '{correct_option}'."
            )

        question["correct_option"] = correct_option

        difficulty = normalize_difficulty(
            question.get("difficulty")
        )

        if not difficulty:

            return fail(
                f"Question {index}: invalid difficulty "
                f"'{question.get('difficulty')}'."
            )

        question["difficulty"] = difficulty

        option_fields = (
            "option_a",
            "option_b",
            "option_c",
            "option_d"
        )

        normalized_options = []

        for field in option_fields:

            value = str(question[field]).strip()

            if not value:

                return fail(
                    f"Question {index}: {field} is empty."
                )

            question[field] = value

            normalized_options.append(
                re.sub(
                    r"\s+",
                    " ",
                    value
                ).casefold()
            )

        if len(set(normalized_options)) != 4:

            return fail(
                f"Question {index}: duplicate options detected."
            )

        question["explanation"] = str(
            question["explanation"]
        ).strip()

    print(
        f"[AI VALIDATION] Successfully validated {total} questions."
    )

    return True


# ============================================================
# BASIC QUESTION VALIDATION
# ============================================================


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
        and counts["Medium"] == medium
        and counts["Hard"] == hard
    )


# ============================================================
# AI DIFFICULTY EVALUATION
# ============================================================

def build_evaluation_schema(question_count):

    """Strict JSON Schema for independent difficulty evaluation."""

    return {

        "type": "object",

        "properties": {

            "evaluations": {

                "type": "array",

                "minItems": question_count,

                "maxItems": question_count,

                "items": {

                    "type": "object",

                    "properties": {

                        "index": {
                            "type": "integer",
                            "minimum": 1
                        },

                        "difficulty": {
                            "type": "string",
                            "enum": [
                                "Easy",
                                "Medium",
                                "Hard"
                            ]
                        },

                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1
                        },

                        "reason": {
                            "type": "string"
                        },
                    },

                    "required": [
                        "index",
                        "difficulty",
                        "confidence",
                        "reason"
                    ],

                    "additionalProperties": False,
                },
            }
        },

        "required": ["evaluations"],

        "additionalProperties": False,
    }


def evaluate_question_difficulties(
    topic,
    questions
):

    """
    Independently evaluate actual question difficulty.
    Uses Ollama Cloud only.
    Returns None if Ollama cannot provide a validated evaluation.
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

            "explanation": question.get("explanation"),
        })

    prompt = f"""
You are an expert educational assessment reviewer.

Topic:
{topic}

Independently determine the ACTUAL difficulty of every question below.
Do NOT use any original/generated difficulty label.
Judge conceptual depth, reasoning, ambiguity, number of steps, prerequisite
knowledge, and expected learner level.

Use exactly one difficulty:
Easy, Medium, Hard.

Return one JSON object in this exact structure:
{{
  "evaluations": [
    {{
      "index": 1,
      "difficulty": "Easy",
      "confidence": 0.95,
      "reason": "Short reason"
    }}
  ]
}}

The evaluations array must contain exactly {len(questions)} objects, in the
same order as the input questions. Return JSON only.

Questions:
{json.dumps(evaluation_questions, ensure_ascii=False, indent=2)}
"""

    def validate(content):

        cleaned = clean_json_content(content)

        data = json.loads(cleaned)

        if isinstance(data, dict):
            evaluations = data.get("evaluations")
        else:
            evaluations = data

        if (
            not isinstance(evaluations, list)
            or len(evaluations) != len(questions)
        ):

            raise ValueError(
                "Evaluation count does not match question count."
            )

        validated = []

        for expected_index, item in enumerate(
            evaluations,
            start=1
        ):

            if not isinstance(item, dict):
                raise ValueError(
                    "Invalid evaluation object."
                )

            try:

                index = int(item.get("index"))

                confidence = float(
                    item.get("confidence")
                )

            except (TypeError, ValueError):

                raise ValueError(
                    "Evaluation index/confidence is invalid."
                )

            difficulty = normalize_difficulty(
                item.get("difficulty")
            )

            if index != expected_index:

                raise ValueError(
                    "Evaluation indexes are incorrect."
                )

            if not difficulty:

                raise ValueError(
                    "Invalid evaluated difficulty."
                )

            if not 0 <= confidence <= 1:

                raise ValueError(
                    "Evaluation confidence must be between 0 and 1."
                )

            reason = str(
                item.get("reason", "")
            ).strip()

            validated.append({

                "index": expected_index,

                "difficulty": difficulty,

                "confidence": round(confidence, 2),

                "reason": (
                    reason
                    or "AI difficulty assessment completed."
                )[:300],
            })

        return validated

    try:

        result, provider = _request_with_ollama(

            prompt=prompt,

            validator=validate,

            operation="difficulty evaluation",

            response_format={

                "type": "json_schema",

                "json_schema": {

                    "name": "difficulty_evaluations",

                    "strict": True,

                    "schema": build_evaluation_schema(
                        len(questions)
                    ),
                },
            },

            max_completion_tokens=3000,

            temperature=0.1,
        )

        print(
            f"[AI DIFFICULTY EVALUATION] "
            f"completed_by={provider} "
            f"count={len(result)}"
        )

        return result

    except Exception as error:

        print(
            f"[AI DIFFICULTY EVALUATION] "
            f"Ollama evaluation failed: {error}"
        )

        return None


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
# JSON RESPONSE EXTRACTION
# ============================================================

def extract_questions_from_response(
    content,
    expected_count,
    difficulty
):
    """
    Extract and validate questions from an AI response.
    Supports:
    - {"questions": [...]}
    - Direct JSON list [...]
    - Markdown fenced JSON
    """

    cleaned = clean_json_content(content)

    if not cleaned:
        raise ValueError(
            "AI returned an empty JSON response."
        )

    try:
        data = json.loads(cleaned)

    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON returned by AI: {error}"
        ) from error

    if isinstance(data, dict):
        questions = data.get("questions")

    elif isinstance(data, list):
        questions = data

    else:
        raise ValueError(
            "AI response must be a JSON object or list."
        )

    if not isinstance(questions, list):
        raise ValueError(
            "The AI response does not contain a valid questions list."
        )

    if not validate_basic_question_structure(
        questions,
        expected_count
    ):
        raise ValueError(
            "Generated questions failed structural validation."
        )

    # Ensure every question belongs to the requested difficulty.
    requested_difficulty = normalize_difficulty(
        difficulty
    )

    for index, question in enumerate(
        questions,
        start=1
    ):

        actual_difficulty = normalize_difficulty(
            question.get("difficulty")
        )

        if actual_difficulty != requested_difficulty:

            raise ValueError(
                f"Question {index} has difficulty "
                f"{actual_difficulty}; expected "
                f"{requested_difficulty}."
            )

    return questions


# ============================================================
# REPLACEMENT QUESTION SCHEMA
# ============================================================

def build_replacement_schema(
    replacement_count,
    difficulty
):
    """
    Strict schema for replacement questions.
    """

    question_schema = {

        "type": "object",

        "properties": {

            "question": {
                "type": "string"
            },

            "option_a": {
                "type": "string"
            },

            "option_b": {
                "type": "string"
            },

            "option_c": {
                "type": "string"
            },

            "option_d": {
                "type": "string"
            },

            "correct_option": {
                "type": "string",
                "enum": ["A", "B", "C", "D"]
            },

            "difficulty": {
                "type": "string",
                "enum": [difficulty]
            },

            "explanation": {
                "type": "string"
            },
        },

        "required": [
            "question",
            "option_a",
            "option_b",
            "option_c",
            "option_d",
            "correct_option",
            "difficulty",
            "explanation"
        ],

        "additionalProperties": False,
    }

    return {

        "type": "object",

        "properties": {

            "questions": {

                "type": "array",

                "items": question_schema,

                "minItems": replacement_count,

                "maxItems": replacement_count,
            }
        },

        "required": ["questions"],

        "additionalProperties": False,
    }


# ============================================================
# REPLACEMENT QUESTION GENERATOR
# ============================================================

def generate_replacement_questions(
    topic,
    failed_questions
):
    """
    Generate replacement questions for failed questions.

    Each replacement:
    - Uses the original question's difficulty.
    - Tests the same topic/concept.
    - Uses a different question statement.
    - Has four unique options.
    - Is structurally validated before returning.

    Returns:
        Dictionary:
        {
            original_question_index: replacement_question
        }

    Returns an empty dictionary if there is nothing to replace.
    """

    if not failed_questions:
        print(
            "[AI REPLACEMENT] No failed questions. "
            "Replacement not required."
        )
        return {}

    replacements = {}

    # Group failed questions by their evaluated difficulty.
    grouped_failures = {
        "Easy": [],
        "Medium": [],
        "Hard": []
    }

    for item in failed_questions:

        if not isinstance(item, dict):
            continue

        original_question = item.get(
            "question",
            {}
        )

        evaluation = item.get(
            "evaluation",
            {}
        )

        if not isinstance(original_question, dict):
            continue

        if not isinstance(evaluation, dict):
            continue

        original_index = item.get("index")

        if original_index is None:
            continue

        difficulty = normalize_difficulty(
            evaluation.get("difficulty")
        )

        if difficulty not in grouped_failures:
            continue

        grouped_failures[difficulty].append({

            "index": original_index,

            "question": original_question,

            "reason": evaluation.get(
                "reason",
                "Difficulty mismatch."
            )
        })

    # Generate replacement batches separately by difficulty.
    for difficulty, failures in grouped_failures.items():

        if not failures:
            continue

        print(
            f"[AI REPLACEMENT] "
            f"Starting {len(failures)} replacements "
            f"for difficulty={difficulty}."
        )

        failed_question_details = []

        for item in failures:

            original = item["question"]

            failed_question_details.append({

                "original_index": item["index"],

                "original_question": original.get(
                    "question",
                    ""
                ),

                "original_options": {

                    "A": original.get("option_a"),

                    "B": original.get("option_b"),

                    "C": original.get("option_c"),

                    "D": original.get("option_d"),
                },

                "original_correct_option": original.get(
                    "correct_option"
                ),

                "original_explanation": original.get(
                    "explanation"
                ),

                "evaluation_reason": item["reason"]
            })

        prompt = f"""
You are an expert educational question setter.

Topic:
{topic}

Your task is to create replacement questions.

Required difficulty:
{difficulty}

Number of replacement questions:
{len(failures)}

The questions below failed an independent difficulty review.

Create one completely new replacement question for each failed question.

IMPORTANT RULES:

1. Preserve the original topic and learning concept.
2. The replacement must have exactly this difficulty: {difficulty}.
3. Do not copy the original question.
4. Do not simply change names, numbers, or a few words.
5. Create a genuinely different question testing the same learning concept.
6. Create exactly four options: A, B, C, D.
7. Exactly one option must be correct.
8. All four options must be different.
9. The correct_option must contain only A, B, C, or D.
10. Provide a short, accurate explanation.
11. Do not include markdown.
12. Do not add any text outside the JSON.

Return this exact structure:

{{
  "questions": [
    {{
      "question": "Question text",
      "option_a": "Option A",
      "option_b": "Option B",
      "option_c": "Option C",
      "option_d": "Option D",
      "correct_option": "A",
      "difficulty": "{difficulty}",
      "explanation": "Explanation"
    }}
  ]
}}

The questions to replace are:

{json.dumps(failed_question_details, ensure_ascii=False, indent=2)}
"""

        def validate_replacements(content):

            new_questions = extract_questions_from_response(
                content,
                expected_count=len(failures),
                difficulty=difficulty
            )

            # Check that replacements are not copies of originals.
            original_texts = {
                re.sub(
                    r"\s+",
                    " ",
                    str(item["question"].get("question", "")).strip()
                ).casefold()
                for item in failures
            }

            for replacement in new_questions:

                replacement_text = re.sub(
                    r"\s+",
                    " ",
                    replacement["question"].strip()
                ).casefold()

                if replacement_text in original_texts:

                    raise ValueError(
                        "Replacement question repeats an original question."
                    )

            return new_questions

        try:

            new_questions, provider = _request_with_ollama(

                prompt=prompt,

                validator=validate_replacements,

                operation=f"{difficulty} replacement generation",

                response_format={

                    "type": "json_schema",

                    "json_schema": {

                        "name": "replacement_questions",

                        "strict": True,

                        "schema": build_replacement_schema(
                            len(failures),
                            difficulty
                        ),
                    },
                },

                max_completion_tokens=3000,

                temperature=0.2,
            )

            for failed_item, replacement in zip(
                failures,
                new_questions
            ):

                replacements[
                    failed_item["index"]
                ] = replacement

            print(
                f"[AI REPLACEMENT] "
                f"SUCCESS provider={provider} "
                f"difficulty={difficulty} "
                f"count={len(new_questions)}"
            )

        except Exception as error:

            print(
                f"[AI REPLACEMENT] FAILED "
                f"difficulty={difficulty} "
                f"error={str(error)[:500]}"
            )

            # Do not replace a failed question with an invalid response.
            # The calling function can decide whether to stop generation.

            continue

    print(
        f"[AI REPLACEMENT] "
        f"Total validated replacements={len(replacements)}"
    )

    return replacements


# ============================================================
# APPLY REPLACEMENTS
# ============================================================

def apply_replacements(
    questions,
    replacements
):
    """
    Replace original questions by their 1-based index.

    Original question order is preserved.
    Only validated replacement questions are applied.
    """

    if not questions or not replacements:
        return questions

    updated_questions = list(questions)

    for original_index, replacement in replacements.items():

        try:
            index = int(original_index) - 1

        except (TypeError, ValueError):
            print(
                f"[AI REPLACEMENT] Invalid index: {original_index}"
            )
            continue

        if index < 0 or index >= len(updated_questions):

            print(
                f"[AI REPLACEMENT] Index out of range: "
                f"{original_index}"
            )

            continue

        if not isinstance(replacement, dict):
            continue

        updated_questions[index] = replacement

        print(
            f"[AI REPLACEMENT] Applied replacement "
            f"at question index={original_index}"
        )

    return updated_questions

# ============================================================
# MAIN QUESTION GENERATOR
# ============================================================

def generate_questions(topic, easy, medium, hard):
    """
    Generate exact-count quiz questions.

    AI provider: Ollama Cloud only

    Existing route contract:
        return questions, evaluations
    """

    # --------------------------------------------------------
    # INPUT VALIDATION
    # --------------------------------------------------------

    try:
        easy = int(easy)
        medium = int(medium)
        hard = int(hard)

    except (TypeError, ValueError):

        raise ValueError(
            "Easy, Medium and Hard counts must be valid integers."
        )

    if min(easy, medium, hard) < 0:

        raise ValueError(
            "Question counts cannot be negative."
        )

    total = easy + medium + hard

    if total <= 0:

        raise ValueError(
            "At least one question is required."
        )

    if total > 150:

        raise ValueError(
            "A quiz cannot contain more than 150 questions."
        )

    topic = str(topic or "").strip()

    if not topic:

        raise ValueError(
            "Quiz topic cannot be empty."
        )

    if not ollama_client:
        raise RuntimeError(
            "Ollama Cloud is not configured. "
            "Please set OLLAMA_API_KEY in the environment."
        )

    # --------------------------------------------------------
    # PREVENT OVERLAPPING GENERATION
    # --------------------------------------------------------

    print(
        f"[AI GENERATION] Waiting for generation slot; "
        f"requested={total}"
    )

    if not _GENERATION_LOCK.acquire(timeout=45):

        raise RuntimeError(
            "Another quiz is currently being generated. "
            "Please wait and try again."
        )

    started_at = time.monotonic()

    try:

        # ----------------------------------------------------
        # BATCH CONFIGURATION
        # ----------------------------------------------------

        try:

            batch_size = int(
                os.getenv(
                    "AI_QUIZ_BATCH_SIZE",
                    "5"
                )
            )

        except (TypeError, ValueError):

            batch_size = 5

        batch_size = max(
            1,
            min(5, batch_size)
        )

        try:

            max_completion_tokens = int(
                os.getenv(
                    "AI_MAX_COMPLETION_TOKENS",
                    "3000"
                )
            )

        except (TypeError, ValueError):

            max_completion_tokens = 3000

        max_completion_tokens = max(
            1000,
            min(6000, max_completion_tokens)
        )

        requirements = [
            ("Easy", easy),
            ("Medium", medium),
            ("Hard", hard),
        ]

        all_questions = []

        seen_questions = set()

        seen_question_order = []

        # ----------------------------------------------------
        # GENERATE EACH DIFFICULTY SEPARATELY
        # ----------------------------------------------------

        for difficulty, requested_count in requirements:

            remaining = requested_count

            while remaining > 0:

                batch_count = min(
                    batch_size,
                    remaining
                )

                # Include recently generated questions to reduce
                # repetition across different batches.

                previous_questions = "\n".join(
                    "- " + question[:180]
                    for question in seen_question_order[-30:]
                ) or "(none yet)"

                prompt = f"""
You are an expert educational quiz question generator.

TOPIC:
{topic}

REQUIRED DIFFICULTY:
{difficulty}

NUMBER OF QUESTIONS:
Exactly {batch_count}

PREVIOUSLY GENERATED QUESTIONS:
{previous_questions}

IMPORTANT INSTRUCTIONS:

1. Generate exactly {batch_count} questions.
2. Every question must belong to the topic.
3. Every question must genuinely match {difficulty} difficulty.
4. Do not repeat or paraphrase previously generated questions.
5. Each question must have exactly four options.
6. Exactly one option must be correct.
7. The correct_option must be A, B, C or D.
8. All four options must be distinct.
9. Avoid ambiguous questions.
10. Do not invent factual information.
11. Include a concise explanation for the correct answer.
12. Do not include markdown or commentary.
13. Return only valid JSON.
14. Do not include extra fields.

DIFFICULTY GUIDELINES:

Easy:
Basic definitions, fundamentals and direct conceptual questions.

Medium:
Conceptual understanding, application and moderate reasoning.

Hard:
Deeper reasoning, analysis, comparison, tracing,
calculations or multi-step problem solving.

REQUIRED JSON STRUCTURE:

{{
    "questions": [
        {{
            "question": "Question text",
            "option_a": "Option A",
            "option_b": "Option B",
            "option_c": "Option C",
            "option_d": "Option D",
            "correct_option": "A",
            "difficulty": "{difficulty}",
            "explanation": "Explanation"
        }}
    ]
}}

The questions array must contain exactly {batch_count} objects.
"""

                # ------------------------------------------------
                # VALIDATE THE AI RESPONSE BEFORE ACCEPTING IT
                # ------------------------------------------------

                def validate_batch(content):

                    generated = extract_questions_from_response(
                        content,
                        expected_count=batch_count,
                        difficulty=difficulty
                    )

                    if not validate_basic_question_structure(
                        generated,
                        batch_count
                    ):

                        raise ValueError(
                            f"Question structure validation failed "
                            f"for {difficulty} batch."
                        )

                    expected_distribution = (

                        batch_count if difficulty == "Easy" else 0,

                        batch_count if difficulty == "Medium" else 0,

                        batch_count if difficulty == "Hard" else 0,
                    )

                    if not check_difficulty_distribution(
                        generated,
                        *expected_distribution
                    ):

                        raise ValueError(
                            f"Difficulty mismatch in {difficulty} batch."
                        )

                    # Check duplicates inside the current batch.

                    normalized_questions = []

                    for item in generated:

                        normalized = re.sub(
                            r"\s+",
                            " ",
                            str(
                                item.get("question", "")
                            ).strip()
                        ).casefold()

                        normalized_questions.append(
                            normalized
                        )

                    if len(set(normalized_questions)) != len(
                        normalized_questions
                    ):

                        raise ValueError(
                            "Duplicate questions detected inside the batch."
                        )

                    # Check duplicates against previous successful batches.

                    for normalized in normalized_questions:

                        if normalized in seen_questions:

                            raise ValueError(
                                "Question duplicates an earlier "
                                "successful batch."
                            )

                    return generated

                # ------------------------------------------------
                # CALL OLLAMA CLOUD ONLY
                # ------------------------------------------------

                batch, provider = _request_with_ollama(
                    prompt=prompt,
                    validator=validate_batch,
                    operation=f"{difficulty} batch generation",
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "quiz_question_batch",
                            "strict": True,
                            "schema": build_quiz_batch_schema(
                                batch_count,
                                difficulty
                            ),
                        },
                    },
                    max_completion_tokens=max_completion_tokens,
                    temperature=0.1,
                )

                # ------------------------------------------------
                # SAVE VALIDATED BATCH IN MEMORY
                # ------------------------------------------------

                for item in batch:

                    normalized_question = re.sub(
                        r"\s+",
                        " ",
                        str(
                            item.get("question", "")
                        ).strip()
                    ).casefold()

                    seen_questions.add(
                        normalized_question
                    )

                    seen_question_order.append(
                        normalized_question
                    )

                all_questions.extend(batch)

                remaining -= batch_count

                print(
                    f"[AI GENERATION] BATCH COMPLETE "
                    f"provider={provider} "
                    f"difficulty={difficulty} "
                    f"count={len(batch)} "
                    f"remaining_in_level={remaining} "
                    f"total_completed={len(all_questions)}/{total}"
                )

        # ----------------------------------------------------
        # FINAL VALIDATION
        # ----------------------------------------------------

        if len(all_questions) != total:

            raise RuntimeError(
                f"Generated {len(all_questions)} questions, "
                f"but expected exactly {total}."
            )

        if not validate_basic_question_structure(
            all_questions,
            total
        ):

            raise RuntimeError(
                "Final quiz failed question-structure validation."
            )

        if not check_difficulty_distribution(
            all_questions,
            easy,
            medium,
            hard
        ):

            raise RuntimeError(
                "Final quiz failed exact difficulty-distribution validation."
            )

        elapsed = time.monotonic() - started_at

        print(
            f"[AI GENERATION] COMPLETE "
            f"total={len(all_questions)} "
            f"easy={easy} "
            f"medium={medium} "
            f"hard={hard} "
            f"elapsed={elapsed:.1f}s"
        )

        # Preserve the existing Flask route contract.
        # Extra evaluation calls are disabled to conserve free API quota.

        return all_questions, []

    except Exception as error:

        print(
            f"[AI GENERATION FAILED] "
            f"type={type(error).__name__} "
            f"error={str(error)[:700]}"
        )

        # Do not return an incomplete quiz as successful.
        raise

    finally:

        _GENERATION_LOCK.release()