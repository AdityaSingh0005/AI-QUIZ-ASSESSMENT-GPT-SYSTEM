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
# STUDENT DASHBOARD
# ============================================================

@student.route("/student_dashboard")
def student_dashboard():

    if "student_id" not in session:
        return redirect("/")

    return render_template(
        "student_dashboard.html",
        name=session.get("name", "Student")
    )


# ============================================================
# AVAILABLE QUIZZES
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
                total_questions,
                duration_minutes,
                question_time_seconds,
                available_from,
                available_until

            FROM quizzes

            WHERE
                available_from <= NOW()

                AND

                (
                    available_until IS NULL
                    OR available_until > NOW()
                )

            ORDER BY quiz_id DESC
            """
        )

        quizzes = cursor.fetchall()

    finally:

        cursor.close()
        db.close()

    return render_template(
        "available_quizzes.html",
        quizzes=quizzes
    )


# ============================================================
# QR GUEST QUIZ ENTRY
# ============================================================
#
# QR CODE opens:
#
# /guest_start_quiz/<quiz_id>
#
# GET:
#     Shows guest details page.
#
# POST:
#     Receives name + roll number
#     and starts quiz.
#
# ============================================================

@student.route(
    "/guest_start_quiz/<int:quiz_id>",
    methods=["GET", "POST"]
)
def guest_start_quiz(quiz_id):

    # ========================================================
    # GET QUIZ
    # ========================================================

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

            AND available_from <= NOW()

            AND
            (
                available_until IS NULL
                OR available_until > NOW()
            )
            """,
            (quiz_id,)
        )

        quiz = cursor.fetchone()

    finally:

        cursor.close()
        db.close()

    # ========================================================
    # QUIZ NOT AVAILABLE
    # ========================================================

    if not quiz:

        return """
        <!DOCTYPE html>

        <html>

        <head>

            <title>Quiz Not Available</title>

            <meta
                name="viewport"
                content="width=device-width, initial-scale=1.0"
            >

            <style>

                body {
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
                }

                .card {
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
                }

                h1 {
                    margin-bottom: 10px;
                }

                p {
                    color: #cbd5e1;
                    line-height: 1.6;
                }

                a {
                    display: inline-block;

                    margin-top: 20px;

                    padding: 12px 20px;

                    color: white;

                    text-decoration: none;

                    border-radius: 10px;

                    background: #2563eb;
                }

            </style>

        </head>

        <body>

            <div class="card">

                <h1>⏰ Quiz Not Available</h1>

                <p>
                    This quiz has expired or is
                    currently unavailable.
                </p>

                <a href="/">
                    ← Back to Login
                </a>

            </div>

        </body>

        </html>
        """, 404

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
#
# Kept for compatibility with existing login page.
#
# POST:
# /guest_start
#
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

    quiz_id = int(quiz_id)

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

            AND available_from <= NOW()

            AND
            (
                available_until IS NULL
                OR available_until > NOW()
            )
            """,
            (quiz_id,)
        )

        quiz = cursor.fetchone()

        if not quiz:

            return """
            <h2>❌ Quiz not available.</h2>

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
            <h2>❌ This quiz has no questions.</h2>

            <a href="/">
                ← Back to Login
            </a>
            """, 404

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

            RETURNING attempt_id
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
        # STORE GUEST SESSION
        # ====================================================

        session["guest_attempt"] = True

        # Compatibility
        session["guest_mode"] = True

        session["guest_attempt_id"] = (
            attempt["attempt_id"]
        )

        # Compatibility
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

        # ====================================================
        # START FROM QUESTION 1
        # ====================================================

        session["current_question"] = 0

        session["answers"] = {}

        # ====================================================
        # QUIZ TIMER
        # ====================================================

        session["quiz_duration_minutes"] = (
            quiz["duration_minutes"] or 30
        )

        session["question_time_seconds"] = (
            quiz["question_time_seconds"] or 60
        )

        # ====================================================
        # AVAILABILITY
        # ====================================================

        if quiz["available_until"]:

            session["quiz_available_until"] = (
                quiz["available_until"].timestamp()
            )

        else:

            session["quiz_available_until"] = None

        # ====================================================
        # START TIME
        # ====================================================

        session["quiz_start_time"] = time.time()

        session["question_start_time"] = time.time()

        session.modified = True

        print(
            f"🎯 GUEST QUIZ STARTED | "
            f"Quiz={quiz_id} | "
            f"Attempt={attempt['attempt_id']} | "
            f"Name={student_name} | "
            f"Roll={roll_number}"
        )

        return redirect("/quiz")

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

    # ========================================================
    # LOGIN CHECK
    # ========================================================

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
                total_questions,
                duration_minutes,
                question_time_seconds,
                available_from,
                available_until

            FROM quizzes

            WHERE quiz_id=%s

            AND available_from <= NOW()

            AND
            (
                available_until IS NULL
                OR available_until > NOW()
            )
            """,
            (quiz_id,)
        )

        quiz = cursor.fetchone()

    finally:

        cursor.close()
        db.close()

    # ========================================================
    # QUIZ NOT AVAILABLE
    # ========================================================

    if not quiz:

        return """
        <h2>⏰ Quiz No Longer Available</h2>

        <p>
            This quiz has expired or is not
            currently available.
        </p>

        <a href="/available_quizzes">
            ← Back to Available Quizzes
        </a>
        """

    # ========================================================
    # GET QUESTIONS
    # ========================================================

    questions = get_quiz_questions(
        quiz_id
    )

    if not questions:

        return """
        <h2>❌ Quiz has no questions.</h2>

        <a href="/available_quizzes">
            ← Back to Available Quizzes
        </a>
        """

    # ========================================================
    # CLEAR OLD GUEST SESSION
    # ========================================================

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

    # ========================================================
    # START STUDENT QUIZ
    # ========================================================

    session["quiz_id"] = quiz_id

    session["questions"] = questions

    session["current_question"] = 0

    session["answers"] = {}

    session["quiz_duration_minutes"] = (
        quiz["duration_minutes"] or 30
    )

    session["question_time_seconds"] = (
        quiz["question_time_seconds"] or 60
    )

    # ========================================================
    # AVAILABILITY
    # ========================================================

    if quiz["available_until"]:

        session["quiz_available_until"] = (
            quiz["available_until"].timestamp()
        )

    else:

        session["quiz_available_until"] = None

    # ========================================================
    # START TIME
    # ========================================================

    session["quiz_start_time"] = time.time()

    session["question_start_time"] = time.time()

    session.modified = True

    return redirect("/quiz")


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

    # ========================================================
    # SAFETY CHECK
    # ========================================================

    if index < 0:

        index = 0

        session["current_question"] = 0

    if index >= len(questions):

        return redirect(
            "/submit_quiz"
        )

    # ========================================================
    # QUIZ AVAILABILITY
    # ========================================================

    quiz_available_until = session.get(
        "quiz_available_until"
    )

    if quiz_available_until is not None:

        if time.time() >= quiz_available_until:

            return redirect(
                "/submit_quiz"
            )

    # ========================================================
    # OVERALL TIMER
    # ========================================================

    start_time = session.get(
        "quiz_start_time"
    )

    duration_minutes = session.get(
        "quiz_duration_minutes",
        30
    )

    total_duration = (
        duration_minutes * 60
    )

    if start_time:

        elapsed_time = (
            time.time()
            - start_time
        )

        if elapsed_time >= total_duration:

            return redirect(
                "/submit_quiz"
            )

    # ========================================================
    # QUESTION TIMER
    # ========================================================

    question_time_seconds = session.get(
        "question_time_seconds",
        60
    )

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
    # POST ANSWER
    # ========================================================

    if request.method == "POST":

        # ====================================================
        # QUESTION TIME
        # ====================================================

        question_elapsed_time = (
            time.time()
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

        # If timer expired
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
    # REMAINING OVERALL TIME
    # ========================================================

    remaining_seconds = total_duration

    if start_time:

        elapsed_time = (
            time.time()
            - start_time
        )

        remaining_seconds = max(
            0,
            int(
                total_duration
                - elapsed_time
            )
        )

    # ========================================================
    # REMAINING QUESTION TIME
    # ========================================================

    question_elapsed_time = (
        time.time()
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
    # RENDER QUIZ
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

        question_time_seconds=(
            question_time_seconds
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

    guest_name = session.get(
        "guest_name"
    )

    guest_roll_number = session.get(
        "guest_roll_number"
    )

    attempt_id = session.get(
        "guest_attempt_id"
    )

    if not attempt_id:

        attempt_id = session.get(
            "quiz_attempt_id"
        )

    score = 0

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        # ====================================================
        # CALCULATE SCORE
        # ====================================================

        for q in questions:

            q_id = q["question_id"]

            selected = answers.get(
                str(q_id)
            )

            if (
                selected is not None
                and
                selected == q["correct_option"]
            ):

                score += 1

        # ====================================================
        # PERCENTAGE
        # ====================================================

        total = len(questions)

        if total > 0:

            percentage = (
                score / total
            ) * 100

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
                percentage
            )

            VALUES
            (
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
                percentage
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

        db.commit()

        print(
            f"🏁 QUIZ SUBMITTED | "
            f"Quiz={quiz_id} | "
            f"Score={score}/{total} | "
            f"Percentage={percentage:.2f}% | "
            f"Attempt={attempt_id}"
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

            "question_time_seconds",

            "quiz_available_until",

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
        # RESULT
        # ====================================================

        return render_template(
            "result.html",

            score=score,

            total=total,

            percentage=percentage
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