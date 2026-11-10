"""
Gemini-only AI Question Generator
AI Quiz Assessment GPT System

Primary provider:
    Google Gemini API

Models:
    1. gemini-3.8-flash
    2. gemini-3.5-flash
    3. gemini-3.5-flash-lite

Public functions:
    generate_questions()
    evaluate_question_difficulties()
    generate_replacement_questions()
    apply_replacements()
    extract_questions_from_response()

No Ollama.
No paid fallback provider.
"""

import json
import os
import re
import time
import random
import threading

from difflib import SequenceMatcher
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

GEMINI_FALLBACK_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
]

GEMINI_MODELS = list(dict.fromkeys([
    GEMINI_MODEL,
    *GEMINI_FALLBACK_MODELS
]))

# Generate 10 questions per API request.
BATCH_SIZE = max(
    1,
    min(
        int(os.getenv("AI_QUIZ_BATCH_SIZE", "10")),
        10
    )
)

MAX_OUTPUT_TOKENS = max(
    2000,
    min(
        int(os.getenv("AI_MAX_COMPLETION_TOKENS", "6000")),
        8000
    )
)

GENERATION_LOCK_WAIT = max(
    5,
    int(os.getenv("AI_GENERATION_LOCK_WAIT", "45"))
)

# Hard cap: 45 seconds per Gemini request.
GEMINI_TIMEOUT_SECONDS = max(
    20,
    min(
        int(os.getenv("GEMINI_TIMEOUT_SECONDS", "45")),
        45
    )
)

# Maximum 4 API attempts per batch, regardless of
# a larger value in the Render environment.
GEMINI_MAX_RETRIES = max(
    1,
    min(
        int(os.getenv("GEMINI_MAX_RETRIES", "4")),
        4
    )
)

# Short retry delays.
RETRY_DELAYS = (1, 2, 3)

# One additional request if the returned batch fails validation.
BATCH_VALIDATION_ATTEMPTS = 2

# Maximum duration for one complete quiz-generation operation.
QUIZ_GENERATION_TIMEOUT_SECONDS = 18 * 60

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

    response = getattr(
        exc,
        "response",
        None
    )

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

    if len(message) > 500:

        message = message[:500] + "..."

    return f"{provider}{status_text}: {message}"


def _remaining_time(deadline):

    if deadline is None:
        return None

    return deadline - time.monotonic()


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

        pass

    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end > start:

        try:

            return json.loads(
                text[start:end + 1]
            )

        except json.JSONDecodeError:

            pass

    start = text.find("[")
    end = text.rfind("]")

    if start >= 0 and end > start:

        try:

            return json.loads(
                text[start:end + 1]
            )

        except json.JSONDecodeError:

            pass

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
        "JSON response must contain a questions array."
    )


# ============================================================
# DUPLICATE DETECTION
# ============================================================

