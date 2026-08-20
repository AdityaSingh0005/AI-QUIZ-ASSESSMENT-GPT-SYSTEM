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

def error_page(
    message,
    back_url="/create_quiz"
):

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

                font-family:
                    Inter,
                    Arial,
                    sans-serif;
            }}

            .error-card {{
                width: min(600px, 90%);

                background: white;

                border-radius: 20px;

                padding: 35px;

                box-shadow:
                    0 15px 40px
                    rgba(0,0,0,0.08);

                border:
                    1px solid #e8ebf2;
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

                padding:
                    12px 18px;

                background:
                    #4f46e5;

                color: white;

                text-decoration: none;

                border-radius: 10px;

                font-weight: 700;
            }}

        </style>

    </head>

    <body>

        <div class="error-card">

            <h2>
                ❌ Quiz Error
            </h2>

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

@teacher.route("/teacher_dashboard")
def teacher_dashboard():

    if not teacher_logged_in():

        return redirect("/")

    db = None
    cursor = None

    try:

        db = get_db_connection()

        cursor = db.cursor(
            cursor_factory=RealDictCursor
        )

        teacher_id = session["teacher_id"]

        # ====================================================
        # TOTAL QUIZZES
        # ====================================================

        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_quizzes

            FROM quizzes

            WHERE teacher_id=%s
            """,
            (
                teacher_id,
            )
        )

        row = cursor.fetchone()

        total_quizzes = int(
            row["total_quizzes"] or 0
        )

        # ====================================================
        # TOTAL PARTICIPANTS
        # ====================================================

        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_participants

            FROM quiz_attempts qa

            INNER JOIN quizzes q
                ON qa.quiz_id=q.quiz_id

            WHERE q.teacher_id=%s
            """,
            (
                teacher_id,
            )
        )

        row = cursor.fetchone()

        total_participants = int(
            row["total_participants"] or 0
        )

        # ====================================================
        # AVERAGE SCORE
        #
        # Registered + guest attempts
        # ====================================================

        cursor.execute(
            """
            SELECT
                AVG(x.percentage) AS average_score

            FROM
            (
                SELECT
                    r.percentage

                FROM results r

                INNER JOIN quizzes q
                    ON r.quiz_id=q.quiz_id

                WHERE q.teacher_id=%s

                UNION ALL

                SELECT
                    qa.percentage

                FROM quiz_attempts qa

                INNER JOIN quizzes q
                    ON qa.quiz_id=q.quiz_id

                WHERE q.teacher_id=%s

                AND qa.student_id IS NULL

                AND qa.status='submitted'

                AND qa.percentage IS NOT NULL
            ) x
            """,
            (
                teacher_id,
                teacher_id
            )
        )

        row = cursor.fetchone()

        average_score = round(
            float(
                row["average_score"] or 0
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
                    DISTINCT qa.attempt_id
                ) AS participants

            FROM quizzes q

            LEFT JOIN quiz_attempts qa
                ON qa.quiz_id=q.quiz_id

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
            (
                teacher_id,
            )
        )

        quizzes = cursor.fetchall()

        # ====================================================
        # ACTIVE QUIZZES
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

                OR

                q.available_until > NOW()
            )

            ORDER BY q.created_at DESC
            """,
            (
                teacher_id,
            )
        )

        live_quizzes = cursor.fetchall()

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

    except Exception as e:

        print(
            "❌ TEACHER DASHBOARD ERROR:",
            e
        )

        return f"""
        <h2>Dashboard Error</h2>
        <p>{str(e)}</p>
        """

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
# CREATE QUIZ
# ============================================================

@teacher.route(
    "/create_quiz",
    methods=["GET", "POST"]
)
def create_quiz():

    if not teacher_logged_in():

        return redirect("/")

    # ========================================================
    # GET
    # ========================================================

    if request.method == "GET":

        return render_template(
            "create_quiz.html"
        )

    print("\n")
    print("=" * 70)
    print("🚀 CREATE QUIZ REQUEST")
    print("=" * 70)

    # ========================================================
    # BASIC DATA
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
    # COUNTS
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

    except (
        ValueError,
        TypeError
    ) as e:

        print(
            "❌ FORM VALUE ERROR:",
            e
        )

        return error_page(
            "Invalid quiz values."
        )

    # ========================================================
    # VALIDATION
    # ========================================================

    if not title:

        return error_page(
            "Quiz title is required."
        )

    if not prompt:

        return error_page(
            "Quiz topic/prompt is required."
        )

    if easy < 0 or medium < 0 or hard < 0:

        return error_page(
            "Question counts cannot be negative."
        )

    total_questions = (
        easy +
        medium +
        hard
    )

    if total_questions <= 0:

        return error_page(
            "Please select at least one question."
        )

    if duration_minutes <= 0:

        return error_page(
            "Quiz duration must be greater than 0."
        )

    if question_time_seconds <= 0:

        return error_page(
            "Question time must be greater than 0."
        )

    # ========================================================
    # AVAILABILITY
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
    # AI GENERATION
    # ========================================================

    print(
        "🤖 Generating questions..."
    )

    try:

        start_time = datetime.now()

        questions = generate_questions(
            prompt,
            easy,
            medium,
            hard
        )

        end_time = datetime.now()

        print(
            "⏱️ AI time:",
            (
                end_time -
                start_time
            ).total_seconds(),
            "seconds"
        )

        print(
            "📦 Questions generated:",
            len(questions)
            if questions
            else 0
        )

    except Exception as e:

        print(
            "❌ AI GENERATION ERROR:",
            e
        )

        return error_page(
            f"AI question generation failed: {str(e)}"
        )

    # ========================================================
    # EMPTY CHECK
    # ========================================================

    if not questions:

        return error_page(
            "AI could not generate questions."
        )

    # ========================================================
    # COUNT CHECK
    # ========================================================

    if len(questions) != total_questions:

        return error_page(
            f"AI generated {len(questions)} questions "
            f"but {total_questions} were requested."
        )

    # ========================================================
    # VALIDATE QUESTIONS
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

    for index, q in enumerate(
        questions,
        start=1
    ):

        if not isinstance(q, dict):

            return error_page(
                f"Invalid question {index}."
            )

        for field in required_fields:

            if field not in q:

                return error_page(
                    f"Question {index} is missing "
                    f"field: {field}"
                )

            if q[field] is None:

                return error_page(
                    f"Question {index} has empty "
                    f"field: {field}"
                )

            if str(
                q[field]
            ).strip() == "":

                return error_page(
                    f"Question {index} has empty "
                    f"field: {field}"
                )

    # ========================================================
    # NORMALIZE
    # ========================================================

    for q in questions:

        difficulty = str(
            q["difficulty"]
        ).strip().lower()

        if difficulty == "easy":

            q["difficulty"] = "Easy"

        elif difficulty == "medium":

            q["difficulty"] = "Medium"

        elif difficulty == "hard":

            q["difficulty"] = "Hard"

        else:

            return error_page(
                "AI returned invalid difficulty."
            )

        q["correct_option"] = str(
            q["correct_option"]
        ).strip().upper()

        if q["correct_option"] not in [
            "A",
            "B",
            "C",
            "D"
        ]:

            return error_page(
                "AI returned invalid correct option."
            )

    # ========================================================
    # DIFFICULTY CHECK
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

    if generated_easy != easy:

        return error_page(
            f"AI generated {generated_easy} Easy "
            f"questions instead of {easy}."
        )

    if generated_medium != medium:

        return error_page(
            f"AI generated {generated_medium} Medium "
            f"questions instead of {medium}."
        )

    if generated_hard != hard:

        return error_page(
            f"AI generated {generated_hard} Hard "
            f"questions instead of {hard}."
        )

    # ========================================================
    # DATABASE
    # ========================================================

    db = None
    cursor = None

    try:

        db = get_db_connection()

        cursor = db.cursor()

        # ====================================================
        # INSERT QUIZ
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

        result = cursor.fetchone()

        if not result:

            raise Exception(
                "Quiz ID was not generated."
            )

        quiz_id = result[0]

        print(
            "✅ Quiz ID:",
            quiz_id
        )

        # ====================================================
        # INSERT QUESTIONS
        # ====================================================

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
                    str(
                        q["question"]
                    ).strip(),

                    str(
                        q["option_a"]
                    ).strip(),

                    str(
                        q["option_b"]
                    ).strip(),

                    str(
                        q["option_c"]
                    ).strip(),

                    str(
                        q["option_d"]
                    ).strip(),

                    q["correct_option"],

                    q["difficulty"]
                )
            )

        # ====================================================
        # QR
        # ====================================================

        qr_path = generate_qr(
            quiz_id
        )

        if not qr_path:

            raise Exception(
                "QR generation failed."
            )

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
            f"🎉 QUIZ {quiz_id} CREATED"
        )

        return redirect(
            f"/quiz_generated/{quiz_id}"
        )

    except Exception as e:

        print(
            "❌ QUIZ CREATION ERROR:",
            e
        )

        if db:

            db.rollback()

        return error_page(
            f"Quiz creation failed: {str(e)}"
        )

    finally:

        if cursor:

            cursor.close()

        if db:

            db.close()


# ============================================================
# GENERATED QUIZ
# ============================================================

@teacher.route(
    "/quiz_generated/<int:quiz_id>"
)
def quiz_generated(quiz_id):

    if not teacher_logged_in():

        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        # ====================================================
        # QUIZ
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
        # LIVE PARTICIPANTS
        # ====================================================

        cursor.execute(
            """
            SELECT

                COUNT(
                    DISTINCT attempt_id
                ) AS total_students

            FROM student_answers

            WHERE quiz_id=%s

            AND attempt_id IS NOT NULL
            """,
            (
                quiz_id,
            )
        )

        live_stats = cursor.fetchone()

        live_total_students = int(
            live_stats["total_students"] or 0
        )

        return render_template(
            "quiz_generated.html",

            quiz=quiz,

            questions=questions,

            live_total_students=
                live_total_students
        )

    finally:

        cursor.close()
        db.close()


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
    # GET
    # ========================================================

    if request.method == "GET":

        return render_template(
            "add_questions.html",
            quiz_id=quiz_id
        )

    # ========================================================
    # POST
    # ========================================================

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

    # ========================================================
    # VALIDATION
    # ========================================================

    if not question:

        return error_page(
            "Question is required.",
            f"/add_questions/{quiz_id}"
        )

    if not all([
        option_a,
        option_b,
        option_c,
        option_d
    ]):

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

    if difficulty not in [
        "Easy",
        "Medium",
        "Hard"
    ]:

        difficulty = "Medium"

    # ========================================================
    # SAVE
    # ========================================================

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

        # ====================================================
        # UPDATE COUNT
        # ====================================================

        cursor.execute(
            """
            UPDATE quizzes

            SET total_questions =
            (
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
            f"Error adding question: {str(e)}",
            f"/quiz_generated/{quiz_id}"
        )

    finally:

        cursor.close()
        db.close()

    return redirect(
        f"/quiz_generated/{quiz_id}"
    )


