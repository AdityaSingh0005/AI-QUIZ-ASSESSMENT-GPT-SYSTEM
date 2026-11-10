"""
Gemini-only AI Question Generator
AI Quiz Assessment GPT System

Primary and only provider:
    Google Gemini API

Compatible public functions:
    generate_questions(topic, easy_count, medium_count, hard_count)
    evaluate_question_difficulties(questions)
    generate_replacement_questions(...)
    apply_replacements(...)
    extract_questions_from_response(...)

Environment variables:
    GEMINI_API_KEY
    GEMINI_MODEL=gemini-3.8-flash
    AI_QUIZ_BATCH_SIZE=5
    AI_MAX_COMPLETION_TOKENS=4000
    AI_GENERATION_LOCK_WAIT=45
    GEMINI_TIMEOUT_SECONDS=150
    GEMINI_MAX_RETRIES=4

Ollama is completely removed.
"""

import json
import os
import re
import time
import random
import threading

from collections import Counter
from google import genai


# ============================================================
# CONFIGURATION
# ============================================================

GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY") or ""
).strip()

GEMINI_MODEL = (
    os.getenv("GEMINI_MODEL") or "gemini-3.8-flash"
).strip()

BATCH_SIZE = max(
    1,
    min(int(os.getenv("AI_QUIZ_BATCH_SIZE", "5")), 10)
)

MAX_OUTPUT_TOKENS = max(
    1000,
    min(
        int(os.getenv("AI_MAX_COMPLETION_TOKENS", "4000")),
        8000
    )
)

GENERATION_LOCK_WAIT = max(
    5,
    int(os.getenv("AI_GENERATION_LOCK_WAIT", "45"))
)

GEMINI_TIMEOUT_SECONDS = max(
    30,
    int(os.getenv("GEMINI_TIMEOUT_SECONDS", "150"))
)

GEMINI_MAX_RETRIES = max(
    1,
    min(int(os.getenv("GEMINI_MAX_RETRIES", "4")), 6)
)

RETRY_DELAYS = (2, 5, 10, 20)

RETRYABLE_STATUS_CODES = {
    408,
    409,
    429,
    500,
    502,
    503,
    504
}


# ============================================================
# GEMINI CLIENT
# ============================================================

gemini_client = None

if GEMINI_API_KEY:

    gemini_client = genai.Client(
        api_key=GEMINI_API_KEY,
        http_options={
            "timeout": GEMINI_TIMEOUT_SECONDS * 1000
        }
    )


_GENERATION_LOCK = threading.Lock()


# ============================================================
# JSON SCHEMAS
# ============================================================

def build_quiz_batch_schema(count):

    return {
        "type": "object",

        "properties": {

            "questions": {

                "type": "array",

                "minItems": count,
                "maxItems": count,

                "items": {

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

                            "enum": [
                                "A",
                                "B",
                                "C",
                                "D"
                            ]
                        },

                        "difficulty": {

                            "type": "string",

                            "enum": [
                                "Easy",
                                "Medium",
                                "Hard"
                            ]
                        },

                        "explanation": {
                            "type": "string"
                        }

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

                    "additionalProperties": False
                }
            }
        },

        "required": [
            "questions"
        ],

        "additionalProperties": False
    }


def build_replacement_schema(count):

    return build_quiz_batch_schema(count)


def build_evaluation_schema(count):

    return {

        "type": "object",

        "properties": {

            "evaluations": {

                "type": "array",

                "minItems": count,
                "maxItems": count,

                "items": {

                    "type": "object",

                    "properties": {

                        "question_index": {
                            "type": "integer"
                        },

                        "evaluated_difficulty": {

                            "type": "string",

                            "enum": [
                                "Easy",
                                "Medium",
                                "Hard"
                            ]
                        },

                        "confidence": {
                            "type": "number"
                        },

                        "reason": {
                            "type": "string"
                        }

                    },

                    "required": [
                        "question_index",
                        "evaluated_difficulty",
                        "confidence",
                        "reason"
                    ],

                    "additionalProperties": False
                }
            }
        },

        "required": [
            "evaluations"
        ],

        "additionalProperties": False
    }