def _normalised_question_key(question):

    question = str(
        question or ""
    ).casefold().strip()

    # Ignore punctuation and repeated whitespace.
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

        # Detect almost-identical reworded questions.
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

        if not isinstance(item, dict):

            raise ValueError(
                f"Question {index} is not a JSON object."
            )

        for key in required_fields:

            value = item.get(key)

            if (

                value is None

                or not str(value).strip()

            ):

                raise ValueError(

                    f"Question {index}: "
                    f"'{key}' is missing."

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
                f"Question {index}: invalid correct_option."
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

                f"Question {index}: "
                "all four options must be unique."

            )

        if _is_duplicate_question(
            item["question"],
            seen_questions
        ):

            raise ValueError(

                f"Duplicate question inside batch "
                f"at question {index}."

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
            q.get("difficulty")
        )

        for q in questions

    )

    expected = {

        "Easy": int(easy_count),

        "Medium": int(medium_count),

        "Hard": int(hard_count)

    }

    for level, expected_count in expected.items():

        if actual.get(level, 0) != expected_count:

            raise ValueError(

                "Difficulty distribution mismatch. "

                f"Expected {expected}, received {dict(actual)}."

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
# GEMINI API - FAST RETRY AND MODEL FAILOVER
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

    model_index = 0

    attempt = 0

    while attempt < GEMINI_MAX_RETRIES:

        remaining_time = _remaining_time(
            deadline
        )

        if (

            remaining_time is not None

            and remaining_time <= 0

        ):

            raise TimeoutError(
                "Quiz generation reached its maximum time limit."
            )

        available_models = [

            model

            for model in GEMINI_MODELS

            if model not in unavailable_models

        ]

        if not available_models:

            raise RuntimeError(
                "All configured Gemini models are unavailable: "
                + ", ".join(GEMINI_MODELS)
            )

        model = available_models[
            model_index % len(available_models)
        ]

        attempt += 1

        try:

            print(
                f"[GEMINI] Attempt {attempt}/"
                f"{GEMINI_MAX_RETRIES} | Model: {model}"
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
                f"[GEMINI SUCCESS] Model: {model}"
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

            # Authentication errors cannot be fixed by
            # switching models.
            if status == 401:

                raise RuntimeError(
                    "Gemini API key is invalid or unauthorized: "
                    + error_message
                ) from exc

            # A model-specific 403 may be bypassed by trying
            # another configured model.
            if status == 403:

                unavailable_models.add(
                    model
                )

                print(
                    f"[GEMINI FAILOVER] Access denied for {model}."
                )

                continue

            # Skip invalid/unavailable model.
            if status == 404:

                unavailable_models.add(
                    model
                )

                print(
                    f"[GEMINI FAILOVER] {model} is unavailable."
                )

                continue

            # Invalid request cannot be repaired by retries.
            if status == 400:

                raise RuntimeError(
                    "Gemini rejected the request: "
                    + error_message
                ) from exc

            if (

                status is not None

                and status not in RETRYABLE_STATUS_CODES

            ):

                raise RuntimeError(
                    "Non-retryable Gemini error: "
                    + error_message
                ) from exc

            if attempt >= GEMINI_MAX_RETRIES:
                break

            delay = RETRY_DELAYS[
                min(
                    attempt - 1,
                    len(RETRY_DELAYS) - 1
                )
            ]

            delay += random.uniform(
                0,
                0.7
            )

            remaining_time = _remaining_time(
                deadline
            )

            if remaining_time is not None:

                delay = min(
                    delay,
                    max(0, remaining_time)
                )

            print(
                f"[GEMINI RETRY] Waiting {delay:.1f}s."
            )

            if delay > 0:

                time.sleep(
                    delay
                )

            model_index += 1

    error_summary = " | ".join(
        errors[-4:]
    )

    raise RuntimeError(

        "Gemini failed after "

        f"{attempt} attempt(s). "

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

    Only Gemini is used.
    """

    if gemini_client is None:

        raise RuntimeError(
            "Gemini API is not configured. "
            "Set GEMINI_API_KEY."
        )

    raw = _gemini_json_content(

        prompt,

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
    previous_questions,
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

            "\nQUESTIONS ALREADY GENERATED. "
            "DO NOT REPEAT OR REPHRASE THEM:\n"

        )

        previous_text += "\n".join(

            f"- {str(q)[:250]}"

            for q in previous_questions

        )

    rejected_text = ""

    if rejected_questions:

        rejected_text = (

            "\nQUESTIONS REJECTED IN THE PREVIOUS RESPONSE. "
            "DO NOT REUSE THEM:\n"

        )

        rejected_text += "\n".join(

            f"- {str(q)[:250]}"

            for q in rejected_questions

        )

    return f"""

You are an expert university examination question setter.

Generate exactly {count} ORIGINAL multiple-choice questions.

TOPIC:
{topic}

REQUIRED DIFFICULTY:
{difficulty}

STRICT REQUIREMENTS:

1. Generate exactly {count} questions.
2. All questions must test different knowledge points.
3. Do not repeat existing questions.
4. Do not rephrase an existing question.
5. Do not use the same question with changed names or numbers.
6. Every question must contain four options.
7. All four options must be different.
8. Exactly one option must be correct.
9. correct_option must be A, B, C or D.
10. difficulty must be exactly "{difficulty}".
11. Give a clear factual explanation.
12. Avoid ambiguous questions.
13. Avoid subjective questions.
14. Questions must be suitable for university-level assessment.
15. Use clear and grammatically correct English.
16. Return JSON only.
17. Do not include Markdown code fences.

Each question must contain:

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

Return exactly {count} questions inside the top-level questions array.

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

    """
    Generate one batch.

    If the response contains duplicates or fails validation,
    regenerate only this batch instead of failing the full quiz.
    """

    last_validation_error = None

    rejected_questions = []

    current_prompt = prompt

    for validation_attempt in range(
        1,
        BATCH_VALIDATION_ATTEMPTS + 1
    ):

        remaining_time = _remaining_time(
            deadline
        )

        if (

            remaining_time is not None

            and remaining_time <= 0

        ):

            raise TimeoutError(
                "Quiz generation exceeded its time limit."
            )

        try:

            print(

                f"[BATCH] {difficulty} | "

                f"{count} questions | "

                f"Validation attempt "
                f"{validation_attempt}/"
                f"{BATCH_VALIDATION_ATTEMPTS}"

            )

            payload = _request_with_fallback(

                prompt=current_prompt,

                validator=validator,

                response_schema=build_quiz_batch_schema(
                    count
                ),

                temperature=0.2,

                deadline=deadline

            )

            batch = validate_basic_question_structure(

                _unwrap_questions(payload),

                expected_count=count

            )

            validator(payload)

            print(
                f"[BATCH SUCCESS] {difficulty}: {count} questions."
            )

            return batch

        except ValueError as exc:

            last_validation_error = exc

            print(
                f"[BATCH VALIDATION ERROR] {exc}"
            )

            if validation_attempt >= BATCH_VALIDATION_ATTEMPTS:

                break

            rejected_questions.append(
                str(exc)
            )

            current_prompt = (

                prompt

                + "\n\nIMPORTANT REGENERATION INSTRUCTION:\n"

                + "Your previous response failed validation.\n"

                + f"Reason: {str(exc)[:500]}\n"

                + "Generate a completely new set of questions.\n"

                + "Do not reuse any question from the previous response.\n"

                + "Check every question for uniqueness before returning JSON."

            )

        except TimeoutError:

            raise

        except RuntimeError:

            raise

    raise ValueError(

        f"Unable to generate a valid unique {difficulty} batch "

        f"after {BATCH_VALIDATION_ATTEMPTS} validation attempts. "

        f"Last validation error: {last_validation_error}"

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
    Generate questions using Gemini only.

    Return format remains:

        (all_questions, [])
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

    except (TypeError, ValueError):

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

        print("\n" + "=" * 60)

        print(
            f"[QUIZ GENERATION] Topic: {topic}"
        )

        print(
            f"[QUIZ GENERATION] Total questions: {total_count}"
        )

        print(
            f"[QUIZ GENERATION] Batch size: {BATCH_SIZE}"
        )

        print(
            f"[QUIZ GENERATION] Total batches: {total_batches}"
        )

        print(
            f"[QUIZ GENERATION] Models: {GEMINI_MODELS}"
        )

        print("=" * 60)

        for difficulty, requested_count in difficulty_batches:

            remaining = requested_count

            while remaining > 0:

                remaining_time = _remaining_time(
                    deadline
                )

                if (

                    remaining_time is not None

                    and remaining_time <= 0

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

                    q["question"]

                    for q in all_questions

                ]

                prompt = _question_prompt(

                    topic=topic,

                    count=batch_count,

                    difficulty=difficulty,

                    previous_questions=previous_questions

                )

                previous_keys = [

                    q["question"]

                    for q in all_questions

                ]

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

                            f"Expected all questions to be "
                            f"{difficulty}."

                        )

                    current_batch_questions = []

                    for question in batch:

                        question_text = question["question"]

                        if _is_duplicate_question(

                            question_text,

                            previous_keys

                        ):

                            raise ValueError(

                                "Duplicate question detected "
                                "across generation batches: "

                                + question_text[:180]

                            )

                        if _is_duplicate_question(

                            question_text,

                            current_batch_questions

                        ):

                            raise ValueError(

                                "Duplicate question inside "
                                "the current batch: "

                                + question_text[:180]

                            )

                        current_batch_questions.append(
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

                all_questions.extend(
                    batch
                )

                remaining -= batch_count

                completed_batches += 1

                elapsed = (
                    time.monotonic() - started_at
                )

                print("\n" + "-" * 55)

                print(
                    f"[QUIZ PROGRESS] "
                    f"{completed_batches}/{total_batches} batches"
                )

                print(
                    f"[QUIZ PROGRESS] "
                    f"{len(all_questions)}/{total_count} questions"
                )

                print(
                    f"[QUIZ PROGRESS] "
                    f"Elapsed: {elapsed:.1f} seconds"
                )

                print("-" * 55)

        # Final structure validation.
        validate_basic_question_structure(

            all_questions,

            expected_count=total_count

        )

        # Final duplicate validation across all batches.
        final_questions = []

        for question in all_questions:

            if _is_duplicate_question(

                question["question"],

                final_questions

            ):

                raise ValueError(

                    "Final duplicate validation failed: "

                    + question["question"][:180]

                )

            final_questions.append(
                question["question"]
            )

        # Exact difficulty distribution.
        check_difficulty_distribution(

            all_questions,

            easy_count,

            medium_count,

            hard_count

        )

        elapsed = (
            time.monotonic() - started_at
        )

        print("\n" + "=" * 60)

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
            f"Generation time: {elapsed:.1f} seconds"
        )

        print("=" * 60 + "\n")

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

            if not isinstance(item, dict):

                raise ValueError(
                    "Invalid evaluation object."
                )

            if normalize_difficulty(

                item.get("evaluated_difficulty")

            ) not in (

                "Easy",
                "Medium",
                "Hard"

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

    evaluations = validator(
        payload
    )

    for item in evaluations:

        item["evaluated_difficulty"] = normalize_difficulty(

            item["evaluated_difficulty"]

        )

        try:

            item["confidence"] = max(

                0.0,

                min(

                    1.0,

                    float(
                        item.get("confidence", 0)
                    )

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

    if gemini_client is None:

        raise RuntimeError(
            "Gemini API is not configured."
        )

    replacements = []

    existing_question_texts = [

        q.get("question", "")

        for q in existing_questions

        if isinstance(q, dict)

    ]

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

                existing_question_texts

                + [

                    q["question"]

                    for q in replacements

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

                        question.get("difficulty")

                    ) != difficulty:

                        raise ValueError(

                            f"Replacement questions "
                            f"must be {difficulty}."

                        )

                    if _is_duplicate_question(

                        question["question"],

                        all_existing

                    ):

                        raise ValueError(

                            "Replacement duplicates an "
                            "existing question: "

                            + question["question"][:180]

                        )

                    if _is_duplicate_question(

                        question["question"],

                        batch_texts

                    ):

                        raise ValueError(
                            "Duplicate replacement in current batch."
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