from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    session
)

from database import get_db_connection

from utils.qr_generator import generate_qr
from utils.ai_generator import generate_questions

from psycopg2.extras import RealDictCursor

from datetime import datetime, timedelta, timezone


teacher = Blueprint("teacher", __name__)


# ============================================================
# HELPER
# ============================================================

def teacher_logged_in():
    """
    Check whether teacher is logged in.
    """
    return "teacher_id" in session


# ============================================================
# TEACHER DASHBOARD
# ============================================================

@teacher.route("/teacher_dashboard")
def teacher_dashboard():

    if not teacher_logged_in():
        return redirect("/")

    return render_template(
        "teacher_dashboard.html",
        name=session.get("name", "Teacher")
    )


# ============================================================
# CREATE QUIZ
# ============================================================

@teacher.route("/create_quiz", methods=["GET", "POST"])
def create_quiz():

    if not teacher_logged_in():
        return redirect("/")

    # ========================================================
    # OPEN CREATE QUIZ PAGE
    # ========================================================

    if request.method == "GET":

        return render_template(
            "create_quiz.html"
        )

    # ========================================================
    # BASIC QUIZ DETAILS
    # ========================================================

    title = request.form.get(
        "title",
        ""
    ).strip()

    prompt = request.form.get(
        "prompt",
        ""
    ).strip()

    try:

        easy = int(
            request.form.get(
                "easy",
                0
            )
        )

        medium = int(
            request.form.get(
                "medium",
                0
            )
        )

        hard = int(
            request.form.get(
                "hard",
                0
            )
        )

        duration_minutes = int(
            request.form.get(
                "duration_minutes",
                30
            )
        )

        question_time_seconds = int(
            request.form.get(
                "question_time_seconds",
                60
            )
        )

    except (ValueError, TypeError):

        return """
        <h2>❌ Invalid quiz values.</h2>

        <a href="/create_quiz">
            ← Back to Create Quiz
        </a>
        """

    # ========================================================
    # VALIDATE TITLE
    # ========================================================

    if not title:

        return """
        <h2>❌ Quiz title is required.</h2>

        <a href="/create_quiz">
            ← Back to Create Quiz
        </a>
        """

    # ========================================================
    # VALIDATE PROMPT
    # ========================================================

    if not prompt:

        return """
        <h2>❌ Quiz topic/prompt is required.</h2>

        <a href="/create_quiz">
            ← Back to Create Quiz
        </a>
        """

    # ========================================================
    # VALIDATE QUESTION COUNTS
    # ========================================================

    if easy < 0 or medium < 0 or hard < 0:

        return """
        <h2>❌ Question counts cannot be negative.</h2>

        <a href="/create_quiz">
            ← Back to Create Quiz
        </a>
        """

    # ========================================================
    # TOTAL QUESTIONS
    # ========================================================

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

    # ========================================================
    # VALIDATE QUIZ DURATION
    # ========================================================

    if duration_minutes <= 0:

        return """
        <h2>❌ Quiz duration must be greater than 0.</h2>

        <a href="/create_quiz">
            ← Back to Create Quiz
        </a>
        """

    # ========================================================
    # VALIDATE QUESTION TIME
    # ========================================================

    if question_time_seconds <= 0:

        return """
        <h2>❌ Question time must be greater than 0.</h2>

        <a href="/create_quiz">
            ← Back to Create Quiz
        </a>
        """

    # ========================================================
    # QUIZ AVAILABILITY
    # ========================================================

    availability = request.form.get(
        "availability",
        "1_day"
    )

    available_from = datetime.now(
        timezone.utc
    )

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

    # ========================================================
    # DATABASE
    # ========================================================

    db = get_db_connection()

    cursor = db.cursor()

    try:

        # ====================================================
        # CREATE QUIZ
        # ====================================================

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

        quiz_id = cursor.fetchone()[0]

        print(
            f"📝 Quiz created with ID: {quiz_id}"
        )

        # ====================================================
        # AI QUESTIONS
        # ====================================================

        questions = generate_questions(
            prompt,
            easy,
            medium,
            hard
        )

        if not questions:

            raise Exception(
                "AI could not generate questions."
            )

        # ====================================================
        # SAVE QUESTIONS
        # ====================================================

        saved_questions = 0

        for q in questions:

            # -----------------------------------------------
            # Basic validation of AI response
            # -----------------------------------------------

            required_fields = [
                "question",
                "option_a",
                "option_b",
                "option_c",
                "option_d",
                "correct_option",
                "difficulty"
            ]

            if not all(
                field in q
                for field in required_fields
            ):
                print(
                    "⚠️ Skipping invalid AI question:",
                    q
                )
                continue

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

            saved_questions += 1

        # ====================================================
        # VALIDATE SAVED QUESTIONS
        # ====================================================

        if saved_questions == 0:

            raise Exception(
                "No valid questions were generated by AI."
            )

        # ====================================================
        # SYNC TOTAL QUESTION COUNT
        # ====================================================

        cursor.execute(
            """
            UPDATE quizzes

            SET total_questions=%s

            WHERE quiz_id=%s
            """,
            (
                saved_questions,
                quiz_id
            )
        )

        # ====================================================
        # GENERATE QR
        # ====================================================

        qr_path = generate_qr(
            quiz_id
        )

        print(
            f"📱 QR PATH: {qr_path}"
        )

        # ====================================================
        # SAVE QR PATH
        # ====================================================

        cursor.execute(
            """
            UPDATE quizzes

            SET qr_code_path=%s

            WHERE quiz_id=%s
            """,
            (
                qr_path,
                quiz_id
            )
        )

        # ====================================================
        # COMMIT
        # ====================================================

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

        # ====================================================
        # OPEN GENERATED QUIZ
        # ====================================================

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


