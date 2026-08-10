from flask import Blueprint, render_template, request, redirect, session
from database import get_db_connection


auth = Blueprint("auth", __name__)


@auth.route("/")
def login_page():

    return render_template("login.html")



@auth.route("/login", methods=["POST"])
def login():

    role = request.form["role"]
    username = request.form["username"]
    password = request.form["password"]


    db = get_db_connection()

    cursor = db.cursor(dictionary=True)


    if role == "teacher":

        cursor.execute(
            """
            SELECT * FROM teachers
            WHERE email=%s AND password=%s
            """,
            (username,password)
        )


        user = cursor.fetchone()


        if user:

            session["teacher_id"] = user["teacher_id"]
            session["name"] = user["full_name"]

            return redirect("/teacher_dashboard")



    elif role == "student":

        cursor.execute(
            """
            SELECT * FROM students
            WHERE roll_number=%s AND password=%s
            """,
            (username,password)
        )


        user = cursor.fetchone()


        if user:

            session["student_id"] = user["student_id"]
            session["name"] = user["full_name"]

            return redirect("/student_dashboard")


    return "Invalid Login"



@auth.route("/logout")
def logout():

    session.clear()

    return redirect("/")