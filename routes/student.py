import os
import time
from datetime import datetime

import psycopg2
from psycopg2.extras import RealDictCursor

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    current_app,
)

student = Blueprint("student", __name__)


# ============================================================
# DATABASE HELPERS
# ============================================================

def _db_connection():
    """Create a PostgreSQL connection using the application's
    configured database URL or Render's DATABASE_URL.
    """
    database_url = (
        current_app.config.get("DATABASE_URL")
        or current_app.config.get("SQLALCHEMY_DATABASE_URI")
        or os.getenv("DATABASE_URL")
    )

    if not database_url:
        raise RuntimeError(
            "Database URL missing. Configure DATABASE_URL."
        )

    connect_args = {}

    if database_url.startswith(("postgres://", "postgresql://")):
        connect_args["sslmode"] = os.getenv(
            "DB_SSLMODE", "require"
        )

    return psycopg2.connect(
        database_url,
        cursor_factory=RealDictCursor,
        **connect_args
    )


def _close_db(connection):
    if connection:
        try:
            connection.close()
        except Exception:
            current_app.logger.exception(
                "Could not close database connection."
            )


def _get_quiz(quiz_id):
    connection = _db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM quizzes
                WHERE quiz_id = %s
                """,
                (quiz_id,),
            )
            return cursor.fetchone()
    finally:
        _close_db(connection)


def _get_student(student_id):
    connection = _db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM students
                WHERE student_id = %s
                """,
                (student_id,),
            )
            return cursor.fetchone()
    finally:
        _close_db(connection)


def _get_attempt(attempt_id):
    connection = _db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM quiz_attempts
                WHERE attempt_id = %s
                """,
                (attempt_id,),
            )
            return cursor.fetchone()
    finally:
        _close_db(connection)


def _get_attempt_questions(quiz_id):
    connection = _db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    question_id,
                    quiz_id,
                    question,
                    option_a,
                    option_b,
                    option_c,
                    option_d,
                    difficulty,
                    explanation
                FROM questions
                WHERE quiz_id = %s
                ORDER BY question_id
                """,
                (quiz_id,),
            )
            return cursor.fetchall()
    finally:
        _close_db(connection)


# ============================================================
# TIME AND QUIZ AVAILABILITY
# ============================================================

def _to_datetime(value):
    if value is None:
        return None

    if isinstance(value, datetime):
        return value

    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _now_like(value):
    now = datetime.now()

    if value is not None and getattr(value, "tzinfo", None):
        from datetime import timezone
        now = datetime.now(timezone.utc)

    return now


def _quiz_status(quiz):
    now = datetime.now()

    available_from = _to_datetime(
        quiz.get("available_from")
    )
    available_until = _to_datetime(
        quiz.get("available_until")
    )

    if available_from is not None:
        now = _now_like(available_from)

        if now < available_from:
            return "upcoming"

    if available_until is not None:
        now = _now_like(available_until)

        if now > available_until:
            return "expired"

    return "available"


def _calculate_quiz_duration_seconds(quiz):
    try:
        duration_minutes = int(
            quiz.get("duration_minutes") or 0
        )
    except (TypeError, ValueError):
        duration_minutes = 0

    return max(duration_minutes, 0) * 60


def _effective_deadline(attempt, quiz):
    started_at = _to_datetime(attempt.get("started_at"))

    if started_at is None:
        return None

    duration_seconds = _calculate_quiz_duration_seconds(
        quiz
    )

    if duration_seconds <= 0:
        return None

    from datetime import timedelta

    return started_at + timedelta(
        seconds=duration_seconds
    )


def _seconds_left(deadline):
    if deadline is None:
        return 0

    now = _now_like(deadline)

    try:
        return max(
            0,
            int((deadline - now).total_seconds()),
        )
    except (TypeError, ValueError):
        return 0


def _get_quiz_duration(quiz):
    return _calculate_quiz_duration_seconds(quiz)


def _can_access_quiz(quiz):
    if not quiz:
        return False

    return _quiz_status(quiz) == "available"