# ============================================================
# GENERATED QUIZ
# ============================================================

@teacher.route("/quiz_generated/<int:quiz_id>")
def quiz_generated(quiz_id):

    if not teacher_logged_in():
        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        # ====================================================
        # QUIZ INFORMATION
        # ====================================================

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

            return "Quiz not found.", 404

        # ====================================================
        # QUESTIONS
        # ====================================================

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
            (
                quiz_id,
            )
        )

        questions = cursor.fetchall()

        # ====================================================
        # INITIAL LIVE PROGRESS
        # ====================================================
        #
        # IMPORTANT:
        #
        # guest participant:
        #     attempt_id -> guest:<id>
        #
        # logged-in participant:
        #     student_id -> student:<id>
        #
        # Prefix prevents ID collision.
        #
        # ====================================================

        cursor.execute(
            """
            SELECT
                COUNT(
                    DISTINCT
                    CASE

                        WHEN attempt_id IS NOT NULL
                        THEN 'attempt:' ||
                             attempt_id::text

                        WHEN student_id IS NOT NULL
                        THEN 'student:' ||
                             student_id::text

                    END
                ) AS total_students

            FROM student_answers

            WHERE quiz_id=%s
            """,
            (
                quiz_id,
            )
        )

        live_stats = cursor.fetchone()

    except Exception as e:

        print(
            "❌ QUIZ GENERATED ERROR:",
            e
        )

        return "Unable to load quiz.", 500

    finally:

        cursor.close()
        db.close()

    return render_template(
        "quiz_generated.html",

        quiz=quiz,

        questions=questions,

        live_total_students=(
            live_stats["total_students"] or 0
        )
    )


# ============================================================
# ADD QUESTION MANUALLY
# ============================================================

@teacher.route(
    "/add_questions/<int:quiz_id>",
    methods=["GET", "POST"]
)
def add_questions(quiz_id):

    if not teacher_logged_in():
        return redirect("/")

    # ========================================================
    # VERIFY QUIZ
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
                title

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

    finally:

        cursor.close()
        db.close()

    if not quiz:

        return "Quiz not found.", 404

    # ========================================================
    # ADD QUESTION
    # ========================================================

    if request.method == "POST":

        question = request.form.get(
            "question",
            ""
        ).strip()

        option_a = request.form.get(
            "option_a",
            ""
        ).strip()

        option_b = request.form.get(
            "option_b",
            ""
        ).strip()

        option_c = request.form.get(
            "option_c",
            ""
        ).strip()

        option_d = request.form.get(
            "option_d",
            ""
        ).strip()

        correct_option = request.form.get(
            "correct_option",
            ""
        ).strip().upper()

        difficulty = request.form.get(
            "difficulty",
            "Medium"
        ).strip()

        # ====================================================
        # VALIDATION
        # ====================================================

        if not all([
            question,
            option_a,
            option_b,
            option_c,
            option_d,
            correct_option
        ]):

            return """
            <h2>❌ All question fields are required.</h2>

            <a href="javascript:history.back()">
                ← Back
            </a>
            """

        if correct_option not in [
            "A",
            "B",
            "C",
            "D"
        ]:

            return """
            <h2>❌ Correct option must be A, B, C or D.</h2>

            <a href="javascript:history.back()">
                ← Back
            </a>
            """

        if difficulty.lower() not in [
            "easy",
            "medium",
            "hard"
        ]:

            difficulty = "Medium"

        db = get_db_connection()

        cursor = db.cursor()

        try:

            # =================================================
            # INSERT QUESTION
            # =================================================

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

            # =================================================
            # UPDATE TOTAL QUESTION COUNT
            # =================================================

            cursor.execute(
                """
                UPDATE quizzes

                SET total_questions = (
                    SELECT COUNT(*)
                    FROM questions
                    WHERE quiz_id=%s
                )

                WHERE quiz_id=%s
                """,
                (
                    quiz_id,
                    quiz_id
                )
            )

            db.commit()

            print(
                f"✅ Question added to quiz {quiz_id}"
            )

        except Exception as e:

            db.rollback()

            print(
                "❌ ADD QUESTION ERROR:",
                e
            )

            return f"""
            <h2>❌ Error adding question</h2>

            <p>{e}</p>

            <a href="/quiz_generated/{quiz_id}">
                ← Back
            </a>
            """

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


