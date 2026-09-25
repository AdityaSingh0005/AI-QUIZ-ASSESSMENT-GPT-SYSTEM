import time

from flask import (
    Blueprint,
    render_template,
    redirect,
    session,
    request
)

from utils.quiz_engine import get_quiz_questions
from database import get_db_connection
from psycopg2.extras import RealDictCursor


student = Blueprint("student", __name__)


# ============================================================
# QUIZ ACCESS CHECK
# ============================================================

def quiz_access_allowed():

    # Normal logged-in student
    if "student_id" in session:
        return True

    # Guest quiz
    if session.get("guest_attempt") is True:
        return True

    # Compatibility with older guest session
    if session.get("guest_mode") is True:
        return True

    return False


# ============================================================
# ENSURE QUESTION EXPLANATIONS
# ============================================================

def _ensure_question_explanations(questions):

    if not questions:
        return questions

    missing_ids = []

    for q in questions:

        if not q.get("explanation"):

            try:
                missing_ids.append(
                    int(q["question_id"])
                )

            except (
                KeyError,
                TypeError,
                ValueError
            ):
                pass

    if not missing_ids:
        return questions

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        cursor.execute(
            """
            SELECT
                question_id,
                explanation

            FROM questions

            WHERE question_id = ANY(%s)
            """,
            (missing_ids,)
        )

        explanation_rows = cursor.fetchall()

        explanation_map = {
            int(row["question_id"]):
                row.get("explanation")
            for row in explanation_rows
        }

        for q in questions:

            try:
                q_id = int(
                    q["question_id"]
                )

            except (
                KeyError,
                TypeError,
                ValueError
            ):
                continue

            if not q.get("explanation"):

                q["explanation"] = (
                    explanation_map.get(q_id)
                )

    except Exception as e:

        print(
            "⚠️ EXPLANATION LOAD WARNING:",
            e
        )

    finally:

        cursor.close()
        db.close()

    return questions


# ============================================================
# CALCULATE QUIZ DURATION
# ============================================================
#
# Total duration is ALWAYS:
#
#       total questions × time per question
#
# Example:
#
#       20 × 15 seconds = 300 seconds = 5 minutes
#
# We intentionally calculate this from the actual loaded
# questions instead of trusting old duration_minutes data.
#
# ============================================================

def _calculate_quiz_duration_seconds(
    questions,
    question_time_seconds
):

    try:

        total_questions = len(
            questions
        )

        question_time_seconds = int(
            question_time_seconds
        )

        if total_questions <= 0:
            return 0

        if question_time_seconds <= 0:
            return 0

        return (
            total_questions
            * question_time_seconds
        )

    except (
        TypeError,
        ValueError
    ):

        return 0


# ============================================================
# CALCULATE EFFECTIVE DEADLINE
# ============================================================
#
# The student gets:
#
#   min(
#       attempt duration deadline,
#       quiz availability end
#   )
#
# Therefore the availability end ALWAYS wins if it comes
# earlier.
#
# ============================================================

def _calculate_effective_deadline(
    start_timestamp,
    total_duration_seconds,
    available_until
):

    attempt_deadline = (
        start_timestamp
        + total_duration_seconds
    )

    if available_until:

        try:

            availability_deadline = (
                available_until.timestamp()
            )

            return min(
                attempt_deadline,
                availability_deadline
            )

        except Exception:
            pass

    return attempt_deadline


# ============================================================
# QUIZ STATUS
# ============================================================

def _get_quiz_status(quiz):

    now = time.time()

    available_from = quiz.get(
        "available_from"
    )

    available_until = quiz.get(
        "available_until"
    )

    # --------------------------------------------------------
    # UPCOMING
    # --------------------------------------------------------

    if available_from:

        try:

            if now < available_from.timestamp():

                return "upcoming"

        except Exception:
            pass

    # --------------------------------------------------------
    # CLOSED
    # --------------------------------------------------------

    if available_until:

        try:

            if now >= available_until.timestamp():

                return "closed"

        except Exception:
            pass

    # --------------------------------------------------------
    # LIVE
    # --------------------------------------------------------

    return "live"


# ============================================================
# QUIZ UNAVAILABLE PAGE
# ============================================================

def _quiz_unavailable_page(
    title="Quiz Not Available",
    message="This quiz is currently unavailable."
):

    return f"""
    <!DOCTYPE html>

    <html>

    <head>

        <title>{title}</title>

        <meta
            name="viewport"
            content="width=device-width, initial-scale=1.0"
        >

        <style>

            body {{
                margin: 0;
                padding: 20px;
                min-height: 100vh;

                display: flex;
                align-items: center;
                justify-content: center;

                font-family: Arial, sans-serif;

                background:
                    linear-gradient(
                        135deg,
                        #0f172a,
                        #1e293b
                    );

                color: white;
            }}

            .card {{
                width: 100%;
                max-width: 450px;

                padding: 35px;

                text-align: center;

                background: rgba(
                    255,
                    255,
                    255,
                    0.08
                );

                border: 1px solid rgba(
                    255,
                    255,
                    255,
                    0.15
                );

                border-radius: 20px;

                box-shadow:
                    0 20px 60px
                    rgba(0,0,0,0.35);
            }}

            h1 {{
                margin-bottom: 10px;
            }}

            p {{
                color: #cbd5e1;
                line-height: 1.6;
            }}

            a {{
                display: inline-block;

                margin-top: 20px;

                padding: 12px 20px;

                color: white;

                text-decoration: none;

                border-radius: 10px;

                background: #2563eb;
            }}

        </style>

    </head>

    <body>

        <div class="card">

            <h1>⏰ {title}</h1>

            <p>
                {message}
            </p>

            <a href="/available_quizzes">
                ← Back to Quizzes
            </a>

        </div>

    </body>

    </html>
    """, 404


