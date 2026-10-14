import time
from datetime import datetime, timezone

from flask import (
    Blueprint,
    render_template,
    redirect,
    session,
    request,
    url_for,
    flash,
    abort,
)
from psycopg2.extras import RealDictCursor

from database import get_db_connection
from utils.quiz_engine import get_quiz_questions, calculate_score


student = Blueprint("student", __name__)


# ---------------------------------------------------------
# GENERAL HELPERS
# ---------------------------------------------------------

def _db_connection():
    return get_db_connection()


def _close_db(cursor=None, db=None):
    try:
        if cursor is not None:
            cursor.close()
    finally:
        if db is not None:
            db.close()


def _as_datetime(value):
    """Convert a datetime or ISO timestamp into a datetime."""
    if value is None:
        return None

    if isinstance(value, datetime):
        return value

    try:
        value = str(value).strip()

        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _now_like(value=None):
    """
    Return current time with compatible timezone awareness.
    Database timestamps may be timezone-aware or naive.
    """
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return datetime.now(value.tzinfo)

        return datetime.now()

    return datetime.now()


def _timestamp_seconds(value):
    value = _as_datetime(value)

    if value is None:
        return None

    if value.tzinfo is None:
        return value.timestamp()

    return value.timestamp()


def _seconds_left(deadline):
    deadline_seconds = _timestamp_seconds(deadline)

    if deadline_seconds is None:
        return 0

    return max(0, int(deadline_seconds - time.time()))


def _quiz_status(quiz):
    """
    Determine whether a quiz is upcoming, available, or expired.
    """
    now = _now_like()

    available_from = _as_datetime(quiz.get("available_from"))
    available_until = _as_datetime(quiz.get("available_until"))

    if available_from is not None:
        comparison_now = now

        if available_from.tzinfo is not None and comparison_now.tzinfo is None:
            comparison_now = comparison_now.replace(tzinfo=available_from.tzinfo)

        elif available_from.tzinfo is None and comparison_now.tzinfo is not None:
            comparison_now = comparison_now.replace(tzinfo=None)

        if comparison_now < available_from:
            return "upcoming"

    if available_until is not None:
        comparison_now = now

        if available_until.tzinfo is not None and comparison_now.tzinfo is None:
            comparison_now = comparison_now.replace(tzinfo=available_until.tzinfo)

        elif available_until.tzinfo is None and comparison_now.tzinfo is not None:
            comparison_now = comparison_now.replace(tzinfo=None)

        if comparison_now >= available_until:
            return "expired"

    return "available"


def _calculate_quiz_duration_seconds(questions, question_time_seconds):
    """
    Calculate duration using the configured per-question timer.

    If no per-question timer is configured, fall back to the
    quiz's duration_minutes value at the caller.
    """
    try:
        seconds_per_question = int(question_time_seconds or 0)
    except (TypeError, ValueError):
        seconds_per_question = 0

    if seconds_per_question > 0:
        return max(1, len(questions) * seconds_per_question)

    return 0


def _effective_deadline(started_at, duration_seconds, available_until=None):
    """
    Effective deadline is the earlier of:
      1. attempt start + configured duration
      2. quiz availability end
    """
    start_seconds = _timestamp_seconds(started_at)

    if start_seconds is None:
        start_seconds = time.time()

    deadline_seconds = start_seconds + max(0, int(duration_seconds or 0))

    availability_seconds = _timestamp_seconds(available_until)

    if availability_seconds is not None:
        deadline_seconds = min(deadline_seconds, availability_seconds)

    return datetime.fromtimestamp(deadline_seconds)


def _get_quiz(quiz_id):
    db = _db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """
            SELECT quiz_id, title, prompt, total_questions,
                   duration_minutes, question_time_seconds,
                   available_from, available_until, created_at
            FROM quizzes
            WHERE quiz_id = %s
            """,
            (quiz_id,),
        )

        return cursor.fetchone()

    finally:
        _close_db(cursor, db)


def _get_student(student_id):
    db = _db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """
            SELECT student_id, full_name, roll_number,
                   department, semester, section, created_at
            FROM students
            WHERE student_id = %s
            """,
            (student_id,),
        )

        return cursor.fetchone()

    finally:
        _close_db(cursor, db)


