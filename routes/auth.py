from werkzeug.security import generate_password_hash, check_password_hash
import hmac
import os
import re
import secrets
import hashlib
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    session,
    flash,
    current_app,
    url_for,
)

from database import get_db_connection
from psycopg2.extras import RealDictCursor





auth = Blueprint("auth", __name__)

def _verify_password_and_upgrade(cursor, table, id_column, user, supplied_password):
    if not user:
        return False

    stored = str(user.get("password") or "")

    if not stored:
        return False

    if stored.startswith(("scrypt:", "pbkdf2:", "argon2:")):
        try:
            return check_password_hash(stored, supplied_password)
        except (ValueError, TypeError):
            return False

    if hmac.compare_digest(stored, str(supplied_password)):
        upgraded = generate_password_hash(supplied_password)
        cursor.execute(
            f"UPDATE {table} SET password = %s WHERE {id_column} = %s",
            (upgraded, user[id_column])
        )
        return True

    return False

def _log_login_activity(
    cursor,
    user_role,
    user_id,
    username,
    full_name,
    login_status
):
    cursor.execute(
        """
        INSERT INTO login_activity (
            user_role,
            user_id,
            username,
            full_name,
            login_status
        )
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            user_role,
            user_id,
            username,
            full_name,
            login_status
        )
    )
def _send_student_email(to_email, subject, body):
    """
    Send student emails using Google Apps Script.
    Supports registration OTP and forgot-password OTP.
    """

    script_url = os.getenv("GOOGLE_APPS_SCRIPT_URL", "").strip()
    api_secret = os.getenv("GOOGLE_APPS_SCRIPT_SECRET", "").strip()

    if not script_url or not api_secret:
        raise RuntimeError(
            "Google Apps Script email service is not configured."
        )

    payload = {
        "secret": api_secret,
        "to": to_email,
        "subject": subject,
        "body": body
    }

    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        script_url,
        data=data,
        headers={
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            response_text = response.read().decode("utf-8")
            result = json.loads(response_text)

        if not result.get("success"):
            raise RuntimeError(
                result.get("message", "Email sending failed.")
            )

        current_app.logger.info(
            "Student email sent successfully to %s",
            to_email
        )

        return True

    except urllib.error.HTTPError as error:
        error_body = error.read().decode(
            "utf-8", errors="replace"
        )

        current_app.logger.error(
            "Google Apps Script HTTP error: %s - %s",
            error.code,
            error_body
        )

        raise RuntimeError(
            "Google Apps Script rejected the email request."
        ) from error

    except urllib.error.URLError as error:
        current_app.logger.exception(
            "Google Apps Script connection failed."
        )

        raise RuntimeError(
            "Unable to connect to Google Apps Script."
        ) from error
              
def _valid_email(value):
    value = (value or "").strip().lower()
    return value if len(value) <= 100 and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) else ""


def _guest_matches_quiz_cohort(course, department, semester, quiz):
    def norm(value):
        return " ".join(str(value or "").strip().casefold().split())
    return (
        str(quiz.get("publication_status") or "draft").strip().lower() == "published"
        and bool(norm(course)) and norm(course) == norm(quiz.get("target_course"))
        and bool(norm(department)) and norm(department) == norm(quiz.get("target_department"))
        and bool(norm(semester)) and norm(semester) == norm(quiz.get("target_semester"))
    )


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
                WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s))
                LIMIT 1
                """,
                (username,)
            )

            user = cursor.fetchone()

            if user and _verify_password_and_upgrade(
                cursor, "teachers", "teacher_id", user, password
            ):

                _log_login_activity(
                    cursor,
                    "teacher",
                    user["teacher_id"],
                    username,
                    user["full_name"],
                    "success"
                )

                db.commit()

                session.pop("student_id", None)
                session.pop("pending_quiz_id", None)

                session["teacher_id"] = user["teacher_id"]
                session["name"] = user["full_name"]

                return redirect("/teacher_dashboard")

        # ====================================================
        # STUDENT LOGIN
        # ====================================================

        elif role == "student":

            cursor.execute(
                """
                SELECT *
                FROM students
                WHERE LOWER(TRIM(roll_number)) = LOWER(TRIM(%s))
                LIMIT 1
                """,
                (username,)
            )

            user = cursor.fetchone()

            if user and _verify_password_and_upgrade(
                cursor, "students", "student_id", user, password
            ):

                _log_login_activity(
                    cursor,
                    "student",
                    user["student_id"],
                    username,
                    user["full_name"],
                    "success"
                )

                db.commit()

                session.pop("teacher_id", None)

                session["student_id"] = user["student_id"]
                session["name"] = user["full_name"]

                pending_quiz_id = session.pop(
                    "pending_quiz_id", None
                )

                if pending_quiz_id:
                    return redirect(
                        f"/start_quiz/{pending_quiz_id}"
                    )

                return redirect("/student_dashboard")

                # ====================================================
        # INVALID LOGIN
        # ====================================================

        _log_login_activity(
            cursor,
            role if role in ["teacher", "student"] else "unknown",
            None,
            username,
            None,
            "failed"
        )

        db.commit()

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

    student_email = _valid_email(request.form.get("student_email", ""))

    
    course = (
    request.form.get("course") or ""
    ).strip().upper()


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
        
        if not course:
            return render_template(
                "register.html",
                error="Course is required."
            )

        if not student_email:
            return render_template(
                "register.html",
                error="A valid email address is required for student password recovery."
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
                    generate_password_hash(password)
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


            cursor.execute(
                "SELECT student_id FROM students WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s)) LIMIT 1",
                (student_email,),
            )
            if cursor.fetchone():
                return render_template(
                    "register.html",
                    error="An account already uses this email address. Please use another email."
                )

            # Verify control of the recovery email before creating the account.
            cursor.execute(
                """SELECT COUNT(*) AS request_count
                   FROM pending_student_registrations
                   WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s))
                     AND created_at > NOW() - INTERVAL '15 minutes'""",
                (student_email,),
            )
            if int(cursor.fetchone()["request_count"]) >= 3:
                return render_template(
                    "register.html",
                    error="Too many verification requests for this email. Please try again after 15 minutes."
                )

            otp = f"{secrets.randbelow(1000000):06d}"
            cursor.execute(
                "DELETE FROM pending_student_registrations WHERE expires_at <= NOW()"
            )
            cursor.execute(
                """INSERT INTO pending_student_registrations
                   (full_name, roll_number, course, department, semester,
                    password_hash, email, otp_hash, expires_at, attempts, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                           NOW() + INTERVAL '10 minutes', 0, NOW())""",
                (
                    full_name, username, course, department, semester,
                    generate_password_hash(password), student_email,
                    generate_password_hash(otp),
                ),
            )
            db.commit()
            try:
                _send_student_email(
                    student_email,
                    "AI Quiz Assessment System - Verify Your Email",
                    f"Your student registration verification code is {otp}. It expires in 10 minutes.",
                )
            except Exception:
                current_app.logger.exception("Student registration verification email could not be sent.")
                cursor.execute(
                    "DELETE FROM pending_student_registrations WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s))",
                    (student_email,),
                )
                db.commit()
                return render_template(
                    "student_forgot_password.html", stage="request",
                    error="Gmail could not send the verification email. Please try again later."
                ), 503
            return render_template(
                "student_forgot_password.html", stage="registration_verify",
                email=student_email,
                success="A verification code has been sent to your email. Enter it to finish creating your student account."
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


@auth.route("/student/register/verify", methods=["POST"])
def student_register_verify():
    email = _valid_email(request.form.get("email", ""))
    otp = (request.form.get("otp") or "").strip()
    if not email or not re.fullmatch(r"\d{6}", otp):
        return render_template("student_forgot_password.html", stage="registration_verify", email=email, error="Enter your email and 6-digit verification code."), 400

    db = get_db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            """SELECT * FROM pending_student_registrations
               WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s))
                 AND expires_at > NOW()
               ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
            (email,),
        )
        pending = cursor.fetchone()
        if not pending or int(pending["attempts"]) >= 5 or not check_password_hash(pending["otp_hash"], otp):
            if pending:
                cursor.execute("UPDATE pending_student_registrations SET attempts = attempts + 1 WHERE pending_id = %s", (pending["pending_id"],))
                db.commit()
            return render_template("student_forgot_password.html", stage="registration_verify", email=email, error="Invalid or expired verification code. Register again to request a new code."), 400

        cursor.execute(
            """INSERT INTO students
               (full_name, roll_number, course, department, semester, password, email)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (pending["full_name"], pending["roll_number"], pending["course"],
             pending["department"], pending["semester"], pending["password_hash"], pending["email"]),
        )
        cursor.execute("DELETE FROM pending_student_registrations WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s))", (email,))
        db.commit()
        return render_template("register.html", success="Email verified and student account created successfully. You can now log in.")
    except Exception:
        db.rollback()
        current_app.logger.exception("Student email verification failed.")
        return render_template("student_forgot_password.html", stage="registration_verify", email=email, error="Unable to verify email. Please try again."), 500
    finally:
        cursor.close()
        db.close()


# ============================================================
# STUDENT FORGOT PASSWORD (TEACHER ACCOUNTS ARE NOT TOUCHED)
# ============================================================

@auth.route("/student/forgot-password", methods=["GET", "POST"])
def student_forgot_password():
    if request.method == "GET":
        return render_template("student_forgot_password.html", stage="request")

    roll_number = (request.form.get("roll_number") or "").strip()
    email = _valid_email(request.form.get("email", ""))
    if not roll_number or not email:
        return render_template("student_forgot_password.html", stage="request", error="Enter your roll number and valid email.")

    db = get_db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            """SELECT student_id, email FROM students
               WHERE LOWER(TRIM(roll_number)) = LOWER(TRIM(%s))
                 AND LOWER(TRIM(email)) = LOWER(TRIM(%s))
               LIMIT 1""",
            (roll_number, email),
        )
        student_row = cursor.fetchone()
        # Use a generic response for unknown roll/email combinations.
        if not student_row:
            return render_template("student_forgot_password.html", stage="request", success="If the details match an account, a verification code will be sent.")

        cursor.execute(
            """SELECT COUNT(*) AS sent_count FROM student_password_resets
               WHERE student_id = %s AND created_at > NOW() - INTERVAL '15 minutes'""",
            (student_row["student_id"],),
        )
        if int(cursor.fetchone()["sent_count"]) >= 3:
            return render_template("student_forgot_password.html", stage="request", error="Too many code requests. Please try again after 15 minutes.")

        otp = f"{secrets.randbelow(1000000):06d}"
        otp_hash = generate_password_hash(otp)
        cursor.execute(
            """INSERT INTO student_password_resets
               (student_id, otp_hash, expires_at, attempts, created_at)
               VALUES (%s, %s, NOW() + INTERVAL '10 minutes', 0, NOW())""",
            (student_row["student_id"], otp_hash),
        )
        db.commit()
        try:
            _send_student_email(
                email,
                "AI Quiz Assessment System - Password Reset Code",
                f"Your password reset code is {otp}. It expires in 10 minutes. If you did not request this, ignore this email.",
            )
        except Exception:
            current_app.logger.exception("Student password reset email could not be sent.")
            db.rollback()
            return render_template("student_forgot_password.html", stage="request", error="Email could not be sent. Please contact the administrator or try again later."), 503

        session["student_password_reset_id"] = student_row["student_id"]
        return render_template("student_forgot_password.html", stage="verify", success="A verification code has been sent to your registered email.")
    except Exception:
        db.rollback()
        current_app.logger.exception("Student forgot-password request failed.")
        return render_template("student_forgot_password.html", stage="request", error="Unable to process the request right now."), 500
    finally:
        cursor.close()
        db.close()


