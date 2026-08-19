from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    session
)

from database import get_db_connection
from psycopg2.extras import RealDictCursor


auth = Blueprint("auth", __name__)


# ============================================================
# LOGIN PAGE
# ============================================================

@auth.route("/")
def login_page():

    return render_template(
        "login.html"
    )


# ============================================================
# LOGIN
# ============================================================

@auth.route("/login", methods=["POST"])
def login():

    role = request.form.get(
        "role",
        ""
    ).strip().lower()

    username = request.form.get(
        "username",
        ""
    ).strip()

    password = request.form.get(
        "password",
        ""
    )

    if not role or not username or not password:

        return render_template(
            "login.html",
            error="Please fill all login fields."
        )

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

                WHERE LOWER(email)=LOWER(%s)

                AND password=%s
                """,
                (
                    username,
                    password
                )
            )

            user = cursor.fetchone()

            if user:

                # Clear student session
                session.pop(
                    "student_id",
                    None
                )

                # Teacher session
                session["teacher_id"] = (
                    user["teacher_id"]
                )

                session["name"] = (
                    user["full_name"]
                )

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

                WHERE LOWER(roll_number)=LOWER(%s)

                AND password=%s
                """,
                (
                    username,
                    password
                )
            )

            user = cursor.fetchone()

            if user:

                # Clear teacher session
                session.pop(
                    "teacher_id",
                    None
                )

                # Student session
                session["student_id"] = (
                    user["student_id"]
                )

                session["name"] = (
                    user["full_name"]
                )

                # =================================================
                # QR QUIZ CHECK
                # =================================================

                pending_quiz_id = session.pop(
                    "pending_quiz_id",
                    None
                )

                if pending_quiz_id:

                    return redirect(
                        f"/start_quiz/{pending_quiz_id}"
                    )

                return redirect(
                    "/student_dashboard"
                )


        # ====================================================
        # INVALID LOGIN
        # ====================================================

        return render_template(
            "login.html",
            error="Invalid account type, username or password."
        )


    except Exception as e:

        print(
            "❌ LOGIN ERROR:",
            e
        )

        return render_template(
            "login.html",
            error="Something went wrong while logging in."
        )


    finally:

        cursor.close()
        db.close()


# ============================================================
# REGISTER PAGE
# ============================================================

@auth.route("/register", methods=["GET"])
def register_page():

    return render_template(
        "register.html"
    )


# ============================================================
# REGISTER ACCOUNT
# ============================================================

@auth.route("/register", methods=["POST"])
def register():

    role = request.form.get(
        "role",
        ""
    ).strip().lower()

    full_name = request.form.get(
        "full_name",
        ""
    ).strip()

    username = request.form.get(
        "username",
        ""
    ).strip()

    password = request.form.get(
        "password",
        ""
    )

    confirm_password = request.form.get(
        "confirm_password",
        ""
    )

    department = request.form.get(
        "department",
        ""
    ).strip()

    semester = request.form.get(
        "semester",
        ""
    ).strip()

    section = request.form.get(
        "section",
        ""
    ).strip()


    # ========================================================
    # BASIC VALIDATION
    # ========================================================

    if not role:

        return render_template(
            "register.html",
            error="Please select account type."
        )


    if role not in ["teacher", "student"]:

        return render_template(
            "register.html",
            error="Invalid account type."
        )


    if not full_name:

        return render_template(
            "register.html",
            error="Full name is required."
        )


    if not username:

        return render_template(
            "register.html",
            error=(
                "Email is required."
                if role == "teacher"
                else "Roll number is required."
            )
        )


    if not password:

        return render_template(
            "register.html",
            error="Password is required."
        )


    if len(password) < 6:

        return render_template(
            "register.html",
            error="Password must contain at least 6 characters."
        )


    if password != confirm_password:

        return render_template(
            "register.html",
            error="Passwords do not match."
        )


    # ========================================================
    # STUDENT EXTRA VALIDATION
    # ========================================================

    if role == "student":

        if not department:

            return render_template(
                "register.html",
                error="Department is required."
            )

        if not semester:

            return render_template(
                "register.html",
                error="Semester is required."
            )

        if not section:

            return render_template(
                "register.html",
                error="Section is required."
            )


    # ========================================================
    # DATABASE
    # ========================================================

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        # ====================================================
        # TEACHER REGISTRATION
        # ====================================================

        if role == "teacher":

            # Check duplicate email
            cursor.execute(
                """
                SELECT teacher_id
                FROM teachers

                WHERE LOWER(email)=LOWER(%s)
                """,
                (
                    username,
                )
            )

            existing_teacher = cursor.fetchone()

            if existing_teacher:

                return render_template(
                    "register.html",
                    error=(
                        "An account already exists "
                        "with this email."
                    )
                )


            # Insert teacher
            cursor.execute(
                """
                INSERT INTO teachers
                (
                    full_name,
                    email,
                    password
                )

                VALUES
                (
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    full_name,
                    username,
                    password
                )
            )


        # ====================================================
        # STUDENT REGISTRATION
        # ====================================================

        elif role == "student":

            # Check duplicate roll number
            cursor.execute(
                """
                SELECT student_id
                FROM students

                WHERE LOWER(roll_number)=LOWER(%s)
                """,
                (
                    username,
                )
            )

            existing_student = cursor.fetchone()

            if existing_student:

                return render_template(
                    "register.html",
                    error=(
                        "An account already exists "
                        "with this roll number."
                    )
                )


            # Insert student
            cursor.execute(
                """
                INSERT INTO students
                (
                    full_name,
                    roll_number,
                    password,
                    department,
                    semester,
                    section
                )

                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    full_name,
                    username,
                    password,
                    department,
                    semester,
                    section
                )
            )


        # ====================================================
        # COMMIT
        # ====================================================

        db.commit()

        print(
            f"✅ NEW {role.upper()} REGISTERED | "
            f"Name={full_name} | "
            f"Username={username}"
        )


        # ====================================================
        # SUCCESS
        # ====================================================

        return render_template(
            "register.html",
            success=(
                "Account created successfully! "
                "You can now login with your new account."
            )
        )


    except Exception as e:

        db.rollback()

        print(
            "❌ REGISTRATION ERROR:",
            e
        )

        return render_template(
            "register.html",
            error=(
                "Registration failed. "
                "Please check your details and try again."
            )
        )


    finally:

        cursor.close()
        db.close()