# ============================================================
# DIFFICULTY NORMALIZATION
# ============================================================

def normalize_difficulty(value):

    value = str(value or "").strip().lower()

    mapping = {

        "easy": "Easy",
        "beginner": "Easy",
        "simple": "Easy",

        "medium": "Medium",
        "moderate": "Medium",
        "intermediate": "Medium",

        "hard": "Hard",
        "difficult": "Hard",
        "advanced": "Hard"
    }

    return mapping.get(value, "")


# ============================================================
# ERROR HELPERS
# ============================================================

def _status_code(exc):

    response = getattr(exc, "response", None)

    candidates = [

        getattr(response, "status_code", None),

        getattr(exc, "status_code", None),

        getattr(exc, "code", None)
    ]

    for value in candidates:

        if value is None:
            continue

        try:

            return int(value)

        except (TypeError, ValueError):

            match = re.search(
                r"\b([1-5][0-9]{2})\b",
                str(value)
            )

            if match:
                return int(match.group(1))

    return None


def _format_provider_error(provider, exc):

    status = _status_code(exc)

    status_text = (
        f" (HTTP {status})"
        if status
        else ""
    )

    message = str(exc).replace(
        "\n",
        " "
    ).strip()

    if len(message) > 700:

        message = message[:700] + "..."

    return f"{provider}{status_text}: {message}"


# ============================================================
# JSON EXTRACTION
# ============================================================

def _extract_json_text(content):

    if isinstance(content, (dict, list)):
        return content

    if not isinstance(content, str):

        raise ValueError(
            "Gemini returned an empty or unsupported response."
        )

    text = content.strip()

    if not text:

        raise ValueError(
            "Gemini returned an empty response."
        )

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
    )

    try:

        return json.loads(text)

    except json.JSONDecodeError:

        candidates = []

        start_obj = text.find("{")
        end_obj = text.rfind("}")

        start_arr = text.find("[")
        end_arr = text.rfind("]")

        if start_obj >= 0 and end_obj > start_obj:

            candidates.append(
                text[start_obj:end_obj + 1]
            )

        if start_arr >= 0 and end_arr > start_arr:

            candidates.append(
                text[start_arr:end_arr + 1]
            )

        for candidate in candidates:

            try:

                return json.loads(candidate)

            except json.JSONDecodeError:

                continue

    raise ValueError(
        "Gemini response was not valid JSON."
    )


def clean_json_content(content):

    return _extract_json_text(content)


# ============================================================
# QUESTION EXTRACTION
# ============================================================

def _unwrap_questions(payload):

    if isinstance(payload, list):

        return payload

    if isinstance(payload, dict):

        questions = payload.get("questions")

        if isinstance(questions, list):

            return questions

    raise ValueError(
        "JSON response must contain a 'questions' array."
    )


# ============================================================
# QUESTION VALIDATION
# ============================================================