# ============================================================
# VIEW RESULTS
#
# IMPORTANT:
# Guest attempts are NOT assumed to have a guest_name column.
#
# We use to_jsonb(qa) so PostgreSQL can safely read possible
# guest fields without crashing when a particular field does
# not exist as a physical column.
# ============================================================

# ============================================================
# VIEW ALL STUDENT RESULTS
# ============================================================

@teacher.route("/view_results")
def view_results():

    # ========================================================
    # TEACHER LOGIN CHECK
    # ========================================================

    if "teacher_id" not in session:
        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        # ====================================================
        # GET ALL RESULTS
        #
        # Registered student:
        #   students.full_name
        #   students.roll_number
        #
        # Guest student:
        #   quiz_attempts.student_name
        #   quiz_attempts.roll_number
        #
        # results.attempt_id connects result with attempt.
        # ====================================================

        cursor.execute(
            """
            SELECT

                results.result_id,

                results.quiz_id,

                quizzes.title,

                results.score,

                results.percentage,

                results.submitted_at,

                results.attempt_id,

                COALESCE(
                    students.full_name,
                    quiz_attempts.student_name,
                    'Guest Student'
                ) AS full_name,

                COALESCE(
                    students.roll_number,
                    quiz_attempts.roll_number,
                    'N/A'
                ) AS roll_number,

                CASE

                    WHEN quiz_attempts.attempt_mode = 'guest'
                    THEN 'Guest'

                    ELSE 'Registered'

                END AS attempt_type

            FROM results

            LEFT JOIN students
                ON results.student_id =
                   students.student_id

            LEFT JOIN quiz_attempts
                ON results.attempt_id =
                   quiz_attempts.attempt_id

            LEFT JOIN quizzes
                ON results.quiz_id =
                   quizzes.quiz_id

            ORDER BY
                results.submitted_at DESC
            """
        )

        results = cursor.fetchall()

        # ====================================================
        # DEBUG
        # ====================================================

        print("=" * 70)

        print("✅ VIEW RESULTS SUCCESS")

        print(
            f"📊 TOTAL RESULTS: {len(results)}"
        )

        for r in results:

            print(
                f"Quiz: {r.get('title')} | "
                f"Name: {r.get('full_name')} | "
                f"Roll: {r.get('roll_number')} | "
                f"Type: {r.get('attempt_type')} | "
                f"Score: {r.get('score')} | "
                f"Percentage: {r.get('percentage')} | "
                f"Submitted: {r.get('submitted_at')}"
            )

        print("=" * 70)

    except Exception as e:

        db.rollback()

        print("=" * 70)

        print("❌ VIEW RESULTS ERROR")

        print(
            "ERROR TYPE:",
            type(e).__name__
        )

        print(
            "ERROR:",
            e
        )

        print("=" * 70)

        return render_template(
            "error.html",
            error_message=str(e)
        )

    finally:

        cursor.close()
        db.close()

    # ========================================================
    # RENDER PAGE
    # ========================================================

    return render_template(
        "view_results.html",
        results=results
    )