# ============================================================
# GUEST QUIZ - ENTER QUIZ ID
# ============================================================

@auth.route(
    "/guest_quiz",
    methods=["GET", "POST"]
)
def guest_quiz():

    # ========================================================
    # GET QUIZ ID
    # ========================================================

    if request.method == "GET":

        quiz_id = request.args.get(
            "quiz_id",
            ""
        ).strip()

    else:

        quiz_id = request.form.get(
            "quiz_id",
            ""
        ).strip()


    # ========================================================
    # VALIDATE QUIZ ID
    # ========================================================

    if not quiz_id.isdigit():

        return render_template(
            "guest_quiz.html",
            error="Please enter a valid Quiz ID."
        )


    quiz_id = int(
        quiz_id
    )


    # ========================================================
    # DATABASE
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
            (
                quiz_id,
            )
        )

        quiz = cursor.fetchone()

    finally:

        cursor.close()
        db.close()


    # ========================================================
    # QUIZ NOT FOUND
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
# QR CODE → GUEST QUIZ PAGE
# ============================================================
#
# IMPORTANT:
# qr_generator.py creates:
#
# /guest_start_quiz/<quiz_id>
#
# QR scanner sends GET request.
#
# Therefore this route MUST exist.
# ============================================================

@auth.route(
    "/guest_start_quiz/<int:quiz_id>",
    methods=["GET"]
)
def guest_start_quiz_page(quiz_id):

    # ========================================================
    # CHECK QUIZ
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
            (
                quiz_id,
            )
        )

        quiz = cursor.fetchone()

    finally:

        cursor.close()
        db.close()


    # ========================================================
    # QUIZ NOT AVAILABLE
    # ========================================================

    if not quiz:

        return render_template(
            "guest_quiz.html",
            error=(
                "This quiz was not found or "
                "is no longer available."
            )
        )


    # ========================================================
    # SHOW NAME + ROLL NUMBER PAGE
    # ========================================================

    return render_template(
        "guest_quiz.html",
        quiz=quiz
    )


# ============================================================
# GUEST START QUIZ
# ============================================================

@auth.route(
    "/guest_start_quiz",
    methods=["POST"]
)
def guest_start_quiz():

    quiz_id = request.form.get(
        "quiz_id",
        ""
    ).strip()

    name = request.form.get(
        "name",
        ""
    ).strip()

    roll_number = request.form.get(
        "roll_number",
        ""
    ).strip()


    # ========================================================
    # VALIDATION
    # ========================================================

    if not quiz_id.isdigit():

        return redirect("/")


    if not name or not roll_number:

        return redirect(
            f"/guest_quiz?quiz_id={quiz_id}"
        )


    quiz_id = int(
        quiz_id
    )


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
            (
                quiz_id,
            )
        )

        quiz = cursor.fetchone()

    finally:

        cursor.close()
        db.close()


    if not quiz:

        return render_template(
            "guest_quiz.html",
            error="Quiz is no longer available."
        )


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

        <a href="/">
            ← Back to Login
        </a>
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


    except Exception as e:

        db.rollback()

        print(
            "❌ GUEST ATTEMPT ERROR:",
            e
        )

        return """
        <h2>❌ Unable to start quiz.</h2>

        <a href="/">
            ← Back
        </a>
        """


    finally:

        cursor.close()
        db.close()


    # ========================================================
    # STORE SESSION
    # ========================================================

    session["guest_attempt"] = True

    session["guest_mode"] = True

    session["guest_attempt_id"] = (
        attempt["attempt_id"]
    )

    session["quiz_attempt_id"] = (
        attempt["attempt_id"]
    )

    session["guest_name"] = name

    session["guest_student_name"] = name

    session["guest_roll_number"] = (
        roll_number
    )

    session["quiz_id"] = quiz_id

    session["questions"] = questions

    session["current_question"] = 0

    session["answers"] = {}


    # ========================================================
    # TIMER
    # ========================================================

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

    import time

    session["quiz_start_time"] = (
        time.time()
    )

    session["question_start_time"] = (
        time.time()
    )

    session.modified = True


    return redirect(
        "/quiz"
    )


# ============================================================
# LOGOUT
# ============================================================

@auth.route("/logout")
def logout():

    session.clear()

    return redirect("/")