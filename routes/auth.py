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

    return render_template("login.html")


# ============================================================
# LOGIN
# ============================================================

@auth.route("/login", methods=["POST"])
def login():

    role = request.form.get("role", "").strip()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    # ========================================================
    # BASIC VALIDATION
    # ========================================================

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

                # Clear student session
                session.pop("student_id", None)

                # Teacher session
                session["teacher_id"] = user["teacher_id"]
                session["name"] = user["full_name"]

                session.modified = True

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

                # Clear teacher session
                session.pop("teacher_id", None)

                # Student session
                session["student_id"] = user["student_id"]
                session["name"] = user["full_name"]

                session.modified = True

                # =================================================
                # QR CODE / PENDING QUIZ CHECK
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

        return render_template(
            "login.html",
            error="Invalid username/roll number or password."
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
# REGISTRATION PAGE
# ============================================================

@auth.route("/register")
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

    email = request.form.get(
        "email",
        ""
    ).strip().lower()

    roll_number = request.form.get(
        "roll_number",
        ""
    ).strip()

    password = request.form.get(
        "password",
        ""
    ).strip()

    confirm_password = request.form.get(
        "confirm_password",
        ""
    ).strip()

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

    if role not in ["teacher", "student"]:

        return render_template(
            "register.html",
            error="Please select a valid account type.",
            form=request.form
        )

    if not full_name:

        return render_template(
            "register.html",
            error="Full name is required.",
            form=request.form
        )

    if not password:

        return render_template(
            "register.html",
            error="Password is required.",
            form=request.form
        )

    if len(password) < 6:

        return render_template(
            "register.html",
            error="Password must contain at least 6 characters.",
            form=request.form
        )

    if password != confirm_password:

        return render_template(
            "register.html",
            error="Passwords do not match.",
            form=request.form
        )

    # ========================================================
    # TEACHER VALIDATION
    # ========================================================

    if role == "teacher":

        if not email:

            return render_template(
                "register.html",
                error="Email is required for teacher account.",
                form=request.form
            )

    # ========================================================
    # STUDENT VALIDATION
    # ========================================================

    if role == "student":

        if not roll_number:

            return render_template(
                "register.html",
                error="Roll number is required for student account.",
                form=request.form
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

            # Check existing email
            cursor.execute(
                """
                SELECT teacher_id
                FROM teachers
                WHERE LOWER(email)=LOWER(%s)
                """,
                (
                    email,
                )
            )

            existing_teacher = cursor.fetchone()

            if existing_teacher:

                return render_template(
                    "register.html",
                    error="An account with this email already exists.",
                    form=request.form
                )

            # Create teacher
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
                RETURNING teacher_id
                """,
                (
                    full_name,
                    email,
                    password
                )
            )

            teacher_user = cursor.fetchone()

            db.commit()

            print(
                f"✅ Teacher registered | "
                f"ID={teacher_user['teacher_id']} | "
                f"Name={full_name}"
            )

            return render_template(
                "register.html",
                success=(
                    "Teacher account created successfully! "
                    "You can now login."
                )
            )

        # ====================================================
        # STUDENT REGISTRATION
        # ====================================================

        elif role == "student":

            # Check existing roll number
            cursor.execute(
                """
                SELECT student_id
                FROM students
                WHERE LOWER(roll_number)=LOWER(%s)
                """,
                (
                    roll_number,
                )
            )

            existing_student = cursor.fetchone()

            if existing_student:

                return render_template(
                    "register.html",
                    error="An account with this roll number already exists.",
                    form=request.form
                )

            # Create student
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
                RETURNING student_id
                """,
                (
                    full_name,
                    roll_number,
                    password,
                    department,
                    semester,
                    section
                )
            )

            student_user = cursor.fetchone()

            db.commit()

            print(
                f"✅ Student registered | "
                f"ID={student_user['student_id']} | "
                f"Name={full_name} | "
                f"Roll={roll_number}"
            )

            return render_template(
                "register.html",
                success=(
                    "Student account created successfully! "
                    "You can now login."
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
                "Unable to create account. "
                "Please check your details."
            ),
            form=request.form
        )

    finally:

        cursor.close()
        db.close()


# ============================================================
# GUEST QUIZ - ENTER QUIZ ID
# ============================================================

@auth.route(
    "/guest_quiz",
    methods=["POST"]
)
def guest_quiz():

    quiz_id = request.form.get(
        "quiz_id",
        ""
    ).strip()

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
# GUEST START QUIZ
# Name + Roll Number → Actual Quiz
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
            (
                quiz_id,
            )
        )

        quiz = cursor.fetchone()

        if not quiz:

            return redirect("/")

        # ====================================================
        # GET QUESTIONS
        # ====================================================

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
                name,
                roll_number
            )
        )

        attempt = cursor.fetchone()

        db.commit()

    except Exception as e:

        db.rollback()

        print(
            "❌ GUEST START ERROR:",
            e
        )

        return f"""
        <h2>❌ Unable to start quiz.</h2>
        <p>{e}</p>
        <a href="/">← Back</a>
        """

    finally:

        cursor.close()
        db.close()

    # ========================================================
    # STORE GUEST SESSION
    # ========================================================

    session["guest_attempt"] = True

    # Compatibility
    session["guest_mode"] = True

    session["guest_attempt_id"] = (
        attempt["attempt_id"]
    )

    session["quiz_attempt_id"] = (
        attempt["attempt_id"]
    )

    session["guest_name"] = name

    session["guest_student_name"] = name

    session["guest_roll_number"] = roll_number

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

    session["quiz_start_time"] = time.time()

    session["question_start_time"] = time.time()

    session.modified = True

    print(
        f"🎯 GUEST QUIZ STARTED | "
        f"Quiz={quiz_id} | "
        f"Attempt={attempt['attempt_id']} | "
        f"Name={name}"
    )

    return redirect("/quiz")


# ============================================================
# LOGOUT
# ============================================================

@auth.route("/logout")
def logout():

    session.clear()

    return redirect("/")