def validate_basic_question_structure(
    questions,
    expected_count=None
):

    if not isinstance(questions, list):

        raise ValueError(
            "Questions must be a list."
        )

    if (
        expected_count is not None
        and len(questions) != expected_count
    ):

        raise ValueError(
            f"Expected {expected_count} questions, "
            f"received {len(questions)}."
        )

    seen_questions = set()

    for index, item in enumerate(
        questions,
        start=1
    ):

        if not isinstance(item, dict):

            raise ValueError(
                f"Question {index} is not a JSON object."
            )

        required = (

            "question",
            "option_a",
            "option_b",
            "option_c",
            "option_d",
            "correct_option",
            "difficulty",
            "explanation"

        )

        for key in required:

            value = item.get(key)

            if (
                value is None
                or not str(value).strip()
            ):

                raise ValueError(
                    f"Question {index}: '{key}' is missing."
                )

        item["question"] = str(
            item["question"]
        ).strip()

        for key in (
            "option_a",
            "option_b",
            "option_c",
            "option_d"
        ):

            item[key] = str(
                item[key]
            ).strip()

        item["correct_option"] = str(
            item["correct_option"]
        ).strip().upper()

        if item["correct_option"] not in (
            "A",
            "B",
            "C",
            "D"
        ):

            raise ValueError(
                f"Question {index}: correct_option "
                "must be A, B, C or D."
            )

        item["difficulty"] = normalize_difficulty(
            item["difficulty"]
        )

        if not item["difficulty"]:

            raise ValueError(
                f"Question {index}: invalid difficulty."
            )

        item["explanation"] = str(
            item["explanation"]
        ).strip()

        options = [

            item["option_a"].casefold(),

            item["option_b"].casefold(),

            item["option_c"].casefold(),

            item["option_d"].casefold()

        ]

        if len(set(options)) != 4:

            raise ValueError(
                f"Question {index}: all four options "
                "must be unique."
            )

        normalized_question = re.sub(
            r"\s+",
            " ",
            item["question"].casefold()
        ).strip()

        if normalized_question in seen_questions:

            raise ValueError(
                f"Duplicate question detected at question {index}."
            )

        seen_questions.add(
            normalized_question
        )

    return questions


def trim_extra_questions(
    questions,
    expected_count
):

    return questions[:expected_count]


def check_difficulty_distribution(
    questions,
    easy_count,
    medium_count,
    hard_count
):

    actual = Counter(

        normalize_difficulty(
            q.get("difficulty")
        )

        for q in questions

    )

    expected = {

        "Easy": int(easy_count),

        "Medium": int(medium_count),

        "Hard": int(hard_count)

    }

    if any(

        actual.get(level, 0) != count

        for level, count in expected.items()

    ):

        raise ValueError(

            "Difficulty distribution mismatch. "

            f"Expected {expected}, received "

            f"{{'Easy': {actual.get('Easy', 0)}, "

            f"'Medium': {actual.get('Medium', 0)}, "

            f"'Hard': {actual.get('Hard', 0)}}}."

        )

    return True


def extract_questions_from_response(
    response,
    expected_count=None
):

    payload = _extract_json_text(response)

    questions = _unwrap_questions(payload)

    if (

        expected_count is not None

        and len(questions) > expected_count

    ):

        questions = trim_extra_questions(
            questions,
            expected_count
        )

    return validate_basic_question_structure(
        questions,
        expected_count
    )


def _normalised_question_key(question):

    return re.sub(
        r"\s+",
        " ",
        str(question or "").casefold()
    ).strip()


# ============================================================
# GEMINI API - RETRY SYSTEM
# ============================================================

