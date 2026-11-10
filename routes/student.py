import time
import json
import random
from datetime import datetime

from psycopg2.extras import RealDictCursor
from database import get_db_connection

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    current_app,
    jsonify,
)

student = Blueprint("student", __name__)



# ============================================================
# DATABASE HELPERS
# ============================================================


def _db_connection():
    # All app routes share the same bounded process-local pool.
    return get_db_connection(cursor_factory=RealDictCursor)


def _close_db(connection):
    if connection is not None:
        connection.close()


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


def _get_attempt_and_quiz(attempt_id, lightweight=False):
    """
    Fetch attempt and quiz metadata in one database round trip.

    GET:
        Fetch complete quiz metadata.

    POST:
        Fetch only the quiz fields required for answer processing.
    """

    connection = _db_connection()

    try:
        with connection.cursor() as cursor:

            if lightweight:
                cursor.execute(
                    """
                    SELECT
                        a.*,
                        jsonb_build_object(
                            'total_questions', q.total_questions,
                            'question_time_seconds', q.question_time_seconds,
                            'duration_minutes', q.duration_minutes
                        ) AS quiz_data
                    FROM quiz_attempts AS a
                    JOIN quizzes AS q
                        ON q.quiz_id = a.quiz_id
                    WHERE a.attempt_id = %s
                    """,
                    (attempt_id,),
                )

            else:
                cursor.execute(
                    """
                    SELECT
                        a.*,
                        to_jsonb(q) AS quiz_data
                    FROM quiz_attempts AS a
                    JOIN quizzes AS q
                        ON q.quiz_id = a.quiz_id
                    WHERE a.attempt_id = %s
                    """,
                    (attempt_id,),
                )

            row = cursor.fetchone()

            if not row:
                return None, None

            attempt = dict(row)
            quiz_data = attempt.pop("quiz_data", None)

            return attempt, quiz_data

    finally:
        _close_db(connection)
        
        
def _normalise_json_list(value):
    """Accept PostgreSQL JSONB values or JSON strings."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return value


def _ensure_attempt_orders(attempt):
    """Persist a unique question and option order for this attempt."""
    question_order = _normalise_json_list(attempt.get("question_order"))
    option_order = _normalise_json_list(attempt.get("option_order"))
    if question_order and option_order:
        return attempt

    connection = _db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT question_order, option_order FROM quiz_attempts WHERE attempt_id = %s FOR UPDATE",
                (attempt["attempt_id"],),
            )
            stored = cursor.fetchone()
            if not stored:
                connection.rollback()
                return attempt

            question_order = _normalise_json_list(stored.get("question_order"))
            option_order = _normalise_json_list(stored.get("option_order"))

            cursor.execute(
                "SELECT question_id FROM questions WHERE quiz_id = %s ORDER BY question_id",
                (attempt["quiz_id"],),
            )
            question_ids = [row["question_id"] for row in cursor.fetchall()]

            if not question_order:
                question_order = question_ids[:]
                random.SystemRandom().shuffle(question_order)

            if not option_order:
                option_order = {}
                for question_id in question_ids:
                    letters = ["A", "B", "C", "D"]
                    random.SystemRandom().shuffle(letters)
                    option_order[str(question_id)] = letters

            cursor.execute(
                """
                UPDATE quiz_attempts
                SET question_order = %s::jsonb,
                    option_order = %s::jsonb
                WHERE attempt_id = %s
                RETURNING question_order, option_order
                """,
                (json.dumps(question_order), json.dumps(option_order), attempt["attempt_id"]),
            )
            saved = cursor.fetchone()
            connection.commit()
            attempt["question_order"] = saved["question_order"]
            attempt["option_order"] = saved["option_order"]
            return attempt
    except Exception:
        connection.rollback()
        current_app.logger.exception("Unable to initialise per-attempt shuffle order.")
        raise
    finally:
        _close_db(connection)


def _get_attempt_questions(quiz_id, attempt=None):
    connection = _db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT question_id, quiz_id, question,
                       option_a, option_b, option_c, option_d,
                       difficulty, explanation
                FROM questions
                WHERE quiz_id = %s
                """,
                (quiz_id,),
            )
            rows = cursor.fetchall()

        if attempt:
            order = _normalise_json_list(attempt.get("question_order")) or []
            position = {int(qid): index for index, qid in enumerate(order)}
            rows.sort(key=lambda row: position.get(int(row["question_id"]), len(position)))
            option_orders = _normalise_json_list(attempt.get("option_order")) or {}
            for row in rows:
                qid = str(row["question_id"])
                letters = option_orders.get(qid, ["A", "B", "C", "D"])
                option_map = {
                    "A": row.get("option_a"), "B": row.get("option_b"),
                    "C": row.get("option_c"), "D": row.get("option_d"),
                }
                row["display_options"] = [
                    (display_label, original_letter, option_map.get(original_letter))
                    for display_label, original_letter in zip(("A", "B", "C", "D"), letters)
                ]
        else:
            rows.sort(key=lambda row: row["question_id"])
        return rows
    finally:
        _close_db(connection)