def _quiz_unavailable_page(quiz, message):
    return render_template(
        "quiz.html",
        quiz=quiz,
        questions=[],
        unavailable=True,
        unavailable_message=message,
    )


# ============================================================
# SESSION HELPERS
# ============================================================

def _active_attempt_id():
    return session.get("active_attempt_id")


def _is_guest():
    return session.get("attempt_mode") == "guest"


def _clear_active_quiz_session():
    keys = [
        "active_attempt_id",
        "active_quiz_id",
        "attempt_id",
        "quiz_id",
        "attempt_mode",
        "question_started_at",
    ]

    for key in keys:
        session.pop(key, None)


def _get_attempt_answers(attempt_id):
    connection = _db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT question_id, selected_option
                FROM student_answers
                WHERE attempt_id = %s
                """,
                (attempt_id,),
            )

            rows = cursor.fetchall()

            return {
                row["question_id"]: row["selected_option"]
                for row in rows
            }
    finally:
        _close_db(connection)


# ============================================================
# ANSWER SAVING
# ============================================================

def _save_answer(
    attempt,
    question_id,
    selected_option,
):
    if selected_option not in ("A", "B", "C", "D"):
        return False

    connection = _db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT question_id
                FROM questions
                WHERE question_id = %s
                  AND quiz_id = %s
                """,
                (
                    question_id,
                    attempt["quiz_id"],
                ),
            )

            if not cursor.fetchone():
                return False

            cursor.execute(
                """
                SELECT student_id, quiz_id, status
                FROM quiz_attempts
                WHERE attempt_id = %s
                FOR UPDATE
                """,
                (attempt["attempt_id"],),
            )

            current_attempt = cursor.fetchone()

            if not current_attempt:
                connection.rollback()
                return False

            if current_attempt["status"] == "submitted":
                connection.rollback()
                return False

            cursor.execute(
                """
                SELECT question_id
                FROM student_answers
                WHERE attempt_id = %s
                  AND question_id = %s
                """,
                (
                    attempt["attempt_id"],
                    question_id,
                ),
            )

            existing = cursor.fetchone()

            if existing:
                cursor.execute(
                    """
                    UPDATE student_answers
                    SET selected_option = %s
                    WHERE attempt_id = %s
                      AND question_id = %s
                    """,
                    (
                        selected_option,
                        attempt["attempt_id"],
                        question_id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO student_answers
                    (
                        student_id,
                        quiz_id,
                        question_id,
                        selected_option,
                        attempt_id
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        current_attempt["student_id"],
                        current_attempt["quiz_id"],
                        question_id,
                        selected_option,
                        attempt["attempt_id"],
                    ),
                )

        connection.commit()
        return True

    except Exception:
        connection.rollback()
        current_app.logger.exception(
            "Failed to save quiz answer."
        )
        raise

    finally:
        _close_db(connection)


# ============================================================
# ATTEMPT SUBMISSION AND RESULT CALCULATION
# ============================================================

def _mark_attempt_submitted(attempt_id):
    connection = _db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM quiz_attempts
                WHERE attempt_id = %s
                FOR UPDATE
                """,
                (attempt_id,),
            )

            attempt = cursor.fetchone()

            if not attempt:
                connection.rollback()
                return None

            if attempt["status"] == "submitted":
                connection.commit()
                return attempt

            cursor.execute(
                """
                SELECT
                    q.question_id,
                    q.correct_option,
                    sa.selected_option
                FROM questions q
                LEFT JOIN student_answers sa
                  ON sa.question_id = q.question_id
                 AND sa.attempt_id = %s
                WHERE q.quiz_id = %s
                """,
                (
                    attempt_id,
                    attempt["quiz_id"],
                ),
            )

            rows = cursor.fetchall()

            total_questions = len(rows)
            score = sum(
                1
                for row in rows
                if row["selected_option"]
                and str(row["selected_option"]).upper()
                == str(row["correct_option"]).upper()
            )

            percentage = (
                round(score * 100.0 / total_questions, 2)
                if total_questions
                else 0.0
            )

            cursor.execute(
                """
                UPDATE quiz_attempts
                SET status = 'submitted',
                    submitted_at = CURRENT_TIMESTAMP,
                    score = %s,
                    percentage = %s
                WHERE attempt_id = %s
                """,
                (
                    score,
                    percentage,
                    attempt_id,
                ),
            )

            cursor.execute(
                """
                SELECT result_id
                FROM results
                WHERE attempt_id = %s
                """,
                (attempt_id,),
            )

            result_exists = cursor.fetchone()

            if result_exists:
                cursor.execute(
                    """
                    UPDATE results
                    SET score = %s,
                        percentage = %s,
                        submitted_at = CURRENT_TIMESTAMP
                    WHERE attempt_id = %s
                    """,
                    (
                        score,
                        percentage,
                        attempt_id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO results
                    (
                        student_id,
                        quiz_id,
                        score,
                        percentage,
                        submitted_at,
                        attempt_id
                    )
                    VALUES (%s, %s, %s, %s,
                            CURRENT_TIMESTAMP, %s)
                    """,
                    (
                        attempt["student_id"],
                        attempt["quiz_id"],
                        score,
                        percentage,
                        attempt_id,
                    ),
                )

        connection.commit()

        return _get_attempt(attempt_id)

    except Exception:
        connection.rollback()
        current_app.logger.exception(
            "Failed to submit quiz attempt."
        )
        raise

    finally:
        _close_db(connection)