# ============================================================
# STUDENT DASHBOARD
# ============================================================

@student.route("/student_dashboard")
def student_dashboard():

    if "student_id" not in session:
        return redirect("/")

    return render_template(
        "student_dashboard.html",
        name=session.get(
            "name",
            "Student"
        )
    )


# ============================================================
# AVAILABLE QUIZZES
# ============================================================
#
# IMPORTANT:
#
# We now fetch UPCOMING + LIVE + CLOSED quizzes.
#
# The frontend will show their status.
#
# The backend /start_quiz route separately enforces the
# actual start/end time.
#
# ============================================================

@student.route("/available_quizzes")
def available_quizzes():

    if "student_id" not in session:
        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        cursor.execute(
            """
            SELECT
                quiz_id,
                title,
                prompt,
                total_questions,
                duration_minutes,
                question_time_seconds,
                available_from,
                available_until,
                created_at

            FROM quizzes

            ORDER BY
                CASE
                    WHEN available_from > NOW()
                        THEN 0

                    WHEN available_until IS NULL
                         OR available_until > NOW()
                        THEN 1

                    ELSE 2
                END,

                available_from ASC,

                quiz_id DESC
            """
        )

        quizzes = cursor.fetchall()

    finally:

        cursor.close()
        db.close()

    # ========================================================
    # PREPARE DISPLAY DATA
    # ========================================================

    for quiz in quizzes:

        quiz["status"] = _get_quiz_status(
            quiz
        )

        # Calculate the real duration from questions ×
        # per-question time.
        try:

            total_seconds = (
                int(quiz["total_questions"] or 0)
                *
                int(quiz["question_time_seconds"] or 0)
            )

            quiz["calculated_duration_seconds"] = (
                total_seconds
            )

            quiz["calculated_duration_minutes"] = (
                (total_seconds + 59) // 60
                if total_seconds > 0
                else 0
            )

        except (
            TypeError,
            ValueError
        ):

            quiz["calculated_duration_seconds"] = 0
            quiz["calculated_duration_minutes"] = 0

        # ----------------------------------------------------
        # Display-friendly dates
        # ----------------------------------------------------

        if quiz.get("available_from"):

            quiz["available_from_display"] = (
                quiz["available_from"].strftime(
                    "%d %b %Y, %I:%M %p"
                )
            )

        else:

            quiz["available_from_display"] = "Not scheduled"

        if quiz.get("available_until"):

            quiz["available_until_display"] = (
                quiz["available_until"].strftime(
                    "%d %b %Y, %I:%M %p"
                )
            )

        else:

            quiz["available_until_display"] = (
                "No closing time"
            )

    return render_template(
        "available_quizzes.html",
        quizzes=quizzes
    )


# ============================================================
# QR GUEST QUIZ ENTRY
# ============================================================

@student.route(
    "/guest_start_quiz/<int:quiz_id>",
    methods=["GET", "POST"]
)
def guest_start_quiz(quiz_id):

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        cursor.execute(
            """
            SELECT
                quiz_id,
                title,
                total_questions,
                duration_minutes,
                question_time_seconds,
                available_from,
                available_until

            FROM quizzes

            WHERE quiz_id=%s
            """,
            (quiz_id,)
        )

        quiz = cursor.fetchone()

    finally:

        cursor.close()
        db.close()

    # ========================================================
    # QUIZ DOES NOT EXIST
    # ========================================================

    if not quiz:

        return _quiz_unavailable_page(
            "Quiz Not Found",
            "The requested quiz does not exist."
        )

    # ========================================================
    # EXACT SERVER-SIDE AVAILABILITY CHECK
    # ========================================================

    status = _get_quiz_status(
        quiz
    )

    if status == "upcoming":

        return _quiz_unavailable_page(
            "Quiz Not Started",
            "This quiz has not started yet. Please come back at the scheduled start time."
        )

    if status == "closed":

        return _quiz_unavailable_page(
            "Quiz Closed",
            "This quiz has already reached its closing time."
        )

    # ========================================================
    # POST
    # ========================================================

    if request.method == "POST":

        student_name = request.form.get(
            "student_name",
            ""
        ).strip()

        roll_number = request.form.get(
            "roll_number",
            ""
        ).strip()

        # ====================================================
        # VALIDATION
        # ====================================================

        if not student_name:

            return render_template(
                "guest_start_quiz.html",
                quiz=quiz,
                error="Please enter your name."
            )

        if not roll_number:

            return render_template(
                "guest_start_quiz.html",
                quiz=quiz,
                error="Please enter your roll number."
            )

        # ====================================================
        # START QUIZ
        # ====================================================

        return _start_guest_quiz(
            quiz_id,
            student_name,
            roll_number
        )

    # ========================================================
    # GET
    # ========================================================

    return render_template(
        "guest_start_quiz.html",
        quiz=quiz,
        error=None
    )


