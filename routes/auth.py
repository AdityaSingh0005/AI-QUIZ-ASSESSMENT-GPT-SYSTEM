
from flask import Blueprint, render_template, request, redirect, session
from database import get_db_connection
from psycopg2.extras import RealDictCursor


auth = Blueprint("auth", __name__)


# ============================================================
# LOGIN PAGE
# ============================================================

@auth.route("/")
def login_page():

    return render_template("login.html")


# ============================================================
# LOGIN
# ============================================================

@auth.route("/login", methods=["POST"])
def login():

    role = request.form["role"]
    username = request.form["username"]
    password = request.form["password"]

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        # ====================================================
        # TEACHER LOGIN
        # ====================================================

        if role == "teacher":

            cursor.execute(
                """
                SELECT *
                FROM teachers
                WHERE email=%s
                AND password=%s
                """,
                (
                    username,
                    password
                )
            )

            user = cursor.fetchone()

            if user:

                # Clear any old student session
                session.pop("student_id", None)

                session["teacher_id"] = user["teacher_id"]
                session["name"] = user["full_name"]

                return redirect(
                    "/teacher_dashboard"
                )


        # ====================================================
        # STUDENT LOGIN
        # ====================================================

        elif role == "student":

            cursor.execute(
                """
                SELECT *
                FROM students
                WHERE roll_number=%s
                AND password=%s
                """,
                (
                    username,
                    password
                )
            )

            user = cursor.fetchone()

            if user:

                # Clear any old teacher session
                session.pop("teacher_id", None)

                session["student_id"] = user["student_id"]
                session["name"] = user["full_name"]

                # =================================================
                # QR CODE QUIZ CHECK
                # =================================================
                #
                # If the student reached the login page by
                # scanning a quiz QR code, student.py stores
                # the quiz_id here.
                #
                # After successful login we send the student
                # directly to that quiz.
                # =================================================

                pending_quiz_id = session.pop(
                    "pending_quiz_id",
                    None
                )

                if pending_quiz_id:

                    return redirect(
                        f"/start_quiz/{pending_quiz_id}"
                    )

                # Normal student login

                return redirect(
                    "/student_dashboard"
                )


        # ====================================================
        # INVALID LOGIN
        # ====================================================

        return "Invalid Login"


    except Exception as e:

        print(
            "LOGIN ERROR:",
            e
        )

        return f"""
        <h2>Login Error</h2>
        <p>{e}</p>
        """


    finally:

        cursor.close()
        db.close()

# ============================================================
# GUEST QUIZ - ENTER QUIZ ID
# ============================================================

@auth.route("/guest_quiz", methods=["POST"])
def guest_quiz():

    quiz_id = request.form.get("quiz_id", "").strip()

    # ========================================================
    # QUIZ ID VALIDATION
    # ========================================================

    if not quiz_id.isdigit():

        return render_template(
            "guest_quiz.html",
            error="Please enter a valid Quiz ID."
        )

    quiz_id = int(quiz_id)

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        # ====================================================
        # CHECK QUIZ
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

    finally:

        cursor.close()
        db.close()

    # ========================================================
    # QUIZ NOT FOUND / NOT AVAILABLE
    # ========================================================

    if not quiz:

        return render_template(
            "guest_quiz.html",
            error=(
                "Quiz not found or this quiz is "
                "currently unavailable."
            )
        )

    # ========================================================
    # QUIZ FOUND
    # ========================================================

    return render_template(
        "guest_quiz.html",
        quiz=quiz
    )
    
# ============================================================
# LOGOUT
# ============================================================

@auth.route("/logout")
def logout():

    session.clear()

    return redirect("/")

# ============================================================
# GUEST START QUIZ
# Name + Roll Number ke baad actual quiz start
# ============================================================

@auth.route("/guest_start_quiz", methods=["POST"])
def guest_start_quiz():

    # ========================================================
    # GET FORM DATA
    # ========================================================

    quiz_id = request.form.get("quiz_id", "").strip()
    name = request.form.get("name", "").strip()
    roll_number = request.form.get("roll_number", "").strip()

    # ========================================================
    # BASIC VALIDATION
    # ========================================================

    if not quiz_id.isdigit():

        return redirect("/")

    if not name or not roll_number:

        return redirect(
            f"/guest_quiz?quiz_id={quiz_id}"
        )

    quiz_id = int(quiz_id)

    # ========================================================
    # DATABASE
    # ========================================================

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

    finally:

        cursor.close()
        db.close()

    # ========================================================
    # QUIZ NOT AVAILABLE
    # ========================================================

    if not quiz:

        return redirect("/")

    # ========================================================
    # GET QUESTIONS
    # ========================================================

    from utils.quiz_engine import get_quiz_questions

    questions = get_quiz_questions(
        quiz_id
    )

    if not questions:

        return """
        <h2>❌ Quiz has no questions.</h2>
        <a href="/">← Back to Login</a>
        """

    # ========================================================
    # CREATE GUEST ATTEMPT
    # ========================================================

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

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
                name,
                roll_number
            )
        )

        attempt = cursor.fetchone()

        db.commit()

    except Exception:

        db.rollback()
        raise

    finally:

        cursor.close()
        db.close()

    # ========================================================
    # STORE GUEST QUIZ SESSION
    # ========================================================

    session["guest_attempt"] = True

    session["guest_attempt_id"] = (
        attempt["attempt_id"]
    )

    session["guest_name"] = name

    session["guest_roll_number"] = roll_number

    session["quiz_id"] = quiz_id

    session["questions"] = questions

    session["current_question"] = 0

    session["answers"] = {}

    # ========================================================
    # TIMER SETTINGS
    # ========================================================

    session["quiz_duration_minutes"] = (
        quiz["duration_minutes"] or 30
    )

    session["question_time_seconds"] = (
        quiz["question_time_seconds"] or 60
    )

    # ========================================================
    # QUIZ AVAILABILITY
    # ========================================================

    if quiz["available_until"]:

        session["quiz_available_until"] = (
            quiz["available_until"].timestamp()
        )

    else:

        session["quiz_available_until"] = None

    # ========================================================
    # QUIZ START TIME
    # ========================================================

    import time

    session["quiz_start_time"] = time.time()

    # ========================================================
    # ACTUAL QUIZ
    # ========================================================

    return redirect("/quiz")