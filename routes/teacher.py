from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    session,
    jsonify
)

from database import get_db_connection

from utils.qr_generator import generate_qr
from utils.ai_generator import generate_questions

from psycopg2.extras import RealDictCursor

from datetime import datetime, timedelta, timezone


teacher = Blueprint("teacher", __name__)


# ============================================================
# HELPER: TEACHER LOGIN CHECK
# ============================================================

def teacher_logged_in():

    return "teacher_id" in session


# ============================================================
# HELPER: ERROR PAGE
# ============================================================

def error_page(message, back_url="/create_quiz"):

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Quiz Error</title>

        <style>
            body {{
                margin: 0;
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                background: #f5f7fb;
                font-family: Inter, Arial, sans-serif;
            }}

            .error-card {{
                width: min(600px, 90%);
                background: white;
                border-radius: 20px;
                padding: 35px;
                box-shadow: 0 15px 40px rgba(0,0,0,0.08);
                border: 1px solid #e8ebf2;
            }}

            h2 {{
                margin-top: 0;
                color: #dc2626;
            }}

            p {{
                color: #596274;
                line-height: 1.6;
                word-break: break-word;
            }}

            a {{
                display: inline-block;
                margin-top: 15px;
                padding: 12px 18px;
                background: #4f46e5;
                color: white;
                text-decoration: none;
                border-radius: 10px;
                font-weight: 700;
            }}
        </style>
    </head>

    <body>

        <div class="error-card">

            <h2>❌ Quiz Generation Failed</h2>

            <p>
                {message}
            </p>

            <a href="{back_url}">
                ← Go Back
            </a>

        </div>

    </body>
    </html>
    """


# ============================================================
# TEACHER DASHBOARD
# ============================================================

# ============================================================
# TEACHER DASHBOARD
# ============================================================

@teacher.route("/teacher_dashboard")
def teacher_dashboard():

    if not teacher_logged_in():
        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        teacher_id = session["teacher_id"]

        # ====================================================
        # TOTAL QUIZZES
        # ====================================================

        cursor.execute(
            """
            SELECT COUNT(*) AS total_quizzes

            FROM quizzes

            WHERE teacher_id=%s
            """,
            (teacher_id,)
        )

        total_quizzes = cursor.fetchone()

        total_quizzes = int(
            total_quizzes["total_quizzes"] or 0
        )

        # ====================================================
        # TOTAL PARTICIPANTS
        # ====================================================

        cursor.execute(
            """
            SELECT COUNT(*) AS total_participants

            FROM quiz_attempts qa

            INNER JOIN quizzes q
                ON qa.quiz_id = q.quiz_id

            WHERE q.teacher_id=%s
            """,
            (teacher_id,)
        )

        total_participants = cursor.fetchone()

        total_participants = int(
            total_participants["total_participants"] or 0
        )

        # ====================================================
        # AVERAGE SCORE
        # ====================================================

        cursor.execute(
            """
            SELECT
                AVG(r.percentage) AS average_score

            FROM results r

            INNER JOIN quizzes q
                ON r.quiz_id = q.quiz_id

            WHERE q.teacher_id=%s
            """,
            (teacher_id,)
        )

        average_score = cursor.fetchone()

        average_score = round(
            float(
                average_score["average_score"] or 0
            ),
            1
        )

        # ====================================================
        # RECENT QUIZZES
        # ====================================================

        cursor.execute(
            """
            SELECT

                q.quiz_id,
                q.title,
                q.total_questions,
                q.duration_minutes,
                q.question_time_seconds,
                q.available_from,
                q.available_until,
                q.created_at,

                COUNT(
                    DISTINCT
                    qa.attempt_id
                ) AS participants

            FROM quizzes q

            LEFT JOIN quiz_attempts qa
                ON qa.quiz_id = q.quiz_id

            WHERE q.teacher_id=%s

            GROUP BY

                q.quiz_id,
                q.title,
                q.total_questions,
                q.duration_minutes,
                q.question_time_seconds,
                q.available_from,
                q.available_until,
                q.created_at

            ORDER BY q.created_at DESC

            LIMIT 6
            """,
            (teacher_id,)
        )

        quizzes = cursor.fetchall()

        # ====================================================
        # ACTIVE / LIVE QUIZZES
        # ====================================================

        cursor.execute(
            """
            SELECT

                q.quiz_id,
                q.title,
                q.total_questions,
                q.duration_minutes,
                q.available_from,
                q.available_until

            FROM quizzes q

            WHERE q.teacher_id=%s

            AND q.available_from <= NOW()

            AND
            (
                q.available_until IS NULL
                OR q.available_until > NOW()
            )

            ORDER BY q.created_at DESC
            """,
            (teacher_id,)
        )

        live_quizzes = cursor.fetchall()

    except Exception as e:

        print(
            "❌ TEACHER DASHBOARD ERROR:",
            e
        )

        return f"""
        <h2>Dashboard Error</h2>
        <p>{e}</p>
        """

    finally:

        cursor.close()
        db.close()

    return render_template(
        "teacher_dashboard.html",

        name=session.get(
            "name",
            "Teacher"
        ),

        total_quizzes=total_quizzes,

        total_participants=total_participants,

        average_score=average_score,

        quizzes=quizzes,

        live_quizzes=live_quizzes
    )

# ============================================================
# CREATE QUIZ
# ============================================================

@teacher.route("/create_quiz", methods=["GET", "POST"])
def create_quiz():

    # --------------------------------------------------------
    # LOGIN CHECK
    # --------------------------------------------------------

    if not teacher_logged_in():
        return redirect("/")

    # --------------------------------------------------------
    # GET REQUEST
    # --------------------------------------------------------

    if request.method == "GET":

        return render_template(
            "create_quiz.html"
        )

    print("\n")
    print("=" * 70)
    print("🚀 CREATE QUIZ REQUEST RECEIVED")
    print("=" * 70)

    # ========================================================
    # BASIC DETAILS
    # ========================================================

    title = request.form.get(
        "title",
        ""
    ).strip()

    prompt = request.form.get(
        "prompt",
        ""
    ).strip()

    # ========================================================
    # GET QUESTION COUNTS
    # ========================================================

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

    except (ValueError, TypeError) as e:

        print(
            "❌ INVALID FORM VALUES:",
            e
        )

        return error_page(
            "Invalid quiz values. Please enter valid numbers."
        )

    # ========================================================
    # PRINT RECEIVED DATA
    # ========================================================

    print("\n📋 QUIZ FORM DATA")
    print("-" * 70)
    print("Title:", title)
    print("Prompt:", prompt)
    print("Easy:", easy)
    print("Medium:", medium)
    print("Hard:", hard)
    print("Duration:", duration_minutes)
    print(
        "Time / Question:",
        question_time_seconds
    )
    print("-" * 70)

    # ========================================================
    # VALIDATE TITLE
    # ========================================================

    if not title:

        print("❌ Quiz title missing")

        return error_page(
            "Quiz title is required."
        )

    # ========================================================
    # VALIDATE PROMPT
    # ========================================================

    if not prompt:

        print("❌ Quiz prompt missing")

        return error_page(
            "Quiz topic/prompt is required."
        )

    # ========================================================
    # VALIDATE COUNTS
    # ========================================================

    if easy < 0 or medium < 0 or hard < 0:

        return error_page(
            "Question counts cannot be negative."
        )

    # ========================================================
    # TOTAL QUESTIONS
    # ========================================================

    total_questions = (
        easy +
        medium +
        hard
    )

    print(
        "📝 Total requested questions:",
        total_questions
    )

    if total_questions <= 0:

        return error_page(
            "Please select at least one question."
        )

    # ========================================================
    # VALIDATE DURATION
    # ========================================================

    if duration_minutes <= 0:

        return error_page(
            "Quiz duration must be greater than 0."
        )

    # ========================================================
    # VALIDATE QUESTION TIME
    # ========================================================

    if question_time_seconds <= 0:

        return error_page(
            "Question time must be greater than 0."
        )

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

    print("\n📅 AVAILABILITY")
    print("-" * 70)
    print("Available From:", available_from)
    print("Available Until:", available_until)
    print("-" * 70)

    # ========================================================
    # AI GENERATION
    # ========================================================
    #
    # IMPORTANT:
    # AI GENERATION IS DONE BEFORE INSERTING QUIZ.
    #
    # This prevents empty/broken quizzes if AI fails.
    #
    # ========================================================

    print("\n")
    print("=" * 70)
    print("🤖 STARTING AI QUESTION GENERATION")
    print("=" * 70)

    print("📌 Prompt:", prompt)
    print("📌 Easy:", easy)
    print("📌 Medium:", medium)
    print("📌 Hard:", hard)

    try:

        ai_start = datetime.now()

        questions = generate_questions(
            prompt,
            easy,
            medium,
            hard
        )

        ai_end = datetime.now()

        ai_seconds = (
            ai_end - ai_start
        ).total_seconds()

        print("\n")
        print("=" * 70)
        print("🤖 AI GENERATION FINISHED")
        print("=" * 70)

        print(
            "⏱️ AI generation time:",
            ai_seconds,
            "seconds"
        )

        print(
            "📦 Returned object type:",
            type(questions)
        )

        print(
            "📦 Returned questions:",
            len(questions)
            if questions
            else 0
        )

        # ----------------------------------------------------
        # CHECK EMPTY
        # ----------------------------------------------------

        if not questions:

            print(
                "❌ AI returned EMPTY questions"
            )

            return error_page(
                "AI could not generate any questions. "
                "Please check your AI/Ollama configuration "
                "and try again."
            )

    except Exception as e:

        print("\n")
        print("=" * 70)
        print("❌ AI GENERATION ERROR")
        print("=" * 70)
        print(
            "ERROR TYPE:",
            type(e).__name__
        )
        print(
            "ERROR:",
            str(e)
        )
        print("=" * 70)

        return error_page(
            f"AI question generation failed: {str(e)}"
        )

    # ========================================================
    # VALIDATE AI QUESTION COUNT
    # ========================================================

    if len(questions) != total_questions:

        print("\n")
        print("⚠️ QUESTION COUNT MISMATCH")
        print(
            "Requested:",
            total_questions
        )
        print(
            "Generated:",
            len(questions)
        )

        return error_page(
            f"AI generated {len(questions)} questions, "
            f"but {total_questions} were requested."
        )

    # ========================================================
    # VALIDATE QUESTION STRUCTURE
    # ========================================================

    required_fields = [
        "question",
        "option_a",
        "option_b",
        "option_c",
        "option_d",
        "correct_option",
        "difficulty"
    ]

    print("\n")
    print("🔍 VALIDATING GENERATED QUESTIONS")
    print("-" * 70)

    for index, q in enumerate(questions, start=1):

        print(
            f"Question {index}:",
            q
        )

        if not isinstance(q, dict):

            return error_page(
                f"AI returned invalid data for question {index}."
            )

        for field in required_fields:

            if field not in q:

                print(
                    f"❌ Missing field '{field}' "
                    f"in question {index}"
                )

                return error_page(
                    f"AI generated an invalid question. "
                    f"Missing field: {field}"
                )

            if q[field] is None:

                return error_page(
                    f"AI generated an empty field "
                    f"'{field}' in question {index}."
                )

            if str(q[field]).strip() == "":

                return error_page(
                    f"AI generated an empty field "
                    f"'{field}' in question {index}."
                )

    print("-" * 70)
    print("✅ ALL AI QUESTIONS VALID")

    # ========================================================
    # NORMALIZE DIFFICULTY
    # ========================================================

    for q in questions:

        difficulty = str(
            q.get(
                "difficulty",
                "Medium"
            )
        ).strip().lower()

        if difficulty == "easy":

            q["difficulty"] = "Easy"

        elif difficulty == "medium":

            q["difficulty"] = "Medium"

        elif difficulty == "hard":

            q["difficulty"] = "Hard"

        else:

            return error_page(
                "AI returned an invalid difficulty value."
            )

    # ========================================================
    # VERIFY DIFFICULTY DISTRIBUTION
    # ========================================================

    generated_easy = sum(
        1
        for q in questions
        if q["difficulty"] == "Easy"
    )

    generated_medium = sum(
        1
        for q in questions
        if q["difficulty"] == "Medium"
    )

    generated_hard = sum(
        1
        for q in questions
        if q["difficulty"] == "Hard"
    )

    print("\n")
    print("📊 DIFFICULTY CHECK")
    print("-" * 70)
    print(
        "Requested:",
        f"Easy={easy}, Medium={medium}, Hard={hard}"
    )
    print(
        "Generated:",
        f"Easy={generated_easy}, "
        f"Medium={generated_medium}, "
        f"Hard={generated_hard}"
    )
    print("-" * 70)

    if generated_easy != easy:

        return error_page(
            f"AI generated {generated_easy} Easy questions "
            f"but {easy} were requested."
        )

    if generated_medium != medium:

        return error_page(
            f"AI generated {generated_medium} Medium questions "
            f"but {medium} were requested."
        )

    if generated_hard != hard:

        return error_page(
            f"AI generated {generated_hard} Hard questions "
            f"but {hard} were requested."
        )

    print("✅ DIFFICULTY DISTRIBUTION VALID")

    # ========================================================
    # DATABASE
    # ========================================================

    db = None
    cursor = None

    try:

        print("\n")
        print("=" * 70)
        print("🗄️ CONNECTING TO DATABASE")
        print("=" * 70)

        db = get_db_connection()

        cursor = db.cursor()

        print("✅ Database connected")

        # ====================================================
        # CREATE QUIZ
        # ====================================================

        print("\n📝 INSERTING QUIZ INTO DATABASE...")

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

        result = cursor.fetchone()

        if not result:

            raise Exception(
                "Database did not return quiz_id."
            )

        quiz_id = result[0]

        print(
            "✅ Quiz created with ID:",
            quiz_id
        )

        # ====================================================
        # SAVE QUESTIONS
        # ====================================================

        print("\n📝 SAVING AI QUESTIONS...")

        for index, q in enumerate(
            questions,
            start=1
        ):

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
                    str(q["question"]).strip(),
                    str(q["option_a"]).strip(),
                    str(q["option_b"]).strip(),
                    str(q["option_c"]).strip(),
                    str(q["option_d"]).strip(),
                    str(q["correct_option"]).strip(),
                    q["difficulty"]
                )
            )

            print(
                f"   ✅ Question {index} saved"
            )

        # ====================================================
        # GENERATE QR
        # ====================================================

        print("\n📱 GENERATING QR CODE...")

        qr_path = generate_qr(
            quiz_id
        )

        print(
            "✅ QR generated:",
            qr_path
        )

        if not qr_path:

            raise Exception(
                "QR code generation failed."
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

        print(
            "✅ QR path saved"
        )

        # ====================================================
        # COMMIT EVERYTHING
        # ====================================================

        print("\n💾 COMMITTING DATABASE...")

        db.commit()

        print(
            "✅ DATABASE COMMIT SUCCESSFUL"
        )

        print("\n")
        print("=" * 70)
        print(
            f"🎉 QUIZ {quiz_id} CREATED SUCCESSFULLY"
        )
        print("=" * 70)
        print("\n")

        # ====================================================
        # REDIRECT
        # ====================================================

        return redirect(
            f"/quiz_generated/{quiz_id}"
        )

    except Exception as e:

        print("\n")
        print("=" * 70)
        print("❌ DATABASE / QUIZ CREATION ERROR")
        print("=" * 70)
        print(
            "ERROR TYPE:",
            type(e).__name__
        )
        print(
            "ERROR:",
            str(e)
        )
        print("=" * 70)

        if db:

            try:
                db.rollback()

                print(
                    "↩️ Database rollback successful"
                )

            except Exception as rollback_error:

                print(
                    "❌ Rollback error:",
                    rollback_error
                )

        return error_page(
            f"Quiz creation failed: {str(e)}"
        )

    finally:

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        if db:

            try:
                db.close()
            except Exception:
                pass


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
        # LIVE STUDENT COUNT
        # ====================================================

        cursor.execute(
            """
            SELECT
                COUNT(
                    DISTINCT
                    COALESCE(
                        attempt_id::text,
                        student_id::text
                    )
                ) AS total_students

            FROM student_answers

            WHERE quiz_id=%s

            AND
            (
                attempt_id IS NOT NULL
                OR
                student_id IS NOT NULL
            )
            """,
            (
                quiz_id,
            )
        )

        live_stats = cursor.fetchone()

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

    finally:

        cursor.close()
        db.close()

    if not quiz:

        return "Quiz not found.", 404

    # ========================================================
    # POST
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
        ).strip()

        difficulty = request.form.get(
            "difficulty",
            "Medium"
        ).strip()

        # ====================================================
        # VALIDATION
        # ====================================================

        if not question:

            return error_page(
                "Question is required.",
                f"/add_questions/{quiz_id}"
            )

        if not option_a or not option_b \
                or not option_c or not option_d:

            return error_page(
                "All four options are required.",
                f"/add_questions/{quiz_id}"
            )

        if correct_option not in [
            "A",
            "B",
            "C",
            "D"
        ]:

            return error_page(
                "Correct option must be A, B, C or D.",
                f"/add_questions/{quiz_id}"
            )

        # ====================================================
        # DATABASE
        # ====================================================

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

            # =================================================
            # UPDATE TOTAL QUESTIONS
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

        except Exception as e:

            db.rollback()

            print(
                "❌ ADD QUESTION ERROR:",
                e
            )

            return error_page(
                f"Error adding question: {e}",
                f"/quiz_generated/{quiz_id}"
            )

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

                r.*,

                q.title AS quiz_title,

                s.full_name AS student_name,

                s.roll_number AS student_roll_number

            FROM results r

            INNER JOIN quizzes q
                ON r.quiz_id = q.quiz_id

            LEFT JOIN students s
                ON r.student_id = s.student_id

            WHERE q.teacher_id = %s

            ORDER BY r.submitted_at DESC
            """,
            (
                session["teacher_id"],
            )
        )

        results = cursor.fetchall()

        print(
            "✅ RESULTS FOUND:",
            len(results)
        )

        for result in results:
            print(
                "RESULT:",
                dict(result)
            )

    except Exception as e:

        print(
            "❌ VIEW RESULTS ERROR:",
            e
        )

        return f"""
        <h2>View Results Error</h2>
        <p>{e}</p>
        """

    finally:

        cursor.close()
        db.close()

    return render_template(
        "view_results.html",
        results=results
    )

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
        # DELETE ATTEMPTS
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

    print("\n")
    print("=" * 70)
    print("🧪 TEST AI ROUTE")
    print("=" * 70)

    try:

        start = datetime.now()

        questions = generate_questions(
            "Database Management System",
            1,
            1,
            1
        )

        end = datetime.now()

        print(
            "⏱️ AI time:",
            (end - start).total_seconds(),
            "seconds"
        )

        print(
            "Questions:",
            questions
        )

        return {
            "status": "success",
            "count": len(questions),
            "questions": questions
        }

    except Exception as e:

        print(
            "❌ TEST AI ERROR:",
            e
        )

        return {
            "status": "error",
            "error_type": type(e).__name__,
            "error": str(e)
        }, 500