def _insert_result(attempt):
    if not attempt:
        return

    if attempt.get("status") != "submitted":
        return

    connection = _db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT result_id
                FROM results
                WHERE attempt_id = %s
                """,
                (attempt["attempt_id"],),
            )

            if cursor.fetchone():
                return

            cursor.execute(
                """
                INSERT INTO results
                (
                    student_id,
                    quiz_id,
                    score,
                    percentage,
                    submitted_at,
                    attempt_id
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    attempt["student_id"],
                    attempt["quiz_id"],
                    attempt["score"] or 0,
                    attempt["percentage"] or 0,
                    attempt["submitted_at"],
                    attempt["attempt_id"],
                ),
            )

        connection.commit()

    except Exception:
        connection.rollback()
        current_app.logger.exception(
            "Failed to insert quiz result."
        )
        raise

    finally:
        _close_db(connection)


def _ensure_question_explanations():
    """Compatibility helper. The questions table already
    includes the explanation column in this project.
    """
    return True


def _build_result_review(attempt_id):
    connection = _db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    q.question_id,
                    q.question,
                    q.option_a,
                    q.option_b,
                    q.option_c,
                    q.option_d,
                    q.correct_option,
                    q.explanation,
                    q.difficulty,
                    sa.selected_option
                FROM quiz_attempts a
                JOIN questions q
                  ON q.quiz_id = a.quiz_id
                LEFT JOIN student_answers sa
                  ON sa.question_id = q.question_id
                 AND sa.attempt_id = a.attempt_id
                WHERE a.attempt_id = %s
                ORDER BY q.question_id
                """,
                (attempt_id,),
            )

            return cursor.fetchall()

    finally:
        _close_db(connection)


# ============================================================
# STUDENT DASHBOARD
# ============================================================

@student.route("/student_dashboard")
def student_dashboard():
    if not session.get("student_id"):
        return redirect(url_for("auth.login"))

    connection = _db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM students
                WHERE student_id = %s
                """,
                (session["student_id"],),
            )
            student_data = cursor.fetchone()

            cursor.execute(
                """
                SELECT *
                FROM quizzes
                ORDER BY created_at DESC
                """
            )
            quizzes = cursor.fetchall()

        return render_template(
            "student_dashboard.html",
            student=student_data,
            quizzes=quizzes,
        )

    finally:
        _close_db(connection)


# ============================================================
# AVAILABLE QUIZZES
# ============================================================