def _get_attempt(attempt_id):
    db = _db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """
            SELECT attempt_id, quiz_id, student_id,
                   student_name, roll_number, attempt_mode,
                   started_at, submitted_at, status,
                   score, percentage
            FROM quiz_attempts
            WHERE attempt_id = %s
            """,
            (attempt_id,),
        )

        return cursor.fetchone()

    finally:
        _close_db(cursor, db)


def _get_attempt_questions(quiz_id):
    """
    Load questions from PostgreSQL instead of storing them in
    Flask's signed-cookie session.
    """
    questions = get_quiz_questions(quiz_id)

    return [dict(question) for question in questions]


def _ensure_question_explanations(questions):
    """
    Ensure the result review has the explanation field.

    Explanations are retrieved from the database if the question
    objects supplied by the quiz engine don't contain them.
    """
    missing_ids = [
        question.get("question_id")
        for question in questions
        if not question.get("explanation")
    ]

    if not missing_ids:
        return questions

    db = _db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """
            SELECT question_id, explanation
            FROM questions
            WHERE question_id = ANY(%s)
            """,
            (missing_ids,),
        )

        explanation_map = {
            str(row["question_id"]): row.get("explanation")
            for row in cursor.fetchall()
        }

        for question in questions:
            if not question.get("explanation"):
                question["explanation"] = explanation_map.get(
                    str(question.get("question_id"))
                )

        return questions

    finally:
        _close_db(cursor, db)


def _quiz_unavailable_page(message, status_code=403):
    return (
        render_template(
            "quiz.html",
            unavailable=True,
            unavailable_message=message,
        ),
        status_code,
    )


def _can_access_quiz():
    return bool(
        session.get("student_id")
        or session.get("guest_attempt")
        or session.get("guest_mode")
    )


def _active_attempt_id():
    return session.get("quiz_attempt_id")


def _is_guest():
    return bool(session.get("guest_attempt") or session.get("guest_mode"))


def _clear_active_quiz_session():
    """
    Clear quiz-related session fields without deleting login data.
    """
    keys = [
        "quiz_id",
        "quiz_attempt_id",
        "current_question",
        "answers",
        "question_started_at",
        "quiz_deadline",
        "quiz_available_until",
        "quiz_duration_seconds",
        "question_time_seconds",
        "guest_attempt",
        "guest_mode",
        "guest_name",
        "guest_roll_number",
        "guest_student_id",
        "attempt_mode",
    ]

    for key in keys:
        session.pop(key, None)


def _get_attempt_answers(attempt_id):
    """
    Retrieve saved answers from the database.
    """
    db = _db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """
            SELECT question_id, selected_option
            FROM student_answers
            WHERE attempt_id = %s
            """,
            (attempt_id,),
        )

        return {
            str(row["question_id"]): row.get("selected_option")
            for row in cursor.fetchall()
        }

    finally:
        _close_db(cursor, db)


def _save_answer(
    attempt_id,
    quiz_id,
    question_id,
    selected_option,
    student_id=None,
):
    """
    Save an answer against the specific attempt and question.

    The upsert prevents duplicate rows for the same attempt/question
    if the database has the corresponding unique constraint.
    """
    db = _db_connection()
    cursor = db.cursor()

    try:
        cursor.execute(
            """
            SELECT attempt_id, quiz_id, student_id, status
            FROM quiz_attempts
            WHERE attempt_id = %s
              AND quiz_id = %s
            FOR UPDATE
            """,
            (attempt_id, quiz_id),
        )

        attempt = cursor.fetchone()

        if not attempt:
            db.rollback()
            return False

        if str(attempt[3]).lower() not in ("in_progress", "in progress"):
            db.rollback()
            return False

        # A guest attempt can have a NULL student_id.
        attempt_student_id = attempt[2]

        if student_id is not None and attempt_student_id is not None:
            if str(attempt_student_id) != str(student_id):
                db.rollback()
                return False

        # Validate that the question belongs to this quiz.
        cursor.execute(
            """
            SELECT question_id
            FROM questions
            WHERE question_id = %s
              AND quiz_id = %s
            """,
            (question_id, quiz_id),
        )

        if not cursor.fetchone():
            db.rollback()
            return False

        # Do not create answer rows for values other than A/B/C/D.
        if selected_option not in ("A", "B", "C", "D", None, ""):
            db.rollback()
            return False

        cursor.execute(
            """
            SELECT 1
            FROM student_answers
            WHERE attempt_id = %s
              AND question_id = %s
            LIMIT 1
            """,
            (attempt_id, question_id),
        )

        existing_answer = cursor.fetchone()

        if existing_answer:
            cursor.execute(
                """
                UPDATE student_answers
                SET selected_option = %s
                WHERE attempt_id = %s
                  AND question_id = %s
                """,
                (
                    selected_option or None,
                    attempt_id,
                    question_id,
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO student_answers
                    (student_id, quiz_id, question_id,
                     selected_option, attempt_id)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    attempt_student_id,
                    quiz_id,
                    question_id,
                    selected_option or None,
                    attempt_id,
                ),
            )

        db.commit()
        return True

    except Exception:
        db.rollback()
        raise

    finally:
        _close_db(cursor, db)


