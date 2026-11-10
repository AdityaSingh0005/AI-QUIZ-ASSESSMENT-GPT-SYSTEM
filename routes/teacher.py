from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    session,
    flash,
    current_app,
    jsonify
)

from database import get_db_connection

from utils.qr_generator import generate_qr

from utils.ai_generator import (
    generate_questions
)

from psycopg2.extras import RealDictCursor

import json
import threading

from datetime import (
    datetime,
    timedelta,
    timezone
)


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
# HELPER: CALCULATE QUIZ DURATION
# ============================================================

def calculate_duration_minutes(
    total_questions,
    question_time_seconds
):

    total_duration_seconds = (
        int(total_questions) *
        int(question_time_seconds)
    )

    duration_minutes = (
        total_duration_seconds + 59
    ) // 60

    return duration_minutes


# ============================================================
# HELPER: CONVERT FORM DATETIME FROM IST TO UTC
# ============================================================

def parse_ist_datetime(
    value
):

    value = (
        value or ""
    ).strip()

    if not value:

        raise ValueError(
            "Date and time are required."
        )

    try:

        naive_datetime = datetime.strptime(
            value,
            "%Y-%m-%dT%H:%M"
        )

    except ValueError:

        raise ValueError(
            "Invalid date/time format."
        )

    ist = timezone(
        timedelta(
            hours=5,
            minutes=30
        )
    )

    ist_datetime = naive_datetime.replace(
        tzinfo=ist
    )

    utc_datetime = ist_datetime.astimezone(
        timezone.utc
    )

    return utc_datetime

# ============================================================
# HELPER: CONVERT STORED DATETIME TO IST FOR DISPLAY
# ============================================================

def to_ist_datetime(value):
    if value is None:
        return None

    if not isinstance(value, datetime):
        return value

    ist = timezone(
        timedelta(hours=5, minutes=30)
    )

    # Database timestamps are expected to be timezone-aware.
    # Treat a naive timestamp as UTC for consistent display.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(ist)

# ============================================================
# HELPER: QUIZ STATUS
# ============================================================

def get_quiz_status(
    available_from,
    available_until
):

    now = datetime.now(
        timezone.utc
    )

    if available_from:

        if available_from.tzinfo is None:

            available_from = (
                available_from.replace(
                    tzinfo=timezone.utc
                )
            )

        else:

            available_from = (
                available_from.astimezone(
                    timezone.utc
                )
            )

    if available_until:

        if available_until.tzinfo is None:

            available_until = (
                available_until.replace(
                    tzinfo=timezone.utc
                )
            )

        else:

            available_until = (
                available_until.astimezone(
                    timezone.utc
                )
            )

    if (
        available_from
        and now < available_from
    ):

        return "upcoming"

    if (
        available_until
        and now >= available_until
    ):

        return "closed"

    return "live"


# ============================================================
# HELPER: BUILD AI DIFFICULTY QUALITY SUMMARY
# ============================================================

def build_difficulty_quality_summary(
    questions,
    evaluations,
    requested_easy=0,
    requested_medium=0,
    requested_hard=0
):
    """
    Convert raw AI evaluations into a template-friendly
    summary.

    No database column is required.

    The summary contains:

    - requested difficulty counts
    - AI verified counts
    - overall match
    - passed / failed
    - average confidence
    - question-wise comparison
    """

    if not evaluations:

        return {
            "enabled": False,

            "evaluation_available": False,

            "overall_match": False,

            "passed_count": 0,

            "failed_count": 0,

            "total": len(questions or []),

            "average_confidence": 0,

            "requested": {
                "Easy": int(requested_easy or 0),
                "Medium": int(requested_medium or 0),
                "Hard": int(requested_hard or 0)
            },

            "verified": {
                "Easy": 0,
                "Medium": 0,
                "Hard": 0
            },

            "questions": []
        }

    evaluation_map = {}

    for item in evaluations:

        try:

            index = int(
                item.get("index")
            )

        except (
            TypeError,
            ValueError
        ):

            continue

        evaluation_map[index] = item

    verified = {
        "Easy": 0,
        "Medium": 0,
        "Hard": 0
    }

    question_items = []

    confidence_values = []

    passed_count = 0

    failed_count = 0

    for index, question in enumerate(
        questions or [],
        start=1
    ):

        evaluation = (
            evaluation_map.get(index)
        )

        generated_difficulty = str(
            question.get(
                "difficulty",
                ""
            )
        ).strip()

        evaluated_difficulty = ""

        confidence = 0

        reason = ""

        match = False

        if evaluation:

            evaluated_difficulty = str(
                evaluation.get(
                    "difficulty",
                    ""
                )
            ).strip()

            try:

                confidence = float(
                    evaluation.get(
                        "confidence",
                        0
                    )
                )

            except (
                TypeError,
                ValueError
            ):

                confidence = 0

            confidence = max(
                0,
                min(
                    1,
                    confidence
                )
            )

            reason = str(
                evaluation.get(
                    "reason",
                    ""
                ) or ""
            ).strip()

            if evaluated_difficulty in verified:

                verified[
                    evaluated_difficulty
                ] += 1

            confidence_values.append(
                confidence
            )

            match = (
                generated_difficulty
                ==
                evaluated_difficulty
            )

        if match:

            passed_count += 1

        else:

            failed_count += 1

        question_items.append({

            "index":
                index,

            "question":
                question.get(
                    "question",
                    ""
                ),

            "generated_difficulty":
                generated_difficulty,

            "evaluated_difficulty":
                evaluated_difficulty,

            "match":
                match,

            "confidence":
                round(
                    confidence,
                    2
                ),

            "confidence_percent":
                round(
                    confidence * 100,
                    1
                ),

            "reason":
                reason
        })

    total = len(question_items)

    average_confidence = (
        sum(confidence_values)
        /
        len(confidence_values)
        if confidence_values
        else 0
    )

    # If an evaluation exists for every question and all match,
    # overall quality check passes.
    overall_match = (
        total > 0
        and
        len(evaluations) == total
        and
        passed_count == total
    )

    return {

        "enabled":
            True,

        "evaluation_available":
            True,

        "overall_match":
            overall_match,

        "passed_count":
            passed_count,

        "failed_count":
            failed_count,

        "total":
            total,

        "average_confidence":
            round(
                average_confidence * 100,
                1
            ),

        "requested": {

            "Easy":
                int(requested_easy or 0),

            "Medium":
                int(requested_medium or 0),

            "Hard":
                int(requested_hard or 0)
        },

        "verified":
            verified,

        "questions":
            question_items
    }


# ============================================================
# HELPER: COMPACT SESSION DATA
# ============================================================

def build_compact_evaluation_data(
    evaluations
):
    """
    Flask's default session is stored in a signed cookie.

    Therefore we keep only compact evaluation information.

    Question text is NOT stored here because it is already
    available from the database.

    This avoids unnecessary session-cookie growth.
    """

    compact = []

    for item in evaluations or []:

        try:

            index = int(
                item.get("index")
            )

        except (
            TypeError,
            ValueError
        ):

            continue

        difficulty = str(
            item.get(
                "difficulty",
                ""
            )
        ).strip()

        try:

            confidence = float(
                item.get(
                    "confidence",
                    0
                )
            )

        except (
            TypeError,
            ValueError
        ):

            confidence = 0

        confidence = max(
            0,
            min(
                1,
                confidence
            )
        )

        reason = str(
            item.get(
                "reason",
                ""
            ) or ""
        ).strip()

        # Keep reason short so session does not become huge.
        if len(reason) > 160:

            reason = (
                reason[:157]
                + "..."
            )

        compact.append({

            "index":
                index,

            "difficulty":
                difficulty,

            "confidence":
                round(
                    confidence,
                    2
                ),

            "reason":
                reason
        })

    return compact


# ============================================================
# TEACHER DASHBOARD
# ============================================================

