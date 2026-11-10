from flask import Blueprint, render_template, request, redirect, url_for, session
from werkzeug.security import check_password_hash
from psycopg2.extras import RealDictCursor
from database import get_db_connection


admin = Blueprint("admin", __name__, url_prefix="/admin")


# ==========================================
# ADMIN LOGIN
# ==========================================

@admin.route("/login", methods=["GET", "POST"])
def login():

    if session.get("admin_id"):
        return redirect(url_for("admin.dashboard"))

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        db = None
        cursor = None

        try:
            db = get_db_connection()
            cursor = db.cursor(cursor_factory=RealDictCursor)

            cursor.execute(
                """
                SELECT admin_id, full_name, email, password
                FROM admin_users
                WHERE LOWER(email) = %s
                AND is_active = TRUE
                """,
                (email,)
            )

            user = cursor.fetchone()

            if user and check_password_hash(user["password"], password):

                cursor.execute(
                    """
                    INSERT INTO login_activity
                    (user_role, user_id, username, full_name, login_status)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        "admin",
                        user["admin_id"],
                        email,
                        user["full_name"],
                        "success"
                    )
                )

                db.commit()

                session["admin_id"] = user["admin_id"]
                session["admin_name"] = user["full_name"]

                return redirect(url_for("admin.dashboard"))

            cursor.execute(
                """
                INSERT INTO login_activity
                (user_role, user_id, username, full_name, login_status)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    "admin",
                    user["admin_id"] if user else None,
                    email,
                    user["full_name"] if user else None,
                    "failed"
                )
            )

            db.commit()

            return render_template(
                "admin_login.html",
                error="Invalid email or password."
            )

        except Exception as e:
            if db:
                db.rollback()

            print("Admin Login Error:", e)

            return render_template(
                "admin_login.html",
                error="Something went wrong. Please try again."
            )

        finally:
            if cursor:
                cursor.close()
            if db:
                db.close()

    return render_template("admin_login.html")


# ==========================================
# ADMIN DASHBOARD
# ==========================================

@admin.route("/dashboard")
def dashboard():

    if not session.get("admin_id"):
        return redirect(url_for("admin.login"))

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            "SELECT COUNT(*) AS total FROM teachers"
        )
        total_teachers = cursor.fetchone()["total"]

        cursor.execute(
            "SELECT COUNT(*) AS total FROM students"
        )
        total_students = cursor.fetchone()["total"]

        cursor.execute(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE login_status = 'success'
                ) AS successful_logins,

                COUNT(*) FILTER (
                    WHERE login_status = 'failed'
                ) AS failed_logins,

                COUNT(*) FILTER (
                    WHERE login_status = 'success'
                    AND user_role = 'teacher'
                ) AS teacher_logins,

                COUNT(*) FILTER (
                    WHERE login_status = 'success'
                    AND user_role = 'student'
                ) AS student_logins,

                COUNT(*) FILTER (
                    WHERE login_status = 'success'
                    AND user_role = 'admin'
                ) AS admin_logins,

                COUNT(DISTINCT (user_role, user_id)) FILTER (
                    WHERE login_status = 'success'
                    AND user_id IS NOT NULL
                ) AS unique_users

            FROM login_activity
            """
        )

        login_stats = cursor.fetchone()

        cursor.execute(
            """
            SELECT
                user_role,
                username,
                full_name,
                login_status,
                login_time
            FROM login_activity
            ORDER BY login_time DESC
            LIMIT 20
            """
        )

        recent_activity = cursor.fetchall()

        return render_template(
            "admin_dashboard.html",
            admin_name=session.get("admin_name"),
            total_teachers=total_teachers,
            total_students=total_students,
            login_stats=login_stats,
            recent_activity=recent_activity
        )

    except Exception as e:
        import traceback
        print("\n========== ADMIN DASHBOARD ERROR ==========")
        traceback.print_exc()
        print("============================================\n")
        return "Unable to load Admin Dashboard.", 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


# ==========================================
# ADMIN LOGOUT
# ==========================================

@admin.route("/logout")
def logout():

    session.pop("admin_id", None)
    session.pop("admin_name", None)

    return redirect(url_for("admin.login"))