@student.route("/available_quizzes")
def available_quizzes():
    connection = _db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM quizzes
                ORDER BY created_at DESC
                """
            )
            quizzes = cursor.fetchall()

        return render_template(
            "available_quizzes.html",
            quizzes=quizzes,
        )

    finally:
        _close_db(connection)


# ============================================================
# LOGGED-IN STUDENT STARTS A QUIZ
# ============================================================

@student.route("/start_quiz/<int:quiz_id>")
def start_quiz(quiz_id):
    student_id = session.get("student_id")

    if not student_id:
        return redirect(url_for("auth.login"))

    quiz = _get_quiz(quiz_id)

    if not quiz:
        flash("Quiz not found.", "error")
        return redirect(
            url_for("student.available_quizzes")
        )

    if not _can_access_quiz(quiz):
        flash(
            "This quiz is not currently available.",
            "error",
        )
        return redirect(
            url_for("student.available_quizzes")
        )

    connection = _db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM students
                WHERE student_id = %s
                """,
                (student_id,),
            )
            student_data = cursor.fetchone()

            if not student_data:
                session.pop("student_id", None)
                flash(
                    "Please log in again.",
                    "error",
                )
                return redirect(url_for("auth.login"))

            cursor.execute(
                """
                SELECT *
                FROM quiz_attempts
                WHERE quiz_id = %s
                  AND student_id = %s
                  AND status = 'in_progress'
                ORDER BY attempt_id DESC
                LIMIT 1
                """,
                (quiz_id, student_id),
            )

            attempt = cursor.fetchone()

            if not attempt:
                cursor.execute(
                    """
                    INSERT INTO quiz_attempts
                    (
                        quiz_id,
                        student_id,
                        student_name,
                        roll_number,
                        attempt_mode,
                        started_at,
                        status,
                        score,
                        percentage
                    )
                    VALUES (
                        %s, %s, %s, %s, 'student',
                        CURRENT_TIMESTAMP,
                        'in_progress', 0, 0
                    )
                    RETURNING *
                    """,
                    (
                        quiz_id,
                        student_id,
                        student_data["full_name"],
                        student_data["roll_number"],
                    ),
                )

                attempt = cursor.fetchone()

        connection.commit()

    except Exception:
        connection.rollback()
        current_app.logger.exception(
            "Unable to start student quiz."
        )
        flash(
            "Unable to start the quiz. Please try again.",
            "error",
        )
        return redirect(
            url_for("student.available_quizzes")
        )

    finally:
        _close_db(connection)

    session["active_attempt_id"] = attempt["attempt_id"]
    session["active_quiz_id"] = quiz_id
    session["attempt_mode"] = "student"

    return redirect(url_for("student.quiz"))


# ============================================================
# GUEST START FORM
# ============================================================

