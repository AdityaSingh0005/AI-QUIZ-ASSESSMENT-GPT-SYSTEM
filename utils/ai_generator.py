"""
Gemini-only AI Question Generator
AI Quiz Assessment GPT System

Features:
    - Gemini-only generation
    - Multiple Gemini model fallback
    - Two-pass model retry
    - HTTP 503 / 504 recovery
    - Batch-level validation recovery
    - Duplicate question detection
    - Exact difficulty distribution
    - JSON structured output
    - Compatible with existing teacher.py

Public functions:
    generate_questions()
    evaluate_question_difficulties()
    generate_replacement_questions()
    apply_replacements()
    extract_questions_from_response()

No Ollama.
No Groq.
No xAI.
No other provider.
"""

import json
import os
import re
import time
import random
import threading

from collections import Counter
from difflib import SequenceMatcher

from google import genai


# ============================================================
# CONFIGURATION
# ============================================================

GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY") or ""
).strip()


# Primary model
GEMINI_MODEL = (
    os.getenv("GEMINI_MODEL") or "gemini-3.8-flash"
).strip()


# Additional Gemini-only models
GEMINI_FALLBACK_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]


GEMINI_MODELS = list(dict.fromkeys([
    GEMINI_MODEL,
    *GEMINI_FALLBACK_MODELS
]))


# Questions per request
BATCH_SIZE = max(
    5,
    min(
        int(os.getenv("AI_QUIZ_BATCH_SIZE", "10")),
        20
    )
)


# Maximum generated tokens
MAX_OUTPUT_TOKENS = max(
    4000,
    min(
        int(os.getenv("AI_MAX_COMPLETION_TOKENS", "8000")),
        8000
    )
)


# API timeout
GEMINI_TIMEOUT_SECONDS = max(
    20,
    min(
        int(os.getenv("GEMINI_TIMEOUT_SECONDS", "45")),
        90
    )
)


# Maximum requests in each generation cycle
GEMINI_MAX_ATTEMPTS = max(
    3,
    min(
        int(os.getenv("GEMINI_MAX_RETRIES", "8")),
        12
    )
)


# Retry delays between cycles
RETRY_DELAYS = (
    3,
    8,
    15,
    25,
    40,
    60
)


# Validation retry
BATCH_VALIDATION_ATTEMPTS = 2


# Maximum complete quiz generation time
QUIZ_GENERATION_TIMEOUT_SECONDS = (
    18 * 60
)


# Generation lock
GENERATION_LOCK_WAIT = max(
    5,
    int(
        os.getenv(
            "AI_GENERATION_LOCK_WAIT",
            "45"
        )
    )
)


RETRYABLE_STATUS_CODES = {
    408,
    409,
    429,
    500,
    502,
    503,
    504
}


_GENERATION_LOCK = threading.Lock()


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

    value = str(
        value or ""
    ).strip().lower()

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

    return mapping.get(
        value,
        ""
    )


# ============================================================
# ERROR HELPERS
# ============================================================

def _status_code(exc):

    response = getattr(
        exc,
        "response",
        None
    )

    candidates = [

        getattr(
            response,
            "status_code",
            None
        ),

        getattr(
            exc,
            "status_code",
            None
        ),

        getattr(
            exc,
            "code",
            None
        )

    ]

    for value in candidates:

        if value is None:
            continue

        try:

            return int(value)

        except (
            TypeError,
            ValueError
        ):

            match = re.search(
                r"\b([1-5][0-9]{2})\b",
                str(value)
            )

            if match:

                return int(
                    match.group(1)
                )

    message = str(exc)

    match = re.search(
        r"\b(408|409|429|500|502|503|504)\b",
        message
    )

    if match:

        return int(
            match.group(1)
        )

    return None


def _format_provider_error(
    model,
    exc
):

    status = _status_code(exc)

    message = str(
        exc
    ).replace(
        "\n",
        " "
    ).strip()

    if len(message) > 450:

        message = (
            message[:450]
            + "..."
        )

    if status:

        return (
            f"{model} (HTTP {status}): "
            f"{message}"
        )

    return (
        f"{model}: {message}"
    )


