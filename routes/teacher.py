
from flask import Blueprint, render_template, request, redirect, session
from database import get_db_connection
from utils.qr_generator import generate_qr
from utils.ai_generator import generate_questions
from psycopg2.extras import RealDictCursor

teacher = Blueprint("teacher", __name__)


# ==========================================
# TEACHER DASHBOARD
# ==========================================

@teacher.route("/teacher_dashboard")
def teacher_dashboard():

    if "teacher_id" not in session:
        return redirect("/")

    return render_template(
        "teacher_dashboard.html",
        name=session["name"]
    )


# ==========================================
# CREATE QUIZ
# ==========================================

@teacher.route("/create_quiz", methods=["GET", "POST"])
def create_quiz():

    if "teacher_id" not in session:
        return redirect("/")

    # =========================
    # OPEN CREATE QUIZ PAGE
    # =========================

    if request.method == "GET":
        return render_template("create_quiz.html")

    # =========================
    # CREATE QUIZ
    # =========================

    title = request.form.get("title")
    prompt = request.form.get("prompt")

    easy = int(request.form.get("easy", 0))
    medium = int(request.form.get("medium", 0))
    hard = int(request.form.get("hard", 0))

    total_questions = easy + medium + hard

    if total_questions <= 0:
        return "Please select at least one question."

    db = get_db_connection()
    cursor = db.cursor()

    try:

        # =========================
        # INSERT QUIZ
        # =========================

        cursor.execute(
            """
            INSERT INTO quizzes
            (
                teacher_id,
                title,
                prompt,
                total_questions
            )
            VALUES (%s, %s, %s, %s)
            RETURNING quiz_id
            """,
            (
                session["teacher_id"],
                title,
                prompt,
                total_questions
            )
        )

        # PostgreSQL way of getting the generated ID
        quiz_id = cursor.fetchone()[0]

        # =========================
        # GENERATE AI QUESTIONS
        # =========================

        questions = generate_questions(
            prompt,
            easy,
            medium,
            hard
        )

        # =========================
        # SAVE QUESTIONS
        # =========================

        for q in questions:

            cursor.execute(
                """
                INSERT INTO questions
                (
                    quiz_id,
                    question,
                    option_a,
                    option_b,
                    option_c,
                    option_d,
                    correct_option,
                    difficulty
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    quiz_id,
                    q["question"],
                    q["option_a"],
                    q["option_b"],
                    q["option_c"],
                    q["option_d"],
                    q["correct_option"],
                    q["difficulty"]
                )
            )

        # =========================
        # GENERATE QR
        # =========================

        qr_path = generate_qr(quiz_id)

        cursor.execute(
            """
            UPDATE quizzes
            SET qr_code_path = %s
            WHERE quiz_id = %s
            """,
            (
                qr_path,
                quiz_id
            )
        )

        db.commit()

        print(
            f"✅ Quiz {quiz_id} created successfully"
        )

        # =========================
        # GO TO MANAGE QUIZ
        # =========================

        return redirect("/teacher_dashboard")

    except Exception as e:

        db.rollback()

        print(
            "❌ ERROR WHILE CREATING QUIZ:",
            e
        )

        return f"""
        <h2>❌ Error while creating quiz</h2>
        <p>{e}</p>
        <br>
        <a href="/create_quiz">
            ← Back to Create Quiz
        </a>
        """

    finally:

        cursor.close()
        db.close()


# ==========================================
# GENERATED QUIZ
# ==========================================

@teacher.route("/quiz_generated/<int:quiz_id>")
def quiz_generated(quiz_id):

    if "teacher_id" not in session:
        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    # Quiz information

    cursor.execute(
        """
        SELECT
            quiz_id,
            title,
            prompt,
            total_questions,
            qr_code_path,
            created_at
        FROM quizzes
        WHERE quiz_id=%s
        AND teacher_id=%s
        """,
        (
            quiz_id,
            session["teacher_id"]
        )
    )

    quiz = cursor.fetchone()

    if not quiz:

        cursor.close()
        db.close()

        return "Quiz not found."

    # Questions

    cursor.execute(
        """
        SELECT
            question_id,
            question,
            option_a,
            option_b,
            option_c,
            option_d,
            correct_option,
            difficulty
        FROM questions
        WHERE quiz_id=%s
        ORDER BY question_id
        """,
        (quiz_id,)
    )

    questions = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template(
        "quiz_generated.html",
        quiz=quiz,
        questions=questions
    )


# ==========================================
# ADD QUESTION MANUALLY
# ==========================================

@teacher.route(
    "/add_questions/<int:quiz_id>",
    methods=["GET", "POST"]
)
def add_questions(quiz_id):

    if "teacher_id" not in session:
        return redirect("/")

    if request.method == "POST":

        question = request.form["question"]
        option_a = request.form["option_a"]
        option_b = request.form["option_b"]
        option_c = request.form["option_c"]
        option_d = request.form["option_d"]
        correct_option = request.form["correct_option"]

        difficulty = request.form.get(
            "difficulty",
            "Medium"
        )

        db = get_db_connection()
        cursor = db.cursor()

        try:

            cursor.execute(
                """
                INSERT INTO questions
                (
                    quiz_id,
                    question,
                    option_a,
                    option_b,
                    option_c,
                    option_d,
                    correct_option,
                    difficulty
                )
                VALUES
                (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    quiz_id,
                    question,
                    option_a,
                    option_b,
                    option_c,
                    option_d,
                    correct_option,
                    difficulty
                )
            )

            db.commit()

        except Exception:

            db.rollback()
            raise

        finally:

            cursor.close()
            db.close()

        return redirect(
            f"/quiz_generated/{quiz_id}"
        )

    return render_template(
        "add_questions.html",
        quiz_id=quiz_id
    )