# ============================================================
# TEACHER QUIZ LIST - FOR LIVE PROGRESS SELECTOR
# ============================================================

@teacher.route("/teacher_quizzes")
def teacher_quizzes():

    if not teacher_logged_in():

        return jsonify({
            "success": False,
            "error": "Unauthorized"
        }), 401


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


        return jsonify({

            "success": True,

            "quizzes": quizzes

        })


    except Exception as e:

        print(
            "❌ TEACHER QUIZ LIST ERROR:",
            e
        )


        return jsonify({

            "success": False,

            "error": str(e)

        }), 500


    finally:

        cursor.close()

        db.close()
        
# ============================================================
# LIVE QUIZ PROGRESS
# ============================================================

@teacher.route("/quiz_progress/<int:quiz_id>")
def quiz_progress(quiz_id):

    # ========================================================
    # LOGIN
    # ========================================================

    if not teacher_logged_in():

        return jsonify({
            "success": False,
            "error": "Unauthorized"
        }), 401

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        # ====================================================
        # VERIFY QUIZ
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

            return jsonify({
                "success": False,
                "error": "Quiz not found"
            }), 404

        # ====================================================
        # ACTUAL QUESTION COUNT
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

        cursor.execute(
            """
            SELECT
                COUNT(
                    DISTINCT
                    COALESCE(
                        attempt_id::text,
                        student_id::text
                    )
                ) AS total_students

            FROM student_answers

            WHERE quiz_id=%s

            AND
            (
                attempt_id IS NOT NULL
                OR
                student_id IS NOT NULL
            )
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
        # QUESTION PROGRESS
        # ====================================================

        cursor.execute(
            """
            SELECT

                q.question_id,

                q.question,

                COUNT(
                    DISTINCT
                    COALESCE(
                        sa.attempt_id::text,
                        sa.student_id::text
                    )
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
        # FORMAT DATA
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
                    "question_id": item[
                        "question_id"
                    ],

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
        # ANSWERED QUESTIONS
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
        # RESPONSE
        # ====================================================

        return jsonify({

            "success": True,

            "quiz_id": quiz_id,

            "title": quiz["title"],

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
        })

    except Exception as e:

        print(
            "❌ LIVE PROGRESS ERROR:",
            e
        )

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

    finally:

        cursor.close()
        db.close()