# ============================================================
# OLD GUEST START ROUTE
# ============================================================

@student.route(
    "/guest_start",
    methods=["POST"]
)
def guest_start():

    student_name = request.form.get(
        "student_name",
        ""
    ).strip()

    roll_number = request.form.get(
        "roll_number",
        ""
    ).strip()

    quiz_id = request.form.get(
        "quiz_id",
        ""
    ).strip()

    # ========================================================
    # VALIDATION
    # ========================================================

    if not student_name or not roll_number:

        return """
        <h2>Student details are required.</h2>

        <a href="/">
            ← Back
        </a>
        """

    if not quiz_id.isdigit():

        return """
        <h2>Invalid Quiz ID.</h2>

        <a href="/">
            ← Back
        </a>
        """

    quiz_id = int(
        quiz_id
    )

    return _start_guest_quiz(
        quiz_id,
        student_name,
        roll_number
    )


# ============================================================
# GUEST QUIZ HELPER
# ============================================================

def _start_guest_quiz(
    quiz_id,
    student_name,
    roll_number
):

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        # ====================================================
        # GET QUIZ
        # ====================================================

        cursor.execute(
            """
            SELECT
                quiz_id,
                title,
                total_questions,
                duration_minutes,
                question_time_seconds,
                available_from,
                available_until

            FROM quizzes

            WHERE quiz_id=%s
            """,
            (quiz_id,)
        )

        quiz = cursor.fetchone()

        if not quiz:

            return _quiz_unavailable_page(
                "Quiz Not Found",
                "The requested quiz does not exist."
            )

        # ====================================================
        # SERVER-SIDE AVAILABILITY
        # ====================================================

        status = _get_quiz_status(
            quiz
        )

        if status == "upcoming":

            return _quiz_unavailable_page(
                "Quiz Not Started",
                "This quiz has not started yet."
            )

        if status == "closed":

            return _quiz_unavailable_page(
                "Quiz Closed",
                "This quiz has already closed."
            )

        # ====================================================
        # GET QUESTIONS
        # ====================================================

        questions = get_quiz_questions(
            quiz_id
        )

        if not questions:

            return """
            <h2>❌ This quiz has no questions.</h2>

            <a href="/">
                ← Back to Login
            </a>
            """, 404

        questions = _ensure_question_explanations(
            questions
        )

        # ====================================================
        # QUESTION TIME
        # ====================================================

        try:

            question_time_seconds = int(
                quiz["question_time_seconds"] or 60
            )

        except (
            TypeError,
            ValueError
        ):

            question_time_seconds = 60

        # ====================================================
        # TOTAL DURATION
        # ====================================================

        total_duration_seconds = (
            _calculate_quiz_duration_seconds(
                questions,
                question_time_seconds
            )
        )

        if total_duration_seconds <= 0:

            return """
            <h2>❌ Invalid quiz timer configuration.</h2>

            <a href="/">
                ← Back
            </a>
            """, 500

        # ====================================================
        # CREATE GUEST ATTEMPT
        # ====================================================

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
                status
            )

            VALUES
            (
                %s,
                NULL,
                %s,
                %s,
                'guest',
                NOW(),
                'in_progress'
            )

            RETURNING
                attempt_id,
                started_at
            """,
            (
                quiz_id,
                student_name,
                roll_number
            )
        )

        attempt = cursor.fetchone()

        db.commit()

        # ====================================================
        # DATABASE START TIME
        # ====================================================

        started_at = attempt[
            "started_at"
        ]

        start_timestamp = (
            started_at.timestamp()
        )

        # ====================================================
        # EFFECTIVE DEADLINE
        # ====================================================

        effective_deadline = (
            _calculate_effective_deadline(
                start_timestamp,
                total_duration_seconds,
                quiz.get("available_until")
            )
        )

        # ====================================================
        # STORE GUEST SESSION
        # ====================================================

        session["guest_attempt"] = True

        session["guest_mode"] = True

        session["guest_attempt_id"] = (
            attempt["attempt_id"]
        )

        session["quiz_attempt_id"] = (
            attempt["attempt_id"]
        )

        session["guest_name"] = (
            student_name
        )

        session["guest_student_name"] = (
            student_name
        )

        session["guest_roll_number"] = (
            roll_number
        )

        session["quiz_id"] = quiz_id

        session["questions"] = questions

        session["current_question"] = 0

        session["answers"] = {}

        # ====================================================
        # TIMER DATA
        # ====================================================

        session["quiz_duration_minutes"] = (
            (total_duration_seconds + 59) // 60
        )

        session["quiz_duration_seconds"] = (
            total_duration_seconds
        )

        session["question_time_seconds"] = (
            question_time_seconds
        )

        # Hard availability deadline
        if quiz.get("available_until"):

            session["quiz_available_until"] = (
                quiz["available_until"].timestamp()
            )

        else:

            session["quiz_available_until"] = None

        # Actual attempt start
        session["quiz_start_time"] = (
            start_timestamp
        )

        # Final effective deadline
        session["quiz_deadline"] = (
            effective_deadline
        )

        # Current question timer
        session["question_start_time"] = (
            time.time()
        )

        session.modified = True

        print(
            f"🎯 GUEST QUIZ STARTED | "
            f"Quiz={quiz_id} | "
            f"Attempt={attempt['attempt_id']} | "
            f"Name={student_name} | "
            f"Roll={roll_number} | "
            f"Duration={total_duration_seconds}s | "
            f"Deadline={effective_deadline}"
        )

        return redirect(
            "/quiz"
        )

    except Exception as e:

        db.rollback()

        print(
            "❌ GUEST QUIZ START ERROR:",
            e
        )

        return f"""
        <h2>❌ Guest Quiz Error</h2>

        <p>{e}</p>

        <br>

        <a href="/">
            ← Back to Login
        </a>
        """, 500

    finally:

        cursor.close()
        db.close()


# ============================================================
# START QUIZ - LOGGED IN STUDENT
# ============================================================

@student.route(
    "/start_quiz/<int:quiz_id>"
)
def start_quiz(quiz_id):

    if "student_id" not in session:
        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        # ====================================================
        # GET QUIZ
        # ====================================================

        cursor.execute(
            """
            SELECT
                quiz_id,
                title,
                total_questions,
                duration_minutes,
                question_time_seconds,
                available_from,
                available_until

            FROM quizzes

            WHERE quiz_id=%s
            """,
            (quiz_id,)
        )

        quiz = cursor.fetchone()

        if not quiz:

            return _quiz_unavailable_page(
                "Quiz Not Found",
                "The requested quiz does not exist."
            )

        # ====================================================
        # EXACT SERVER-SIDE START / END CHECK
        # ====================================================

        status = _get_quiz_status(
            quiz
        )

        if status == "upcoming":

            return _quiz_unavailable_page(
                "Quiz Not Started",
                "This quiz will become available at its scheduled start time."
            )

        if status == "closed":

            return _quiz_unavailable_page(
                "Quiz Closed",
                "This quiz has already reached its closing time."
            )

        # ====================================================
        # GET STUDENT DETAILS
        # ====================================================

        cursor.execute(
            """
            SELECT
                full_name,
                roll_number

            FROM students

            WHERE student_id=%s
            """,
            (
                session["student_id"],
            )
        )

        student_data = cursor.fetchone()

        if not student_data:

            return """
            <h2>❌ Student account not found.</h2>

            <a href="/">
                ← Back to Login
            </a>
            """, 404

        # ====================================================
        # GET QUESTIONS
        # ====================================================

        questions = get_quiz_questions(
            quiz_id
        )

        if not questions:

            return """
            <h2>❌ Quiz has no questions.</h2>

            <a href="/available_quizzes">
                ← Back to Available Quizzes
            </a>
            """, 404

        questions = _ensure_question_explanations(
            questions
        )

        # ====================================================
        # QUESTION TIME
        # ====================================================

        try:

            question_time_seconds = int(
                quiz["question_time_seconds"] or 60
            )

        except (
            TypeError,
            ValueError
        ):

            question_time_seconds = 60

        # ====================================================
        # TOTAL DURATION
        # ====================================================

        total_duration_seconds = (
            _calculate_quiz_duration_seconds(
                questions,
                question_time_seconds
            )
        )

        if total_duration_seconds <= 0:

            return """
            <h2>❌ Invalid quiz timer configuration.</h2>

            <a href="/available_quizzes">
                ← Back to Available Quizzes
            </a>
            """, 500

        # ====================================================
        # FIND EXISTING IN-PROGRESS ATTEMPT
        # ====================================================

        cursor.execute(
            """
            SELECT
                attempt_id,
                started_at,
                status

            FROM quiz_attempts

            WHERE
                quiz_id=%s
                AND student_id=%s
                AND status='in_progress'

            ORDER BY started_at DESC

            LIMIT 1
            """,
            (
                quiz_id,
                session["student_id"]
            )
        )

        existing_attempt = cursor.fetchone()

        # ====================================================
        # EXISTING ATTEMPT
        # ====================================================

        if existing_attempt:

            attempt_id = (
                existing_attempt["attempt_id"]
            )

            started_at = (
                existing_attempt["started_at"]
            )

            print(
                f"♻️ EXISTING STUDENT ATTEMPT | "
                f"Quiz={quiz_id} | "
                f"Attempt={attempt_id}"
            )

        # ====================================================
        # NEW ATTEMPT
        # ====================================================

        else:

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
                    status
                )

                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    'login',
                    NOW(),
                    'in_progress'
                )

                RETURNING
                    attempt_id,
                    started_at
                """,
                (
                    quiz_id,
                    session["student_id"],
                    student_data["full_name"],
                    student_data["roll_number"]
                )
            )

            attempt = cursor.fetchone()

            attempt_id = (
                attempt["attempt_id"]
            )

            started_at = (
                attempt["started_at"]
            )

            db.commit()

            print(
                f"🎯 STUDENT QUIZ STARTED | "
                f"Quiz={quiz_id} | "
                f"Student={session['student_id']} | "
                f"Attempt={attempt_id}"
            )

        # ====================================================
        # IMPORTANT:
        #
        # Use DATABASE started_at.
        #
        # Do NOT reset timer to current time when an existing
        # attempt is reopened/refreshed.
        # ====================================================

        start_timestamp = (
            started_at.timestamp()
        )

        # ====================================================
        # EFFECTIVE DEADLINE
        # ====================================================

        effective_deadline = (
            _calculate_effective_deadline(
                start_timestamp,
                total_duration_seconds,
                quiz.get("available_until")
            )
        )

        # ====================================================
        # CHECK IF EXISTING ATTEMPT HAS ALREADY EXPIRED
        # ====================================================

        if time.time() >= effective_deadline:

            cursor.execute(
                """
                UPDATE quiz_attempts

                SET
                    submitted_at = NOW(),
                    status = 'submitted'

                WHERE attempt_id=%s
                  AND status='in_progress'
                """,
                (
                    attempt_id,
                )
            )

            db.commit()

            return _quiz_unavailable_page(
                "Quiz Time Expired",
                "The time allowed for this quiz attempt has already expired."
            )

        # ====================================================
        # CLEAR OLD GUEST SESSION
        # ====================================================

        session.pop(
            "guest_attempt",
            None
        )

        session.pop(
            "guest_mode",
            None
        )

        session.pop(
            "guest_attempt_id",
            None
        )

        session.pop(
            "quiz_attempt_id",
            None
        )

        session.pop(
            "guest_name",
            None
        )

        session.pop(
            "guest_student_name",
            None
        )

        session.pop(
            "guest_roll_number",
            None
        )

        # ====================================================
        # START STUDENT QUIZ
        # ====================================================

        session["quiz_id"] = quiz_id

        session["quiz_attempt_id"] = (
            attempt_id
        )

        session["questions"] = questions

        # Only initialise question index/answers for a NEW
        # attempt. Existing session data is preserved when
        # possible.
        if (
            session.get("quiz_id") != quiz_id
            or session.get("quiz_attempt_id") != attempt_id
        ):
            session["current_question"] = 0
            session["answers"] = {}

        # If no valid question index exists, initialise it.
        if "current_question" not in session:
            session["current_question"] = 0

        if not isinstance(
            session.get("answers"),
            dict
        ):
            session["answers"] = {}

        session["quiz_duration_minutes"] = (
            (total_duration_seconds + 59) // 60
        )

        session["quiz_duration_seconds"] = (
            total_duration_seconds
        )

        session["question_time_seconds"] = (
            question_time_seconds
        )

        # ====================================================
        # AVAILABILITY END
        # ====================================================

        if quiz.get("available_until"):

            session["quiz_available_until"] = (
                quiz["available_until"].timestamp()
            )

        else:

            session["quiz_available_until"] = None

        # ====================================================
        # START TIME FROM DATABASE
        # ====================================================

        session["quiz_start_time"] = (
            start_timestamp
        )

        session["quiz_deadline"] = (
            effective_deadline
        )

        # ====================================================
        # QUESTION TIMER
        # ====================================================

        # If this is a genuinely new attempt, start question 1.
        #
        # If the student is continuing the existing session,
        # do not reset question_start_time.
        if not session.get(
            "question_start_time"
        ):

            session["question_start_time"] = (
                time.time()
            )

        session.modified = True

        return redirect(
            "/quiz"
        )

    except Exception as e:

        db.rollback()

        print(
            "❌ STUDENT QUIZ START ERROR:",
            e
        )

        return f"""
        <h2>❌ Quiz Start Error</h2>

        <p>{e}</p>

        <br>

        <a href="/available_quizzes">
            ← Back to Available Quizzes
        </a>
        """, 500

    finally:

        cursor.close()
        db.close()