@student.route(
    "/guest_start_quiz/<int:quiz_id>",
    methods=["GET", "POST"],
)
def guest_start_quiz(quiz_id):
    quiz = _get_quiz(quiz_id)

    if not quiz:
        return "Quiz not found.", 404

    if request.method == "GET":
        return render_template(
            "guest_start_quiz.html",
            quiz=quiz,
            error=None,
        )

    # Accept all field names used by older form versions.
    student_name = (
        request.form.get("student_name")
        or request.form.get("name")
        or request.form.get("full_name")
        or ""
    ).strip()

    roll_number = (
        request.form.get("roll_number")
        or request.form.get("roll")
        or ""
    ).strip()

    if not student_name or not roll_number:
        return render_template(
            "guest_start_quiz.html",
            quiz=quiz,
            error="Please enter your name and roll number.",
        ), 400

    if not _can_access_quiz(quiz):
        return render_template(
            "guest_start_quiz.html",
            quiz=quiz,
            error="This quiz is not currently available.",
        ), 400

    connection = _db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO quiz_attempts
                (
                    quiz_id,
                    student_id,
                    student_name,
                    roll_number,
                    attempt_mode,
                    started_at,
                    status,
                    score,
                    percentage
                )
                VALUES (
                    %s, NULL, %s, %s, 'guest',
                    CURRENT_TIMESTAMP,
                    'in_progress', 0, 0
                )
                RETURNING *
                """,
                (
                    quiz_id,
                    student_name,
                    roll_number,
                ),
            )

            attempt = cursor.fetchone()

        connection.commit()

    except Exception:
        connection.rollback()
        current_app.logger.exception(
            "Unable to create guest attempt."
        )

        return render_template(
            "guest_start_quiz.html",
            quiz=quiz,
            error="Unable to start the quiz. Please try again.",
        ), 500

    finally:
        _close_db(connection)

    _clear_active_quiz_session()

    session["active_attempt_id"] = attempt["attempt_id"]
    session["active_quiz_id"] = quiz_id
    session["attempt_mode"] = "guest"
    session["guest_name"] = student_name
    session["guest_roll_number"] = roll_number

    return redirect(url_for("student.quiz"))


@student.route("/guest_start", methods=["POST"])
def guest_start():
    quiz_id = request.form.get("quiz_id", type=int)

    if not quiz_id:
        flash("Invalid quiz ID.", "error")
        return redirect(
            url_for("student.available_quizzes")
        )

    return redirect(
        url_for(
            "student.guest_start_quiz",
            quiz_id=quiz_id,
        )
    )


# ============================================================
# QUIZ PAGE AND ANSWER SUBMISSION
# ============================================================

@student.route("/quiz", methods=["GET", "POST"])
def quiz():
    attempt_id = _active_attempt_id()

    if not attempt_id:
        flash(
            "Please start a quiz first.",
            "error",
        )
        return redirect(
            url_for("student.available_quizzes")
        )

    attempt = _get_attempt(attempt_id)

    if not attempt:
        _clear_active_quiz_session()
        flash("Quiz attempt not found.", "error")
        return redirect(
            url_for("student.available_quizzes")
        )

    quiz_data = _get_quiz(attempt["quiz_id"])

    if not quiz_data:
        _clear_active_quiz_session()
        return "Quiz not found.", 404

    # Prevent access to a different student's attempt.
    if not _is_guest():
        logged_in_id = session.get("student_id")

        if (
            not logged_in_id
            or attempt["student_id"] != logged_in_id
        ):
            _clear_active_quiz_session()
            return redirect(url_for("auth.login"))

    # Show results if the attempt was already submitted.
    if attempt["status"] == "submitted":
        review = _build_result_review(attempt_id)

        return render_template(
            "quiz.html",
            quiz=quiz_data,
            questions=[],
            result=attempt,
            review=review,
            show_result=True,
            unavailable=False,
        )

    questions = _get_attempt_questions(
        attempt["quiz_id"]
    )

    if not questions:
        return _quiz_unavailable_page(
            quiz_data,
            "This quiz does not contain any questions.",
        )

    deadline = _effective_deadline(
        attempt,
        quiz_data,
    )

    remaining_seconds = _seconds_left(deadline)

    if request.method == "POST":
        posted_question_id = request.form.get(
            "question_id",
            type=int,
        )

        selected_option = (
            request.form.get("selected_option")
            or ""
        ).strip().upper()

        # Save only a valid question belonging to this quiz.
        if posted_question_id:
            question_ids = {
                question["question_id"]
                for question in questions
            }

            if posted_question_id not in question_ids:
                flash(
                    "Invalid question. Please try again.",
                    "error",
                )
                return redirect(url_for("student.quiz"))

            if selected_option in ("A", "B", "C", "D"):
                if remaining_seconds > 0:
                    try:
                        _save_answer(
                            attempt,
                            posted_question_id,
                            selected_option,
                        )
                    except Exception:
                        flash(
                            "Unable to save your answer.",
                            "error",
                        )
                        return redirect(
                            url_for("student.quiz")
                        )

        # Submit once the overall timer has expired.
        if remaining_seconds <= 0:
            _mark_attempt_submitted(attempt_id)

            return redirect(
                url_for("student.quiz")
            )

        action = request.form.get("action", "save")

        if action == "submit":
            _mark_attempt_submitted(attempt_id)

            return redirect(
                url_for("student.quiz")
            )

        return redirect(url_for("student.quiz"))

    # Refresh attempt in case it was submitted elsewhere.
    attempt = _get_attempt(attempt_id)

    if not attempt or attempt["status"] == "submitted":
        return redirect(url_for("student.quiz"))

    answers = _get_attempt_answers(attempt_id)

    try:
        question_time_seconds = int(
            quiz_data.get("question_time_seconds") or 0
        )
    except (TypeError, ValueError):
        question_time_seconds = 0

    return render_template(
        "quiz.html",
        quiz=quiz_data,
        questions=questions,
        answers=answers,
        attempt=attempt,
        remaining_seconds=remaining_seconds,
        question_time_seconds=question_time_seconds,
        unavailable=False,
        show_result=False,
    )


# ============================================================
# EXPLICIT SUBMIT ROUTE
# ============================================================

@student.route(
    "/submit_quiz",
    methods=["GET", "POST"],
)
def submit_quiz():
    attempt_id = _active_attempt_id()

    if not attempt_id:
        flash("No active quiz found.", "error")
        return redirect(
            url_for("student.available_quizzes")
        )

    attempt = _get_attempt(attempt_id)

    if not attempt:
        _clear_active_quiz_session()
        return redirect(
            url_for("student.available_quizzes")
        )

    if not _is_guest():
        if (
            not session.get("student_id")
            or attempt["student_id"] != session.get("student_id")
        ):
            _clear_active_quiz_session()
            return redirect(url_for("auth.login"))

    if attempt["status"] != "submitted":
        _mark_attempt_submitted(attempt_id)

    # Do not clear the active attempt yet; the result page
    # needs it to show the submitted score and answer review.
    return redirect(url_for("student.quiz"))


# ============================================================
# STUDENT RESULTS
# ============================================================

@student.route("/my_results")
def my_results():
    student_id = session.get("student_id")

    if not student_id:
        return redirect(url_for("auth.login"))

    connection = _db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    r.*,
                    q.title AS quiz_title
                FROM results r
                LEFT JOIN quizzes q
                  ON q.quiz_id = r.quiz_id
                WHERE r.student_id = %s
                ORDER BY r.submitted_at DESC
                """,
                (student_id,),
            )

            results = cursor.fetchall()

        return render_template(
            "my_results.html",
            results=results,
        )

    finally:
        _close_db(connection)