def _gemini_json_content(
    prompt,
    response_schema=None,
    temperature=0.2
):

    if gemini_client is None:

        raise RuntimeError(
            "GEMINI_API_KEY is not configured."
        )

    config = {

        "temperature": temperature,

        "max_output_tokens": MAX_OUTPUT_TOKENS,

        "response_mime_type": "application/json"

    }

    if response_schema is not None:

        config["response_json_schema"] = response_schema

    last_error = None

    for attempt in range(GEMINI_MAX_RETRIES):

        try:

            print(
                f"[GEMINI] Requesting model {GEMINI_MODEL}"
            )

            response = gemini_client.models.generate_content(

                model=GEMINI_MODEL,

                contents=prompt,

                config=config

            )

            content = getattr(
                response,
                "text",
                None
            )

            if (
                not content
                or not str(content).strip()
            ):

                prompt_feedback = getattr(
                    response,
                    "prompt_feedback",
                    None
                )

                candidates = getattr(
                    response,
                    "candidates",
                    None
                )

                finish_reason = None

                if candidates:

                    finish_reason = getattr(
                        candidates[0],
                        "finish_reason",
                        None
                    )

                raise ValueError(

                    "Gemini returned empty content "

                    f"(finish_reason={finish_reason}, "

                    f"prompt_feedback={prompt_feedback})."

                )

            return content

        except Exception as exc:

            last_error = exc

            status = _status_code(exc)

            # Retry temporary HTTP errors only.
            # Do not retry invalid keys, invalid models or bad requests.

            if status is not None:

                if status not in RETRYABLE_STATUS_CODES:

                    break

            # Unknown connection-level exceptions can be temporary.
            # Retry them within the configured retry limit.

            if attempt >= GEMINI_MAX_RETRIES - 1:

                break

            delay = RETRY_DELAYS[
                min(
                    attempt,
                    len(RETRY_DELAYS) - 1
                )
            ]

            delay += random.uniform(0, 1)

            print(

                "[GEMINI RETRY] "

                f"Attempt {attempt + 1}/"

                f"{GEMINI_MAX_RETRIES} failed. "

                f"HTTP status: {status}. "

                f"Retrying in {delay:.1f} seconds."

            )

            time.sleep(delay)

    raise RuntimeError(

        "Gemini API failed after "

        f"{attempt + 1} attempt(s): {last_error}"

    ) from last_error


# ============================================================
# GEMINI ONLY REQUEST HANDLER
# ============================================================

def _request_with_fallback(
    prompt,
    validator=None,
    response_schema=None,
    temperature=0.2
):

    """
    Backward-compatible function name.

    Gemini is the only AI provider.
    No Ollama or other provider is called.
    """

    if gemini_client is None:

        raise RuntimeError(
            "Gemini API is not configured. "
            "Set GEMINI_API_KEY in Render."
        )

    raw = _gemini_json_content(

        prompt,

        response_schema=response_schema,

        temperature=temperature

    )

    parsed = _extract_json_text(raw)

    if validator is not None:

        validator(parsed)

    return parsed


# ============================================================
# PROMPT CONSTRUCTION
# ============================================================

def _question_prompt(
    topic,
    count,
    difficulty,
    previous_questions
):

    previous_text = ""

    if previous_questions:

        previous_text = (

            "\nAvoid creating questions similar "
            "to these existing questions:\n"

        )

        previous_text += "\n".join(

            f"- {str(q)[:350]}"

            for q in previous_questions[-30:]

        )

    return f"""

Create exactly {count} original multiple-choice questions.

TOPIC:
{topic}

REQUIRED DIFFICULTY:
{difficulty}

STRICT RULES:

1. Return exactly {count} questions.
2. Every question must have exactly four options.
3. Options must be A, B, C and D.
4. Exactly one option must be correct.
5. All four options must be unique.
6. correct_option must be A, B, C or D.
7. difficulty must be exactly "{difficulty}".
8. Provide a clear and factually correct explanation.
9. Do not repeat earlier questions.
10. Avoid ambiguous or subjective questions.
11. Keep questions suitable for university-level assessment.
12. Return valid JSON only.
13. Use a top-level "questions" array.

Every question must contain:

question
option_a
option_b
option_c
option_d
correct_option
difficulty
explanation

{previous_text}

""".strip()


# ============================================================
# MAIN QUESTION GENERATION
# ============================================================