# ============================================================
# SHOW QR
# ============================================================

@teacher.route(
    "/show_qr/<int:quiz_id>"
)
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

@teacher.route(
    "/generate_qr_page"
)
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

@teacher.route(
    "/manage_quizzes"
)
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

@teacher.route(
    "/delete_quiz/<int:quiz_id>"
)
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
        # ANSWERS
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
        # ATTEMPTS
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
        # RESULTS
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
        # QUESTIONS
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
        # QUIZ
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

@teacher.route(
    "/test_ai"
)
def test_ai():

    print("\n")
    print("=" * 70)
    print("🧪 TEST AI")
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

        return {
            "status": "success",

            "count":
                len(questions),

            "time":
                (
                    end - start
                ).total_seconds(),

            "questions":
                questions
        }

    except Exception as e:

        print(
            "❌ TEST AI ERROR:",
            e
        )

        return {
            "status": "error",

            "error_type":
                type(e).__name__,

            "error":
                str(e)
        }, 500


# ============================================================
# TEACHER QUIZ LIST
# ============================================================

@teacher.route(
    "/teacher_quizzes"
)
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

        # RealDictRow -> dict
        quizzes = [
            dict(q)
            for q in quizzes
        ]

        return jsonify({

            "success": True,

            "quizzes":
                quizzes

        })

    except Exception as e:

        print(
            "❌ TEACHER QUIZ LIST ERROR:",
            e
        )

        return jsonify({

            "success": False,

            "error":
                str(e)

        }), 500

    finally:

        cursor.close()
        db.close()