def _remaining_time(deadline):

    if deadline is None:
        return None

    return (
        deadline - time.monotonic()
    )


# ============================================================
# JSON EXTRACTION
# ============================================================

def _extract_json_text(content):

    if isinstance(
        content,
        (dict, list)
    ):

        return content

    if not isinstance(
        content,
        str
    ):

        raise ValueError(
            "Gemini returned an unsupported response."
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

        pass

    start = text.find("{")

    end = text.rfind("}")

    if (
        start >= 0
        and end > start
    ):

        try:

            return json.loads(
                text[start:end + 1]
            )

        except json.JSONDecodeError:

            pass

    start = text.find("[")

    end = text.rfind("]")

    if (
        start >= 0
        and end > start
    ):

        try:

            return json.loads(
                text[start:end + 1]
            )

        except json.JSONDecodeError:

            pass

    raise ValueError(
        "Gemini response is not valid JSON."
    )


def clean_json_content(content):

    return _extract_json_text(
        content
    )


# ============================================================
# QUESTION EXTRACTION
# ============================================================

def _unwrap_questions(payload):

    if isinstance(payload, list):

        return payload

    if isinstance(payload, dict):

        questions = payload.get(
            "questions"
        )

        if isinstance(
            questions,
            list
        ):

            return questions

    raise ValueError(
        "Response must contain a questions array."
    )


# ============================================================
# DUPLICATE DETECTION
# ============================================================

def _normalised_question_key(question):

    question = str(
        question or ""
    ).casefold().strip()

    question = re.sub(
        r"[^\w\s]",
        " ",
        question
    )

    question = re.sub(
        r"\s+",
        " ",
        question
    )

    return question.strip()


def _is_duplicate_question(
    question,
    existing_questions,
    similarity_threshold=0.94
):

    new_key = _normalised_question_key(
        question
    )

    if not new_key:
        return True

    for existing in existing_questions:

        old_key = _normalised_question_key(
            existing
        )

        if not old_key:
            continue

        if new_key == old_key:
            return True

        similarity = SequenceMatcher(
            None,
            new_key,
            old_key
        ).ratio()

        if similarity >= similarity_threshold:
            return True

    return False


# ============================================================
# BASIC QUESTION VALIDATION
# ============================================================

def validate_basic_question_structure(
    questions,
    expected_count=None
):

    if not isinstance(
        questions,
        list
    ):

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

    seen_questions = []

    required_fields = (

        "question",
        "option_a",
        "option_b",
        "option_c",
        "option_d",
        "correct_option",
        "difficulty",
        "explanation"

    )

    for index, item in enumerate(
        questions,
        start=1
    ):

        if not isinstance(
            item,
            dict
        ):

            raise ValueError(
                f"Question {index} is not an object."
            )

        for key in required_fields:

            value = item.get(key)

            if (
                value is None
                or not str(value).strip()
            ):

                raise ValueError(
                    f"Question {index}: missing field '{key}'."
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
                f"Question {index}: invalid correct option."
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
                f"Question {index}: options must be unique."
            )

        if _is_duplicate_question(
            item["question"],
            seen_questions
        ):

            raise ValueError(
                f"Duplicate question inside batch at question {index}."
            )

        seen_questions.append(
            item["question"]
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
            question.get("difficulty")
        )

        for question in questions

    )

    expected = {

        "Easy": int(easy_count),

        "Medium": int(medium_count),

        "Hard": int(hard_count)

    }

    for level, count in expected.items():

        if actual.get(level, 0) != count:

            raise ValueError(

                f"Difficulty distribution mismatch. "
                f"Expected {expected}, "
                f"received {dict(actual)}."

            )

    return True


def extract_questions_from_response(
    response,
    expected_count=None
):

    payload = _extract_json_text(
        response
    )

    questions = _unwrap_questions(
        payload
    )

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


# ============================================================
# GEMINI API REQUEST
# MULTI MODEL + TWO PASS RETRY
# ============================================================