@teacher.route("/teacher_dashboard")
def teacher_dashboard():
    if not teacher_logged_in():
        return redirect("/")

    db = get_db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)
    try:
        teacher_id = session["teacher_id"]

        # One aggregate query replaces three separate dashboard round trips.
        cursor.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM quizzes WHERE teacher_id=%s) AS total_quizzes,
                (SELECT COUNT(*) FROM quiz_attempts qa
                 JOIN quizzes q ON q.quiz_id=qa.quiz_id
                 WHERE q.teacher_id=%s) AS total_participants,
                (SELECT AVG(r.percentage) FROM results r
                 JOIN quizzes q ON q.quiz_id=r.quiz_id
                 WHERE q.teacher_id=%s AND r.percentage IS NOT NULL) AS average_score
            """,
            (teacher_id, teacher_id, teacher_id),
        )
        stats = cursor.fetchone()

        # One indexed quiz/attempt query supplies both recent and live sections.
        cursor.execute(
            """
            SELECT q.quiz_id, q.title, q.total_questions, q.duration_minutes,
                   q.question_time_seconds, q.available_from, q.available_until,
                   q.created_at, COUNT(DISTINCT qa.attempt_id) AS participants
            FROM quizzes q
            LEFT JOIN quiz_attempts qa ON qa.quiz_id=q.quiz_id
            WHERE q.teacher_id=%s
            GROUP BY q.quiz_id
            ORDER BY q.created_at DESC
            """,
            (teacher_id,),
        )
        all_quizzes = cursor.fetchall()
        quizzes = all_quizzes[:6]
        live_quizzes = []

        for quiz in all_quizzes:
            quiz["status"] = get_quiz_status(quiz.get("available_from"), quiz.get("available_until"))
            quiz["calculated_duration_minutes"] = calculate_duration_minutes(
                quiz.get("total_questions") or 0,
                quiz.get("question_time_seconds") or 0,
            )
            if quiz["status"] == "live":
                live_quizzes.append(quiz)

        return render_template(
            "teacher_dashboard.html",
            name=session.get("name", "Teacher"),
            total_quizzes=int(stats.get("total_quizzes") or 0),
            total_participants=int(stats.get("total_participants") or 0),
            average_score=round(float(stats.get("average_score") or 0), 1),
            quizzes=quizzes,
            live_quizzes=live_quizzes,
        )
    except Exception:
        db.rollback()
        current_app.logger.exception("Teacher dashboard failed")
        return "Unable to load Teacher Dashboard. Please retry.", 500
    finally:
        cursor.close()
        db.close()


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

    subject = request.form.get(
        "subject",
        ""
    ).strip()

    prompt = request.form.get(
        "prompt",
        ""
    ).strip()

    # ========================================================
    # COUNTS + QUESTION TIMER
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

    if not subject:

        return error_page(
            "Quiz subject is required."
        )

    if len(subject) > 100:

        return error_page(
            "Subject name cannot exceed 100 characters."
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

    if question_time_seconds <= 0:

        return error_page(
            "Question time must be greater than 0."
        )

    # ========================================================
    # AUTOMATIC TOTAL DURATION
    # ========================================================

    total_duration_seconds = (
        total_questions *
        question_time_seconds
    )

    duration_minutes = (
        total_duration_seconds + 59
    ) // 60

    print(
        "⏱️ TOTAL QUESTIONS:",
        total_questions
    )

    print(
        "⏱️ TIME PER QUESTION:",
        question_time_seconds,
        "seconds"
    )

    print(
        "⏱️ TOTAL QUIZ DURATION:",
        total_duration_seconds,
        "seconds"
    )

    print(
        "⏱️ STORED DURATION:",
        duration_minutes,
        "minutes"
    )

    # ========================================================
    # AVAILABILITY
    # ========================================================

    available_from_raw = request.form.get(
        "available_from",
        ""
    ).strip()

    available_until_raw = request.form.get(
        "available_until",
        ""
    ).strip()

    if not available_from_raw:

        return error_page(
            "Quiz start date and time are required."
        )

    if not available_until_raw:

        return error_page(
            "Quiz end date and time are required."
        )

    try:

        available_from = parse_ist_datetime(
            available_from_raw
        )

        available_until = parse_ist_datetime(
            available_until_raw
        )

    except ValueError as e:

        print(
            "❌ AVAILABILITY ERROR:",
            e
        )

        return error_page(
            str(e)
        )

    if available_until <= available_from:

        return error_page(
            "Quiz end time must be later than quiz start time."
        )

    now_utc = datetime.now(
        timezone.utc
    )

    if available_from < (
        now_utc -
        timedelta(seconds=30)
    ):

        return error_page(
            "Quiz start time cannot be in the past."
        )

    print(
        "📅 AVAILABLE FROM (UTC):",
        available_from
    )

    print(
        "📅 AVAILABLE UNTIL (UTC):",
        available_until
    )

    # ========================================================
    # AI GENERATION
    #
    # IMPORTANT:
    #
    # generate_questions() now returns:
    #
    # questions, evaluations
    # ========================================================

    print(
        "🤖 Generating questions..."
    )

    try:

        start_time = datetime.now()

        questions, evaluations = generate_questions(
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

        print(
            "🧠 AI evaluations:",
            len(evaluations)
            if evaluations
            else 0
        )

    except Exception as e:

        # Keep the complete traceback in Render logs. The short message is
        # still shown to the teacher, without exposing credentials.
        current_app.logger.exception(
            "AI GENERATION ERROR | teacher_id=%s | topic=%r | requested=%s "
            "(easy=%s, medium=%s, hard=%s)",
            session.get("teacher_id"),
            prompt[:120],
            total_questions,
            easy,
            medium,
            hard,
        )

        return error_page(
            f"AI question generation failed: {type(e).__name__}: {str(e)}"
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

        q["explanation"] = str(
            q.get(
                "explanation",
                ""
            )
            or ""
        ).strip()

    # ========================================================
    # DIFFICULTY DISTRIBUTION CHECK
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
    # BUILD QUALITY SUMMARY
    #
    # This happens BEFORE database insertion so we can verify
    # that evaluation data is valid.
    # ========================================================

    difficulty_quality = (
        build_difficulty_quality_summary(
            questions=questions,
            evaluations=evaluations,
            requested_easy=easy,
            requested_medium=medium,
            requested_hard=hard
        )
    )

    print(
        "🧠 AI QUALITY CHECK:"
    )

    print(
        "   Evaluation available:",
        difficulty_quality[
            "evaluation_available"
        ]
    )

    print(
        "   Passed:",
        difficulty_quality[
            "passed_count"
        ]
    )

    print(
        "   Failed:",
        difficulty_quality[
            "failed_count"
        ]
    )

    print(
        "   Average confidence:",
        difficulty_quality[
            "average_confidence"
        ],
        "%"
    )

    print(
        "   Overall match:",
        difficulty_quality[
            "overall_match"
        ]
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
                subject,
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
                %s,
                %s
            )

            RETURNING quiz_id
            """,
            (
                session["teacher_id"],
                title,
                subject,
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
                    difficulty,
                    explanation
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

                    q["difficulty"],

                    q["explanation"]
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

        print(
            f"⏱️ Duration: "
            f"{total_questions} × "
            f"{question_time_seconds}s = "
            f"{total_duration_seconds}s"
        )

        print(
            f"📅 Start: {available_from}"
        )

        print(
            f"📅 End: {available_until}"
        )

        # ====================================================
        # SAVE COMPACT AI EVALUATION IN SESSION
        #
        # No DB schema change required.
        # ====================================================

        if evaluations:

            session[
                "difficulty_evaluation"
            ] = {

                "quiz_id":
                    quiz_id,

                "evaluation":
                    build_compact_evaluation_data(
                        evaluations
                    ),

                "requested": {

                    "Easy":
                        easy,

                    "Medium":
                        medium,

                    "Hard":
                        hard
                }
            }

        else:

            session.pop(
                "difficulty_evaluation",
                None
            )

        session.modified = True

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
        # CALCULATED DURATION
        # ====================================================

        quiz["calculated_duration_minutes"] = (
            calculate_duration_minutes(
                quiz.get("total_questions") or 0,
                quiz.get("question_time_seconds") or 0
            )
        )

        quiz["total_duration_seconds"] = (
            int(
                quiz.get("total_questions") or 0
            ) *
            int(
                quiz.get("question_time_seconds") or 0
            )
        )

        quiz["status"] = get_quiz_status(
            quiz.get("available_from"),
            quiz.get("available_until")
        )
        
        # Convert schedule to IST only after calculating status.
        quiz["available_from"] = to_ist_datetime(
            quiz.get("available_from")
        )

        quiz["available_until"] = to_ist_datetime(
            quiz.get("available_until")
        )

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
        # AI DIFFICULTY QUALITY CHECK
        #
        # Evaluation was generated during quiz creation.
        # We retrieve its compact copy from the session.
        # ====================================================

        stored_evaluation = (
            session.get(
                "difficulty_evaluation"
            )
        )

        evaluations = []

        requested_easy = 0
        requested_medium = 0
        requested_hard = 0

        if (
            stored_evaluation
            and
            int(
                stored_evaluation.get(
                    "quiz_id",
                    -1
                )
            ) == int(quiz_id)
        ):

            evaluations = (
                stored_evaluation.get(
                    "evaluation",
                    []
                )
            )

            requested = (
                stored_evaluation.get(
                    "requested",
                    {}
                )
            )

            requested_easy = int(
                requested.get(
                    "Easy",
                    0
                )
            )

            requested_medium = int(
                requested.get(
                    "Medium",
                    0
                )
            )

            requested_hard = int(
                requested.get(
                    "Hard",
                    0
                )
            )

        # ====================================================
        # BUILD TEMPLATE SUMMARY
        # ====================================================

        difficulty_quality = (
            build_difficulty_quality_summary(
                questions=questions,
                evaluations=evaluations,
                requested_easy=requested_easy,
                requested_medium=requested_medium,
                requested_hard=requested_hard
            )
        )

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

        # ====================================================
        # TEMPLATE
        # ====================================================

        return render_template(
            "quiz_generated.html",

            quiz=quiz,

            questions=questions,

            live_total_students=
                live_total_students,

            difficulty_quality=
                difficulty_quality
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

                quiz_id,
                total_questions,
                question_time_seconds

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

    explanation = request.form.get(
        "explanation",
        ""
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
                difficulty,
                explanation
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
                difficulty,
                explanation
            )
        )

        # ====================================================
        # UPDATE QUESTION COUNT + DURATION
        # ====================================================

        cursor.execute(
            """
            UPDATE quizzes

            SET

                total_questions =
                (
                    SELECT COUNT(*)

                    FROM questions

                    WHERE quiz_id=%s
                ),

                duration_minutes =
                CEILING(
                    (
                        (
                            SELECT COUNT(*)

                            FROM questions

                            WHERE quiz_id=%s
                        )
                        *
                        question_time_seconds
                    ) / 60.0
                )::INTEGER

            WHERE quiz_id=%s
            """,
            (
                quiz_id,
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
# VIEW ALL STUDENT RESULTS
# ============================================================
@teacher.route("/view_results", methods=["GET"])
def view_results():
    if "teacher_id" not in session:
        return redirect("/teacher_login")

    teacher_id = session["teacher_id"]
    quiz_id_raw = request.args.get("quiz_id", "").strip()
    
    percentage_filter = request.args.get("percentage_filter", "all").strip()

    min_percentage_raw = request.args.get("min_percentage", "").strip()
    max_percentage_raw = request.args.get("max_percentage", "").strip()

    min_percentage = None
    max_percentage = None

    if percentage_filter not in {"all", "75_plus", "below_75", "90_plus", "custom"}:
        return "Invalid percentage filter.", 400

    if percentage_filter == "custom":
        try:
            min_percentage = float(min_percentage_raw) if min_percentage_raw else None
            max_percentage = float(max_percentage_raw) if max_percentage_raw else None

            if (
                min_percentage is None
                and max_percentage is None
            ):
                return "Enter at least one percentage limit.", 400

            if (
                min_percentage is not None
                and not 0 <= min_percentage <= 100
            ):
                return "Minimum percentage must be between 0 and 100.", 400

            if (
                max_percentage is not None
                and not 0 <= max_percentage <= 100
            ):
                return "Maximum percentage must be between 0 and 100.", 400

            if (
                min_percentage is not None
                and max_percentage is not None
                and min_percentage > max_percentage
            ):
                return "Minimum percentage cannot exceed maximum percentage.", 400

        except ValueError:
            return "Enter valid percentage values.", 400

    selected_quiz_id = None
    selected_quiz = None

    if quiz_id_raw:
        try:
            selected_quiz_id = int(quiz_id_raw)
            if selected_quiz_id <= 0:
                raise ValueError
        except ValueError:
            return "Invalid quiz ID.", 400

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # Fetch only quizzes belonging to this teacher.
        cursor.execute(
            """
            SELECT quiz_id, title, created_at
            FROM quizzes
            WHERE teacher_id = %s
            ORDER BY created_at DESC
            """,
            (teacher_id,)
        )
        quizzes = cursor.fetchall()

        # Validate the requested quiz before displaying its results.
        if selected_quiz_id is not None:
            selected_quiz = next(
                (
                    quiz for quiz in quizzes
                    if int(quiz["quiz_id"]) == selected_quiz_id
                ),
                None
            )

            if selected_quiz is None:
                return "Quiz not found or access denied.", 404

        # Keep this query aligned with the existing database schema.
        query = """
            SELECT
                r.result_id,
                qa.attempt_id,
                q.quiz_id,
                q.title,

                COALESCE(
                    NULLIF(TRIM(s.full_name), ''),
                    NULLIF(TRIM(qa.student_name), ''),
                    'Guest Student'
                ) AS full_name,

                COALESCE(
                    NULLIF(TRIM(qa.roll_number), ''),
                    NULLIF(TRIM(s.roll_number), ''),
                    'N/A'
                ) AS roll_number,

                COALESCE(
                    NULLIF(TRIM(qa.attempt_mode), ''),
                    CASE
                        WHEN qa.student_id IS NULL THEN 'Guest'
                        ELSE 'Registered'
                    END
                ) AS attempt_type,

                COALESCE(r.score, qa.score, 0) AS score,

                COALESCE(
                    r.percentage,
                    qa.percentage,
                    0
                ) AS percentage,

                COALESCE(
                    qa.submitted_at,
                    r.submitted_at
                ) AS submitted_at

            FROM quiz_attempts qa

            INNER JOIN quizzes q
                ON q.quiz_id = qa.quiz_id

            LEFT JOIN results r
                ON r.attempt_id = qa.attempt_id

            LEFT JOIN students s
                ON s.student_id = qa.student_id

            WHERE q.teacher_id = %s
              AND qa.status = 'submitted'
        """

        params = [teacher_id]

        # Selecting a quiz limits results to that quiz only.
        if selected_quiz_id is not None:
            query += " AND q.quiz_id = %s"
            params.append(selected_quiz_id)

        query += """
            ORDER BY
                COALESCE(
                    qa.submitted_at,
                    r.submitted_at
                ) DESC NULLS LAST,
                qa.attempt_id DESC
        """

        cursor.execute(query, tuple(params))
        results = cursor.fetchall()

        if percentage_filter == "75_plus":
            results = [
                row for row in results
                if float(row["percentage"] or 0) >= 75
            ]

        elif percentage_filter == "below_75":
            results = [
                row for row in results
                if float(row["percentage"] or 0) < 75
            ]

        elif percentage_filter == "90_plus":
            results = [
                row for row in results
                if float(row["percentage"] or 0) >= 90
            ]

        elif percentage_filter == "custom":
            filtered_results = []

            for row in results:
                percentage = float(row["percentage"] or 0)

                if (
                    (min_percentage is None or percentage >= min_percentage)
                    and
                    (max_percentage is None or percentage <= max_percentage)
                ):
                    filtered_results.append(row)

            results = filtered_results
            
            
        percentages = [
            float(row["percentage"])
            for row in results
            if row["percentage"] is not None
        ]

        total_submissions = len(results)

        average_percentage = round(
            sum(percentages) / len(percentages), 1
        ) if percentages else 0

        highest_percentage = round(
            max(percentages), 1
        ) if percentages else 0
        
        for row in results:
            row["submitted_at"] = to_ist_datetime(
                row.get("submitted_at")
            )

        return render_template(
            "view_results.html",
            results=results,
            quizzes=quizzes,
            selected_quiz=selected_quiz,
            selected_quiz_id=selected_quiz_id,
            total_submissions=total_submissions,
            average_percentage=average_percentage,
            highest_percentage=highest_percentage,
            percentage_filter=percentage_filter,
            min_percentage=min_percentage,
            max_percentage=max_percentage
        )

    finally:
        cursor.close()
        conn.close()
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

    for quiz in quizzes:

        quiz["status"] = get_quiz_status(
            quiz.get("available_from"),
            quiz.get("available_until")
        )

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
                subject,
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

    for quiz in quizzes:

        quiz["status"] = get_quiz_status(
            quiz.get("available_from"),
            quiz.get("available_until")
        )
        
        quiz["available_from"] = to_ist_datetime(
            quiz.get("available_from")
        )

        quiz["available_until"] = to_ist_datetime(
            quiz.get("available_until")
        )

        quiz["calculated_duration_minutes"] = (
            calculate_duration_minutes(
                quiz.get("total_questions") or 0,
                quiz.get("question_time_seconds") or 0
            )
        )

        quiz["total_duration_seconds"] = (
            int(
                quiz.get("total_questions") or 0
            ) *
            int(
                quiz.get("question_time_seconds") or 0
            )
        )

    return render_template(
        "manage_quizzes.html",
        quizzes=quizzes
    )



# ============================================================
# ASSIGN / UPDATE QUIZ SUBJECT
# ============================================================

@teacher.route(
    "/assign_quiz_subject/<int:quiz_id>",
    methods=["POST"]
)
def assign_quiz_subject(quiz_id):

    if not teacher_logged_in():
        return redirect("/")

    teacher_id = session["teacher_id"]

    subject = request.form.get(
        "subject", ""
    ).strip()

    if not subject:
        flash("Subject cannot be empty.", "error")
        return redirect("/manage_quizzes")

    if len(subject) > 150:
        flash(
            "Subject name cannot exceed 150 characters.",
            "error"
        )
        return redirect("/manage_quizzes")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        cursor.execute(
            """
            UPDATE quizzes
            SET subject = %s
            WHERE quiz_id = %s
              AND teacher_id = %s
            RETURNING quiz_id
            """,
            (
                subject,
                quiz_id,
                teacher_id
            )
        )

        updated_quiz = cursor.fetchone()

        if not updated_quiz:

            db.rollback()

            flash(
                "Quiz not found or you do not have permission to update it.",
                "error"
            )

        else:

            db.commit()

            flash(
                "Quiz subject updated successfully.",
                "success"
            )

    except Exception as e:

        db.rollback()

        print(
            "Assign Quiz Subject Error:",
            e
        )

        flash(
            "Unable to update quiz subject. Please try again.",
            "error"
        )

    finally:

        cursor.close()
        db.close()

    return redirect("/manage_quizzes")

# ============================================================
# EDIT QUIZ SCHEDULE
# ============================================================

@teacher.route(
    "/edit_quiz_schedule/<int:quiz_id>",
    methods=["GET", "POST"]
)
def edit_quiz_schedule(quiz_id):

    if not teacher_logged_in():
        return redirect("/")

    teacher_id = session["teacher_id"]

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
                available_from,
                available_until
            FROM quizzes
            WHERE quiz_id = %s
              AND teacher_id = %s
            """,
            (quiz_id, teacher_id)
        )

        quiz = cursor.fetchone()

        if not quiz:
            flash("Quiz not found.", "error")
            return redirect("/manage_quizzes")

        if request.method == "POST":
            try:
                available_from = parse_ist_datetime(
                    request.form.get("available_from")
                )

                available_until = parse_ist_datetime(
                    request.form.get("available_until")
                )

                if available_until <= available_from:
                    raise ValueError(
                        "End time must be later than start time."
                    )

            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(
                    request.url
                )

            cursor.execute(
                """
                UPDATE quizzes
                SET available_from = %s,
                    available_until = %s
                WHERE quiz_id = %s
                  AND teacher_id = %s
                """,
                (
                    available_from,
                    available_until,
                    quiz_id,
                    teacher_id
                )
            )

            db.commit()

            flash(
                "Quiz schedule updated successfully.",
                "success"
            )

            return redirect("/manage_quizzes")

        ist = timezone(
            timedelta(hours=5, minutes=30)
        )

        def format_datetime_local(value):
            if value is None:
                return ""

            if value.tzinfo is None:
                value = value.replace(
                    tzinfo=timezone.utc
                )

            return value.astimezone(ist).strftime(
                "%Y-%m-%dT%H:%M"
            )

        quiz["available_from_local"] = (
            format_datetime_local(
                quiz.get("available_from")
            )
        )

        quiz["available_until_local"] = (
            format_datetime_local(
                quiz.get("available_until")
            )
        )

        return render_template(
            "edit_quiz_schedule.html",
            quiz=quiz
        )

    except Exception:
        db.rollback()
        current_app.logger.exception(
            "Unable to edit quiz schedule."
        )

        flash(
            "Unable to update quiz schedule. Please try again.",
            "error"
        )

        return redirect("/manage_quizzes")

    finally:
        cursor.close()
        db.close()
        
# ============================================================
# DELETE QUIZ
# ============================================================

@teacher.route("/delete_quiz/<int:quiz_id>", methods=["POST", "GET"])
def delete_quiz(quiz_id):
    """Delete a teacher-owned quiz and its dependent records safely."""
    if not teacher_logged_in():
        return redirect("/")

    if request.method == "GET":
        return render_template("confirm_delete_quiz.html", quiz_id=quiz_id)

    db = get_db_connection()
    cursor = db.cursor()
    try:
        cursor.execute(
            "SELECT quiz_id FROM quizzes WHERE quiz_id=%s AND teacher_id=%s",
            (quiz_id, session["teacher_id"]),
        )
        if not cursor.fetchone():
            flash("Quiz not found or access denied.", "error")
            return redirect("/manage_quizzes")

        # Child records must be deleted before parent attempts/quizzes.
        cursor.execute("DELETE FROM student_answers WHERE quiz_id=%s", (quiz_id,))
        cursor.execute("DELETE FROM results WHERE quiz_id=%s", (quiz_id,))
        cursor.execute("DELETE FROM quiz_attempts WHERE quiz_id=%s", (quiz_id,))
        cursor.execute("DELETE FROM questions WHERE quiz_id=%s", (quiz_id,))
        cursor.execute(
            "DELETE FROM quizzes WHERE quiz_id=%s AND teacher_id=%s",
            (quiz_id, session["teacher_id"]),
        )
        db.commit()
        flash("Quiz and its recorded attempts/results were deleted.", "success")
    except Exception:
        db.rollback()
        current_app.logger.exception("DELETE QUIZ ERROR for quiz_id=%s", quiz_id)
        flash("Quiz could not be deleted. Check the server logs for the database error.", "error")
    finally:
        cursor.close()
        db.close()

    return redirect("/manage_quizzes")


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

        questions, evaluations = generate_questions(
            "Database Management System",
            1,
            1,
            1
        )

        end = datetime.now()

        quality = (
            build_difficulty_quality_summary(
                questions=questions,
                evaluations=evaluations,
                requested_easy=1,
                requested_medium=1,
                requested_hard=1
            )
        )

        return {

            "status":
                "success",

            "count":
                len(questions),

            "time":
                (
                    end - start
                ).total_seconds(),

            "questions":
                questions,

            "difficulty_quality":
                quality
        }

    except Exception as e:

        print(
            "❌ TEST AI ERROR:",
            e
        )

        return {

            "status":
                "error",

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

        quizzes = [
            dict(q)
            for q in quizzes
        ]

        for quiz in quizzes:

            quiz["status"] = get_quiz_status(
                quiz.get("available_from"),
                quiz.get("available_until")
            )

            quiz["total_duration_seconds"] = (
                int(
                    quiz.get("total_questions") or 0
                ) *
                int(
                    quiz.get("question_time_seconds") or 0
                )
            )

        return jsonify({

            "success":
                True,

            "quizzes":
                quizzes

        })

    except Exception as e:

        print(
            "❌ TEACHER QUIZ LIST ERROR:",
            e
        )

        return jsonify({

            "success":
                False,

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
                ) AS response_count,

                COUNT(DISTINCT CASE
                    WHEN UPPER(TRIM(sa.selected_option)) = UPPER(TRIM(q.correct_option))
                    THEN sa.attempt_id END
                ) AS correct_count,

                COUNT(DISTINCT CASE
                    WHEN sa.selected_option IS NOT NULL
                     AND UPPER(TRIM(sa.selected_option)) <> UPPER(TRIM(q.correct_option))
                    THEN sa.attempt_id END
                ) AS incorrect_count

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
                "question_id": item["question_id"],
                "question": item["question"],
                "response_count": response_count,
                "correct_count": int(item.get("correct_count") or 0),
                "incorrect_count": int(item.get("incorrect_count") or 0),
                "unanswered_count": max(total_students - response_count, 0),
                "percentage": percentage
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

            "success":
                True,

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

            "success":
                False,

            "error":
                str(e)

        }), 500

    finally:

        cursor.close()
        db.close()
        

# ============================================================
# EXPORT TEACHER RESULTS AS CSV (Excel-compatible)
# ============================================================
@teacher.route("/export_results", methods=["GET"])
def export_results():
    """Export submitted results for the logged-in teacher only."""
    import csv
    import io
    from flask import make_response

    if "teacher_id" not in session:
        return redirect("/teacher_login")

    teacher_id = session["teacher_id"]
    quiz_id_raw = (request.args.get("quiz_id") or "").strip()
    selected_quiz_id = None

    if quiz_id_raw:
        try:
            selected_quiz_id = int(quiz_id_raw)
            if selected_quiz_id <= 0:
                raise ValueError
        except ValueError:
            return "Invalid quiz ID.", 400

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        query = """
            SELECT
                COALESCE(NULLIF(TRIM(s.full_name), ''),
                         NULLIF(TRIM(qa.student_name), ''),
                         'Guest Student') AS student_name,
                COALESCE(NULLIF(TRIM(qa.roll_number), ''),
                         NULLIF(TRIM(s.roll_number), ''),
                         'N/A') AS roll_number,
                q.title AS quiz_title,
                COALESCE(q.total_questions, 0) AS total_marks,
                COALESCE(r.score, qa.score, 0) AS marks,
                COALESCE(r.percentage, qa.percentage, 0) AS percentage,
                COALESCE(qa.submitted_at, r.submitted_at) AS submitted_at,
                qa.status AS attempt_status,
                COALESCE(NULLIF(TRIM(qa.attempt_mode), ''),
                         CASE WHEN qa.student_id IS NULL
                              THEN 'Guest' ELSE 'Registered' END) AS attempt_type
            FROM quiz_attempts qa
            JOIN quizzes q ON q.quiz_id = qa.quiz_id
            LEFT JOIN results r ON r.attempt_id = qa.attempt_id
            LEFT JOIN students s ON s.student_id = qa.student_id
            WHERE q.teacher_id = %s AND qa.status = 'submitted'
        """
        params = [teacher_id]
        if selected_quiz_id is not None:
            query += " AND q.quiz_id = %s"
            params.append(selected_quiz_id)
        query += """
            ORDER BY COALESCE(qa.submitted_at, r.submitted_at) DESC NULLS LAST,
                     qa.attempt_id DESC
        """
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()

        output = io.StringIO()
        # UTF-8 BOM makes names display correctly in desktop Excel.
        output.write("\ufeff")
        writer = csv.writer(output)
        writer.writerow([
            "Student Name", "Roll Number", "Quiz Title", "Total Marks",
            "Marks Obtained", "Percentage", "Submission Time",
            "Attempt Status", "Attempt Type"
        ])
        for row in rows:
            writer.writerow([
                row["student_name"],
                row["roll_number"],
                row["quiz_title"],
                # Marks are currently one mark per question; total marks are
                # obtained from the quiz's question count in the query below.
                row.get("total_marks", ""),
                row["marks"],
                row["percentage"],
                row["submitted_at"].isoformat(sep=" ") if row["submitted_at"] else "",
                row["attempt_status"],
                row["attempt_type"],
            ])

        response = make_response(output.getvalue())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        suffix = f"_quiz_{selected_quiz_id}" if selected_quiz_id else ""
        response.headers["Content-Disposition"] = (
            f'attachment; filename="quiz_results{suffix}.csv"'
        )
        return response
    finally:
        cursor.close()
        conn.close()



# ============================================================
# INTERNAL ASSESSMENT (COURSE + SEMESTER + REGISTERED/GUEST)
# ============================================================


# Internal Assessment schema bootstrap. The feature previously redirected to the
# dashboard when one of its additive migration tables was missing. Create the
# required additive schema once per worker so the feature can open reliably.
_IA_SCHEMA_READY = False
_IA_SCHEMA_LOCK = threading.Lock()


def _ensure_internal_assessment_schema(conn, cursor):
    global _IA_SCHEMA_READY
    if _IA_SCHEMA_READY:
        return

    with _IA_SCHEMA_LOCK:
        if _IA_SCHEMA_READY:
            return

        statements = [
            "ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS subject VARCHAR(150)",
            """CREATE TABLE IF NOT EXISTS internal_assessments (
                assessment_id BIGSERIAL PRIMARY KEY,
                teacher_id INTEGER NOT NULL REFERENCES teachers(teacher_id) ON DELETE CASCADE,
                title VARCHAR(200) NOT NULL,
                course VARCHAR(30), department VARCHAR(100), semester VARCHAR(20),
                calculation_method VARCHAR(30) NOT NULL DEFAULT 'percentage_average',
                final_out_of INTEGER NOT NULL DEFAULT 30,
                status VARCHAR(20) NOT NULL DEFAULT 'draft',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finalized_at TIMESTAMPTZ
            )""",
            "ALTER TABLE internal_assessments ADD COLUMN IF NOT EXISTS course VARCHAR(30)",
            "ALTER TABLE internal_assessments ADD COLUMN IF NOT EXISTS department VARCHAR(100)",
            "ALTER TABLE internal_assessments ADD COLUMN IF NOT EXISTS semester VARCHAR(20)",
            "ALTER TABLE internal_assessments ADD COLUMN IF NOT EXISTS calculation_method VARCHAR(30) NOT NULL DEFAULT 'percentage_average'",
            "ALTER TABLE internal_assessments ADD COLUMN IF NOT EXISTS final_out_of INTEGER NOT NULL DEFAULT 30",
            "ALTER TABLE internal_assessments ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'draft'",
            "ALTER TABLE internal_assessments ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP",
            "ALTER TABLE internal_assessments ADD COLUMN IF NOT EXISTS finalized_at TIMESTAMPTZ",
            """CREATE TABLE IF NOT EXISTS internal_assessment_quizzes (
                assessment_id BIGINT NOT NULL REFERENCES internal_assessments(assessment_id) ON DELETE CASCADE,
                quiz_id INTEGER NOT NULL REFERENCES quizzes(quiz_id) ON DELETE RESTRICT,
                PRIMARY KEY (assessment_id, quiz_id)
            )""",
            """CREATE TABLE IF NOT EXISTS internal_assessment_students (
                assessment_id BIGINT NOT NULL REFERENCES internal_assessments(assessment_id) ON DELETE CASCADE,
                student_id INTEGER NOT NULL REFERENCES students(student_id) ON DELETE RESTRICT,
                PRIMARY KEY (assessment_id, student_id)
            )""",
            """CREATE TABLE IF NOT EXISTS internal_assessment_participants_v2 (
                participant_id BIGSERIAL PRIMARY KEY,
                assessment_id BIGINT NOT NULL REFERENCES internal_assessments(assessment_id) ON DELETE CASCADE,
                student_id INTEGER NULL REFERENCES students(student_id) ON DELETE SET NULL,
                participant_name VARCHAR(150) NOT NULL,
                roll_number VARCHAR(50) NOT NULL,
                course VARCHAR(30), department VARCHAR(100), semester VARCHAR(20),
                participant_type VARCHAR(20) NOT NULL CHECK (participant_type IN ('registered','guest')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (assessment_id, participant_id)
            )""",
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_ia_v2_registered_participant
                ON internal_assessment_participants_v2(assessment_id, student_id) WHERE student_id IS NOT NULL""",
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_ia_v2_guest_roll
                ON internal_assessment_participants_v2(assessment_id, UPPER(BTRIM(roll_number))) WHERE student_id IS NULL""",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_ia_v2_participant_assessment ON internal_assessment_participants_v2(assessment_id, participant_id)",
            """INSERT INTO internal_assessment_participants_v2
                (assessment_id, student_id, participant_name, roll_number, course, department, semester, participant_type)
                SELECT ias.assessment_id, st.student_id, st.full_name, st.roll_number,
                       COALESCE(ia.course, st.course), COALESCE(ia.department, st.department),
                       COALESCE(ia.semester, st.semester), 'registered'
                FROM internal_assessment_students ias
                JOIN students st ON st.student_id=ias.student_id
                JOIN internal_assessments ia ON ia.assessment_id=ias.assessment_id
                ON CONFLICT DO NOTHING""",
            """CREATE TABLE IF NOT EXISTS internal_assessment_participant_quizzes_v2 (
                assessment_id BIGINT NOT NULL,
                participant_id BIGINT NOT NULL,
                quiz_id INTEGER NOT NULL,
                handling_status VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (handling_status IN ('pending','zero','exclude')),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (assessment_id, participant_id, quiz_id),
                FOREIGN KEY (assessment_id, participant_id)
                    REFERENCES internal_assessment_participants_v2(assessment_id, participant_id) ON DELETE CASCADE,
                FOREIGN KEY (assessment_id, quiz_id)
                    REFERENCES internal_assessment_quizzes(assessment_id, quiz_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS internal_assessment_participant_results_v2 (
                assessment_id BIGINT NOT NULL,
                participant_id BIGINT NOT NULL,
                average_percentage NUMERIC(7,3) NOT NULL,
                final_marks NUMERIC(7,2) NOT NULL,
                calculation_details JSONB NOT NULL DEFAULT '[]'::jsonb,
                finalized_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (assessment_id, participant_id),
                FOREIGN KEY (assessment_id, participant_id)
                    REFERENCES internal_assessment_participants_v2(assessment_id, participant_id) ON DELETE CASCADE
            )""",
            "CREATE INDEX IF NOT EXISTS idx_internal_assessments_teacher ON internal_assessments(teacher_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_ia_v2_participants_assessment ON internal_assessment_participants_v2(assessment_id, UPPER(BTRIM(roll_number)))",
            "CREATE INDEX IF NOT EXISTS idx_ia_v2_results_assessment ON internal_assessment_participant_results_v2(assessment_id)",
        ]
        try:
            # Serialize first-time schema setup across multiple web workers.
            cursor.execute("SELECT pg_advisory_xact_lock(824731, 20261110)")
            for statement in statements:
                cursor.execute(statement)
            conn.commit()
            _IA_SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise


def _internal_assessment_teacher():
    return session.get("teacher_id")


def _calculate_internal_assessment(student_rows, quiz_rows, method, final_out_of):
    """Calculate a weighted total or quiz-percentage average for one participant."""
    included, pending, details = [], [], []

    for quiz in quiz_rows:
        qid = int(quiz["quiz_id"])
        item = student_rows.get(qid, {})
        attempts = item.get("attempts", [])
        handling = item.get("handling_status", "pending")
        maximum = float(quiz.get("total_questions") or 0)

        if handling == "exclude":
            details.append({"quiz_id": qid, "title": quiz["title"], "status": "excluded"})
            continue
        if handling == "zero":
            included.append({"percentage": 0.0, "average_score": 0.0, "max_marks": maximum})
            details.append({
                "quiz_id": qid, "title": quiz["title"], "status": "zero",
                "percentage": 0.0, "average_score": 0.0, "max_marks": maximum,
            })
            continue
        if not attempts:
            pending.append(quiz["title"])
            details.append({"quiz_id": qid, "title": quiz["title"], "status": "pending"})
            continue

        scores = [float(a.get("score") or 0) for a in attempts]
        percentages = [
            float(a["percentage"]) if a.get("percentage") is not None
            else ((float(a.get("score") or 0) / maximum) * 100 if maximum else 0.0)
            for a in attempts
        ]
        avg_score = sum(scores) / len(scores)
        avg_percentage = sum(percentages) / len(percentages)
        included.append({"percentage": avg_percentage, "average_score": avg_score, "max_marks": maximum})
        details.append({
            "quiz_id": qid, "title": quiz["title"], "status": "attempted",
            "percentage": round(avg_percentage, 2), "average_score": round(avg_score, 2),
            "max_marks": maximum, "attempt_count": len(attempts),
        })

    if pending:
        return {"pending": pending, "average_percentage": None, "final_marks": None, "details": details}
    if not included:
        return {"pending": [], "average_percentage": None, "final_marks": None,
                "details": details, "error": "No quiz is included in this calculation."}

    if method == "total_marks_average":
        total_max = sum(x["max_marks"] for x in included)
        average_percentage = (sum(x["average_score"] for x in included) / total_max * 100) if total_max else 0.0
    else:
        average_percentage = sum(x["percentage"] for x in included) / len(included)

    return {
        "pending": [], "average_percentage": round(average_percentage, 3),
        "final_marks": round(average_percentage * int(final_out_of) / 100, 2),
        "details": details,
    }


def _assessment_participant_data(cursor, assessment_id, quiz_ids):
    """Fetch participant roster and matching registered + guest submitted attempts."""
    cursor.execute(
        """
        SELECT participant_id, student_id, participant_name, roll_number,
               course, department, semester, participant_type
        FROM internal_assessment_participants_v2
        WHERE assessment_id=%s
        ORDER BY UPPER(BTRIM(roll_number)), participant_name
        """,
        (assessment_id,),
    )
    participants = cursor.fetchall()

    cursor.execute(
        """
        SELECT p.participant_id, qa.quiz_id, qa.attempt_id, qa.score,
               qa.percentage, qa.submitted_at, qa.attempt_mode
        FROM internal_assessment_participants_v2 p
        JOIN quiz_attempts qa
          ON qa.quiz_id = ANY(%s::int[])
         AND qa.status = 'submitted'
         AND (
              (p.student_id IS NOT NULL AND qa.student_id = p.student_id)
              OR
              UPPER(BTRIM(COALESCE(qa.roll_number, ''))) = UPPER(BTRIM(p.roll_number))
         )
        WHERE p.assessment_id=%s
        ORDER BY qa.submitted_at, qa.attempt_id
        """,
        (quiz_ids, assessment_id),
    )
    attempts = {}
    for row in cursor.fetchall():
        attempts.setdefault((int(row["participant_id"]), int(row["quiz_id"])), []).append(row)
    return participants, attempts


@teacher.route("/internal_assessment", methods=["GET"])
def internal_assessment_list():
    if not teacher_logged_in():
        return redirect("/")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _ensure_internal_assessment_schema(conn, cursor)
        cursor.execute(
            """
            SELECT ia.*, COUNT(DISTINCT iaq.quiz_id) AS quiz_count,
                   COUNT(DISTINCT iap.participant_id) AS student_count
            FROM internal_assessments ia
            LEFT JOIN internal_assessment_quizzes iaq ON iaq.assessment_id=ia.assessment_id
            LEFT JOIN internal_assessment_participants_v2 iap ON iap.assessment_id=ia.assessment_id
            WHERE ia.teacher_id=%s
            GROUP BY ia.assessment_id
            ORDER BY ia.created_at DESC
            """,
            (session["teacher_id"],),
        )
        assessments = cursor.fetchall()
        return render_template("internal_assessment_list.html", assessments=assessments)
    except Exception:
        conn.rollback()
        current_app.logger.exception("Internal assessment list failed")
        return render_template(
            "internal_assessment_error.html",
            page_title="Internal Assessments",
            message="Internal Assessment workspace load nahi ho saka.",
            detail=str(__import__("sys").exc_info()[1])[:500],
        ), 500
    finally:
        cursor.close()
        conn.close()


@teacher.route("/internal_assessment/new", methods=["GET", "POST"])
def internal_assessment_new():
    if not teacher_logged_in():
        return redirect("/")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    teacher_id = session["teacher_id"]
    try:
        _ensure_internal_assessment_schema(conn, cursor)
        if request.method == "POST":
            # Keep the creation request bounded and observable instead of leaving
            # the browser spinner running indefinitely on a slow DB query.
            cursor.execute("SET LOCAL statement_timeout = '60s'")
            title = (request.form.get("title") or "").strip()
            course = (request.form.get("course") or "").strip().upper()
            department = (request.form.get("department") or "").strip()
            semester = (request.form.get("semester") or "").strip()
            method = (request.form.get("calculation_method") or "").strip()
            subjects = sorted({x.strip() for x in request.form.getlist("subjects") if x and x.strip()})
            normalized_subjects = [x.casefold() for x in subjects]
            try:
                final_out_of = int(request.form.get("final_out_of", "0"))
                quiz_ids = sorted({int(x) for x in request.form.getlist("quiz_ids")})
            except (TypeError, ValueError):
                final_out_of, quiz_ids = 0, []

            errors = []
            if not title:
                errors.append("Assessment title enter karein.")
            if not course or not department:
                errors.append("Course aur department select karein.")
            if not semester.isdigit() or not 1 <= int(semester) <= 8:
                errors.append("Semester 1 se 8 ke beech select karein.")
            if not 1 <= final_out_of <= 1000:
                errors.append("Final marks out of 1 se 1000 ke beech enter karein.")
            if method not in ("percentage_average", "total_marks_average"):
                errors.append("Marks Based ya Percentage Based calculation method select karein.")
            if not subjects or not quiz_ids:
                errors.append("Kam se kam ek subject aur ek quiz select karein.")

            if errors:
                for message in errors:
                    flash(message, "error")
            else:
                # Validate that every selected quiz belongs to this teacher and
                # one of the selected subjects before creating any records.
                cursor.execute(
                    """SELECT q.quiz_id
                       FROM quizzes q
                       WHERE q.teacher_id=%s AND q.quiz_id=ANY(%s::int[])
                         AND LOWER(COALESCE(NULLIF(BTRIM(q.subject),''),'General'))=ANY(%s::text[])""",
                    (teacher_id, quiz_ids, normalized_subjects),
                )
                valid_quiz_ids = {int(r["quiz_id"]) for r in cursor.fetchall()}
                if valid_quiz_ids != set(quiz_ids):
                    flash("Selected quizzes mein invalid subject ya kisi doosre teacher ka quiz hai. Subject aur quizzes dobara select karein.", "error")
                else:
                    # Only include registered students who actually submitted at
                    # least one chosen quiz. This avoids adding an entire cohort.
                    cursor.execute(
                        """SELECT DISTINCT s.student_id, s.full_name, s.roll_number,
                                  UPPER(BTRIM(COALESCE(s.roll_number,''))) AS normalized_roll_number
                           FROM students s
                           JOIN quiz_attempts qa ON qa.student_id=s.student_id
                           WHERE qa.quiz_id=ANY(%s::int[])
                             AND qa.status='submitted'
                             AND UPPER(BTRIM(COALESCE(s.course,'')))=%s
                             AND LOWER(BTRIM(COALESCE(s.department,'')))=LOWER(BTRIM(%s))
                             AND REGEXP_REPLACE(COALESCE(s.semester,''), '[^0-9]', '', 'g')=%s
                           ORDER BY normalized_roll_number""",
                        (quiz_ids, course, department, semester),
                    )
                    registered = cursor.fetchall()
                    registered_rolls = {
                        str(r.get("roll_number") or "").strip().upper()
                        for r in registered if str(r.get("roll_number") or "").strip()
                    }

                    # Guest attempts with a roll number matching a registered
                    # student are deliberately merged into that registered row.
                    cursor.execute(
                        """SELECT DISTINCT ON (UPPER(BTRIM(qa.roll_number)))
                                  qa.student_name, qa.roll_number
                           FROM quiz_attempts qa
                           WHERE qa.quiz_id=ANY(%s::int[])
                             AND qa.status='submitted'
                             AND qa.student_id IS NULL
                             AND qa.attempt_mode='guest'
                             AND UPPER(BTRIM(COALESCE(qa.course,'')))=%s
                             AND LOWER(BTRIM(COALESCE(qa.department,'')))=LOWER(BTRIM(%s))
                             AND REGEXP_REPLACE(COALESCE(qa.semester,''), '[^0-9]', '', 'g')=%s
                             AND BTRIM(COALESCE(qa.roll_number,''))<>''
                           ORDER BY UPPER(BTRIM(qa.roll_number)), qa.submitted_at DESC NULLS LAST, qa.attempt_id DESC""",
                        (quiz_ids, course, department, semester),
                    )
                    guest_rows = cursor.fetchall()
                    guest_only = [g for g in guest_rows
                                  if str(g.get("roll_number") or "").strip().upper() not in registered_rolls]

                    if not registered and not guest_only:
                        flash("In selected quizzes, is course/department/semester ke liye koi submitted student attempt nahi mila. Quiz attempts aur cohort details verify karein.", "error")
                    else:
                        try:
                            cursor.execute(
                                """INSERT INTO internal_assessments
                                   (teacher_id, title, course, department, semester, calculation_method, final_out_of)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING assessment_id""",
                                (teacher_id, title, course, department, semester, method, final_out_of),
                            )
                            assessment_id = int(cursor.fetchone()["assessment_id"])
                            cursor.executemany(
                                "INSERT INTO internal_assessment_quizzes (assessment_id, quiz_id) VALUES (%s,%s)",
                                [(assessment_id, qid) for qid in quiz_ids],
                            )
                            if registered:
                                cursor.executemany(
                                    """INSERT INTO internal_assessment_participants_v2
                                       (assessment_id, student_id, participant_name, roll_number, course, department, semester, participant_type)
                                       VALUES (%s,%s,%s,%s,%s,%s,%s,'registered') ON CONFLICT DO NOTHING""",
                                    [(assessment_id, r["student_id"], r["full_name"], r["roll_number"], course, department, semester)
                                     for r in registered],
                                )
                            if guest_only:
                                cursor.executemany(
                                    """INSERT INTO internal_assessment_participants_v2
                                       (assessment_id, student_id, participant_name, roll_number, course, department, semester, participant_type)
                                       VALUES (%s,NULL,%s,%s,%s,%s,%s,'guest') ON CONFLICT DO NOTHING""",
                                    [(assessment_id, r["student_name"], r["roll_number"], course, department, semester)
                                     for r in guest_only],
                                )
                            conn.commit()
                            current_app.logger.info(
                                "Internal assessment %s created: %s quizzes, %s registered and %s unmatched guests",
                                assessment_id, len(quiz_ids), len(registered), len(guest_only)
                            )
                            flash("Internal Assessment successfully create ho gaya. Student marks automatically calculate honge.", "success")
                            return redirect(f"/internal_assessment/{assessment_id}")
                        except Exception:
                            conn.rollback()
                            raise

        cursor.execute(
            """
            SELECT DISTINCT course, department, semester
            FROM (
                SELECT course, department, semester FROM students
                UNION
                SELECT course, department, semester FROM quiz_attempts
                WHERE attempt_mode='guest' AND status='submitted'
            ) cohorts
            WHERE COALESCE(BTRIM(course),'')<>''
              AND COALESCE(BTRIM(department),'')<>''
              AND COALESCE(BTRIM(semester),'')<>''
            ORDER BY course, department, semester
            """
        )
        cohorts = cursor.fetchall()
        course_options = sorted(
            {str(r.get("course") or "").strip() for r in cohorts if str(r.get("course") or "").strip()}
            | {"MCA", "M.Tech", "B.Tech", "B.Sc", "M.Sc", "BCA", "MBA", "BBA", "BA", "MA", "M.Com", "B.Com"},
            key=str.casefold,
        )
        department_options = sorted(
            {str(r.get("department") or "").strip() for r in cohorts if str(r.get("department") or "").strip()}
            | {"Computer Science", "Computer Science & Engineering", "Information Technology",
               "Mathematics", "Physics", "Chemistry", "Electronics", "Electrical Engineering",
               "Mechanical Engineering", "Civil Engineering", "Management", "Commerce"},
            key=str.casefold,
        )
        cursor.execute(
            """SELECT DISTINCT COALESCE(NULLIF(BTRIM(subject),''),'General') AS subject
               FROM quizzes WHERE teacher_id=%s ORDER BY subject""",
            (teacher_id,),
        )
        subjects = [r["subject"] for r in cursor.fetchall()]
        cursor.execute(
            """SELECT q.quiz_id, q.title,
                      COALESCE(NULLIF(BTRIM(q.subject),''),'General') AS subject,
                      COALESCE(NULLIF(q.total_questions,0), qc.question_count, 0) AS total_questions,
                      q.created_at
               FROM quizzes q
               LEFT JOIN (
                   SELECT quiz_id, COUNT(*)::int AS question_count
                   FROM questions GROUP BY quiz_id
               ) qc ON qc.quiz_id=q.quiz_id
               WHERE q.teacher_id=%s ORDER BY q.created_at DESC""",
            (teacher_id,),
        )
        quizzes = cursor.fetchall()
        return render_template(
            "internal_assessment_new.html", quizzes=quizzes, subjects=subjects,
            cohorts=cohorts, course_options=course_options,
            department_options=department_options, semester_options=range(1, 9)
        )
    except Exception:
        conn.rollback()
        current_app.logger.exception("Internal assessment creation failed")
        return render_template(
            "internal_assessment_error.html",
            page_title="Create Internal Assessment",
            message="Assessment create nahi ho saka.",
            detail=str(__import__("sys").exc_info()[1])[:500],
        ), 500
    finally:
        cursor.close()
        conn.close()


@teacher.route("/internal_assessment/<int:assessment_id>", methods=["GET", "POST"])
def internal_assessment_detail(assessment_id):
    if not teacher_logged_in():
        return redirect("/")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _ensure_internal_assessment_schema(conn, cursor)
        cursor.execute(
            "SELECT * FROM internal_assessments WHERE assessment_id=%s AND teacher_id=%s",
            (assessment_id, session["teacher_id"]),
        )
        assessment = cursor.fetchone()
        if not assessment:
            flash("Internal Assessment nahi mila.", "error")
            return redirect("/internal_assessment")
        if request.method == "POST":
            if assessment["status"] == "finalized":
                flash("Finalized assessment lock hai.", "error")
                return redirect(f"/internal_assessment/{assessment_id}")
            cursor.execute("SELECT participant_id FROM internal_assessment_participants_v2 WHERE assessment_id=%s", (assessment_id,))
            participant_ids = [int(r["participant_id"]) for r in cursor.fetchall()]
            cursor.execute("SELECT quiz_id FROM internal_assessment_quizzes WHERE assessment_id=%s", (assessment_id,))
            quiz_ids = [int(r["quiz_id"]) for r in cursor.fetchall()]
            handling_rows = []
            for pid in participant_ids:
                for qid in quiz_ids:
                    value = request.form.get(f"handling_{pid}_{qid}", "pending")
                    if value not in ("pending", "zero", "exclude"):
                        value = "pending"
                    handling_rows.append((assessment_id, pid, qid, value))
            cursor.executemany(
                """INSERT INTO internal_assessment_participant_quizzes_v2
                       (assessment_id, participant_id, quiz_id, handling_status)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (assessment_id, participant_id, quiz_id)
                   DO UPDATE SET handling_status=EXCLUDED.handling_status, updated_at=CURRENT_TIMESTAMP""",
                handling_rows,
            )
            conn.commit()
            flash("Student-wise absent handling save ho gaya.", "success")
            return redirect(f"/internal_assessment/{assessment_id}")

        cursor.execute(
            """SELECT q.quiz_id, q.title,
                      COALESCE(NULLIF(q.total_questions,0), qc.question_count, 0) AS total_questions,
                      q.subject
               FROM internal_assessment_quizzes iaq
               JOIN quizzes q ON q.quiz_id=iaq.quiz_id
               LEFT JOIN (SELECT quiz_id, COUNT(*)::int AS question_count FROM questions GROUP BY quiz_id) qc
                 ON qc.quiz_id=q.quiz_id
               WHERE iaq.assessment_id=%s ORDER BY q.created_at, q.quiz_id""",
            (assessment_id,),
        )
        quizzes = cursor.fetchall()
        quiz_ids = [int(q["quiz_id"]) for q in quizzes]
        participants, attempts_map = _assessment_participant_data(cursor, assessment_id, quiz_ids)
        cursor.execute(
            """SELECT participant_id, quiz_id, handling_status
               FROM internal_assessment_participant_quizzes_v2 WHERE assessment_id=%s""",
            (assessment_id,),
        )
        handling_map = {(int(r["participant_id"]), int(r["quiz_id"])): r["handling_status"] for r in cursor.fetchall()}

        rows, pending_total, completed_total = [], 0, 0
        for participant in participants:
            pid = int(participant["participant_id"])
            per_quiz = {}
            calc_input = {}
            for quiz in quizzes:
                qid = int(quiz["quiz_id"])
                attempts = attempts_map.get((pid, qid), [])
                handling = handling_map.get((pid, qid), "pending")
                percentages = [
                    float(a["percentage"]) if a["percentage"] is not None
                    else (float(a["score"] or 0) / float(quiz.get("total_questions") or 1) * 100)
                    for a in attempts
                ]
                average_score = (sum(float(a.get("score") or 0) for a in attempts) / len(attempts)) if attempts else (0.0 if handling == "zero" else None)
                per_quiz[qid] = {
                    "attempts": attempts,
                    "handling_status": handling,
                    "average_percentage": (sum(percentages) / len(percentages)) if percentages else (0.0 if handling == "zero" else None),
                    "average_score": average_score,
                    "max_marks": float(quiz.get("total_questions") or 0),
                }
                calc_input[qid] = {"attempts": attempts, "handling_status": handling}
                if attempts:
                    completed_total += 1
                elif handling == "pending":
                    pending_total += 1
            calculation = _calculate_internal_assessment(
                calc_input, quizzes, assessment["calculation_method"], assessment["final_out_of"]
            )
            row = dict(participant)
            row["quiz_data"] = per_quiz
            row["calculation"] = calculation
            included_items = [
                item for item in calculation.get("details", [])
                if item.get("status") in ("attempted", "zero")
            ]
            row["total_obtained"] = round(sum(float(item.get("average_score") or 0) for item in included_items), 2)
            row["total_max"] = round(sum(float(item.get("max_marks") or 0) for item in included_items), 2)
            rows.append(row)

        finalized_results = {}
        if assessment["status"] == "finalized":
            cursor.execute(
                """SELECT participant_id, average_percentage, final_marks, calculation_details
                   FROM internal_assessment_participant_results_v2 WHERE assessment_id=%s""",
                (assessment_id,),
            )
            finalized_results = {int(r["participant_id"]): r for r in cursor.fetchall()}
            # A finalized assessment is an immutable snapshot: display its saved
            # quiz-wise values instead of live attempts that may change later.
            for row in rows:
                saved = finalized_results.get(int(row["participant_id"]))
                if not saved:
                    continue
                details = saved.get("calculation_details") or []
                if isinstance(details, str):
                    try:
                        details = json.loads(details)
                    except (TypeError, ValueError):
                        details = []
                total_obtained = 0.0
                total_max = 0.0
                for detail in details:
                    qid = int(detail.get("quiz_id") or 0)
                    if qid not in row["quiz_data"]:
                        continue
                    item = row["quiz_data"][qid]
                    item["snapshot_status"] = detail.get("status")
                    item["display_attempt_count"] = int(detail.get("attempt_count") or 0)
                    if detail.get("status") in ("attempted", "zero"):
                        item["average_score"] = float(detail.get("average_score") or 0)
                        item["max_marks"] = float(detail.get("max_marks") or 0)
                        item["average_percentage"] = float(detail.get("percentage") or 0)
                        total_obtained += item["average_score"]
                        total_max += item["max_marks"]
                    elif detail.get("status") == "excluded":
                        item["handling_status"] = "exclude"
                    elif detail.get("status") == "pending":
                        item["handling_status"] = "pending"
                row["total_obtained"] = round(total_obtained, 2)
                row["total_max"] = round(total_max, 2)
                row["calculation"]["average_percentage"] = float(saved["average_percentage"])
                row["calculation"]["final_marks"] = float(saved["final_marks"])
                row["calculation"]["pending"] = []

        return render_template(
            "internal_assessment_detail.html", assessment=assessment, quizzes=quizzes,
            rows=rows, pending_total=pending_total, completed_total=completed_total,
            finalized_results=finalized_results,
        )
    except Exception:
        conn.rollback()
        current_app.logger.exception("Internal assessment detail failed")
        return render_template(
            "internal_assessment_error.html",
            page_title="Assessment Details",
            message="Assessment details load nahi ho sake.",
            detail=str(__import__("sys").exc_info()[1])[:500],
        ), 500
    finally:
        cursor.close()
        conn.close()


@teacher.route("/internal_assessment/<int:assessment_id>/finalize", methods=["POST"])
def internal_assessment_finalize(assessment_id):
    if not teacher_logged_in():
        return redirect("/")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _ensure_internal_assessment_schema(conn, cursor)
        cursor.execute(
            "SELECT * FROM internal_assessments WHERE assessment_id=%s AND teacher_id=%s FOR UPDATE",
            (assessment_id, session["teacher_id"]),
        )
        assessment = cursor.fetchone()
        if not assessment:
            flash("Assessment nahi mila.", "error")
            return redirect("/internal_assessment")
        if assessment["status"] == "finalized":
            flash("Assessment pehle se finalized hai.", "info")
            return redirect(f"/internal_assessment/{assessment_id}")

        cursor.execute(
            """SELECT q.quiz_id, q.title,
                      COALESCE(NULLIF(q.total_questions,0), qc.question_count, 0) AS total_questions
               FROM internal_assessment_quizzes iaq
               JOIN quizzes q ON q.quiz_id=iaq.quiz_id
               LEFT JOIN (SELECT quiz_id, COUNT(*)::int AS question_count FROM questions GROUP BY quiz_id) qc
                 ON qc.quiz_id=q.quiz_id
               WHERE iaq.assessment_id=%s ORDER BY q.quiz_id""",
            (assessment_id,),
        )
        quizzes = cursor.fetchall()
        quiz_ids = [int(q["quiz_id"]) for q in quizzes]
        participants, attempts_map = _assessment_participant_data(cursor, assessment_id, quiz_ids)
        cursor.execute(
            """SELECT participant_id, quiz_id, handling_status
               FROM internal_assessment_participant_quizzes_v2 WHERE assessment_id=%s""",
            (assessment_id,),
        )
        handling_map = {(int(r["participant_id"]), int(r["quiz_id"])): r["handling_status"] for r in cursor.fetchall()}

        final_rows, unresolved = [], []
        for participant in participants:
            pid = int(participant["participant_id"])
            per_quiz = {
                int(q["quiz_id"]): {
                    "attempts": attempts_map.get((pid, int(q["quiz_id"])), []),
                    "handling_status": handling_map.get((pid, int(q["quiz_id"])), "pending"),
                }
                for q in quizzes
            }
            result = _calculate_internal_assessment(
                per_quiz, quizzes, assessment["calculation_method"], assessment["final_out_of"]
            )
            if result.get("pending") or result.get("error"):
                unresolved.append(participant.get("participant_name") or participant.get("roll_number"))
            else:
                final_rows.append((pid, result))

        if unresolved:
            flash(f"{len(unresolved)} participant(s) ke liye Pending quiz hai. Pending ko Zero/Exclude karein ya attempt complete karwayein.", "error")
            conn.rollback()
            return redirect(f"/internal_assessment/{assessment_id}")

        cursor.execute("DELETE FROM internal_assessment_participant_results_v2 WHERE assessment_id=%s", (assessment_id,))
        cursor.executemany(
            """INSERT INTO internal_assessment_participant_results_v2
                   (assessment_id, participant_id, average_percentage, final_marks, calculation_details)
               VALUES (%s,%s,%s,%s,%s::jsonb)""",
            [(assessment_id, pid, result["average_percentage"], result["final_marks"], json.dumps(result["details"]))
             for pid, result in final_rows],
        )
        cursor.execute(
            "UPDATE internal_assessments SET status='finalized', finalized_at=CURRENT_TIMESTAMP WHERE assessment_id=%s",
            (assessment_id,),
        )
        conn.commit()
        flash("Assessment finalize ho gaya. Marks snapshot save ho chuka hai.", "success")
        return redirect(f"/internal_assessment/{assessment_id}")
    except Exception:
        conn.rollback()
        current_app.logger.exception("Internal assessment finalization failed")
        flash("Assessment finalize nahi ho saka. Server logs check karein.", "error")
        return redirect(f"/internal_assessment/{assessment_id}")
    finally:
        cursor.close()
        conn.close()


@teacher.route("/internal_assessment/<int:assessment_id>/delete", methods=["POST"])
def internal_assessment_delete(assessment_id):
    if not teacher_logged_in():
        return redirect("/")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _ensure_internal_assessment_schema(conn, cursor)
        cursor.execute(
            "DELETE FROM internal_assessments WHERE assessment_id=%s AND teacher_id=%s RETURNING assessment_id",
            (assessment_id, session["teacher_id"]),
        )
        deleted = cursor.fetchone()
        if not deleted:
            conn.rollback()
            flash("Assessment nahi mila ya aapko ise delete karne ki permission nahi hai.", "error")
        else:
            conn.commit()
            flash("Internal Assessment permanently delete ho gaya.", "success")
    except Exception:
        conn.rollback()
        current_app.logger.exception("Internal assessment deletion failed")
        flash("Assessment delete nahi ho saka. Server logs check karein.", "error")
    finally:
        cursor.close()
        conn.close()
    return redirect("/internal_assessment")


@teacher.route("/export_internal_assessment/<int:assessment_id>", methods=["GET"])
def export_internal_assessment(assessment_id):
    if not teacher_logged_in():
        return redirect("/")
    import csv
    import io
    from flask import make_response

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _ensure_internal_assessment_schema(conn, cursor)
        cursor.execute(
            """SELECT ia.title, ia.final_out_of, p.participant_name, p.roll_number,
                      p.course, p.department, p.semester, p.participant_type,
                      r.average_percentage, r.final_marks, r.calculation_details
               FROM internal_assessments ia
               JOIN internal_assessment_participant_results_v2 r ON r.assessment_id=ia.assessment_id
               JOIN internal_assessment_participants_v2 p ON p.participant_id=r.participant_id
               WHERE ia.assessment_id=%s AND ia.teacher_id=%s AND ia.status='finalized'
               ORDER BY UPPER(BTRIM(p.roll_number))""",
            (assessment_id, session["teacher_id"]),
        )
        rows = cursor.fetchall()
        if not rows:
            flash("Finalized assessment results nahi mile.", "error")
            return redirect(f"/internal_assessment/{assessment_id}")
        output = io.StringIO()
        output.write("\ufeff")
        writer = csv.writer(output)
        quiz_titles = []
        for row in rows:
            details = row.get("calculation_details") or []
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except (TypeError, ValueError):
                    details = []
            for item in details:
                title = item.get("title")
                if title and title not in quiz_titles:
                    quiz_titles.append(title)
        writer.writerow([
            "Assessment", "Student Name", "Roll Number", "Course", "Department",
            "Semester", "Source", *[f"{title} (Marks)" for title in quiz_titles],
            "Total Raw Marks", "Average Percentage", "Final Marks", "Maximum Marks"
        ])
        for row in rows:
            details = row.get("calculation_details") or []
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except (TypeError, ValueError):
                    details = []
            detail_by_title = {item.get("title"): item for item in details if item.get("title")}
            raw_total = 0.0
            raw_values = []
            for title in quiz_titles:
                item = detail_by_title.get(title, {})
                if item.get("status") == "excluded":
                    raw_values.append("Excluded")
                else:
                    score = float(item.get("average_score") or 0)
                    maximum = float(item.get("max_marks") or 0)
                    raw_total += score
                    raw_values.append(f"{score:g}/{maximum:g}" if maximum else "0")
            writer.writerow([
                row["title"], row["participant_name"], row["roll_number"], row["course"],
                row["department"], row["semester"], row["participant_type"], *raw_values,
                round(raw_total, 2), row["average_percentage"], row["final_marks"], row["final_out_of"]
            ])
        response = make_response(output.getvalue())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = 'attachment; filename="internal_assessment_results.csv"'
        return response
    finally:
        cursor.close()
        conn.close()