# ==========================================
# VIEW RESULTS
# ==========================================

@teacher.route("/view_results")
def view_results():

    if "teacher_id" not in session:
        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    cursor.execute(
        """
        SELECT
            students.full_name,
            students.roll_number,
            quizzes.title,
            results.score,
            results.percentage,
            results.submitted_at
        FROM results

        INNER JOIN students
            ON results.student_id =
               students.student_id

        INNER JOIN quizzes
            ON results.quiz_id =
               quizzes.quiz_id

        WHERE quizzes.teacher_id=%s

        ORDER BY results.submitted_at DESC
        """,
        (
            session["teacher_id"],
        )
    )

    results = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template(
        "view_results.html",
        results=results
    )


# ==========================================
# SHOW QR
# ==========================================

@teacher.route("/show_qr/<int:quiz_id>")
def show_qr(quiz_id):

    if "teacher_id" not in session:
        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    cursor.execute(
        """
        SELECT
            title,
            qr_code_path
        FROM quizzes
        WHERE quiz_id=%s
        AND teacher_id=%s
        """,
        (
            quiz_id,
            session["teacher_id"]
        )
    )

    quiz = cursor.fetchone()

    cursor.close()
    db.close()

    return render_template(
        "show_qr.html",
        quiz=quiz
    )


# ==========================================
# QR PAGE
# ==========================================

@teacher.route("/generate_qr_page")
def generate_qr_page():

    if "teacher_id" not in session:
        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    cursor.execute(
        """
        SELECT
            quiz_id,
            title,
            qr_code_path
        FROM quizzes
        WHERE teacher_id=%s
        ORDER BY quiz_id DESC
        """,
        (
            session["teacher_id"],
        )
    )

    quizzes = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template(
        "generate_qr_page.html",
        quizzes=quizzes
    )


# ==========================================
# MANAGE QUIZZES
# ==========================================

@teacher.route("/manage_quizzes")
def manage_quizzes():

    if "teacher_id" not in session:
        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    cursor.execute(
        """
        SELECT
            quiz_id,
            title,
            total_questions,
            created_at
        FROM quizzes
        WHERE teacher_id=%s
        ORDER BY quiz_id DESC
        """,
        (
            session["teacher_id"],
        )
    )

    quizzes = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template(
        "manage_quizzes.html",
        quizzes=quizzes
    )


# ==========================================
# DELETE QUIZ
# ==========================================

@teacher.route("/delete_quiz/<int:quiz_id>")
def delete_quiz(quiz_id):

    if "teacher_id" not in session:
        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor()

    try:

        # Student answers

        cursor.execute(
            """
            DELETE FROM student_answers
            WHERE quiz_id=%s
            """,
            (quiz_id,)
        )

        # Results

        cursor.execute(
            """
            DELETE FROM results
            WHERE quiz_id=%s
            """,
            (quiz_id,)
        )

        # Questions

        cursor.execute(
            """
            DELETE FROM questions
            WHERE quiz_id=%s
            """,
            (quiz_id,)
        )

        # Quiz

        cursor.execute(
            """
            DELETE FROM quizzes
            WHERE quiz_id=%s
            AND teacher_id=%s
            """,
            (
                quiz_id,
                session["teacher_id"]
            )
        )

        db.commit()

    except Exception as e:

        db.rollback()

        print(
            "DELETE QUIZ ERROR:",
            e
        )

    finally:

        cursor.close()
        db.close()

    return redirect(
        "/manage_quizzes"
    )


# ==========================================
# TEST AI
# ==========================================

@teacher.route("/test_ai")
def test_ai():

    questions = generate_questions(
        "Database Management System",
        1,
        1,
        1
    )

    return {
        "status": "success",
        "questions": questions
    }

