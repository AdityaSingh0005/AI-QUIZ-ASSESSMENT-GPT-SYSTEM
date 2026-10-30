from pathlib import Path
import re
import shutil
from datetime import datetime

ROOT = Path.cwd()
FILES = {
    "student": ROOT / "routes" / "student.py",
    "generator": ROOT / "utils" / "ai_generator.py",
    "template": ROOT / "templates" / "quiz.html",
}
for path in FILES.values():
    if not path.is_file():
        raise SystemExit(f"ERROR: Required file not found: {path}\nRun this script from the project root.")

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
for path in FILES.values():
    shutil.copy2(path, path.with_suffix(path.suffix + f".backup_{stamp}"))

# 1) Prevent duplicate guest attempts for the same quiz + roll number.
path = FILES["student"]
source = path.read_text(encoding="utf-8")
start = source.find("def guest_start_quiz(quiz_id):")
if start < 0:
    raise SystemExit("ERROR: guest_start_quiz(quiz_id) was not found.")
end = source.find('\n@student.route("/guest_start"', start)
if end < 0:
    end = source.find("\n@student.route(", start + 10)
if end < 0:
    end = len(source)
route = source[start:end]
if "pg_advisory_xact_lock" not in route:
    anchor = '        with connection.cursor() as cursor:\n            cursor.execute(\n                """\n                INSERT INTO quiz_attempts'
    if anchor not in route:
        raise SystemExit("ERROR: Could not safely find guest INSERT. Backups were created; no student.py change made.")
    replacement = r'''        with connection.cursor() as cursor:
            # Serialize concurrent requests for the same quiz + normalized roll number.
            normalized_roll = roll_number.strip().casefold()
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
                (quiz_id, normalized_roll),
            )
            cursor.execute(
                """
                SELECT attempt_id
                FROM quiz_attempts
                WHERE quiz_id = %s
                  AND attempt_mode = 'guest'
                  AND LOWER(BTRIM(roll_number)) = LOWER(BTRIM(%s))
                LIMIT 1
                """,
                (quiz_id, roll_number),
            )
            existing_attempt = cursor.fetchone()
            if existing_attempt:
                connection.rollback()
                return render_template(
                    "guest_start_quiz.html",
                    quiz=quiz,
                    error=(
                        "This roll number has already been used for this quiz. "
                        "Only one attempt is allowed."
                    ),
                ), 409

            cursor.execute(
                """
                INSERT INTO quiz_attempts'''
    route = route.replace(anchor, replacement, 1)
    source = source[:start] + route + source[end:]
    path.write_text(source, encoding="utf-8")
    print("PATCHED routes/student.py: duplicate guest attempts blocked")
else:
    print("SKIPPED routes/student.py: duplicate guard already exists")

# 2) Replace monolithic generator with bounded per-difficulty batches.
path = FILES["generator"]
source = path.read_text(encoding="utf-8")
pattern = re.compile(r"^def generate_questions\(\s*topic,\s*easy,\s*medium,\s*hard\s*\):[\s\S]*\Z", re.M)
if not pattern.search(source):
    raise SystemExit("ERROR: generate_questions() was not found at end of utils/ai_generator.py. Backups were created.")
new_function = r'''def generate_questions(topic, easy, medium, hard):
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
'''
source = pattern.sub(lambda _match: new_function, source)
path.write_text(source, encoding="utf-8")
print("PATCHED utils/ai_generator.py: 10-question batches; expensive extra AI passes skipped")

# 3) Keep legacy submit button in DOM but hidden; nextButton handles both actions.
path = FILES["template"]
source = path.read_text(encoding="utf-8")
submit = re.compile(r'(<button\b(?=[^>]*\bid=["\']submitButton["\'])[^>]*)(>)([\s\S]*?</button>)', re.I)
m = submit.search(source)
if not m:
    raise SystemExit("ERROR: submitButton was not found in templates/quiz.html. Backups were created.")
opening = m.group(1)
if not re.search(r"\bhidden\b", opening):
    opening += " hidden"
source = source[:m.start()] + opening + m.group(2) + m.group(3) + source[m.end():]
source, label_count = re.subn(r'\?\s*["\']Save Answer["\']', '? "Submit Quiz"', source, count=1)
handler = re.compile(
    r'nextButton\.addEventListener\("click",\s*function\s*\(\)\s*\{\s*'
    r'if\s*\(submitting\)\s*\{\s*return;\s*\}\s*'
    r'postAction\("save"\);\s*\}\);', re.S
)
new_handler = '''nextButton.addEventListener("click", function () {
        if (submitting) {
            return;
        }
        const isLastQuestion = currentIndex === cards.length - 1;
        if (!isLastQuestion) {
            postAction("save");
            return;
        }
        const confirmed = window.confirm("Are you sure you want to submit your quiz?");
        if (confirmed) {
            postAction("submit");
        }
    });'''
source, handler_count = handler.subn(lambda _match: new_handler, source, count=1)
if handler_count != 1:
    raise SystemExit("ERROR: Could not safely patch nextButton handler in quiz.html. Backups were created.")
path.write_text(source, encoding="utf-8")
print("PATCHED templates/quiz.html: only one visible button; last question submits")
print(f"\nSUCCESS. Backups ending .backup_{stamp} are beside the originals.")
print("Next: python -m py_compile routes/student.py utils/ai_generator.py")