def generate_questions(
    topic,
    easy_count,
    medium_count,
    hard_count
):

    """
    Generate questions using Gemini only.

    Return format:
        (all_questions, [])
    """

    topic = str(
        topic or ""
    ).strip()

    try:

        easy_count = int(easy_count)

        medium_count = int(medium_count)

        hard_count = int(hard_count)

    except (TypeError, ValueError):

        raise ValueError(
            "Easy, Medium and Hard counts "
            "must be whole numbers."
        )

    counts = (

        easy_count,

        medium_count,

        hard_count

    )

    if any(
        count < 0
        for count in counts
    ):

        raise ValueError(
            "Question counts cannot be negative."
        )

    if any(
        count > 150
        for count in counts
    ):

        raise ValueError(
            "Maximum 150 questions per difficulty is allowed."
        )

    if sum(counts) <= 0:

        raise ValueError(
            "At least one question must be requested."
        )

    if not topic:

        raise ValueError(
            "Please enter a topic or prompt."
        )

    if gemini_client is None:

        raise RuntimeError(
            "Gemini API is not configured. "
            "Set GEMINI_API_KEY."
        )

    if not _GENERATION_LOCK.acquire(
        timeout=GENERATION_LOCK_WAIT
    ):

        raise RuntimeError(
            "Another quiz is currently being generated. "
            "Please try again shortly."
        )

    try:

        all_questions = []

        previous_keys = set()

        difficulty_batches = (

            ("Easy", easy_count),

            ("Medium", medium_count),

            ("Hard", hard_count)

        )

        for difficulty, requested_count in difficulty_batches:

            remaining = requested_count

            while remaining > 0:

                batch_count = min(
                    BATCH_SIZE,
                    remaining
                )

                previous_questions = [

                    q["question"]

                    for q in all_questions

                ]

                prompt = _question_prompt(

                    topic=topic,

                    count=batch_count,

                    difficulty=difficulty,

                    previous_questions=previous_questions

                )

                def validate_batch(payload):

                    batch = validate_basic_question_structure(

                        _unwrap_questions(payload),

                        expected_count=batch_count

                    )

                    wrong_difficulty = [

                        q

                        for q in batch

                        if normalize_difficulty(
                            q.get("difficulty")
                        ) != difficulty

                    ]

                    if wrong_difficulty:

                        raise ValueError(

                            f"All {batch_count} questions "

                            f"must be {difficulty}."

                        )

                    local_keys = set()

                    for question in batch:

                        key = _normalised_question_key(

                            question["question"]

                        )

                        if (

                            key in previous_keys

                            or key in local_keys

                        ):

                            raise ValueError(

                                "Duplicate question detected "

                                "across generation batches."

                            )

                        local_keys.add(key)

                    return batch

                payload = _request_with_fallback(

                    prompt=prompt,

                    validator=validate_batch,

                    response_schema=build_quiz_batch_schema(
                        batch_count
                    ),

                    temperature=0.2

                )

                batch = validate_batch(payload)

                for question in batch:

                    previous_keys.add(

                        _normalised_question_key(

                            question["question"]

                        )

                    )

                all_questions.extend(batch)

                remaining -= batch_count

        validate_basic_question_structure(

            all_questions,

            expected_count=sum(counts)

        )

        check_difficulty_distribution(

            all_questions,

            easy_count,

            medium_count,

            hard_count

        )

        return all_questions, []

    finally:

        _GENERATION_LOCK.release()


# ============================================================
# DIFFICULTY EVALUATION
# ============================================================