def _mark_attempt_submitted(attempt_id, score, percentage):
    db = _db_connection()
    cursor = db.cursor()

    try:
        cursor.execute(
            """
            UPDATE quiz_attempts
            SET submitted_at = CURRENT_TIMESTAMP,
                status = 'submitted',
                score = %s,
                percentage = %s
            WHERE attempt_id = %s
              AND LOWER(status) IN ('in_progress', 'in progress')
            """,
            (score, percentage, attempt_id),
        )

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        _close_db(cursor, db)


def _insert_result(student_id, quiz_id, score, percentage, attempt_id):
    """
    Save the final result once per attempt.
    """
    db = _db_connection()
    cursor = db.cursor()

    try:
        cursor.execute(
            """
            SELECT result_id
            FROM results
            WHERE attempt_id = %s
            LIMIT 1
            """,
            (attempt_id,),
        )

        existing = cursor.fetchone()

        if not existing:
            cursor.execute(
                """
                INSERT INTO results
                    (student_id, quiz_id, score, percentage,
                     submitted_at, attempt_id)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
                """,
                (
                    student_id,
                    quiz_id,
                    score,
                    percentage,
                    attempt_id,
                ),
            )

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        _close_db(cursor, db)


def _build_result_review(questions, answers):
    review = []

    for question in questions:
        question_id = str(question["question_id"])
        selected = answers.get(question_id)
        correct = question.get("correct_option")

        review.append(
            {
                **dict(question),
                "selected_option": selected,
                "correct_option": correct,
                "is_correct": bool(selected and selected == correct),
            }
        )

    return review


def _get_quiz_duration(quiz, questions):
    """
    Prefer per-question timing if configured. Otherwise use the
    quiz's overall duration_minutes setting.
    """
    seconds = _calculate_quiz_duration_seconds(
        questions,
        quiz.get("question_time_seconds"),
    )

    if seconds > 0:
        return seconds

    try:
        minutes = int(quiz.get("duration_minutes") or 0)
    except (TypeError, ValueError):
        minutes = 0

    if minutes > 0:
        return minutes * 60

    return max(1, len(questions) * 60)


# ---------------------------------------------------------
# STUDENT DASHBOARD
# ---------------------------------------------------------

@student.route("/student_dashboard")
def student_dashboard():
    if not session.get("student_id"):
        return redirect(url_for("auth.login"))

    student_record = _get_student(session["student_id"])

    if not student_record:
        session.pop("student_id", None)
        return redirect(url_for("auth.login"))

    db = _db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """
            SELECT quiz_id, title, prompt, total_questions,
                   duration_minutes, question_time_seconds,
                   available_from, available_until, created_at
            FROM quizzes
            ORDER BY created_at DESC
            """
        )

        quizzes = cursor.fetchall()

        for quiz in quizzes:
            quiz["status"] = _quiz_status(quiz)

        cursor.execute(
            """
            SELECT r.result_id, r.quiz_id, r.score, r.percentage,
                   r.submitted_at, q.title
            FROM results r
            LEFT JOIN quizzes q ON q.quiz_id = r.quiz_id
            WHERE r.student_id = %s
            ORDER BY r.submitted_at DESC
            LIMIT 5
            """,
            (session["student_id"],),
        )

        recent_results = cursor.fetchall()

        return render_template(
            "student_dashboard.html",
            student=student_record,
            quizzes=quizzes,
            recent_results=recent_results,
        )

    finally:
        _close_db(cursor, db)