# ============================================================
# LIVE QUIZ PROGRESS
# ============================================================

@teacher.route(
    "/quiz_progress/<int:quiz_id>"
)
def quiz_progress(quiz_id):

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

        row = cursor.fetchone()

        total_questions = int(
            row["total_questions"] or 0
        )

        # ====================================================
        # TOTAL PARTICIPANTS
        #
        # quiz_attempts is the reliable source because
        # guest attempts also have attempts.
        # ====================================================

        cursor.execute(
            """
            SELECT

                COUNT(
                    DISTINCT attempt_id
                ) AS total_students

            FROM quiz_attempts

            WHERE quiz_id=%s
            """,
            (
                quiz_id,
            )
        )

        row = cursor.fetchone()

        total_students = int(
            row["total_students"] or 0
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
                    DISTINCT sa.attempt_id
                ) AS response_count

            FROM questions q

            LEFT JOIN student_answers sa

                ON sa.question_id=
                   q.question_id

                AND sa.quiz_id=
                    q.quiz_id

                AND sa.attempt_id IS NOT NULL

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
        # FORMAT
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

            question_progress.append({

                "question_id":
                    item["question_id"],

                "question":
                    item["question"],

                "response_count":
                    response_count,

                "percentage":
                    percentage
            })

        # ====================================================
        # ANSWERED QUESTIONS
        # ====================================================

        answered_questions = sum(

            1

            for item
            in question_progress

            if item[
                "response_count"
            ] > 0
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

            "quiz_id":
                quiz_id,

            "title":
                quiz["title"],

            "total_questions":
                total_questions,

            "total_students":
                total_students,

            "answered_questions":
                answered_questions,

            "overall_progress":
                overall_progress,

            "total_answer_events":
                total_answer_events,

            "progress":
                question_progress
        })

    except Exception as e:

        print(
            "❌ LIVE PROGRESS ERROR:",
            e
        )

        return jsonify({

            "success": False,

            "error":
                str(e)

        }), 500

    finally:

        cursor.close()
        db.close()