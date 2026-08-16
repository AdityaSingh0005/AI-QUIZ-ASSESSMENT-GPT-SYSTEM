
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

