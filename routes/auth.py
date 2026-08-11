
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
# LOGOUT
# ============================================================

@auth.route("/logout")
def logout():

    session.clear()

    return redirect("/")