def _gemini_json_content(
    prompt,
    response_schema=None,
    temperature=0.2,
    deadline=None
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

    errors = []

    last_error = None

    unavailable_models = set()

    total_attempts = 0

    # First pass tries every model.
    # Second pass retries available models.
    for cycle in range(2):

        available_models = [

            model

            for model in GEMINI_MODELS

            if model not in unavailable_models

        ]

        if not available_models:
            break

        for model in available_models:

            if total_attempts >= GEMINI_MAX_ATTEMPTS:
                break

            remaining = _remaining_time(
                deadline
            )

            if (
                remaining is not None
                and remaining <= 0
            ):

                raise TimeoutError(
                    "Quiz generation deadline exceeded."
                )

            total_attempts += 1

            try:

                print(
                    "\n[GEMINI REQUEST]"
                )

                print(
                    f"Cycle: {cycle + 1}/2"
                )

                print(
                    f"Attempt: {total_attempts}/"
                    f"{GEMINI_MAX_ATTEMPTS}"
                )

                print(
                    f"Model: {model}"
                )

                response = gemini_client.models.generate_content(

                    model=model,

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

                    raise ValueError(
                        f"Empty response from {model}."
                    )

                print(
                    f"[GEMINI SUCCESS] {model}"
                )

                return content

            except Exception as exc:

                last_error = exc

                status = _status_code(
                    exc
                )

                error_message = _format_provider_error(
                    model,
                    exc
                )

                errors.append(
                    error_message
                )

                print(
                    f"[GEMINI ERROR] {error_message}"
                )

                # Authentication failure:
                # switching models will not fix it.
                if status == 401:

                    raise RuntimeError(
                        "Gemini authentication failed. "
                        + error_message
                    ) from exc

                # Model not found.
                if status == 404:

                    unavailable_models.add(
                        model
                    )

                    continue

                # Access denied for this model.
                if status == 403:

                    unavailable_models.add(
                        model
                    )

                    continue

                # Invalid request.
                if status == 400:

                    raise RuntimeError(
                        "Gemini rejected the request: "
                        + error_message
                    ) from exc

                # Non-retryable error.
                if (
                    status is not None
                    and status not in RETRYABLE_STATUS_CODES
                ):

                    raise RuntimeError(
                        "Non-retryable Gemini error: "
                        + error_message
                    ) from exc

        if total_attempts >= GEMINI_MAX_ATTEMPTS:
            break

        if cycle == 0:

            remaining = _remaining_time(
                deadline
            )

            if (
                remaining is not None
                and remaining <= 0
            ):
                break

            delay = 8 + random.uniform(
                1,
                3
            )

            if remaining is not None:

                delay = min(
                    delay,
                    remaining
                )

            print(
                f"[GEMINI RETRY CYCLE] "
                f"Waiting {delay:.1f}s."
            )

            if delay > 0:
                time.sleep(delay)

    error_summary = " | ".join(
        errors[-8:]
    )

    raise RuntimeError(

        f"Gemini failed after {total_attempts} attempts. "

        f"Last error: {last_error}. "

        f"Recent errors: {error_summary}"

    ) from last_error


# ============================================================
# GEMINI REQUEST HANDLER
# ============================================================

def _request_with_fallback(
    prompt,
    validator=None,
    response_schema=None,
    temperature=0.2,
    deadline=None
):

    """
    Backward-compatible function name.

    Gemini is the only provider.
    """

    if gemini_client is None:

        raise RuntimeError(
            "Gemini API is not configured."
        )

    raw = _gemini_json_content(

        prompt=prompt,

        response_schema=response_schema,

        temperature=temperature,

        deadline=deadline

    )

    parsed = _extract_json_text(
        raw
    )

    if validator is not None:

        validator(parsed)

    return parsed


# ============================================================
# QUESTION PROMPT
# ============================================================

def _question_prompt(
    topic,
    count,
    difficulty,
    previous_questions=None,
    rejected_questions=None
):

    previous_questions = list(
        previous_questions or []
    )

    rejected_questions = list(
        rejected_questions or []
    )

    previous_text = ""

    if previous_questions:

        previous_text = (

            "\nALREADY GENERATED QUESTIONS:\n"

        )

        previous_text += "\n".join(

            f"- {str(question)[:250]}"

            for question in previous_questions

        )

    rejected_text = ""

    if rejected_questions:

        rejected_text = (

            "\nREJECTED QUESTIONS:\n"

        )

        rejected_text += "\n".join(

            f"- {str(question)[:250]}"

            for question in rejected_questions

        )

    return f"""

You are an expert university examination question setter.

Generate exactly {count} original multiple-choice questions.

TOPIC:
{topic}

REQUIRED DIFFICULTY:
{difficulty}

STRICT RULES:

1. Generate exactly {count} questions.
2. Every question must test a different knowledge point.
3. Do not repeat existing questions.
4. Do not rephrase existing questions.
5. Do not generate trivial wording variations.
6. Every question must contain four options.
7. All four options must be different.
8. Exactly one answer must be correct.
9. correct_option must be A, B, C or D.
10. difficulty must be exactly "{difficulty}".
11. Give a factual explanation.
12. Avoid ambiguous questions.
13. Avoid subjective questions.
14. Questions must be appropriate for university-level assessment.
15. Use clear English.
16. Return JSON only.
17. Do not return Markdown.

QUESTION FIELDS:

question
option_a
option_b
option_c
option_d
correct_option
difficulty
explanation

{previous_text}

{rejected_text}

Return exactly {count} questions inside a top-level questions array.

""".strip()


# ============================================================
# VALIDATED BATCH GENERATION
# ============================================================

def _generate_validated_batch(
    prompt,
    validator,
    count,
    difficulty,
    deadline=None
):

    last_error = None

    current_prompt = prompt

    for attempt in range(
        1,
        BATCH_VALIDATION_ATTEMPTS + 1
    ):

        remaining = _remaining_time(
            deadline
        )

        if (
            remaining is not None
            and remaining <= 0
        ):

            raise TimeoutError(
                "Quiz generation time limit exceeded."
            )

        try:

            print(
                f"[BATCH] {difficulty} | "
                f"{count} questions | "
                f"Validation attempt "
                f"{attempt}/{BATCH_VALIDATION_ATTEMPTS}"
            )

            payload = _request_with_fallback(

                prompt=current_prompt,

                validator=None,

                response_schema=build_quiz_batch_schema(
                    count
                ),

                temperature=0.2,

                deadline=deadline

            )

            # Validator runs exactly once.
            batch = validator(
                payload
            )

            print(
                f"[BATCH SUCCESS] {difficulty} | "
                f"{len(batch)} questions"
            )

            return batch

        except ValueError as exc:

            last_error = exc

            print(
                f"[BATCH VALIDATION ERROR] {exc}"
            )

            if attempt >= BATCH_VALIDATION_ATTEMPTS:
                break

            current_prompt = (

                prompt

                + "\n\nPREVIOUS RESPONSE FAILED VALIDATION.\n"

                + f"Reason: {str(exc)[:400]}\n"

                + "Generate a completely different batch.\n"

                + "Do not reuse any previous question.\n"

                + "Check all questions for uniqueness."

            )

        except TimeoutError:

            raise

        except RuntimeError:

            raise

        except Exception as exc:

            raise RuntimeError(
                f"Unexpected batch generation error: {exc}"
            ) from exc

    raise ValueError(

        f"Unable to generate a valid {difficulty} batch. "

        f"Last validation error: {last_error}"

    )


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
    Generate the complete quiz.

    Returns:
        (questions, evaluations)

    Existing teacher.py compatibility:
        questions, evaluations = generate_questions(...)
    """

    topic = str(
        topic or ""
    ).strip()

    try:

        easy_count = int(
            easy_count
        )

        medium_count = int(
            medium_count
        )

        hard_count = int(
            hard_count
        )

    except (
        TypeError,
        ValueError
    ):

        raise ValueError(
            "Question counts must be whole numbers."
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

    total_count = sum(
        counts
    )

    if total_count <= 0:

        raise ValueError(
            "At least one question is required."
        )

    if not topic:

        raise ValueError(
            "Topic cannot be empty."
        )

    if gemini_client is None:

        raise RuntimeError(
            "GEMINI_API_KEY is not configured."
        )

    if not _GENERATION_LOCK.acquire(
        timeout=GENERATION_LOCK_WAIT
    ):

        raise RuntimeError(
            "Another quiz is being generated. "
            "Please try again shortly."
        )

    started_at = time.monotonic()

    deadline = (
        started_at
        + QUIZ_GENERATION_TIMEOUT_SECONDS
    )

    try:

        all_questions = []

        difficulty_batches = (

            ("Easy", easy_count),

            ("Medium", medium_count),

            ("Hard", hard_count)

        )

        total_batches = sum(

            (
                count + BATCH_SIZE - 1
            ) // BATCH_SIZE

            for _, count in difficulty_batches

        )

        completed_batches = 0

        print("\n" + "=" * 65)

        print(
            "[QUIZ GENERATION STARTED]"
        )

        print(
            f"Topic: {topic}"
        )

        print(
            f"Total questions: {total_count}"
        )

        print(
            f"Easy: {easy_count}"
        )

        print(
            f"Medium: {medium_count}"
        )

        print(
            f"Hard: {hard_count}"
        )

        print(
            f"Batch size: {BATCH_SIZE}"
        )

        print(
            f"Total batches: {total_batches}"
        )

        print(
            f"Gemini models: {GEMINI_MODELS}"
        )

        print("=" * 65)

        for difficulty, requested_count in difficulty_batches:

            remaining = requested_count

            while remaining > 0:

                time_left = _remaining_time(
                    deadline
                )

                if (
                    time_left is not None
                    and time_left <= 0
                ):

                    raise TimeoutError(

                        "Quiz generation reached its "
                        "18-minute time limit. "

                        f"Generated {len(all_questions)} "
                        f"of {total_count} questions."

                    )

                batch_count = min(
                    BATCH_SIZE,
                    remaining
                )

                previous_questions = [

                    question["question"]

                    for question in all_questions

                ]

                prompt = _question_prompt(

                    topic=topic,

                    count=batch_count,

                    difficulty=difficulty,

                    previous_questions=previous_questions

                )

                previous_keys = list(
                    previous_questions
                )

                def validate_batch(payload):

                    batch = validate_basic_question_structure(

                        _unwrap_questions(payload),

                        expected_count=batch_count

                    )

                    batch_texts = []

                    for question in batch:

                        if normalize_difficulty(
                            question["difficulty"]
                        ) != difficulty:

                            raise ValueError(

                                f"Expected {difficulty} questions."

                            )

                        question_text = question[
                            "question"
                        ]

                        if _is_duplicate_question(

                            question_text,

                            previous_keys

                        ):

                            raise ValueError(

                                "Duplicate question detected "
                                "across batches: "

                                + question_text[:150]

                            )

                        if _is_duplicate_question(

                            question_text,

                            batch_texts

                        ):

                            raise ValueError(

                                "Duplicate question detected "
                                "inside current batch: "

                                + question_text[:150]

                            )

                        batch_texts.append(
                            question_text
                        )

                    return batch

                batch = _generate_validated_batch(

                    prompt=prompt,

                    validator=validate_batch,

                    count=batch_count,

                    difficulty=difficulty,

                    deadline=deadline

                )

                # Preserve all successful batches in memory.
                all_questions.extend(
                    batch
                )

                remaining -= batch_count

                completed_batches += 1

                elapsed = (
                    time.monotonic() - started_at
                )

                print("\n" + "-" * 60)

                print(
                    f"[PROGRESS] "
                    f"{completed_batches}/{total_batches} batches"
                )

                print(
                    f"[PROGRESS] "
                    f"{len(all_questions)}/{total_count} questions"
                )

                print(
                    f"[PROGRESS] "
                    f"Elapsed: {elapsed:.1f} seconds"
                )

                print("-" * 60)

        # Final validation
        validate_basic_question_structure(

            all_questions,

            expected_count=total_count

        )

        # Exact distribution
        check_difficulty_distribution(

            all_questions,

            easy_count,

            medium_count,

            hard_count

        )

        # Final duplicate check
        final_questions = []

        for question in all_questions:

            if _is_duplicate_question(

                question["question"],

                final_questions

            ):

                raise ValueError(

                    "Final duplicate validation failed: "

                    + question["question"][:150]

                )

            final_questions.append(
                question["question"]
            )

        elapsed = (
            time.monotonic() - started_at
        )

        print("\n" + "=" * 65)

        print(
            "[QUIZ GENERATION SUCCESS]"
        )

        print(
            f"Total questions: {len(all_questions)}"
        )

        print(
            f"Easy: {easy_count}"
        )

        print(
            f"Medium: {medium_count}"
        )

        print(
            f"Hard: {hard_count}"
        )

        print(
            f"Time: {elapsed:.1f} seconds"
        )

        print("=" * 65)

        # Keep existing teacher.py contract.
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

        "Return a JSON object containing an evaluations array. "

        "Every evaluation must contain question_index, "
        "evaluated_difficulty, confidence and reason.\n\n"

        + json.dumps(
            {
                "questions": compact_questions
            },
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
                "Evaluation count mismatch."
            )

        for item in evaluations:

            if not isinstance(item, dict):

                raise ValueError(
                    "Invalid evaluation object."
                )

            difficulty = normalize_difficulty(
                item.get("evaluated_difficulty")
            )

            if not difficulty:

                raise ValueError(
                    "Invalid evaluated difficulty."
                )

        return evaluations

    payload = _request_with_fallback(

        prompt=prompt,

        validator=None,

        response_schema=build_evaluation_schema(
            len(questions)
        ),

        temperature=0.1

    )

    evaluations = validator(
        payload
    )

    output = []

    for item in evaluations:

        try:

            index = int(
                item.get("question_index")
            )

        except (
            TypeError,
            ValueError
        ):

            continue

        difficulty = normalize_difficulty(
            item.get("evaluated_difficulty")
        )

        try:

            confidence = float(
                item.get("confidence", 0)
            )

        except (
            TypeError,
            ValueError
        ):

            confidence = 0

        confidence = max(
            0.0,
            min(
                1.0,
                confidence
            )
        )

        reason = str(
            item.get("reason") or ""
        ).strip()

        # Match teacher.py expected structure.
        output.append({

            "index": index,

            "difficulty": difficulty,

            "confidence": confidence,

            "reason": reason[:300]

        })

    return output


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

    if gemini_client is None:

        raise RuntimeError(
            "GEMINI_API_KEY is not configured."
        )

    replacements = []

    existing_texts = [

        question.get("question", "")

        for question in existing_questions

        if isinstance(question, dict)

    ]

    groups = {

        level: [

            question

            for question in failed_questions

            if normalize_difficulty(

                question.get(
                    "evaluated_difficulty"
                )
                or question.get(
                    "difficulty"
                )

            ) == level

        ]

        for level in (
            "Easy",
            "Medium",
            "Hard"
        )

    }

    deadline = (
        time.monotonic()
        + QUIZ_GENERATION_TIMEOUT_SECONDS
    )

    for difficulty, items in groups.items():

        remaining = len(items)

        while remaining > 0:

            batch_count = min(
                BATCH_SIZE,
                remaining
            )

            all_existing = (

                existing_texts

                + [

                    question["question"]

                    for question in replacements

                ]

            )

            prompt = _question_prompt(

                topic=topic,

                count=batch_count,

                difficulty=difficulty,

                previous_questions=all_existing

            )

            def validator(payload):

                batch = validate_basic_question_structure(

                    _unwrap_questions(payload),

                    expected_count=batch_count

                )

                batch_texts = []

                for question in batch:

                    if normalize_difficulty(
                        question["difficulty"]
                    ) != difficulty:

                        raise ValueError(
                            f"Expected {difficulty} replacement questions."
                        )

                    if _is_duplicate_question(

                        question["question"],

                        all_existing

                    ):

                        raise ValueError(
                            "Replacement duplicates an existing question."
                        )

                    if _is_duplicate_question(

                        question["question"],

                        batch_texts

                    ):

                        raise ValueError(
                            "Duplicate replacement inside batch."
                        )

                    batch_texts.append(
                        question["question"]
                    )

                return batch

            batch = _generate_validated_batch(

                prompt=prompt,

                validator=validator,

                count=batch_count,

                difficulty=difficulty,

                deadline=deadline

            )

            replacements.extend(
                batch
            )

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