# ---------------------------------------------------------
# AVAILABLE QUIZZES
# ---------------------------------------------------------

@student.route("/available_quizzes")
def available_quizzes():
    if not session.get("student_id"):
        return redirect(url_for("auth.login"))

    db = _db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """
            SELECT quiz_id, title, prompt, total_questions,
                   duration_minutes, question_time_seconds,
                   available_from, available_until, created_at
            FROM quizzes
            ORDER BY created_at DESC
            """
        )

        quizzes = cursor.fetchall()

        for quiz in quizzes:
            quiz["status"] = _quiz_status(quiz)

        return render_template(
            "available_quizzes.html",
            quizzes=quizzes,
        )

    finally:
        _close_db(cursor, db)


# ---------------------------------------------------------
# START QUIZ FOR LOGGED-IN STUDENT
# ---------------------------------------------------------

@student.route("/start_quiz/<int:quiz_id>")
def start_quiz(quiz_id):
    if not session.get("student_id"):
        return redirect(url_for("auth.login"))

    student_id = session["student_id"]

    quiz = _get_quiz(quiz_id)

    if not quiz:
        abort(404)

    status = _quiz_status(quiz)

    if status == "upcoming":
        return _quiz_unavailable_page(
            "This quiz has not started yet.",
            403,
        )

    if status == "expired":
        return _quiz_unavailable_page(
            "The availability window for this quiz has ended.",
            403,
        )

    student_record = _get_student(student_id)

    if not student_record:
        session.pop("student_id", None)
        return redirect(url_for("auth.login"))

    questions = _get_attempt_questions(quiz_id)

    if not questions:
        return _quiz_unavailable_page(
            "This quiz does not contain any questions.",
            404,
        )

    duration_seconds = _get_quiz_duration(quiz, questions)

    db = _db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)

    try:
        # Resume an existing attempt only if it is still in progress.
        cursor.execute(
            """
            SELECT attempt_id, started_at, status
            FROM quiz_attempts
            WHERE quiz_id = %s
              AND student_id = %s
              AND LOWER(status) IN ('in_progress', 'in progress')
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (quiz_id, student_id),
        )

        attempt = cursor.fetchone()

        if attempt:
            attempt_id = attempt["attempt_id"]
            started_at = attempt["started_at"]

        else:
            cursor.execute(
                """
                INSERT INTO quiz_attempts
                    (quiz_id, student_id, student_name, roll_number,
                     attempt_mode, started_at, status)
                VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
                RETURNING attempt_id, started_at
                """,
                (
                    quiz_id,
                    student_id,
                    student_record.get("full_name"),
                    student_record.get("roll_number"),
                    "student",
                    "in_progress",
                ),
            )

            attempt = cursor.fetchone()
            attempt_id = attempt["attempt_id"]
            started_at = attempt["started_at"]

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        _close_db(cursor, db)

    deadline = _effective_deadline(
        started_at,
        duration_seconds,
        quiz.get("available_until"),
    )

    if _seconds_left(deadline) <= 0:
        return redirect(url_for("student.submit_quiz"))

    # Initialize state only when switching to a different attempt.
    previous_attempt_id = session.get("quiz_attempt_id")
    previous_quiz_id = session.get("quiz_id")

    if (
        str(previous_attempt_id) != str(attempt_id)
        or str(previous_quiz_id) != str(quiz_id)
    ):
        session["current_question"] = 0
        session["answers"] = {}
        session["question_started_at"] = time.time()

    session.pop("guest_attempt", None)
    session.pop("guest_mode", None)
    session.pop("guest_name", None)
    session.pop("guest_roll_number", None)
    session.pop("guest_student_id", None)

    session["quiz_id"] = quiz_id
    session["quiz_attempt_id"] = attempt_id
    session["attempt_mode"] = "student"
    session["quiz_deadline"] = deadline.isoformat()
    session["quiz_available_until"] = (
        quiz["available_until"].isoformat()
        if quiz.get("available_until")
        else None
    )
    session["quiz_duration_seconds"] = duration_seconds
    session["question_time_seconds"] = int(
        quiz.get("question_time_seconds") or 0
    )

    # Never put the questions list in Flask's cookie session.
    # Answers are loaded from the database when needed.
    return redirect(url_for("student.quiz"))


# ---------------------------------------------------------
# GUEST QUIZ START
# ---------------------------------------------------------

@student.route("/guest_start_quiz/<int:quiz_id>", methods=["GET", "POST"])
def guest_start_quiz(quiz_id):
    quiz = _get_quiz(quiz_id)

    if not quiz:
        abort(404)

    status = _quiz_status(quiz)

    if status != "available":
        message = (
            "This quiz has not started yet."
            if status == "upcoming"
            else "The availability window for this quiz has ended."
        )
        return _quiz_unavailable_page(message, 403)

    if request.method == "GET":
        return render_template("guest_start.html", quiz=quiz)

    guest_name = (
        request.form.get("name")
        or request.form.get("full_name")
        or ""
    ).strip()

    roll_number = (
        request.form.get("roll_number")
        or request.form.get("roll")
        or ""
    ).strip()

    if not guest_name or not roll_number:
        flash("Please enter your name and roll number.", "error")
        return render_template("guest_start.html", quiz=quiz), 400

    questions = _get_attempt_questions(quiz_id)

    if not questions:
        return _quiz_unavailable_page(
            "This quiz does not contain any questions.",
            404,
        )

    duration_seconds = _get_quiz_duration(quiz, questions)

    db = _db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """
            INSERT INTO quiz_attempts
                (quiz_id, student_id, student_name, roll_number,
                 attempt_mode, started_at, status)
            VALUES (%s, NULL, %s, %s, %s, CURRENT_TIMESTAMP, %s)
            RETURNING attempt_id, started_at
            """,
            (
                quiz_id,
                guest_name,
                roll_number,
                "guest",
                "in_progress",
            ),
        )

        attempt = cursor.fetchone()
        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        _close_db(cursor, db)

    _clear_active_quiz_session()

    deadline = _effective_deadline(
        attempt["started_at"],
        duration_seconds,
        quiz.get("available_until"),
    )

    session["quiz_id"] = quiz_id
    session["quiz_attempt_id"] = attempt["attempt_id"]
    session["attempt_mode"] = "guest"
    session["guest_attempt"] = True
    session["guest_name"] = guest_name
    session["guest_roll_number"] = roll_number
    session["current_question"] = 0
    session["answers"] = {}
    session["question_started_at"] = time.time()
    session["quiz_deadline"] = deadline.isoformat()
    session["quiz_available_until"] = (
        quiz["available_until"].isoformat()
        if quiz.get("available_until")
        else None
    )
    session["quiz_duration_seconds"] = duration_seconds
    session["question_time_seconds"] = int(
        quiz.get("question_time_seconds") or 0
    )

    return redirect(url_for("student.quiz"))


@student.route("/guest_start", methods=["POST"])
def guest_start():
    """
    Compatibility endpoint for older templates/forms.

    It accepts quiz_id plus guest name and roll number.
    """
    quiz_id = request.form.get("quiz_id", type=int)

    if not quiz_id:
        flash("Please select a valid quiz.", "error")
        return redirect(url_for("student.available_quizzes"))

    return redirect(
        url_for("student.guest_start_quiz", quiz_id=quiz_id)
    )


# ---------------------------------------------------------
# QUIZ PAGE AND ANSWER SUBMISSION
# ---------------------------------------------------------

@student.route("/quiz", methods=["GET", "POST"])
def quiz():
    if not _can_access_quiz():
        return redirect(url_for("auth.login"))

    quiz_id = session.get("quiz_id")
    attempt_id = session.get("quiz_attempt_id")

    if not quiz_id or not attempt_id:
        return redirect(url_for("student.available_quizzes"))

    quiz_record = _get_quiz(quiz_id)

    if not quiz_record:
        _clear_active_quiz_session()
        abort(404)

    attempt = _get_attempt(attempt_id)

    if not attempt:
        _clear_active_quiz_session()
        return redirect(url_for("student.available_quizzes"))

    if str(attempt.get("quiz_id")) != str(quiz_id):
        _clear_active_quiz_session()
        return redirect(url_for("student.available_quizzes"))

    # Prevent access to another student's attempt.
    if not _is_guest():
        if str(attempt.get("student_id")) != str(session.get("student_id")):
            abort(403)

    if str(attempt.get("status", "")).lower() not in (
        "in_progress",
        "in progress",
    ):
        return redirect(url_for("student.submit_quiz"))

    questions = _get_attempt_questions(quiz_id)

    if not questions:
        return _quiz_unavailable_page(
            "This quiz does not contain any questions.",
            404,
        )

    quiz_duration = session.get("quiz_duration_seconds")

    if not quiz_duration:
        quiz_duration = _get_quiz_duration(quiz_record, questions)
        session["quiz_duration_seconds"] = quiz_duration

    deadline_value = session.get("quiz_deadline")

    if deadline_value:
        deadline = _as_datetime(deadline_value)
    else:
        deadline = _effective_deadline(
            attempt.get("started_at"),
            quiz_duration,
            quiz_record.get("available_until"),
        )
        session["quiz_deadline"] = deadline.isoformat()

    # Keep the session deadline aligned with the actual attempt start.
    if _seconds_left(deadline) <= 0:
        return redirect(url_for("student.submit_quiz"))

    answers = session.get("answers")

    if not isinstance(answers, dict):
        answers = {}

    # Restore saved answers after refresh or session-state loss.
    try:
        database_answers = _get_attempt_answers(attempt_id)
        answers.update(database_answers)
    except Exception:
        # A failed read should not silently destroy session answers.
        pass

    session["answers"] = answers

    try:
        current_index = int(session.get("current_question", 0))
    except (TypeError, ValueError):
        current_index = 0

    current_index = max(0, min(current_index, len(questions) - 1))

    question = questions[current_index]

    try:
        per_question_seconds = int(
            session.get("question_time_seconds") or 0
        )
    except (TypeError, ValueError):
        per_question_seconds = 0

    if request.method == "POST":
        submitted_question_id = request.form.get("question_id")

        expected_question_id = str(question["question_id"])

        # Prevent a stale/double POST from answering a different question.
        if (
            submitted_question_id
            and submitted_question_id != expected_question_id
        ):
            return redirect(url_for("student.quiz"))

        # Check overall timer.
        if _seconds_left(deadline) <= 0:
            return redirect(url_for("student.submit_quiz"))

        question_started_at = session.get("question_started_at")

        if question_started_at is None:
            question_started_at = time.time()
            session["question_started_at"] = question_started_at

        question_expired = False

        if per_question_seconds > 0:
            try:
                question_expired = (
                    time.time() - float(question_started_at)
                    >= per_question_seconds
                )
            except (TypeError, ValueError):
                question_expired = False

        selected_option = request.form.get("answer")

        if selected_option not in ("A", "B", "C", "D"):
            selected_option = None

        if question_expired:
            selected_option = None

        student_id = None if _is_guest() else session.get("student_id")

        # Save answer for this exact question/attempt.
        try:
            saved = _save_answer(
                attempt_id=attempt_id,
                quiz_id=quiz_id,
                question_id=question["question_id"],
                selected_option=selected_option,
                student_id=student_id,
            )
        except Exception:
            flash(
                "Your answer could not be saved. Please try again.",
                "error",
            )
            return redirect(url_for("student.quiz"))

        if not saved:
            return redirect(url_for("student.submit_quiz"))

        answers[expected_question_id] = selected_option
        session["answers"] = answers

        if current_index + 1 >= len(questions):
            return redirect(url_for("student.submit_quiz"))

        session["current_question"] = current_index + 1
        session["question_started_at"] = time.time()

        return redirect(url_for("student.quiz"))

    remaining_seconds = _seconds_left(deadline)

    question_remaining_seconds = 0

    if per_question_seconds > 0:
        question_started_at = session.get("question_started_at")

        if question_started_at is None:
            question_started_at = time.time()
            session["question_started_at"] = question_started_at

        try:
            elapsed = time.time() - float(question_started_at)
        except (TypeError, ValueError):
            elapsed = 0

        question_remaining_seconds = max(
            0,
            int(per_question_seconds - elapsed),
        )

        # A refreshed page should not restart the per-question timer.
        if question_remaining_seconds <= 0:
            question_remaining_seconds = 0

    quiz_deadline = _timestamp_seconds(deadline)

    quiz_available_until = _timestamp_seconds(
        quiz_record.get("available_until")
    )

    return render_template(
        "quiz.html",
        quiz=quiz_record,
        question=question,
        number=current_index + 1,
        total=len(questions),
        remaining_seconds=remaining_seconds,
        question_remaining_seconds=question_remaining_seconds,
        quiz_deadline=quiz_deadline,
        quiz_available_until=quiz_available_until,
        selected_answer=answers.get(str(question["question_id"])),
        answers=answers,
        unavailable=False,
    )


# ---------------------------------------------------------
# SUBMIT QUIZ AND SHOW RESULT
# ---------------------------------------------------------

@student.route("/submit_quiz", methods=["GET", "POST"])
def submit_quiz():
    if not _can_access_quiz():
        return redirect(url_for("auth.login"))

    quiz_id = session.get("quiz_id")
    attempt_id = session.get("quiz_attempt_id")

    if not quiz_id or not attempt_id:
        return redirect(url_for("student.available_quizzes"))

    attempt = _get_attempt(attempt_id)

    if not attempt:
        _clear_active_quiz_session()
        return redirect(url_for("student.available_quizzes"))

    if str(attempt.get("quiz_id")) != str(quiz_id):
        abort(403)

    if not _is_guest():
        if str(attempt.get("student_id")) != str(session.get("student_id")):
            abort(403)

    # If already submitted, do not create a second result.
    if str(attempt.get("status", "")).lower() not in (
        "in_progress",
        "in progress",
    ):
        flash("This quiz has already been submitted.", "info")
        return redirect(url_for("student.my_results"))

    questions = _get_attempt_questions(quiz_id)

    if not questions:
        return _quiz_unavailable_page(
            "This quiz does not contain any questions.",
            404,
        )

    answers = _get_attempt_answers(attempt_id)

    # Include answers still present in the cookie session.
    session_answers = session.get("answers", {})

    if isinstance(session_answers, dict):
        for question_id, selected in session_answers.items():
            if question_id not in answers:
                answers[question_id] = selected

    score = calculate_score(questions, answers)
    total_questions = len(questions)

    percentage = (
        round((score / total_questions) * 100, 2)
        if total_questions
        else 0
    )

    student_id = None if _is_guest() else session.get("student_id")

    db = _db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)

    try:
        # Lock the attempt so two simultaneous submissions don't
        # both calculate and save different results.
        cursor.execute(
            """
            SELECT attempt_id, status
            FROM quiz_attempts
            WHERE attempt_id = %s
            FOR UPDATE
            """,
            (attempt_id,),
        )

        locked_attempt = cursor.fetchone()

        if not locked_attempt:
            db.rollback()
            _clear_active_quiz_session()
            return redirect(url_for("student.available_quizzes"))

        if str(locked_attempt["status"]).lower() not in (
            "in_progress",
            "in progress",
        ):
            db.commit()
            return redirect(url_for("student.my_results"))

        cursor.execute(
            """
            UPDATE quiz_attempts
            SET submitted_at = CURRENT_TIMESTAMP,
                status = 'submitted',
                score = %s,
                percentage = %s
            WHERE attempt_id = %s
            """,
            (score, percentage, attempt_id),
        )

        cursor.execute(
            """
            SELECT result_id
            FROM results
            WHERE attempt_id = %s
            LIMIT 1
            """,
            (attempt_id,),
        )

        existing_result = cursor.fetchone()

        if not existing_result:
            cursor.execute(
                """
                INSERT INTO results
                    (student_id, quiz_id, score, percentage,
                     submitted_at, attempt_id)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
                """,
                (
                    student_id,
                    quiz_id,
                    score,
                    percentage,
                    attempt_id,
                ),
            )

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        _close_db(cursor, db)

    _ensure_question_explanations(questions)

    review = _build_result_review(questions, answers)

    # Keep the template variable names commonly used by result.html.
    result_data = {
        "score": score,
        "total": total_questions,
        "total_questions": total_questions,
        "percentage": percentage,
        "answers": answers,
        "review": review,
        "questions": review,
        "quiz_id": quiz_id,
        "quiz_title": None,
        "attempt_id": attempt_id,
    }

    quiz_record = _get_quiz(quiz_id)

    if quiz_record:
        result_data["quiz_title"] = quiz_record.get("title")

    # Clear attempt state only after successfully saving the result.
    _clear_active_quiz_session()

    return render_template(
        "result.html",
        **result_data,
        result=result_data,
        review=review,
        questions=review,
    )


# ---------------------------------------------------------
# MY RESULTS
# ---------------------------------------------------------

@student.route("/my_results")
def my_results():
    if not session.get("student_id"):
        return redirect(url_for("auth.login"))

    db = _db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """
            SELECT r.result_id, r.quiz_id, r.score, r.percentage,
                   r.submitted_at, r.attempt_id, q.title,
                   q.total_questions
            FROM results r
            LEFT JOIN quizzes q ON q.quiz_id = r.quiz_id
            WHERE r.student_id = %s
            ORDER BY r.submitted_at DESC
            """,
            (session["student_id"],),
        )

        results = cursor.fetchall()

        return render_template("my_results.html", results=results)

    finally:
        _close_db(cursor, db)


# ---------------------------------------------------------
# LEADERBOARD
# ---------------------------------------------------------

@student.route("/leaderboard")
def leaderboard():
    db = _db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """
            SELECT
                COALESCE(s.full_name, qa.student_name, 'Guest') AS student_name,
                COALESCE(s.roll_number, qa.roll_number, '-') AS roll_number,
                r.quiz_id,
                q.title AS quiz_title,
                r.score,
                r.percentage,
                r.submitted_at
            FROM results r
            LEFT JOIN students s ON s.student_id = r.student_id
            LEFT JOIN quiz_attempts qa ON qa.attempt_id = r.attempt_id
            LEFT JOIN quizzes q ON q.quiz_id = r.quiz_id
            ORDER BY r.percentage DESC NULLS LAST,
                     r.score DESC NULLS LAST,
                     r.submitted_at ASC
            LIMIT 100
            """
        )

        leaderboard_data = cursor.fetchall()

        return render_template(
            "leaderboard.html",
            leaderboard=leaderboard_data,
            results=leaderboard_data,
        )

    finally:
        _close_db(cursor, db)


# ---------------------------------------------------------
# STUDENT PROFILE
# ---------------------------------------------------------

@student.route("/student_profile")
def student_profile():
    if not session.get("student_id"):
        return redirect(url_for("auth.login"))

    student_record = _get_student(session["student_id"])

    if not student_record:
        session.pop("student_id", None)
        return redirect(url_for("auth.login"))

    db = _db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """
            SELECT COUNT(*) AS total_quizzes,
                   COALESCE(AVG(percentage), 0) AS average_percentage,
                   COALESCE(MAX(percentage), 0) AS best_percentage
            FROM results
            WHERE student_id = %s
            """,
            (session["student_id"],),
        )

        stats = cursor.fetchone()

        cursor.execute(
            """
            SELECT r.result_id, r.quiz_id, r.score, r.percentage,
                   r.submitted_at, q.title
            FROM results r
            LEFT JOIN quizzes q ON q.quiz_id = r.quiz_id
            WHERE r.student_id = %s
            ORDER BY r.submitted_at DESC
            LIMIT 10
            """,
            (session["student_id"],),
        )

        recent_results = cursor.fetchall()

        return render_template(
            "student_profile.html",
            student=student_record,
            stats=stats,
            recent_results=recent_results,
        )

    finally:
        _close_db(cursor, db)

