from flask import Blueprint, render_template, request, redirect, session
from database import get_db_connection
from utils.qr_generator import generate_qr
from utils.ai_generator import generate_questions
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta, timezone


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

    # ==========================================
    # OPEN CREATE QUIZ PAGE
    # ==========================================

    if request.method == "GET":

        return render_template(
            "create_quiz.html"
        )

    # ==========================================
    # BASIC QUIZ DETAILS
    # ==========================================

    title = request.form.get("title")
    prompt = request.form.get("prompt")

    easy = int(
        request.form.get("easy", 0)
    )

    medium = int(
        request.form.get("medium", 0)
    )

    hard = int(
        request.form.get("hard", 0)
    )

    # ==========================================
    # QUIZ DURATION
    # ==========================================

    duration_minutes = int(
        request.form.get(
            "duration_minutes",
            30
        )
    )

    # ==========================================
    # TIME PER QUESTION
    # ==========================================

    question_time_seconds = int(
        request.form.get(
            "question_time_seconds",
            60
        )
    )

    # ==========================================
    # QUIZ AVAILABILITY
    # ==========================================

    availability = request.form.get(
        "availability",
        "1_day"
    )

    # ==========================================
    # TOTAL QUESTIONS
    # ==========================================

    total_questions = (
        easy +
        medium +
        hard
    )

    if total_questions <= 0:

        return """
        <h2>❌ Please select at least one question.</h2>

        <a href="/create_quiz">
            ← Back to Create Quiz
        </a>
        """

    # ==========================================
    # VALIDATE QUIZ DURATION
    # ==========================================

    if duration_minutes <= 0:

        return """
        <h2>❌ Quiz duration must be greater than 0.</h2>

        <a href="/create_quiz">
            ← Back to Create Quiz
        </a>
        """

    # ==========================================
    # VALIDATE QUESTION TIME
    # ==========================================

    if question_time_seconds <= 0:

        return """
        <h2>❌ Question time must be greater than 0.</h2>

        <a href="/create_quiz">
            ← Back to Create Quiz
        </a>
        """

    # ==========================================
    # CALCULATE QUIZ AVAILABILITY
    # ==========================================

    # IMPORTANT:
    # Database columns are TIMESTAMPTZ.
    # Therefore we use timezone-aware UTC datetime.

    available_from = datetime.now(timezone.utc)

    if availability == "1_hour":

        available_until = (
            available_from +
            timedelta(hours=1)
        )

    elif availability == "1_day":

        available_until = (
            available_from +
            timedelta(days=1)
        )

    elif availability == "1_week":

        available_until = (
            available_from +
            timedelta(weeks=1)
        )

    elif availability == "1_month":

        available_until = (
            available_from +
            timedelta(days=30)
        )

    elif availability == "1_year":

        available_until = (
            available_from +
            timedelta(days=365)
        )

    elif availability == "never":

        available_until = None

    else:

        available_until = (
            available_from +
            timedelta(days=1)
        )

    # ==========================================
    # DATABASE CONNECTION
    # ==========================================

    db = get_db_connection()
    cursor = db.cursor()

    try:

        # ==========================================
        # INSERT QUIZ
        # ==========================================

        cursor.execute(
            """
            INSERT INTO quizzes
            (
                teacher_id,
                title,
                prompt,
                total_questions,
                duration_minutes,
                question_time_seconds,
                available_from,
                available_until
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
            RETURNING quiz_id
            """,
            (
                session["teacher_id"],
                title,
                prompt,
                total_questions,
                duration_minutes,
                question_time_seconds,
                available_from,
                available_until
            )
        )

        # ==========================================
        # GET GENERATED QUIZ ID
        # ==========================================

        quiz_id = cursor.fetchone()[0]

        # ==========================================
        # GENERATE AI QUESTIONS
        # ==========================================

        questions = generate_questions(
            prompt,
            easy,
            medium,
            hard
        )

        # ==========================================
        # CHECK AI QUESTIONS
        # ==========================================

        if not questions:

            raise Exception(
                "AI could not generate questions."
            )

        # ==========================================
        # SAVE QUESTIONS
        # ==========================================

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
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
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

        # ==========================================
        # GENERATE QR CODE
        # ==========================================

        qr_path = generate_qr(
            quiz_id
        )

        # ==========================================
        # SAVE QR PATH
        # ==========================================

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

        # ==========================================
        # COMMIT EVERYTHING
        # ==========================================

        db.commit()

        print(
            f"✅ Quiz {quiz_id} created successfully"
        )

        print(
            f"📅 Available From: {available_from}"
        )

        print(
            f"📅 Available Until: {available_until}"
        )

        # ==========================================
        # OPEN GENERATED QUIZ
        # ==========================================

        return redirect(
            f"/quiz_generated/{quiz_id}"
        )

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

    # ==========================================
    # QUIZ INFORMATION
    # ==========================================

    cursor.execute(
        """
        SELECT
            quiz_id,
            title,
            prompt,
            total_questions,
            duration_minutes,
            question_time_seconds,
            available_from,
            available_until,
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

    # ==========================================
    # QUESTIONS
    # ==========================================

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
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
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
            qr_code_path,
            available_from,
            available_until
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
            duration_minutes,
            question_time_seconds,
            available_from,
            available_until,
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

        # ==========================================
        # STUDENT ANSWERS
        # ==========================================

        cursor.execute(
            """
            DELETE FROM student_answers
            WHERE quiz_id=%s
            """,
            (quiz_id,)
        )

        # ==========================================
        # RESULTS
        # ==========================================

        cursor.execute(
            """
            DELETE FROM results
            WHERE quiz_id=%s
            """,
            (quiz_id,)
        )

        # ==========================================
        # QUESTIONS
        # ==========================================

        cursor.execute(
            """
            DELETE FROM questions
            WHERE quiz_id=%s
            """,
            (quiz_id,)
        )

        # ==========================================
        # QUIZ
        # ==========================================

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
    
 # ==========================================
# LIVE QUIZ PROGRESS
# ==========================================

@teacher.route("/quiz_progress/<int:quiz_id>")
def quiz_progress(quiz_id):

    # ==========================================
    # TEACHER LOGIN CHECK
    # ==========================================

    if "teacher_id" not in session:
        return {
            "error": "Unauthorized"
        }, 401

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        # ==========================================
        # VERIFY QUIZ BELONGS TO TEACHER
        # ==========================================

        cursor.execute(
            """
            SELECT
                quiz_id,
                total_questions
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

            return {
                "error": "Quiz not found"
            }, 404

        # ==========================================
        # GET RESPONSE COUNT FOR EACH QUESTION
        # ==========================================

        cursor.execute(
            """
            SELECT
                q.question_id,
                COUNT(sa.student_answer_id) AS response_count

            FROM questions q

            LEFT JOIN student_answers sa
                ON sa.question_id = q.question_id
                AND sa.quiz_id = q.quiz_id

            WHERE q.quiz_id=%s

            GROUP BY
                q.question_id

            ORDER BY
                q.question_id
            """,
            (quiz_id,)
        )

        progress = cursor.fetchall()

        # ==========================================
        # TOTAL UNIQUE RESPONSES
        # ==========================================

        cursor.execute(
            """
            SELECT
                COUNT(DISTINCT attempt_id)
                AS total_students

            FROM student_answers

            WHERE quiz_id=%s
            """,
            (quiz_id,)
        )

        total = cursor.fetchone()

        return {
            "quiz_id": quiz_id,
            "total_questions": quiz["total_questions"],
            "total_students": (
                total["total_students"] or 0
            ),
            "progress": progress
        }

    finally:

        cursor.close()
        db.close()
        
        # ==========================================
# LIVE QUIZ PROGRESS API
# ==========================================

@teacher.route("/api/quiz_progress/<int:quiz_id>")
def quiz_progress(quiz_id):

    # ==========================================
    # TEACHER LOGIN CHECK
    # ==========================================

    if "teacher_id" not in session:

        return {
            "success": False,
            "message": "Unauthorized"
        }, 401

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        # ==========================================
        # VERIFY QUIZ BELONGS TO THIS TEACHER
        # ==========================================

        cursor.execute(
            """
            SELECT
                quiz_id,
                total_questions
            FROM quizzes
            WHERE
                quiz_id=%s
                AND teacher_id=%s
            """,
            (
                quiz_id,
                session["teacher_id"]
            )
        )

        quiz = cursor.fetchone()

        if not quiz:

            return {
                "success": False,
                "message": "Quiz not found"
            }, 404

        # ==========================================
        # GET QUESTIONS + ANSWER COUNT
        # ==========================================

        cursor.execute(
            """
            SELECT
                q.question_id,
                q.question,

                COUNT(
                    CASE
                        WHEN sa.answer_id IS NOT NULL
                        THEN 1
                    END
                ) AS response_count

            FROM questions q

            LEFT JOIN student_answers sa
                ON sa.question_id = q.question_id
                AND sa.quiz_id = q.quiz_id

            WHERE
                q.quiz_id=%s

            GROUP BY
                q.question_id,
                q.question

            ORDER BY
                q.question_id
            """,
            (quiz_id,)
        )

        questions = cursor.fetchall()

        # ==========================================
        # TOTAL PARTICIPANTS
        # ==========================================

        cursor.execute(
            """
            SELECT
                COUNT(DISTINCT attempt_id) AS total_attempts
            FROM student_answers
            WHERE quiz_id=%s
            """,
            (quiz_id,)
        )

        participant_data = cursor.fetchone()

        total_attempts = (
            participant_data["total_attempts"]
            or 0
        )

        # ==========================================
        # TOTAL RESPONSES
        # ==========================================

        total_responses = sum(
            int(q["response_count"] or 0)
            for q in questions
        )

        # ==========================================
        # RETURN JSON
        # ==========================================

        return {
            "success": True,

            "quiz_id": quiz_id,

            "total_questions": (
                quiz["total_questions"]
            ),

            "total_participants": (
                total_attempts
            ),

            "total_responses": (
                total_responses
            ),

            "questions": [
                {
                    "question_id": q["question_id"],

                    "question": q["question"],

                    "response_count": int(
                        q["response_count"] or 0
                    )
                }

                for q in questions
            ]
        }

    except Exception as e:

        print(
            "❌ LIVE PROGRESS ERROR:",
            e
        )

        return {
            "success": False,
            "message": str(e)
        }, 500

    finally:

        cursor.close()
        db.close()