# ============================================================
# VIEW RESULTS
# ============================================================

@teacher.route("/view_results")
def view_results():

    if not teacher_logged_in():
        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

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

    finally:

        cursor.close()
        db.close()

    return render_template(
        "view_results.html",
        results=results
    )


# ============================================================
# SHOW QR
# ============================================================

@teacher.route("/show_qr/<int:quiz_id>")
def show_qr(quiz_id):

    if not teacher_logged_in():
        return redirect("/")

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

    finally:

        cursor.close()
        db.close()

    if not quiz:

        return "Quiz not found.", 404

    return render_template(
        "show_qr.html",
        quiz=quiz
    )


# ============================================================
# QR PAGE
# ============================================================

@teacher.route("/generate_qr_page")
def generate_qr_page():

    if not teacher_logged_in():
        return redirect("/")

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

    finally:

        cursor.close()
        db.close()

    return render_template(
        "generate_qr_page.html",
        quizzes=quizzes
    )


# ============================================================
# MANAGE QUIZZES
# ============================================================

@teacher.route("/manage_quizzes")
def manage_quizzes():

    if not teacher_logged_in():
        return redirect("/")

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

    finally:

        cursor.close()
        db.close()

    return render_template(
        "manage_quizzes.html",
        quizzes=quizzes
    )


# ============================================================
# DELETE QUIZ
# ============================================================