# ============================================================
# ACTUAL QUIZ
# ============================================================

@student.route(
    "/quiz",
    methods=["GET", "POST"]
)
def quiz():

    # ========================================================
    # ACCESS CHECK
    # ========================================================

    if not quiz_access_allowed():

        return redirect("/")

    # ========================================================
    # GET QUESTIONS
    # ========================================================

    questions = session.get(
        "questions",
        []
    )

    if not questions:

        if session.get("guest_attempt"):

            return redirect("/")

        return redirect(
            "/available_quizzes"
        )

    # ========================================================
    # CURRENT QUESTION
    # ========================================================

    index = session.get(
        "current_question",
        0
    )

    if index < 0:

        index = 0

        session["current_question"] = 0

    if index >= len(questions):

        return redirect(
            "/submit_quiz"
        )

    # ========================================================
    # QUESTION TIME
    # ========================================================

    try:

        question_time_seconds = int(
            session.get(
                "question_time_seconds",
                60
            )
        )

    except (
        TypeError,
        ValueError
    ):

        question_time_seconds = 60

    # ========================================================
    # TOTAL DURATION
    # ========================================================

    total_duration_seconds = (
        _calculate_quiz_duration_seconds(
            questions,
            question_time_seconds
        )
    )

    if total_duration_seconds <= 0:

        return """
        <h2>❌ Invalid quiz timer.</h2>
        """, 500

    # ========================================================
    # START TIME
    # ========================================================

    start_time = session.get(
        "quiz_start_time"
    )

    if not start_time:

        start_time = time.time()

        session["quiz_start_time"] = (
            start_time
        )

    # ========================================================
    # AVAILABILITY END
    # ========================================================

    quiz_available_until = session.get(
        "quiz_available_until"
    )

    # ========================================================
    # EFFECTIVE DEADLINE
    # ========================================================

    stored_deadline = session.get(
        "quiz_deadline"
    )

    if stored_deadline:

        effective_deadline = float(
            stored_deadline
        )

    else:

        effective_deadline = (
            _calculate_effective_deadline(
                start_time,
                total_duration_seconds,
                quiz_available_until
            )
        )

        session["quiz_deadline"] = (
            effective_deadline
        )

    # ========================================================
    # SERVER-SIDE OVERALL TIME CHECK
    # ========================================================

    now = time.time()

    if now >= effective_deadline:

        print(
            f"⏰ QUIZ DEADLINE REACHED | "
            f"Quiz={session.get('quiz_id')} | "
            f"Deadline={effective_deadline} | "
            f"Now={now}"
        )

        return redirect(
            "/submit_quiz"
        )

    # ========================================================
    # POST ANSWER
    # ========================================================

    if request.method == "POST":

        now = time.time()

        # ----------------------------------------------------
        # HARD DEADLINE CHECK
        # ----------------------------------------------------
        #
        # If the student sends a POST after the effective
        # deadline, do NOT accept a new answer.
        #
        # Existing answers remain in session/database and
        # submit_quiz will calculate the final result.
        #
        # ----------------------------------------------------

        if now >= effective_deadline:

            print(
                "⏰ POST RECEIVED AFTER QUIZ DEADLINE"
            )

            return redirect(
                "/submit_quiz"
            )

        # ====================================================
        # QUESTION TIMER
        # ====================================================

        question_start_time = session.get(
            "question_start_time"
        )

        if not question_start_time:

            question_start_time = now

            session["question_start_time"] = (
                question_start_time
            )

        question_elapsed_time = (
            now
            - question_start_time
        )

        question_time_expired = (
            question_elapsed_time
            >= question_time_seconds
        )

        # ====================================================
        # GET ANSWER
        # ====================================================

        answer = request.form.get(
            "answer"
        )

        # If question timer expired, ignore answer.
        if question_time_expired:

            answer = None

        # ====================================================
        # QUESTION ID
        # ====================================================

        question_id = str(
            questions[index]["question_id"]
        )

        # ====================================================
        # SESSION ANSWERS
        # ====================================================

        answers = session.get(
            "answers",
            {}
        )

        if not isinstance(
            answers,
            dict
        ):

            answers = {}

        # ====================================================
        # SAVE ANSWER
        # ====================================================

        answers[question_id] = answer

        session["answers"] = answers

        session.modified = True

        # ====================================================
        # SAVE ANSWER TO DATABASE
        # ====================================================

        db = get_db_connection()

        cursor = db.cursor()

        try:

            student_id = session.get(
                "student_id"
            )

            attempt_id = session.get(
                "quiz_attempt_id"
            )

            if not attempt_id:

                attempt_id = session.get(
                    "guest_attempt_id"
                )

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

                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    student_id,
                    session["quiz_id"],
                    int(question_id),
                    answer,
                    attempt_id
                )
            )

            db.commit()

            print(
                f"✅ ANSWER SAVED | "
                f"Quiz={session.get('quiz_id')} | "
                f"Question={question_id} | "
                f"Answer={answer} | "
                f"Attempt={attempt_id}"
            )

        except Exception as e:

            db.rollback()

            print(
                "❌ LIVE ANSWER SAVE ERROR:",
                e
            )

        finally:

            cursor.close()
            db.close()

        # ====================================================
        # NEXT QUESTION
        # ====================================================

        if index < len(questions) - 1:

            next_index = index + 1

            session["current_question"] = (
                next_index
            )

            session["question_start_time"] = (
                time.time()
            )

            session.modified = True

            print(
                f"➡️ NEXT QUESTION | "
                f"{index + 1} -> {next_index + 1}"
            )

            return redirect(
                "/quiz"
            )

        # ====================================================
        # LAST QUESTION
        # ====================================================

        print(
            "🏁 LAST QUESTION ANSWERED"
        )

        return redirect(
            "/submit_quiz"
        )

    # ========================================================
    # CURRENT QUESTION
    # ========================================================

    question = questions[index]

    # ========================================================
    # QUESTION START TIME
    # ========================================================

    question_start_time = session.get(
        "question_start_time"
    )

    if not question_start_time:

        question_start_time = time.time()

        session["question_start_time"] = (
            question_start_time
        )

        session.modified = True

    # ========================================================
    # REMAINING OVERALL TIME
    # ========================================================
    #
    # This is the IMPORTANT calculation:
    #
    # remaining =
    #     effective_deadline - current_time
    #
    # effective_deadline itself is:
    #
    # min(
    #     attempt_start + total duration,
    #     quiz availability end
    # )
    #
    # ========================================================

    now = time.time()

    remaining_seconds = max(
        0,
        int(
            effective_deadline
            - now
        )
    )

    # ========================================================
    # REMAINING QUESTION TIME
    # ========================================================

    question_elapsed_time = (
        now
        - question_start_time
    )

    question_remaining_seconds = max(
        0,
        int(
            question_time_seconds
            - question_elapsed_time
        )
    )

    # ========================================================
    # IMPORTANT:
    #
    # Question timer can NEVER go beyond the overall deadline.
    # ========================================================

    question_remaining_seconds = min(
        question_remaining_seconds,
        remaining_seconds
    )

    # ========================================================
    # DURATION IN MINUTES
    # ========================================================

    duration_minutes = (
        total_duration_seconds / 60
    )

    # ========================================================
    # RENDER
    # ========================================================

    return render_template(
        "quiz.html",

        question=question,

        question_number=index + 1,

        total_questions=len(
            questions
        ),

        number=index + 1,

        total=len(
            questions
        ),

        remaining_seconds=(
            remaining_seconds
        ),

        question_remaining_seconds=(
            question_remaining_seconds
        ),

        quiz_duration_minutes=(
            duration_minutes
        ),

        quiz_duration_seconds=(
            total_duration_seconds
        ),

        question_time_seconds=(
            question_time_seconds
        ),

        quiz_deadline=(
            effective_deadline
        ),

        quiz_available_until=(
            quiz_available_until
        ),

        quiz_id=session.get(
            "quiz_id"
        ),

        guest_attempt=session.get(
            "guest_attempt",
            False
        ),

        guest_name=session.get(
            "guest_name"
        ),

        guest_roll_number=session.get(
            "guest_roll_number"
        )
    )


