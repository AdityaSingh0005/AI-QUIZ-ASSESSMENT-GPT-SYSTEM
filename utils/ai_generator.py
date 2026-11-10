"""
AI question generation for AI Quiz Assessment GPT System.

Provider order:
    1. xAI Grok (primary)
    2. Ollama Cloud (fallback)

Public functions kept compatible with teacher.py:
    generate_questions(topic, easy_count, medium_count, hard_count)
    evaluate_question_difficulties(questions)
    generate_replacement_questions(...)
    apply_replacements(...)
    extract_questions_from_response(...)

Environment variables:
    XAI_API_KEY
    XAI_MODEL=grok-3
    OLLAMA_API_KEY
    OLLAMA_MODEL=gpt-oss:20b
    AI_QUIZ_BATCH_SIZE=10
    AI_MAX_COMPLETION_TOKENS=4000
    AI_GENERATION_LOCK_WAIT=45
"""

import json
import os
import re
import threading
import time
from collections import Counter

from openai import OpenAI
from ollama import Client as OllamaClient


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

XAI_API_KEY = (os.getenv("XAI_API_KEY") or "").strip()
XAI_MODEL = (os.getenv("XAI_MODEL") or "grok-3").strip()
XAI_BASE_URL = "https://api.x.ai/v1"

OLLAMA_API_KEY = (os.getenv("OLLAMA_API_KEY") or "").strip()
OLLAMA_MODEL = (os.getenv("OLLAMA_MODEL") or "gpt-oss:20b").strip()
OLLAMA_HOST = (os.getenv("OLLAMA_HOST") or "https://ollama.com").strip()

BATCH_SIZE = max(1, min(int(os.getenv("AI_QUIZ_BATCH_SIZE", "10")), 10))
MAX_COMPLETION_TOKENS = max(
    1000, min(int(os.getenv("AI_MAX_COMPLETION_TOKENS", "4000")), 8000)
)
GENERATION_LOCK_WAIT = max(
    5, int(os.getenv("AI_GENERATION_LOCK_WAIT", "45"))
)

XAI_TIMEOUT_SECONDS = max(30, int(os.getenv("XAI_TIMEOUT_SECONDS", "150")))
OLLAMA_TIMEOUT_SECONDS = max(30, int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "300")))

xai_client = (
    OpenAI(
        api_key=XAI_API_KEY,
        base_url=XAI_BASE_URL,
        timeout=XAI_TIMEOUT_SECONDS,
        max_retries=0,
    )
    if XAI_API_KEY
    else None
)

ollama_client = (
    OllamaClient(
        host=OLLAMA_HOST,
        headers={"Authorization": f"Bearer {OLLAMA_API_KEY}"},
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )
    if OLLAMA_API_KEY
    else None
)

_GENERATION_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# JSON schemas
# ---------------------------------------------------------------------------

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
                        "question": {"type": "string"},
                        "option_a": {"type": "string"},
                        "option_b": {"type": "string"},
                        "option_c": {"type": "string"},
                        "option_d": {"type": "string"},
                        "correct_option": {
                            "type": "string",
                            "enum": ["A", "B", "C", "D"],
                        },
                        "difficulty": {
                            "type": "string",
                            "enum": ["Easy", "Medium", "Hard"],
                        },
                        "explanation": {"type": "string"},
                    },
                    "required": [
                        "question", "option_a", "option_b", "option_c",
                        "option_d", "correct_option", "difficulty",
                        "explanation",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["questions"],
        "additionalProperties": False,
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
                        "question_index": {"type": "integer"},
                        "evaluated_difficulty": {
                            "type": "string",
                            "enum": ["Easy", "Medium", "Hard"],
                        },
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": [
                        "question_index", "evaluated_difficulty",
                        "confidence", "reason",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["evaluations"],
        "additionalProperties": False,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        "advanced": "Hard",
    }
    return mapping.get(value, "")


def _status_code(exc):
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) or getattr(exc, "status_code", None)