@teacher.route("/delete_quiz/<int:quiz_id>")
def delete_quiz(quiz_id):

    if not teacher_logged_in():
        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor()

    try:

        # ====================================================
        # VERIFY OWNERSHIP
        # ====================================================

        cursor.execute(
            """
            SELECT
                quiz_id

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

            return redirect(
                "/manage_quizzes"
            )

        # ====================================================
        # DELETE STUDENT ANSWERS
        # ====================================================

        cursor.execute(
            """
            DELETE FROM student_answers

            WHERE quiz_id=%s
            """,
            (
                quiz_id,
            )
        )

        # ====================================================
        # DELETE QUIZ ATTEMPTS
        # ====================================================

        cursor.execute(
            """
            DELETE FROM quiz_attempts

            WHERE quiz_id=%s
            """,
            (
                quiz_id,
            )
        )

        # ====================================================
        # DELETE RESULTS
        # ====================================================

        cursor.execute(
            """
            DELETE FROM results

            WHERE quiz_id=%s
            """,
            (
                quiz_id,
            )
        )

        # ====================================================
        # DELETE QUESTIONS
        # ====================================================

        cursor.execute(
            """
            DELETE FROM questions

            WHERE quiz_id=%s
            """,
            (
                quiz_id,
            )
        )

        # ====================================================
        # DELETE QUIZ
        # ====================================================

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

        print(
            f"🗑️ Quiz {quiz_id} deleted."
        )

    except Exception as e:

        db.rollback()

        print(
            "❌ DELETE QUIZ ERROR:",
            e
        )

    finally:

        cursor.close()
        db.close()

    return redirect(
        "/manage_quizzes"
    )


# ============================================================
# TEST AI
# ============================================================

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


# ============================================================
# LIVE QUIZ PROGRESS
# ============================================================
#
# This API is called by quiz_generated.html every 2 seconds.
#
# Example:
#
# /quiz_progress/34
#
# It returns:
#
# - total participants
# - total questions
# - answered questions
# - overall progress
# - question-wise response count
#
# ============================================================

@teacher.route("/quiz_progress/<int:quiz_id>")
def quiz_progress(quiz_id):

    # ========================================================
    # TEACHER LOGIN CHECK
    # ========================================================

    if not teacher_logged_in():

        return {
            "success": False,
            "error": "Unauthorized"
        }, 401

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        # ====================================================
        # VERIFY QUIZ BELONGS TO TEACHER
        # ====================================================

        cursor.execute(
            """
            SELECT
                quiz_id,
                title,
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
                "success": False,
                "error": "Quiz not found"
            }, 404

        # ====================================================
        # TOTAL QUESTIONS
        # ====================================================

        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_questions

            FROM questions

            WHERE quiz_id=%s
            """,
            (
                quiz_id,
            )
        )

        question_count = cursor.fetchone()

        total_questions = int(
            question_count["total_questions"] or 0
        )

        # ====================================================
        # TOTAL UNIQUE PARTICIPANTS
        # ====================================================
        #
        # IMPORTANT:
        #
        # Guest:
        #     attempt_id
        #
        # Logged-in:
        #     student_id
        #
        # Prefixes avoid collision.
        #
        # Example:
        #
        # attempt:1
        # student:1
        #
        # These are treated as two different users.
        #
        # ====================================================

        cursor.execute(
            """
            SELECT
                COUNT(
                    DISTINCT
                    CASE

                        WHEN attempt_id IS NOT NULL
                        THEN 'attempt:' ||
                             attempt_id::text

                        WHEN student_id IS NOT NULL
                        THEN 'student:' ||
                             student_id::text

                    END
                ) AS total_students

            FROM student_answers

            WHERE quiz_id=%s
            """,
            (
                quiz_id,
            )
        )

        total = cursor.fetchone()

        total_students = int(
            total["total_students"] or 0
        )

        # ====================================================
        # QUESTION-WISE PROGRESS
        # ====================================================

        cursor.execute(
            """
            SELECT

                q.question_id,

                q.question,

                COUNT(
                    DISTINCT
                    CASE

                        WHEN sa.attempt_id IS NOT NULL
                        THEN 'attempt:' ||
                             sa.attempt_id::text

                        WHEN sa.student_id IS NOT NULL
                        THEN 'student:' ||
                             sa.student_id::text

                    END
                ) AS response_count

            FROM questions q

            LEFT JOIN student_answers sa

                ON sa.question_id =
                   q.question_id

                AND sa.quiz_id =
                    q.quiz_id

                AND
                (
                    sa.attempt_id IS NOT NULL
                    OR
                    sa.student_id IS NOT NULL
                )

            WHERE q.quiz_id=%s

            GROUP BY
                q.question_id,
                q.question

            ORDER BY
                q.question_id
            """,
            (
                quiz_id,
            )
        )

        progress = cursor.fetchall()

        # ====================================================
        # BUILD RESPONSE
        # ====================================================

        question_progress = []

        total_answer_events = 0

        for item in progress:

            response_count = int(
                item["response_count"] or 0
            )

            total_answer_events += (
                response_count
            )

            # -----------------------------------------------
            # QUESTION PERCENTAGE
            # -----------------------------------------------

            if total_students > 0:

                percentage = round(
                    (
                        response_count /
                        total_students
                    ) * 100,
                    1
                )

            else:

                percentage = 0

            question_progress.append(
                {
                    "question_id": int(
                        item["question_id"]
                    ),

                    "question": item[
                        "question"
                    ],

                    "response_count": (
                        response_count
                    ),

                    "percentage": (
                        percentage
                    )
                }
            )

        # ====================================================
        # QUESTIONS WITH AT LEAST ONE RESPONSE
        # ====================================================

        answered_questions = sum(
            1
            for item in question_progress
            if item["response_count"] > 0
        )

        # ====================================================
        # OVERALL PROGRESS
        # ====================================================

        if total_questions > 0:

            overall_progress = round(
                (
                    answered_questions /
                    total_questions
                ) * 100,
                1
            )

        else:

            overall_progress = 0

        # ====================================================
        # RETURN JSON
        # ====================================================

        return {
            "success": True,

            "quiz_id": int(
                quiz_id
            ),

            "title": quiz[
                "title"
            ],

            "total_questions": (
                total_questions
            ),

            "total_students": (
                total_students
            ),

            "answered_questions": (
                answered_questions
            ),

            "overall_progress": (
                overall_progress
            ),

            "total_answer_events": (
                total_answer_events
            ),

            "progress": (
                question_progress
            )
        }

    except Exception as e:

        print(
            "❌ LIVE PROGRESS ERROR:",
            e
        )

        return {
            "success": False,
            "error": str(e)
        }, 500

    finally:

        cursor.close()
        db.close()