def evaluate_question_difficulties(questions):

    if not questions:

        return []

    compact_questions = [

        {

            "question_index": index,

            "question": item.get(
                "question",
                ""
            ),

            "options": [

                item.get("option_a", ""),

                item.get("option_b", ""),

                item.get("option_c", ""),

                item.get("option_d", "")

            ],

            "correct_option": item.get(
                "correct_option",
                ""
            ),

            "declared_difficulty": item.get(
                "difficulty",
                ""
            )

        }

        for index, item in enumerate(
            questions,
            start=1
        )

    ]

    prompt = (

        "Evaluate the academic difficulty of each "
        "multiple-choice question. "

        "Use Easy, Medium or Hard. "

        "Do not change any question. "

        "Return JSON with an evaluations array. "

        "Each item must contain question_index, "
        "evaluated_difficulty, confidence from 0 to 1, "
        "and a short reason.\n\n"

        + json.dumps(
            {"questions": compact_questions},
            ensure_ascii=False
        )

    )

    def validator(payload):

        evaluations = (

            payload.get("evaluations")

            if isinstance(payload, dict)

            else None

        )

        if (

            not isinstance(evaluations, list)

            or len(evaluations) != len(questions)

        ):

            raise ValueError(
                "Evaluation count does not match question count."
            )

        for item in evaluations:

            if (

                not isinstance(item, dict)

                or normalize_difficulty(
                    item.get("evaluated_difficulty")
                ) not in (
                    "Easy",
                    "Medium",
                    "Hard"
                )

            ):

                raise ValueError(
                    "Invalid evaluated difficulty."
                )

        return evaluations

    payload = _request_with_fallback(

        prompt,

        validator=validator,

        response_schema=build_evaluation_schema(
            len(questions)
        ),

        temperature=0.1

    )

    evaluations = validator(payload)

    for item in evaluations:

        item["evaluated_difficulty"] = normalize_difficulty(

            item["evaluated_difficulty"]

        )

        try:

            item["confidence"] = max(

                0.0,

                min(
                    1.0,
                    float(item.get("confidence", 0))
                )

            )

        except (TypeError, ValueError):

            item["confidence"] = 0.0

        item["reason"] = str(

            item.get("reason") or ""

        ).strip()[:300]

    return evaluations


# ============================================================
# REPLACEMENT QUESTION GENERATION
# ============================================================

def generate_replacement_questions(
    topic,
    failed_questions,
    existing_questions=None
):

    failed_questions = list(
        failed_questions or []
    )

    existing_questions = list(
        existing_questions or []
    )

    if not failed_questions:

        return []

    replacements = []

    existing_keys = {

        _normalised_question_key(
            q.get("question")
        )

        for q in existing_questions

        if isinstance(q, dict)

    }

    groups = {

        level: [

            q

            for q in failed_questions

            if normalize_difficulty(

                q.get("evaluated_difficulty")
                or q.get("difficulty")

            ) == level

        ]

        for level in (
            "Easy",
            "Medium",
            "Hard"
        )

    }

    for difficulty, items in groups.items():

        remaining = len(items)

        while remaining > 0:

            batch_count = min(
                BATCH_SIZE,
                remaining
            )

            prompt = _question_prompt(

                topic=topic,

                count=batch_count,

                difficulty=difficulty,

                previous_questions=[

                    q.get("question", "")

                    for q in (
                        existing_questions + replacements
                    )

                ]

            )

            def validator(payload):

                batch = validate_basic_question_structure(

                    _unwrap_questions(payload),

                    expected_count=batch_count

                )

                local_keys = set()

                for question in batch:

                    if normalize_difficulty(

                        question.get("difficulty")

                    ) != difficulty:

                        raise ValueError(

                            f"Replacement questions "
                            f"must be {difficulty}."

                        )

                    key = _normalised_question_key(

                        question["question"]

                    )

                    if (

                        key in existing_keys

                        or key in local_keys

                    ):

                        raise ValueError(

                            "Replacement duplicates "
                            "an existing question."

                        )

                    local_keys.add(key)

                return batch

            payload = _request_with_fallback(

                prompt,

                validator=validator,

                response_schema=build_replacement_schema(
                    batch_count
                ),

                temperature=0.25

            )

            batch = validator(payload)

            for question in batch:

                existing_keys.add(

                    _normalised_question_key(

                        question["question"]

                    )

                )

            replacements.extend(batch)

            remaining -= batch_count

    return replacements


# ============================================================
# APPLY REPLACEMENTS
# ============================================================

def apply_replacements(
    questions,
    replacements,
    failed_indexes
):

    output = list(
        questions or []
    )

    replacements = iter(
        replacements or []
    )

    for index in failed_indexes or []:

        try:

            output[int(index)] = next(
                replacements
            )

        except (

            StopIteration,

            IndexError,

            TypeError,

            ValueError

        ):

            continue

    return output