def _get_attempt_question_at(quiz_id, question_index, attempt=None):
    """Read the server-generated shuffled question ID without another DB trip.

    The order is persisted on quiz_attempts when the attempt is initialized.
    _save_answer() still validates that the selected question belongs to this
    quiz and that the attempt is active before writing the answer.
    """
    order = _normalise_json_list((attempt or {}).get("question_order")) or []
    if order and 0 <= question_index < len(order):
        try:
            return {"question_id": int(order[question_index])}
        except (TypeError, ValueError):
            pass

    connection = _db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT question_id FROM questions WHERE quiz_id = %s ORDER BY question_id LIMIT 1 OFFSET %s",
                (quiz_id, question_index),
            )
            return cursor.fetchone()
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




def _quiz_countdown_page(quiz):
    """Show a live countdown without creating an attempt before the start time."""
    return render_template(
        "quiz_countdown.html",
        quiz=quiz,
        start_time=quiz.get("available_from"),
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
        "question_started_index",
        "quiz_current_index",
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


def _save_answer(attempt, question_id, selected_option):
    import time

    if selected_option not in ("A", "B", "C", "D"):
        return False

    total_start = time.perf_counter()

    connection_start = time.perf_counter()
    connection = _db_connection()
    connection_ms = (
        time.perf_counter() - connection_start
    ) * 1000

    query_ms = 0
    fetch_ms = 0
    commit_ms = 0
    saved_answer = None

    try:
        with connection.cursor() as cursor:
            query_start = time.perf_counter()

            cursor.execute(
                """
                WITH locked_attempt AS MATERIALIZED (
                    SELECT
                        attempt_id,
                        student_id,
                        quiz_id,
                        status
                    FROM quiz_attempts
                    WHERE attempt_id = %s
                    FOR UPDATE
                ),
                valid_question AS (
                    SELECT q.question_id
                    FROM questions q
                    JOIN locked_attempt a
                      ON a.quiz_id = q.quiz_id
                    WHERE q.question_id = %s
                      AND a.status <> 'submitted'
                )
                INSERT INTO student_answers (
                    student_id,
                    quiz_id,
                    question_id,
                    selected_option,
                    attempt_id
                )
                SELECT
                    a.student_id,
                    a.quiz_id,
                    q.question_id,
                    %s,
                    a.attempt_id
                FROM locked_attempt a
                JOIN valid_question q ON TRUE
                WHERE a.status <> 'submitted'
                ON CONFLICT (attempt_id, question_id)
                    WHERE attempt_id IS NOT NULL
                      AND question_id IS NOT NULL
                DO UPDATE SET
                    selected_option = EXCLUDED.selected_option
                RETURNING question_id
                """,
                (
                    attempt["attempt_id"],
                    question_id,
                    selected_option,
                ),
            )

            query_ms = (
                time.perf_counter() - query_start
            ) * 1000

            fetch_start = time.perf_counter()
            saved_answer = cursor.fetchone()

            fetch_ms = (
                time.perf_counter() - fetch_start
            ) * 1000

        commit_start = time.perf_counter()
        connection.commit()

        commit_ms = (
            time.perf_counter() - commit_start
        ) * 1000

        return saved_answer is not None

    except Exception:
        connection.rollback()

        current_app.logger.exception(
            "Failed to save quiz answer."
        )
        raise

    finally:
        total_ms = (
            time.perf_counter() - total_start
        ) * 1000

        current_app.logger.info(
            "ANSWER_SAVE_PERFORMANCE "
            "connection_ms=%.2f "
            "query_ms=%.2f "
            "fetch_ms=%.2f "
            "commit_ms=%.2f "
            "total_ms=%.2f",
            connection_ms,
            query_ms,
            fetch_ms,
            commit_ms,
            total_ms,
        )

        _close_db(connection)

# ============================================================
# ATTEMPT SUBMISSION AND RESULT CALCULATION
# ============================================================

def _mark_attempt_submitted(attempt_id):
    """Idempotently score and submit an attempt in one short transaction."""
    connection = _db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM quiz_attempts WHERE attempt_id = %s FOR UPDATE",
                (attempt_id,),
            )
            attempt = cursor.fetchone()
            if not attempt:
                connection.rollback()
                return None
            if attempt["status"] == "submitted":
                connection.commit()
                return attempt

            # Aggregate in PostgreSQL instead of transferring every answer to Python.
            cursor.execute(
                """
                WITH totals AS (
                    SELECT
                        COUNT(q.question_id)::INTEGER AS total_questions,
                        COUNT(*) FILTER (
                            WHERE sa.selected_option IS NOT NULL
                              AND UPPER(BTRIM(sa.selected_option)) =
                                  UPPER(BTRIM(q.correct_option))
                        )::INTEGER AS score
                    FROM questions q
                    LEFT JOIN student_answers sa
                      ON sa.question_id = q.question_id
                     AND sa.attempt_id = %s
                    WHERE q.quiz_id = %s
                )
                UPDATE quiz_attempts a
                SET status = 'submitted',
                    submitted_at = COALESCE(a.submitted_at, CURRENT_TIMESTAMP),
                    score = totals.score,
                    percentage = CASE
                        WHEN totals.total_questions > 0
                        THEN ROUND(totals.score * 100.0 / totals.total_questions, 2)
                        ELSE 0
                    END
                FROM totals
                WHERE a.attempt_id = %s
                RETURNING a.*
                """,
                (attempt_id, attempt["quiz_id"], attempt_id),
            )
            submitted_attempt = cursor.fetchone()

            cursor.execute(
                """
                INSERT INTO results
                    (student_id, quiz_id, score, percentage, submitted_at, attempt_id)
                VALUES (%s, %s, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP), %s)
                ON CONFLICT (attempt_id) WHERE attempt_id IS NOT NULL
                DO UPDATE SET
                    score = EXCLUDED.score,
                    percentage = EXCLUDED.percentage,
                    submitted_at = EXCLUDED.submitted_at
                """,
                (
                    submitted_attempt["student_id"],
                    submitted_attempt["quiz_id"],
                    submitted_attempt["score"],
                    submitted_attempt["percentage"],
                    submitted_attempt.get("submitted_at"),
                    attempt_id,
                ),
            )

        connection.commit()
        return submitted_attempt
    except Exception:
        connection.rollback()
        current_app.logger.exception("Failed to submit quiz attempt.")
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

            rows = cursor.fetchall()

        review = []

        for number, row in enumerate(rows, start=1):
            selected = (
                (row.get("selected_option") or "").strip().upper()
            )
            correct = (
                (row.get("correct_option") or "").strip().upper()
            )

            options = {
                "A": row.get("option_a"),
                "B": row.get("option_b"),
                "C": row.get("option_c"),
                "D": row.get("option_d"),
            }

            review.append({
                "question_id": row["question_id"],
                "question_number": number,
                "question": row.get("question"),
                "difficulty": row.get("difficulty"),
                "selected_option": selected or None,
                "selected_text": options.get(selected),
                "correct_option": correct or None,
                "correct_text": options.get(correct),
                "explanation": (
                    row.get("explanation")
                    or "No explanation is available for this question."
                ),
                "is_correct": bool(selected and correct and selected == correct),
            })

        return review

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
# HELPER: CONVERT STORED DATETIME TO IST FOR DISPLAY
# ============================================================

