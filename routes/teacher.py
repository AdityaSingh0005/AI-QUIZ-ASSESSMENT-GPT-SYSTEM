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

from utils.ai_generator import (
    generate_questions
)

from psycopg2.extras import RealDictCursor

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

        for quiz in quizzes:

            quiz["status"] = get_quiz_status(
                quiz.get("available_from"),
                quiz.get("available_until")
            )

            quiz["calculated_duration_minutes"] = (
                calculate_duration_minutes(
                    quiz.get("total_questions") or 0,
                    quiz.get("question_time_seconds") or 0
                )
            )

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
                q.question_time_seconds,
                q.available_from,
                q.available_until

            FROM quizzes q

            WHERE q.teacher_id=%s

            AND q.available_from <= NOW()

            AND
            (
                q.available_until IS NULL

                OR

                NOW() < q.available_until
            )

            ORDER BY q.available_from ASC
            """,
            (
                teacher_id,
            )
        )

        live_quizzes = cursor.fetchall()

        for quiz in live_quizzes:

            quiz["status"] = "live"

            quiz["calculated_duration_minutes"] = (
                calculate_duration_minutes(
                    quiz.get("total_questions") or 0,
                    quiz.get("question_time_seconds") or 0
                )
            )

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

@teacher.route("/view_results")
def view_results():

    if not teacher_logged_in():

        return redirect("/")

    db = None
    cursor = None

    try:

        db = get_db_connection()

        cursor = db.cursor(
            cursor_factory=RealDictCursor
        )

        cursor.execute(
            """
            SELECT

                qa.attempt_id,

                qa.quiz_id,

                q.title,

                qa.student_id,

                qa.student_name,

                qa.roll_number,

                qa.attempt_mode,

                qa.status,

                qa.score AS attempt_score,

                qa.percentage AS attempt_percentage,

                qa.submitted_at AS attempt_submitted_at,

                r.result_id,

                r.score AS result_score,

                r.percentage AS result_percentage,

                r.submitted_at AS result_submitted_at,

                s.full_name AS registered_name,

                s.roll_number AS registered_roll_number

            FROM quiz_attempts qa

            INNER JOIN quizzes q
                ON qa.quiz_id = q.quiz_id

            LEFT JOIN results r
                ON r.attempt_id = qa.attempt_id

            LEFT JOIN students s
                ON qa.student_id = s.student_id

            WHERE q.teacher_id = %s

            AND qa.status = 'submitted'

            ORDER BY

                COALESCE(
                    qa.submitted_at,
                    r.submitted_at
                ) DESC
            """,
            (
                session["teacher_id"],
            )
        )

        attempts = cursor.fetchall()

        results = []

        for attempt in attempts:

            if (
                attempt.get("attempt_mode") == "guest"
            ):

                full_name = (
                    attempt.get("student_name")
                    or "Guest Student"
                )

            else:

                full_name = (
                    attempt.get("registered_name")
                    or attempt.get("student_name")
                    or "Student"
                )

            if (
                attempt.get("attempt_mode") == "guest"
            ):

                roll_number = (
                    attempt.get("roll_number")
                    or "N/A"
                )

            else:

                roll_number = (
                    attempt.get("registered_roll_number")
                    or attempt.get("roll_number")
                    or "N/A"
                )

            score = (
                attempt.get("result_score")
                if attempt.get("result_score") is not None
                else attempt.get("attempt_score")
            )

            percentage = (
                attempt.get("result_percentage")
                if attempt.get("result_percentage") is not None
                else attempt.get("attempt_percentage")
            )

            submitted_at = (
                attempt.get("result_submitted_at")
                if attempt.get("result_submitted_at") is not None
                else attempt.get("attempt_submitted_at")
            )

            if (
                attempt.get("attempt_mode") == "guest"
            ):

                attempt_type = "Guest"

            else:

                attempt_type = "Registered"

            results.append({

                "result_id":
                    attempt.get("result_id"),

                "attempt_id":
                    attempt.get("attempt_id"),

                "quiz_id":
                    attempt.get("quiz_id"),

                "title":
                    attempt.get("title"),

                "full_name":
                    full_name,

                "roll_number":
                    roll_number,

                "attempt_type":
                    attempt_type,

                "score":
                    score,

                "percentage":
                    percentage,

                "submitted_at":
                    submitted_at

            })

        print("=" * 70)

        print(
            "✅ VIEW RESULTS SUCCESS"
        )

        print(
            f"📊 TOTAL SUBMITTED ATTEMPTS: "
            f"{len(results)}"
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

        return render_template(
            "view_results.html",
            results=results
        )

    except Exception as e:

        if db:

            db.rollback()

        print("=" * 70)

        print(
            "❌ VIEW RESULTS ERROR"
        )

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

        cursor.execute(
            """
            DELETE FROM student_answers

            WHERE quiz_id=%s
            """,
            (
                quiz_id,
            )
        )

        cursor.execute(
            """
            DELETE FROM quiz_attempts

            WHERE quiz_id=%s
            """,
            (
                quiz_id,
            )
        )

        cursor.execute(
            """
            DELETE FROM results

            WHERE quiz_id=%s
            """,
            (
                quiz_id,
            )
        )

        cursor.execute(
            """
            DELETE FROM questions

            WHERE quiz_id=%s
            """,
            (
                quiz_id,
            )
        )

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