@auth.route("/student/reset-password", methods=["POST"])
def student_reset_password():
    student_id = session.get("student_password_reset_id")
    otp = (request.form.get("otp") or "").strip()
    new_password = request.form.get("new_password") or ""
    confirm_password = request.form.get("confirm_password") or ""

    if not student_id:
        return redirect(url_for("auth.student_forgot_password"))
    if not re.fullmatch(r"\d{6}", otp):
        return render_template("student_forgot_password.html", stage="verify", error="Enter the 6-digit verification code.")
    if len(new_password) < 8:
        return render_template("student_forgot_password.html", stage="verify", error="New password must contain at least 8 characters.")
    if new_password != confirm_password:
        return render_template("student_forgot_password.html", stage="verify", error="Passwords do not match.")

    db = get_db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            """SELECT reset_id, otp_hash, attempts
               FROM student_password_resets
               WHERE student_id = %s AND used_at IS NULL AND expires_at > NOW()
               ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
            (student_id,),
        )
        reset_row = cursor.fetchone()
        if not reset_row or int(reset_row["attempts"]) >= 5 or not check_password_hash(reset_row["otp_hash"], otp):
            if reset_row:
                cursor.execute("UPDATE student_password_resets SET attempts = attempts + 1 WHERE reset_id = %s", (reset_row["reset_id"],))
                db.commit()
            return render_template("student_forgot_password.html", stage="verify", error="Invalid or expired code. Request a new code and try again."), 400

        cursor.execute(
            "UPDATE students SET password = %s WHERE student_id = %s",
            (generate_password_hash(new_password), student_id),
        )
        cursor.execute(
            "UPDATE student_password_resets SET used_at = NOW() WHERE reset_id = %s",
            (reset_row["reset_id"],),
        )
        cursor.execute(
            "UPDATE student_password_resets SET used_at = NOW() WHERE student_id = %s AND used_at IS NULL",
            (student_id,),
        )
        db.commit()
        session.pop("student_password_reset_id", None)
        return render_template("student_forgot_password.html", stage="request", success="Password changed successfully. You can now log in with your new password.")
    except Exception:
        db.rollback()
        current_app.logger.exception("Student password reset failed.")
        return render_template("student_forgot_password.html", stage="verify", error="Unable to reset password right now."), 500
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
                available_until,
                target_course,
                target_department,
                target_semester,
                publication_status

            FROM quizzes

            WHERE quiz_id=%s
            AND publication_status = 'published'
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
                available_until,
                target_course,
                target_department,
                target_semester,
                publication_status

            FROM quizzes

            WHERE quiz_id=%s
            AND publication_status = 'published'
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
    course = (request.form.get("course") or "").strip().upper()
    department = (request.form.get("department") or "").strip()
    semester = (request.form.get("semester") or "").strip()


    # ========================================================
    # VALIDATION
    # ========================================================

    if not quiz_id.isdigit():

        return redirect("/")


    if not all([name, roll_number, course, department, semester]):
        return redirect(f"/guest_quiz?quiz_id={quiz_id}")


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
                available_until,
                target_course,
                target_department,
                target_semester,
                publication_status

            FROM quizzes

            WHERE quiz_id=%s
            AND publication_status = 'published'
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

    if not _guest_matches_quiz_cohort(course, department, semester, quiz):
        return render_template(
            "guest_quiz.html",
            error="This quiz is not assigned to your course, department and semester."
        ), 403


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
                course,
                department,
                semester,
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
                %s,
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
                roll_number,
                course,
                department,
                semester
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
    session["guest_course"] = course
    session["guest_department"] = department
    session["guest_semester"] = semester

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