# ============================================================
# LEADERBOARD
# ============================================================

@student.route("/leaderboard")
def leaderboard():
    connection = _db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    a.student_name,
                    a.roll_number,
                    q.title AS quiz_title,
                    a.score,
                    a.percentage,
                    a.submitted_at
                FROM quiz_attempts a
                JOIN quizzes q
                  ON q.quiz_id = a.quiz_id
                WHERE a.status = 'submitted'
                ORDER BY
                    a.percentage DESC NULLS LAST,
                    a.score DESC NULLS LAST,
                    a.submitted_at ASC
                """
            )

            leaderboard_data = cursor.fetchall()

        return render_template(
            "leaderboard.html",
            leaderboard=leaderboard_data,
            results=leaderboard_data,
        )

    finally:
        _close_db(connection)


# ============================================================
# STUDENT PROFILE
# ============================================================

@student.route("/student_profile")
def student_profile():
    student_id = session.get("student_id")

    if not student_id:
        return redirect(url_for("auth.login"))

    student_data = _get_student(student_id)

    if not student_data:
        session.pop("student_id", None)
        return redirect(url_for("auth.login"))

    connection = _db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS total_attempts,
                    COALESCE(AVG(percentage), 0) AS average_percentage
                FROM quiz_attempts
                WHERE student_id = %s
                  AND status = 'submitted'
                """,
                (student_id,),
            )

            statistics = cursor.fetchone()

        return render_template(
            "student_profile.html",
            student=student_data,
            statistics=statistics,
        )

    finally:
        _close_db(connection)