def _is_permanent_provider_error(exc):
    status = _status_code(exc)
    return status in (400, 401, 403, 404, 422)


def _extract_json_text(content):
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        return content
    if not isinstance(content, str):
        raise ValueError("AI provider returned an empty or unsupported response.")

    text = content.strip()
    if not text:
        raise ValueError("AI provider returned empty response.")

    # Remove markdown fences if a provider ignores JSON-only instructions.
    text = re.sub(r"^\s*```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start_obj = text.find("{")
        end_obj = text.rfind("}")
        start_arr = text.find("[")
        end_arr = text.rfind("]")

        candidates = []
        if start_obj >= 0 and end_obj > start_obj:
            candidates.append(text[start_obj:end_obj + 1])
        if start_arr >= 0 and end_arr > start_arr:
            candidates.append(text[start_arr:end_arr + 1])

        for candidate in candidates:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    raise ValueError("AI response was not valid JSON.")


def clean_json_content(content):
    return _extract_json_text(content)


def _unwrap_questions(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        questions = payload.get("questions")
        if isinstance(questions, list):
            return questions
    raise ValueError("JSON response must contain a 'questions' array.")


def validate_basic_question_structure(questions, expected_count=None):
    if not isinstance(questions, list):
        raise ValueError("Questions must be a list.")

    if expected_count is not None and len(questions) != expected_count:
        raise ValueError(
            f"Expected {expected_count} questions, received {len(questions)}."
        )

    seen_questions = set()

    for index, item in enumerate(questions, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Question {index} is not a JSON object.")

        required = (
            "question", "option_a", "option_b", "option_c",
            "option_d", "correct_option", "difficulty", "explanation",
        )
        for key in required:
            value = item.get(key)
            if value is None or not str(value).strip():
                raise ValueError(f"Question {index}: '{key}' is missing.")

        item["question"] = str(item["question"]).strip()
        for key in ("option_a", "option_b", "option_c", "option_d"):
            item[key] = str(item[key]).strip()

        item["correct_option"] = str(item["correct_option"]).strip().upper()
        if item["correct_option"] not in ("A", "B", "C", "D"):
            raise ValueError(
                f"Question {index}: correct_option must be A, B, C or D."
            )

        item["difficulty"] = normalize_difficulty(item["difficulty"])
        if not item["difficulty"]:
            raise ValueError(f"Question {index}: invalid difficulty.")

        item["explanation"] = str(item["explanation"]).strip()

        options = [
            item["option_a"].casefold(),
            item["option_b"].casefold(),
            item["option_c"].casefold(),
            item["option_d"].casefold(),
        ]
        if len(set(options)) != 4:
            raise ValueError(f"Question {index}: all four options must be unique.")

        normalized_question = re.sub(
            r"\s+", " ", item["question"].casefold()
        ).strip()
        if normalized_question in seen_questions:
            raise ValueError(f"Duplicate question detected at question {index}.")
        seen_questions.add(normalized_question)

    return questions


def trim_extra_questions(questions, expected_count):
    return questions[:expected_count]


def check_difficulty_distribution(questions, easy_count, medium_count, hard_count):
    actual = Counter(normalize_difficulty(q.get("difficulty")) for q in questions)
    expected = {
        "Easy": int(easy_count),
        "Medium": int(medium_count),
        "Hard": int(hard_count),
    }
    if any(actual.get(level, 0) != count for level, count in expected.items()):
        raise ValueError(
            "Difficulty distribution mismatch. "
            f"Expected {expected}, received "
            f"{{'Easy': {actual.get('Easy', 0)}, "
            f"'Medium': {actual.get('Medium', 0)}, "
            f"'Hard': {actual.get('Hard', 0)}}}."
        )
    return True


def extract_questions_from_response(response, expected_count=None):
    payload = _extract_json_text(response)
    questions = _unwrap_questions(payload)
    if expected_count is not None and len(questions) > expected_count:
        questions = trim_extra_questions(questions, expected_count)
    return validate_basic_question_structure(questions, expected_count)


def _normalised_question_key(question):
    return re.sub(
        r"\s+", " ", str(question or "").casefold()
    ).strip()


def _format_provider_error(provider, exc):
    status = _status_code(exc)
    status_text = f" (HTTP {status})" if status else ""
    message = str(exc).replace("\n", " ").strip()
    if len(message) > 700:
        message = message[:700] + "..."
    return f"{provider}{status_text}: {message}"


# ---------------------------------------------------------------------------
# Provider calls
# ---------------------------------------------------------------------------

def _xai_json_content(prompt, response_schema=None, temperature=0.2):
    if xai_client is None:
        raise RuntimeError("XAI_API_KEY is not configured.")

    kwargs = {
        "model": XAI_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a precise academic assessment generator. "
                    "Follow the requested JSON structure exactly. "
                    "Do not add markdown or text outside JSON."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": MAX_COMPLETION_TOKENS,
    }

    if response_schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "quiz_generation_response",
                "strict": True,
                "schema": response_schema,
            },
        }

    response = xai_client.chat.completions.create(**kwargs)
    if not response.choices:
        raise ValueError("Grok returned no completion choices.")

    choice = response.choices[0]
    message = choice.message
    content = message.content

    if not content or not str(content).strip():
        refusal = getattr(message, "refusal", None)
        if refusal:
            raise ValueError(f"Grok refused the request: {refusal}")
        raise ValueError(
            f"Grok returned empty content (finish_reason={choice.finish_reason})."
        )

    if choice.finish_reason == "length":
        raise ValueError("Grok response was truncated because the token limit was reached.")

    return content


def _ollama_json_content(prompt, response_schema=None, temperature=0.2):
    if ollama_client is None:
        raise RuntimeError("OLLAMA_API_KEY is not configured.")

    response = ollama_client.chat(
        model=OLLAMA_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a precise academic assessment generator. "
                    "Return valid JSON only. Follow the requested structure."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        format="json",
        options={"temperature": temperature},
    )

    message = response.get("message", {}) if isinstance(response, dict) else getattr(response, "message", {})
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    if not content or not str(content).strip():
        raise ValueError("Ollama returned empty content.")
    return content


def _request_with_fallback(
    prompt,
    validator=None,
    response_schema=None,
    temperature=0.2,
):
    """
    Try Grok first, then Ollama. Each returned payload is locally validated
    before it is accepted. Permanent request/auth errors are not retried.
    """
    errors = []

    providers = [
        ("Grok", _xai_json_content),
        ("Ollama", _ollama_json_content),
    ]

    for provider_name, provider_call in providers:
        try:
            raw = provider_call(
                prompt,
                response_schema=response_schema,
                temperature=temperature,
            )
            parsed = _extract_json_text(raw)

            if validator is not None:
                validator(parsed)

            return parsed

        except Exception as exc:
            errors.append(_format_provider_error(provider_name, exc))
            # Move directly to fallback; do not waste time repeating a bad
            # schema, invalid request, auth failure, or exhausted account.
            continue

    raise RuntimeError(
        "AI generation failed. Grok (primary) and Ollama (fallback) "
        "both failed or returned invalid data. "
        + " | ".join(errors)
    )


# ---------------------------------------------------------------------------
# Prompt construction and generation
# ---------------------------------------------------------------------------

def _question_prompt(topic, count, difficulty, previous_questions):
    previous_text = ""
    if previous_questions:
        previous_text = "\nAvoid creating questions similar to these existing questions:\n"
        previous_text += "\n".join(
            f"- {str(q)[:350]}" for q in previous_questions[-30:]
        )

    return f"""
Create exactly {count} original multiple-choice questions for the topic:
{topic}

Required difficulty for EVERY question in this batch: {difficulty}.

Rules:
1. Return exactly {count} questions.
2. Every question must have exactly four options: A, B, C and D.
3. Exactly one option must be correct.
4. All four options must be distinct and plausible.
5. correct_option must be exactly one of A, B, C or D.
6. difficulty must be exactly "{difficulty}" for every question.
7. Provide a clear, factually correct explanation for the answer.
8. Do not repeat a question or create a trivial rewording of an earlier one.
9. Avoid ambiguous, subjective or outdated questions.
10. Keep questions appropriate for university-level assessment.
11. Return JSON only, in this exact shape:
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
{previous_text}
""".strip()


def generate_questions(topic, easy_count, medium_count, hard_count):
    """Generate exact requested counts and return (questions, evaluations)."""
    topic = str(topic or "").strip()

    try:
        easy_count = int(easy_count)
        medium_count = int(medium_count)
        hard_count = int(hard_count)
    except (TypeError, ValueError):
        raise ValueError("Easy, Medium and Hard counts must be whole numbers.")

    counts = (easy_count, medium_count, hard_count)
    if any(count < 0 for count in counts):
        raise ValueError("Question counts cannot be negative.")
    if any(count > 150 for count in counts):
        raise ValueError("Maximum 150 questions per difficulty is allowed.")
    if sum(counts) <= 0:
        raise ValueError("At least one question must be requested.")
    if not topic:
        raise ValueError("Please enter a topic or prompt.")

    if xai_client is None and ollama_client is None:
        raise RuntimeError(
            "No AI provider is configured. Set XAI_API_KEY and/or OLLAMA_API_KEY."
        )

    if not _GENERATION_LOCK.acquire(timeout=GENERATION_LOCK_WAIT):
        raise RuntimeError(
            "Another quiz is currently being generated. Please try again shortly."
        )

    try:
        all_questions = []
        previous_keys = set()

        for difficulty, requested_count in (
            ("Easy", easy_count),
            ("Medium", medium_count),
            ("Hard", hard_count),
        ):
            remaining = requested_count

            while remaining > 0:
                batch_count = min(BATCH_SIZE, remaining)
                previous_questions = [
                    q["question"] for q in all_questions
                ]

                prompt = _question_prompt(
                    topic=topic,
                    count=batch_count,
                    difficulty=difficulty,
                    previous_questions=previous_questions,
                )

                def validate_batch(payload):
                    batch = validate_basic_question_structure(
                        _unwrap_questions(payload),
                        expected_count=batch_count,
                    )

                    wrong_difficulty = [
                        q for q in batch
                        if normalize_difficulty(q.get("difficulty")) != difficulty
                    ]
                    if wrong_difficulty:
                        raise ValueError(
                            f"All {batch_count} questions in this batch "
                            f"must be {difficulty}."
                        )

                    local_keys = set()
                    for question in batch:
                        key = _normalised_question_key(question["question"])
                        if key in previous_keys or key in local_keys:
                            raise ValueError(
                                "Duplicate question detected across generation batches."
                            )
                        local_keys.add(key)

                    return batch

                payload = _request_with_fallback(
                    prompt=prompt,
                    validator=validate_batch,
                    response_schema=build_quiz_batch_schema(batch_count),
                    temperature=0.2,
                )

                batch = validate_batch(payload)
                for question in batch:
                    previous_keys.add(
                        _normalised_question_key(question["question"])
                    )
                all_questions.extend(batch)
                remaining -= batch_count

        validate_basic_question_structure(
            all_questions,
            expected_count=sum(counts),
        )
        check_difficulty_distribution(
            all_questions,
            easy_count,
            medium_count,
            hard_count,
        )

        # Preserve the existing teacher.py return contract. Evaluation is
        # intentionally empty during normal generation to avoid extra API calls.
        return all_questions, []

    finally:
        _GENERATION_LOCK.release()


# ---------------------------------------------------------------------------
# Optional difficulty evaluation
# ---------------------------------------------------------------------------

def evaluate_question_difficulties(questions):
    if not questions:
        return []

    compact_questions = [
        {
            "question_index": index,
            "question": item.get("question", ""),
            "options": [
                item.get("option_a", ""),
                item.get("option_b", ""),
                item.get("option_c", ""),
                item.get("option_d", ""),
            ],
            "correct_option": item.get("correct_option", ""),
            "declared_difficulty": item.get("difficulty", ""),
        }
        for index, item in enumerate(questions, start=1)
    ]

    prompt = (
        "Evaluate the academic difficulty of each multiple-choice question. "
        "Use Easy, Medium or Hard. Do not change any question. "
        "Return JSON with an evaluations array. Each item must contain "
        "question_index, evaluated_difficulty, confidence (0 to 1), and "
        "a short reason.\n\n"
        + json.dumps({"questions": compact_questions}, ensure_ascii=False)
    )

    def validator(payload):
        evaluations = payload.get("evaluations") if isinstance(payload, dict) else None
        if not isinstance(evaluations, list) or len(evaluations) != len(questions):
            raise ValueError("Evaluation count does not match question count.")
        for item in evaluations:
            if normalize_difficulty(item.get("evaluated_difficulty")) not in (
                "Easy", "Medium", "Hard"
            ):
                raise ValueError("Invalid evaluated difficulty.")
        return evaluations

    payload = _request_with_fallback(
        prompt,
        validator=validator,
        response_schema=build_evaluation_schema(len(questions)),
        temperature=0.1,
    )
    evaluations = validator(payload)

    for item in evaluations:
        item["evaluated_difficulty"] = normalize_difficulty(
            item["evaluated_difficulty"]
        )
        try:
            item["confidence"] = max(
                0.0, min(1.0, float(item.get("confidence", 0)))
            )
        except (TypeError, ValueError):
            item["confidence"] = 0.0
        item["reason"] = str(item.get("reason") or "").strip()[:300]

    return evaluations


# ---------------------------------------------------------------------------
# Replacement question support
# ---------------------------------------------------------------------------

def generate_replacement_questions(
    topic,
    failed_questions,
    existing_questions=None,
):
    """
    Generate replacement questions for failed questions.
    Failed items should contain a difficulty field.
    """
    failed_questions = list(failed_questions or [])
    existing_questions = list(existing_questions or [])

    if not failed_questions:
        return []

    replacements = []
    existing_keys = {
        _normalised_question_key(q.get("question"))
        for q in existing_questions
        if isinstance(q, dict)
    }

    groups = {
        level: [
            q for q in failed_questions
            if normalize_difficulty(q.get("evaluated_difficulty") or q.get("difficulty")) == level
        ]
        for level in ("Easy", "Medium", "Hard")
    }

    for difficulty, items in groups.items():
        remaining = len(items)
        while remaining:
            batch_count = min(BATCH_SIZE, remaining)
            prompt = _question_prompt(
                topic=topic,
                count=batch_count,
                difficulty=difficulty,
                previous_questions=[
                    q.get("question", "") for q in existing_questions + replacements
                ],
            )

            def validator(payload):
                batch = validate_basic_question_structure(
                    _unwrap_questions(payload),
                    expected_count=batch_count,
                )
                for q in batch:
                    if normalize_difficulty(q.get("difficulty")) != difficulty:
                        raise ValueError(
                            f"Replacement questions must be {difficulty}."
                        )
                    key = _normalised_question_key(q["question"])
                    if key in existing_keys:
                        raise ValueError("Replacement duplicates an existing question.")
                return batch

            payload = _request_with_fallback(
                prompt,
                validator=validator,
                response_schema=build_replacement_schema(batch_count),
                temperature=0.25,
            )
            batch = validator(payload)
            for q in batch:
                existing_keys.add(_normalised_question_key(q["question"]))
            replacements.extend(batch)
            remaining -= batch_count

    return replacements


def apply_replacements(questions, replacements, failed_indexes):
    """Replace questions at zero-based indexes, preserving original order."""
    output = list(questions or [])
    replacements = iter(replacements or [])

    for index in failed_indexes or []:
        try:
            output[int(index)] = next(replacements)
        except (StopIteration, IndexError, TypeError, ValueError):
            continue

    return output