def _to_ist_display_datetime(value):
    if value is None:
        return None

    if not isinstance(value, datetime):
        return value

    from datetime import timezone, timedelta

    ist = timezone(timedelta(hours=5, minutes=30))

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(ist)
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

            quizzes = [
                dict(quiz)
                for quiz in cursor.fetchall()
            ]

               # Calculate status using the original database timestamps.
        for quiz in quizzes:
            actual_status = _quiz_status(quiz)

            # Convert timestamps only for display.
            quiz["available_from"] = _to_ist_display_datetime(
                quiz.get("available_from")
            )

            quiz["available_until"] = _to_ist_display_datetime(
                quiz.get("available_until")
            )

            if actual_status == "upcoming":
                quiz["status"] = "upcoming"

            elif actual_status == "available":
                quiz["status"] = "live"

            else:
                quiz["status"] = "closed"

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

    if _quiz_status(quiz) == "upcoming":
        return _quiz_countdown_page(quiz)

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
                        course,
                        department,
                        semester,
                        attempt_mode,
                        started_at,
                        status,
                        score,
                        percentage
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        'login', CURRENT_TIMESTAMP,
                        'in_progress', 0, 0
                    )
                    ON CONFLICT (quiz_id, student_id)
                    WHERE status = 'in_progress'
                      AND student_id IS NOT NULL
                    DO NOTHING
                    RETURNING *
                    """,
                    (
                        quiz_id,
                        student_id,
                        student_data["full_name"],
                        student_data["roll_number"],
                        student_data["course"],
                        student_data["department"],
                        student_data["semester"],
                    ),
                )

                attempt = cursor.fetchone()

                # Another simultaneous request may have created
                # the in-progress attempt first.
                if not attempt:
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
                    raise RuntimeError(
                        "Unable to create or retrieve quiz attempt."
                    )

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
    session["quiz_current_index"] = 0
    session["question_started_index"] = 0
    session["question_started_at"] = time.time()

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

    if _quiz_status(quiz) == "upcoming":
        return _quiz_countdown_page(quiz)

    if request.method == "GET":
        return render_template(
            "guest_start_quiz.html",
            quiz=quiz,
            error=None,
        )

    # Student details
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

    course = (
        request.form.get("course") or ""
    ).strip().upper()

    department = (
        request.form.get("department") or ""
    ).strip()

    semester = (
        request.form.get("semester") or ""
    ).strip()

    # Validation
    if not all([
        student_name,
        roll_number,
        course,
        department,
        semester
    ]):
        return render_template(
            "guest_start_quiz.html",
            quiz=quiz,
            error="Please fill in all student details.",
        ), 400

    allowed_courses = {
        "MCA", "MSC", "BTECH",
        "MTECH", "BCA", "OTHER"
    }

    if course not in allowed_courses:
        return render_template(
            "guest_start_quiz.html",
            quiz=quiz,
            error="Please select a valid course.",
        ), 400

    if not semester.isdigit() or not 1 <= int(semester) <= 8:
        return render_template(
            "guest_start_quiz.html",
            quiz=quiz,
            error="Please select a valid semester.",
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
                    course,
                    department,
                    semester,
                    attempt_mode,
                    started_at,
                    status,
                    score,
                    percentage
                )
                VALUES (
                    %s, NULL, %s, %s, %s, %s, %s,
                    'guest',
                    CURRENT_TIMESTAMP,
                    'in_progress',
                    0,
                    0
                )
                RETURNING *
                """,
                (
                    quiz_id,
                    student_name,
                    roll_number,
                    course,
                    department,
                    semester,
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
    session["guest_course"] = course
    session["guest_department"] = department
    session["guest_semester"] = semester

    session["quiz_current_index"] = 0
    session["question_started_index"] = 0
    session["question_started_at"] = time.time()

    return redirect(url_for("student.quiz"))



@student.route("/guest_start", methods=["POST"])
def guest_start():
    quiz_id = request.form.get("quiz_id", type=int)

    if not quiz_id:
        flash("Invalid quiz ID.", "error")
        return redirect(
            url_for("student.available_quizzes")
        )

    # Process the submitted name and roll number
    # using the existing guest quiz-start logic.
    return guest_start_quiz(quiz_id)


# ============================================================
# QUIZ PAGE AND ANSWER SUBMISSION
# ============================================================


@student.route("/quiz", methods=["GET", "POST"])
def quiz():
    """Render and process a quiz. AJAX requests save answers without a full page reload."""

    request_started = time.perf_counter()
    timings = {}

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    def ajax_error(message, status=400, **extra):
        payload = {"ok": False, "message": message}
        payload.update(extra)
        return jsonify(payload), status

    attempt_id = _active_attempt_id()
    if not attempt_id:
        if is_ajax:
            return ajax_error("No active quiz found. Please start again.", 401)
        flash("Please start a quiz first.", "error")
        return redirect(url_for("student.available_quizzes"))

    step_started = time.perf_counter()
    attempt, quiz_data = _get_attempt_and_quiz(
    attempt_id,
    lightweight=(request.method == "POST")
)
    timings["get_attempt_and_quiz_ms"] = round(
        (time.perf_counter() - step_started) * 1000, 2
    )
    if not attempt:
        _clear_active_quiz_session()
        if is_ajax:
            return ajax_error("Quiz attempt not found. Please start again.", 404)
        flash("Quiz attempt not found.", "error")
        return redirect(url_for("student.available_quizzes"))

    if not quiz_data:
        _clear_active_quiz_session()
        if is_ajax:
            return ajax_error("Quiz not found.", 404)
        return "Quiz not found.", 404

    # Keep the same randomized sequence for this student's entire attempt.
    try:
        attempt = _ensure_attempt_orders(attempt)
    except Exception:
        if is_ajax:
            return ajax_error("Unable to prepare this quiz. Please retry.", 500)
        return "Unable to prepare this quiz. Please try again.", 500

    # Verify ownership of logged-in student's attempt.
    if not _is_guest():
        logged_in_id = session.get("student_id")

        if not logged_in_id or attempt["student_id"] != logged_in_id:
            _clear_active_quiz_session()
            if is_ajax:
                return ajax_error("Please log in again.", 401)
            return redirect(url_for("auth.login"))

        # Show marks after submission, without revealing answers.
    if attempt["status"] == "submitted":
        if is_ajax:
            return jsonify({
                "ok": True,
                "submitted": True,
                "redirect_url": url_for("student.quiz"),
                "message": "Your quiz has been submitted successfully."
            })

        total_questions = len(
            _get_attempt_questions(attempt["quiz_id"])
        )

        return render_template(
            "quiz.html",
            quiz=quiz_data,
            questions=[],
            result={
                "score": attempt.get("score") or 0,
                "percentage": attempt.get("percentage") or 0,
                "submitted_at": attempt.get("submitted_at"),
            },
            review=None,
            show_result=False,
            submission_complete=True,
            unavailable=False,
            total_questions=total_questions,
        )



        # Load only the current question for AJAX POST requests.
    if request.method == "POST":
        try:
            expected_index = int(session.get("quiz_current_index", 0))
        except (TypeError, ValueError):
            expected_index = 0

        expected_index = max(0, expected_index)

        total_questions = int(
            quiz_data.get("total_questions") or 0
        )

        if total_questions <= 0:
            return ajax_error(
                "Invalid question count for this quiz.", 400
            )

        expected_index = min(
            expected_index, total_questions - 1
        )

        step_started = time.perf_counter()

        question_state = _get_attempt_question_at(
            attempt["quiz_id"], expected_index, attempt
        )

        timings["get_question_ms"] = round(
            (time.perf_counter() - step_started) * 1000, 2
        )

        if not question_state:
            return ajax_error(
                "Unable to load the current question.", 500
            )

        expected_question_id = question_state["question_id"]
        questions = None

    else:
        # GET requests still need the complete question list for rendering.
        questions = _get_attempt_questions(attempt["quiz_id"], attempt)

        if not questions:
            return _quiz_unavailable_page(
                quiz_data, "This quiz does not contain any questions."
            )

        total_questions = len(questions)

        try:
            expected_index = int(session.get("quiz_current_index", 0))
        except (TypeError, ValueError):
            expected_index = 0

        expected_index = max(0, min(expected_index, total_questions - 1))
        expected_question_id = None

    session["quiz_current_index"] = expected_index

    try:
        question_time_seconds = max(
            0, int(quiz_data.get("question_time_seconds") or 0)
        )
    except (TypeError, ValueError):
        question_time_seconds = 0

    deadline = _effective_deadline(attempt, quiz_data)
    remaining_seconds = _seconds_left(deadline)

    # Server-side question clock: reset only when the server advances to a new question.
    try:
        clock_index = int(session.get("question_started_index", -1))
        question_started_at = float(session.get("question_started_at", 0) or 0)
    except (TypeError, ValueError):
        clock_index, question_started_at = -1, 0
    if clock_index != expected_index or question_started_at <= 0:
        question_started_at = time.time()
        session["question_started_at"] = question_started_at
        session["question_started_index"] = expected_index
    question_remaining_seconds = question_time_seconds
    if question_time_seconds > 0:
        question_remaining_seconds = max(0, question_time_seconds - int(time.time() - question_started_at))

    if request.method == "POST":
        posted_question_id = request.form.get("question_id", type=int)
        selected_option = (request.form.get("selected_option") or "").strip().upper()
        action = (request.form.get("action") or "save").strip().lower()

        if not posted_question_id or posted_question_id != expected_question_id:
            current_app.logger.warning(
                "Blocked invalid quiz navigation: attempt_id=%s posted=%s expected=%s",
                attempt_id, posted_question_id, expected_question_id)
            if is_ajax:
                return ajax_error("Question state mismatch. Please try again.", 409,
                                  current_index=expected_index)
            return redirect(url_for("student.quiz"))

        # Overall deadline is authoritative on the server.
        if deadline is not None and remaining_seconds <= 0:
            _mark_attempt_submitted(attempt_id)
            session.pop("question_started_at", None)
            if is_ajax:
                return jsonify({"ok": True, "submitted": True,
                                "redirect_url": url_for("student.quiz"),
                                "message": "Time is up. Your quiz was submitted."})
            return redirect(url_for("student.quiz"))

        # If the per-question timer expired, ignore its submitted answer and move on.
        question_expired = question_time_seconds > 0 and question_remaining_seconds <= 0
        if not question_expired and selected_option in ("A", "B", "C", "D"):
            try:
                step_started = time.perf_counter()

                saved = _save_answer(
                    attempt,
                    posted_question_id,
                    selected_option
                )

                timings["save_answer_ms"] = round(
                    (time.perf_counter() - step_started) * 1000, 2
                )
            except Exception:
                current_app.logger.exception("Unable to save quiz answer.")
                if is_ajax:
                    return ajax_error("Unable to save your answer. Please retry.", 500)
                flash("Unable to save your answer. Please try again.", "error")
                return redirect(url_for("student.quiz"))
            if not saved:
                if is_ajax:
                    return ajax_error("Your answer could not be saved. Please retry.", 409)
                flash("Your answer could not be saved. Please try again.", "error")
                return redirect(url_for("student.quiz"))

        if action == "submit" or expected_index == total_questions - 1:
            _mark_attempt_submitted(attempt_id)
            session.pop("question_started_at", None)
            session.pop("question_started_index", None)
            if is_ajax:
                return jsonify({"ok": True, "submitted": True,
                                "redirect_url": url_for("student.quiz")})
            return redirect(url_for("student.quiz"))

        next_index = expected_index + 1
        session["quiz_current_index"] = next_index
        session["question_started_index"] = next_index
        session["question_started_at"] = time.time()
        if is_ajax:
            timings["total_ms"] = round(
            (time.perf_counter() - request_started) * 1000, 2
        )

        current_app.logger.info(
            "QUIZ_SAVE_NEXT_PERFORMANCE: %s",
            timings
        )

        return jsonify({
            "ok": True,
            "submitted": False,
            "current_index": next_index,
            "question_time_seconds": question_time_seconds,
            "question_remaining_seconds": question_time_seconds,
            "remaining_seconds": remaining_seconds,
            "message": "Question saved."
        })
        return redirect(url_for("student.quiz"))

    # GET: render the current question and existing answers.
        if attempt["status"] == "submitted":
            return redirect(url_for("student.quiz"))
    if attempt["status"] == "submitted":
        return redirect(url_for("student.quiz"))
    answers = _get_attempt_answers(attempt_id)
    return render_template(
        "quiz.html", quiz=quiz_data, questions=questions, answers=answers,
        attempt=attempt, remaining_seconds=remaining_seconds,
        question_time_seconds=question_time_seconds,
        question_remaining_seconds=question_remaining_seconds,
        current_index=expected_index, unavailable=False, show_result=False,
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
                    q.title AS quiz_title,
                    q.total_questions AS total_questions
                FROM results r
                LEFT JOIN quizzes q
                    ON q.quiz_id = r.quiz_id
                WHERE r.student_id = %s
                ORDER BY r.submitted_at DESC, r.result_id DESC
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


@student.route("/review_attempt/<int:attempt_id>")
def review_attempt(attempt_id):
    student_id = session.get("student_id")

    if not student_id:
        return redirect(url_for("auth.login"))

    connection = _db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    a.attempt_id,
                    a.student_id,
                    a.quiz_id,
                    a.status,
                    a.score,
                    a.percentage,
                    a.submitted_at,
                    q.title AS quiz_title,
                    q.total_questions
                FROM quiz_attempts a
                JOIN quizzes q
                    ON q.quiz_id = a.quiz_id
                WHERE a.attempt_id = %s
                  AND a.student_id = %s
                  AND a.status = 'submitted'
                """,
                (attempt_id, student_id),
            )
            attempt = cursor.fetchone()

        if not attempt:
            return "Attempt not found or access denied.", 404

    finally:
        _close_db(connection)

    review = _build_result_review(attempt_id)

    return render_template(
        "result.html",
        score=attempt["score"] or 0,
        total=attempt["total_questions"] or len(review),
        percentage=attempt["percentage"] or 0,
        review=review,
        quiz_title=attempt["quiz_title"],
        submitted_at=attempt["submitted_at"],
        show_result=True,
    )


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


# ============================================================
# STUDENT PERFORMANCE ANALYTICS (database-backed)
# ============================================================
@student.route("/performance_analytics")
def performance_analytics():
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("auth.login"))

    connection = _db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    COUNT(*) AS completed_quizzes,
                    COALESCE(AVG(qa.percentage), 0) AS average_percentage,
                    COALESCE(MAX(qa.percentage), 0) AS best_percentage,
                    COALESCE(SUM(qa.score), 0) AS total_marks
                FROM quiz_attempts qa
                WHERE qa.student_id = %s AND qa.status = 'submitted'
            """, (student_id,))
            summary = cursor.fetchone()

            cursor.execute("""
                SELECT
                    q.quiz_id,
                    q.title AS quiz_title,
                    COALESCE(qa.score, 0) AS score,
                    COALESCE(q.total_questions, 0) AS total_marks,
                    COALESCE(qa.percentage, 0) AS percentage,
                    qa.submitted_at
                FROM quiz_attempts qa
                JOIN quizzes q ON q.quiz_id = qa.quiz_id
                WHERE qa.student_id = %s AND qa.status = 'submitted'
                ORDER BY qa.submitted_at ASC NULLS LAST, qa.attempt_id ASC
            """, (student_id,))
            quiz_results = cursor.fetchall()

            # Question difficulty is the available classification in this schema.
            cursor.execute("""
                SELECT
                    COALESCE(NULLIF(TRIM(q.difficulty), ''), 'Unclassified') AS topic,
                    COUNT(*) AS answered,
                    SUM(CASE
                        WHEN UPPER(TRIM(sa.selected_option)) =
                             UPPER(TRIM(q.correct_option)) THEN 1 ELSE 0
                    END) AS correct,
                    ROUND(
                        100.0 * SUM(CASE
                            WHEN UPPER(TRIM(sa.selected_option)) =
                                 UPPER(TRIM(q.correct_option)) THEN 1 ELSE 0
                        END) / NULLIF(COUNT(*), 0), 1
                    ) AS percentage
                FROM quiz_attempts qa
                JOIN questions q ON q.quiz_id = qa.quiz_id
                JOIN student_answers sa
                  ON sa.attempt_id = qa.attempt_id
                 AND sa.question_id = q.question_id
                WHERE qa.student_id = %s AND qa.status = 'submitted'
                GROUP BY COALESCE(NULLIF(TRIM(q.difficulty), ''), 'Unclassified')
                ORDER BY percentage DESC NULLS LAST
            """, (student_id,))
            difficulty_analysis = cursor.fetchall()

        percentages = [float(row["percentage"] or 0) for row in quiz_results]
        if len(percentages) >= 2:
            trend = round(percentages[-1] - percentages[0], 1)
        else:
            trend = None

        return render_template(
            "performance_analytics.html",
            summary=summary,
            quiz_results=quiz_results,
            difficulty_analysis=difficulty_analysis,
            trend=trend,
        )
    finally:
        _close_db(connection)