# ============================================================
# SUBMIT QUIZ
# ============================================================

@student.route(
    "/submit_quiz"
)
def submit_quiz():

    # ========================================================
    # ACCESS
    # ========================================================

    if not quiz_access_allowed():

        return redirect("/")

    questions = session.get(
        "questions",
        []
    )

    answers = session.get(
        "answers",
        {}
    )

    if not questions:

        return redirect("/")

    quiz_id = session.get(
        "quiz_id"
    )

    if not quiz_id:

        return redirect("/")

    student_id = session.get(
        "student_id"
    )

    attempt_id = session.get(
        "quiz_attempt_id"
    )

    if not attempt_id:

        attempt_id = session.get(
            "guest_attempt_id"
        )

    # ========================================================
    # MAKE SURE EXPLANATIONS ARE AVAILABLE
    # ========================================================

    questions = _ensure_question_explanations(
        questions
    )

    score = 0

    review = []

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        # ====================================================
        # CALCULATE SCORE + BUILD REVIEW
        # ====================================================

        for index, q in enumerate(questions):

            q_id = q["question_id"]

            # ------------------------------------------------
            # STUDENT ANSWER
            # ------------------------------------------------

            selected = answers.get(
                str(q_id)
            )

            if selected is not None:

                selected = str(
                    selected
                ).strip().upper()

            # ------------------------------------------------
            # CORRECT ANSWER
            # ------------------------------------------------

            correct_option = str(
                q["correct_option"]
            ).strip().upper()

            # ------------------------------------------------
            # OPTION MAP
            # ------------------------------------------------

            option_map = {

                "A": q.get(
                    "option_a",
                    ""
                ),

                "B": q.get(
                    "option_b",
                    ""
                ),

                "C": q.get(
                    "option_c",
                    ""
                ),

                "D": q.get(
                    "option_d",
                    ""
                )
            }

            # ------------------------------------------------
            # STUDENT ANSWER TEXT
            # ------------------------------------------------

            selected_text = None

            if selected in option_map:

                selected_text = option_map[
                    selected
                ]

            # ------------------------------------------------
            # CORRECT ANSWER TEXT
            # ------------------------------------------------

            correct_text = option_map.get(
                correct_option,
                ""
            )

            # ------------------------------------------------
            # CHECK ANSWER
            # ------------------------------------------------

            is_correct = (
                selected is not None
                and
                selected == correct_option
            )

            if is_correct:

                score += 1

            # ------------------------------------------------
            # EXPLANATION
            # ------------------------------------------------

            explanation = q.get(
                "explanation"
            )

            if not explanation:

                explanation = (
                    "No explanation is available "
                    "for this question."
                )

            # ------------------------------------------------
            # REVIEW ITEM
            # ------------------------------------------------

            review.append({

                "question_number":
                    index + 1,

                "question":
                    q.get(
                        "question",
                        ""
                    ),

                "selected_option":
                    selected,

                "selected_text":
                    selected_text,

                "correct_option":
                    correct_option,

                "correct_text":
                    correct_text,

                "is_correct":
                    is_correct,

                "explanation":
                    explanation
            })

        # ====================================================
        # PERCENTAGE
        # ====================================================

        total = len(
            questions
        )

        if total > 0:

            percentage = round(
                (
                    score
                    / total
                ) * 100,
                2
            )

        else:

            percentage = 0

        # ====================================================
        # SAVE RESULT
        # ====================================================

        cursor.execute(
            """
            INSERT INTO results
            (
                student_id,
                quiz_id,
                score,
                percentage,
                attempt_id
            )

            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                student_id,
                quiz_id,
                score,
                percentage,
                attempt_id
            )
        )

        # ====================================================
        # UPDATE ATTEMPT
        # ====================================================

        if attempt_id:

            cursor.execute(
                """
                UPDATE quiz_attempts

                SET
                    submitted_at = NOW(),
                    status = 'submitted',
                    score = %s,
                    percentage = %s

                WHERE attempt_id=%s
                """,
                (
                    score,
                    percentage,
                    attempt_id
                )
            )

        # ====================================================
        # COMMIT
        # ====================================================

        db.commit()

        print(
            f"🏁 QUIZ SUBMITTED | "
            f"Quiz={quiz_id} | "
            f"Score={score}/{total} | "
            f"Percentage={percentage:.2f}% | "
            f"Attempt={attempt_id}"
        )

        print(
            f"📋 REVIEW GENERATED | "
            f"Questions={len(review)}"
        )

        # ====================================================
        # CLEAR QUIZ SESSION
        # ====================================================

        quiz_session_keys = [

            "questions",

            "answers",

            "current_question",

            "quiz_start_time",

            "question_start_time",

            "quiz_duration_minutes",

            "quiz_duration_seconds",

            "question_time_seconds",

            "quiz_available_until",

            "quiz_deadline",

            "quiz_id",

            "guest_attempt",

            "guest_mode",

            "guest_attempt_id",

            "quiz_attempt_id",

            "guest_name",

            "guest_student_name",

            "guest_roll_number"
        ]

        for key in quiz_session_keys:

            session.pop(
                key,
                None
            )

        session.modified = True

        # ====================================================
        # RESULT PAGE
        # ====================================================

        return render_template(
            "result.html",

            score=score,

            total=total,

            percentage=percentage,

            review=review
        )

    except Exception as e:

        db.rollback()

        print(
            "❌ SUBMIT QUIZ ERROR:",
            e
        )

        return f"""
        <h2>❌ Error while submitting quiz</h2>

        <p>{e}</p>

        <br>

        <a href="/">
            ← Back to Login
        </a>
        """, 500

    finally:

        cursor.close()
        db.close()


# ============================================================
# MY RESULTS
# ============================================================

@student.route(
    "/my_results"
)
def my_results():

    if "student_id" not in session:

        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        cursor.execute(
            """
            SELECT
                quizzes.title,
                results.score,
                results.percentage,
                results.submitted_at

            FROM results

            JOIN quizzes
            ON results.quiz_id =
               quizzes.quiz_id

            WHERE results.student_id=%s

            ORDER BY results.submitted_at DESC
            """,
            (
                session["student_id"],
            )
        )

        results = cursor.fetchall()

    finally:

        cursor.close()
        db.close()

    return render_template(
        "my_results.html",
        results=results
    )


# ============================================================
# LEADERBOARD
# ============================================================

@student.route(
    "/leaderboard"
)
def leaderboard():

    if "student_id" not in session:

        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        cursor.execute(
            """
            SELECT
                students.full_name,
                students.roll_number,

                ROUND(
                    AVG(results.percentage),
                    2
                ) AS average_percentage,

                COUNT(results.result_id)
                AS total_attempts

            FROM students

            JOIN results
            ON students.student_id =
               results.student_id

            GROUP BY
                students.student_id,
                students.full_name,
                students.roll_number

            ORDER BY
                average_percentage DESC
            """
        )

        leaderboard = cursor.fetchall()

    finally:

        cursor.close()
        db.close()

    return render_template(
        "leaderboard.html",
        leaderboard=leaderboard
    )


# ============================================================
# STUDENT PROFILE
# ============================================================

@student.route(
    "/student_profile"
)
def student_profile():

    if "student_id" not in session:

        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        # ====================================================
        # STUDENT DETAILS
        # ====================================================

        cursor.execute(
            """
            SELECT
                full_name,
                roll_number,
                department,
                semester,
                section,
                created_at

            FROM students

            WHERE student_id=%s
            """,
            (
                session["student_id"],
            )
        )

        student_data = cursor.fetchone()

        # ====================================================
        # STATISTICS
        # ====================================================

        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_quizzes,

                MAX(score) AS best_score,

                ROUND(
                    AVG(percentage),
                    2
                ) AS average_percentage

            FROM results

            WHERE student_id=%s
            """,
            (
                session["student_id"],
            )
        )

        stats = cursor.fetchone()

    finally:

        cursor.close()
        db.close()

    return render_template(
        "student_profile.html",

        student=student_data,

        stats=stats
    )