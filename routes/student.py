from flask import Blueprint, render_template, redirect, session, request
from utils.quiz_engine import get_quiz_questions
from database import get_db_connection
from psycopg2.extras import RealDictCursor
import time


student = Blueprint("student", __name__)

# ==========================================
# QUIZ ACCESS CHECK
# ==========================================

def quiz_access_allowed():

    return (
        "student_id" in session
        or
        session.get("guest_attempt") is True
    )


# ==========================================
# STUDENT DASHBOARD
# ==========================================

@student.route("/student_dashboard")
def student_dashboard():

    if "student_id" not in session:
        return redirect("/")

    return render_template(
        "student_dashboard.html",
        name=session["name"]
    )


# ==========================================
# AVAILABLE QUIZZES
# ==========================================

@student.route("/available_quizzes")
def available_quizzes():

    if "student_id" not in session:
        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        # ==========================================
        # ONLY ACTIVE QUIZZES
        # ==========================================

        cursor.execute(
            """
            SELECT
                quiz_id,
                title,
                total_questions,
                duration_minutes,
                question_time_seconds,
                available_from,
                available_until
            FROM quizzes
            WHERE
                available_from <= NOW()
                AND
                (
                    available_until IS NULL
                    OR
                    available_until > NOW()
                )
            ORDER BY quiz_id DESC
            """
        )

        quizzes = cursor.fetchall()

    finally:

        cursor.close()
        db.close()

    return render_template(
        "available_quizzes.html",
        quizzes=quizzes
    )


# ==========================================
# GUEST START QUIZ
# ==========================================

@student.route("/guest_start", methods=["POST"])
def guest_start():

    student_name = request.form.get(
        "student_name",
        ""
    ).strip()

    roll_number = request.form.get(
        "roll_number",
        ""
    ).strip()

    quiz_id = request.form.get(
        "quiz_id",
        ""
    ).strip()

    # ==========================================
    # VALIDATION
    # ==========================================

    if not student_name or not roll_number:

        return """
        <h2>Student details are required.</h2>
        <a href="/">← Back</a>
        """

    if not quiz_id.isdigit():

        return """
        <h2>Invalid Quiz ID.</h2>
        <a href="/">← Back</a>
        """

    quiz_id = int(quiz_id)

    # ==========================================
    # DATABASE
    # ==========================================

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
                available_until

            FROM quizzes

            WHERE quiz_id=%s

            AND available_from <= NOW()

            AND
            (
                available_until IS NULL
                OR available_until > NOW()
            )
            """,
            (quiz_id,)
        )

        quiz = cursor.fetchone()

        if not quiz:

            return """
            <h2>Quiz not available.</h2>
            <a href="/">← Back</a>
            """

        # ==========================================
        # GET QUESTIONS
        # ==========================================

        questions = get_quiz_questions(
            quiz_id
        )

        if not questions:

            return """
            <h2>❌ This quiz has no questions.</h2>
            <a href="/">← Back</a>
            """

        # ==========================================
        # CREATE GUEST ATTEMPT
        # ==========================================

        cursor.execute(
            """
            INSERT INTO quiz_attempts
            (
                quiz_id,
                student_name,
                roll_number,
                attempt_mode,
                started_at,
                status
            )
            VALUES
            (
                %s,
                %s,
                %s,
                'guest',
                NOW(),
                'in_progress'
            )
            RETURNING attempt_id
            """,
            (
                quiz_id,
                student_name,
                roll_number
            )
        )

        attempt = cursor.fetchone()

        db.commit()

        # ==========================================
        # STORE QUIZ SESSION
        # ==========================================

        session["guest_mode"] = True

        session["guest_attempt_id"] = (
            attempt["attempt_id"]
        )

        session["guest_student_name"] = (
            student_name
        )

        session["guest_roll_number"] = (
            roll_number
        )

        session["quiz_id"] = quiz_id

        session["questions"] = questions

        session["current_question"] = 0

        session["answers"] = {}

        session["quiz_duration_minutes"] = (
            quiz["duration_minutes"] or 30
        )

        session["question_time_seconds"] = (
            quiz["question_time_seconds"] or 60
        )

        # ==========================================
        # AVAILABILITY
        # ==========================================

        if quiz["available_until"]:

            session["quiz_available_until"] = (
                quiz["available_until"].timestamp()
            )

        else:

            session["quiz_available_until"] = None

        # ==========================================
        # START TIME
        # ==========================================

        session["quiz_start_time"] = time.time()

        return redirect("/quiz")

    except Exception as e:

        db.rollback()

        print(
            "❌ GUEST START ERROR:",
            e
        )

        return f"""
        <h2>Guest Quiz Error</h2>
        <p>{e}</p>
        <a href="/">← Back to Login</a>
        """

    finally:

        cursor.close()
        db.close()
# ==========================================
# START QUIZ
# ==========================================

@student.route("/start_quiz/<int:quiz_id>")
def start_quiz(quiz_id):

    # ==========================================
    # STUDENT LOGIN CHECK
    # ==========================================

    if not quiz_access_allowed():
        return redirect("/")

    # ==========================================
    # GET QUIZ SETTINGS
    # ==========================================

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
                duration_minutes,
                question_time_seconds,
                available_from,
                available_until
            FROM quizzes
            WHERE
                quiz_id=%s

                AND available_from <= NOW()

                AND
                (
                    available_until IS NULL
                    OR
                    available_until > NOW()
                )
            """,
            (quiz_id,)
        )

        quiz = cursor.fetchone()

    finally:

        cursor.close()
        db.close()

    # ==========================================
    # QUIZ NOT AVAILABLE
    # ==========================================

    if not quiz:

        return """
        <!DOCTYPE html>

        <html>

        <head>

            <meta charset="UTF-8">

            <meta name="viewport"
                  content="width=device-width, initial-scale=1.0">

            <title>
                Quiz Unavailable
            </title>

            <style>

                * {
                    box-sizing: border-box;
                }

                body {

                    font-family:
                        Arial,
                        sans-serif;

                    background:
                        linear-gradient(
                            135deg,
                            #eef2ff,
                            #f8fafc
                        );

                    display: flex;

                    justify-content: center;

                    align-items: center;

                    min-height: 100vh;

                    margin: 0;

                    padding: 20px;
                }

                .box {

                    background: white;

                    padding: 40px;

                    border-radius: 20px;

                    text-align: center;

                    box-shadow:
                        0 20px 50px
                        rgba(0,0,0,0.10);

                    max-width: 460px;

                    width: 100%;
                }

                .icon {

                    font-size: 55px;

                    margin-bottom: 10px;
                }

                h2 {

                    color: #ef4444;

                    margin-bottom: 12px;
                }

                p {

                    color: #64748b;

                    line-height: 1.6;

                    font-size: 15px;
                }

                a {

                    display: inline-block;

                    margin-top: 20px;

                    padding: 13px 22px;

                    background:
                        #4f46e5;

                    color: white;

                    text-decoration: none;

                    border-radius: 11px;

                    font-weight: 600;
                }

                a:hover {

                    background:
                        #4338ca;
                }

            </style>

        </head>

        <body>

            <div class="box">

                <div class="icon">
                    ⏰
                </div>

                <h2>
                    Quiz No Longer Available
                </h2>

                <p>
                    This quiz has expired or is not
                    currently available for students.
                </p>

                <a href="/available_quizzes">
                    ← Back to Available Quizzes
                </a>

            </div>

        </body>

        </html>
        """

    # ==========================================
    # GET QUESTIONS
    # ==========================================

    questions = get_quiz_questions(
        quiz_id
    )

    if not questions:

        return """
        <!DOCTYPE html>

        <html>

        <head>

            <meta charset="UTF-8">

            <title>
                No Questions
            </title>

        </head>

        <body>

            <h2>
                ❌ Quiz has no questions.
            </h2>

            <a href="/available_quizzes">
                ← Back to Available Quizzes
            </a>

        </body>

        </html>
        """

    # ==========================================
    # START QUIZ SESSION
    # ==========================================

    session["quiz_id"] = quiz_id

    session["questions"] = questions

    session["current_question"] = 0

    session["answers"] = {}

    # ==========================================
    # TIMER SETTINGS
    # ==========================================

    session["quiz_duration_minutes"] = (
        quiz["duration_minutes"] or 30
    )

    session["question_time_seconds"] = (
        quiz["question_time_seconds"] or 60
    )

    # ==========================================
    # QUIZ AVAILABILITY
    # ==========================================

    # Store availability information in session
    # so we can protect the quiz during an attempt.

    if quiz["available_until"]:

        session["quiz_available_until"] = (
            quiz["available_until"].timestamp()
        )

    else:

        session["quiz_available_until"] = None

    # ==========================================
    # QUIZ START TIME
    # ==========================================

    session["quiz_start_time"] = time.time()

    return redirect("/quiz")


# ==========================================
# QUIZ
# ==========================================

@student.route("/quiz", methods=["GET", "POST"])
def quiz():

    # ==========================================
    # LOGIN OR GUEST ACCESS
    # ==========================================

    if not quiz_access_allowed():

        return redirect("/")

    questions = session.get(
        "questions",
        []
    )

    if not questions:

        return redirect(
            "/available_quizzes"
        )

    index = session.get(
        "current_question",
        0
    )

    # ==========================================
    # SAFETY CHECK
    # ==========================================

    if index >= len(questions):

        return redirect(
            "/submit_quiz"
        )

    # ==========================================
    # CHECK QUIZ AVAILABILITY
    # ==========================================

    quiz_available_until = session.get(
        "quiz_available_until"
    )

    if quiz_available_until is not None:

        if time.time() >= quiz_available_until:

            # Quiz availability has expired.
            # Automatically submit the quiz.

            return redirect(
                "/submit_quiz"
            )

    # ==========================================
    # OVERALL QUIZ TIMER
    # ==========================================

    start_time = session.get(
        "quiz_start_time"
    )

    duration_minutes = session.get(
        "quiz_duration_minutes",
        30
    )

    total_duration = (
        duration_minutes * 60
    )

    # ==========================================
    # CHECK TOTAL QUIZ TIME
    # ==========================================

    if start_time:

        elapsed_time = (
            time.time() - start_time
        )

        if elapsed_time >= total_duration:

            return redirect(
                "/submit_quiz"
            )

    # ==========================================
    # POST ANSWER
    # ==========================================

    if request.method == "POST":

    # ==========================================
    # CHECK QUESTION TIMER
    # ==========================================

        question_start_time = session.get(
        "question_start_time"
    )

    question_time_expired = False

    if question_start_time:

        question_elapsed_time = (
            time.time()
            -
            question_start_time
        )

        if (
            
            question_elapsed_time >= question_time_seconds # type: ignore
        ):

            question_time_expired = True

    # ==========================================
    # ANSWER
    # ==========================================

    answer = request.form.get(
        "answer"
    )

    question_id = str(
        questions[index]["question_id"]
    )

    answers = session.get(
        "answers",
        {}
    )

    # ==========================================
    # TIMER EXPIRED
    # ==========================================

    if question_time_expired:

        # No answer is saved when time expires.

        answers[question_id] = None

    else:

        answers[question_id] = answer

    session["answers"] = answers

    # ==========================================
    # NEXT QUESTION
    # ==========================================

    if index < len(questions) - 1:

        session["current_question"] = (
            index + 1
        )

        # ======================================
        # RESET QUESTION TIMER
        # ======================================

        session["question_start_time"] = (
            time.time()
        )

        return redirect("/quiz")

    # ==========================================
    # LAST QUESTION
    # ==========================================

    return redirect(
        "/submit_quiz"
    )

    question_id = str(
            questions[index]["question_id"]
        )

    answers = session.get(
            "answers",
            {}
        )

    answers[question_id] = answer

    session["answers"] = answers

        # ==========================================
        # NEXT QUESTION
        # ==========================================

    if index < len(questions) - 1:

            session["current_question"] = (
                index + 1
            )
            
            session["question_start_time"] = (
    time.time()
)

            return redirect("/quiz")

        # ==========================================
        # LAST QUESTION
        # ==========================================

    return redirect(
            "/submit_quiz"
        )

    # ==========================================
    # CURRENT QUESTION
    # ==========================================

    question = questions[index]

    # ==========================================
    # REMAINING QUIZ TIME
    # ==========================================

    remaining_seconds = total_duration

    if start_time:

        elapsed_time = (
            time.time() - start_time
        )

        remaining_seconds = max(
            0,
            int(
                total_duration -
                elapsed_time
            )
        )

    # ==========================================
    # QUESTION TIMER
    # ==========================================

    question_time_seconds = session.get(
        "question_time_seconds",
        60
    )
    
    
    
    # ==========================================
    # QUESTION TIMER
    # ==========================================

    question_start_time = session.get(
                "question_start_time"
            )

    if not question_start_time:

                question_start_time = time.time()

                session["question_start_time"] = (
                    question_start_time
                )


                question_elapsed_time = (
                time.time() - question_start_time
            )


    question_remaining_seconds = max(
                0,
                int(
                    question_time_seconds -
                    question_elapsed_time
                )
            )

    # ==========================================
    # RENDER QUIZ
    # ==========================================

    return render_template(
    "quiz.html",

    question=question,

    number=index + 1,

    total=len(questions),

    quiz_duration_minutes=(
        duration_minutes
    ),

    question_time_seconds=(
        question_time_seconds
    ),

    remaining_seconds=(
        remaining_seconds
    ),

    question_remaining_seconds=(
        question_remaining_seconds
    ),

    guest_attempt=session.get(
        "guest_attempt",
        False
    ),

    guest_name=session.get(
        "guest_name"
    ),

    guest_roll_number=session.get(
        "guest_roll_number"
    )
)

# ==========================================
# SUBMIT QUIZ
# ==========================================

@student.route("/submit_quiz")
def submit_quiz():

    # ==========================================
    # LOGIN OR GUEST ACCESS
    # ==========================================

    if not quiz_access_allowed():

        return redirect("/")

    questions = session.get(
        "questions",
        []
    )

    answers = session.get(
        "answers",
        {}
    )

    if not questions:

        return redirect(
            "/available_quizzes"
        )

    score = 0

    db = get_db_connection()

    cursor = db.cursor()

    try:

        # ==========================================
        # SAVE STUDENT ANSWERS
        # ==========================================

        for q in questions:

            q_id = q["question_id"]

            selected = answers.get(
                str(q_id)
            )

        student_id = session.get(
            "student_id"
        )

        guest_name = session.get(
            "guest_name"
        )

        guest_roll_number = session.get(
            "guest_roll_number"
        )
        
        cursor.execute(
    """
    INSERT INTO student_answers
    (
        student_id,
        quiz_id,
        question_id,
        selected_option
    )
    VALUES
    (
        %s,
        %s,
        %s,
        %s
    )
    """,
    (
        student_id,
        session["quiz_id"],
        q_id,
        selected
    )
)

        # ==========================================
        # CALCULATE SCORE
        # ==========================================

        for q in questions:

            q_id = str(
                q["question_id"]
            )

            if q_id in answers:

                if (
                    answers[q_id]
                    ==
                    q["correct_option"]
                ):

                    score += 1

        # ==========================================
        # CALCULATE PERCENTAGE
        # ==========================================

        total = len(questions)

        if total > 0:

            percentage = (
                (score / total) * 100
            )

        else:

            percentage = 0

        # ==========================================
        # SAVE RESULT
        # ==========================================

        cursor.execute(
        """
        INSERT INTO results
        (
            student_id,
            quiz_id,
            score,
            percentage
        )
        VALUES
        (
            %s,
            %s,
            %s,
            %s
        )
        """,
        (
            session.get("student_id"),
            session["quiz_id"],
            score,
            percentage
        )
    )
        
        # ==========================================
        # UPDATE QUIZ ATTEMPT
        # ==========================================

        attempt_id = session.get(
            "quiz_attempt_id"
        )

        if attempt_id:

            cursor.execute(
                """
                UPDATE quiz_attempts

                SET
                    submitted_at = NOW(),
                    status = %s,
                    score = %s,
                    percentage = %s

                WHERE attempt_id = %s
                """,
                (
                    "submitted",
                    score,
                    percentage,
                    attempt_id
                )
            )

        db.commit()

        # ==========================================
        # CLEAR QUIZ SESSION
        # ==========================================

        session.pop(
            "questions",
            None
        )

        session.pop(
            "answers",
            None
        )

        session.pop(
            "current_question",
            None
        )

        session.pop(
            "quiz_start_time",
            None
        )

        session.pop(
            "quiz_duration_minutes",
            None
        )

        session.pop(
            "question_time_seconds",
            None
        )

        session.pop(
            "quiz_available_until",
            None
        )

        session.pop(
            "quiz_id",
            None
        )
        
        session.pop(
                "guest_attempt",
                None
            )

        session.pop(
                "guest_name",
                None
            )

        session.pop(
                "guest_roll_number",
                None
            )

        session.pop(
                "quiz_attempt_id",
                None
            )

        session.pop(
                "question_start_time",
                None
            )

        # ==========================================
        # RESULT PAGE
        # ==========================================

        return render_template(
            "result.html",
            score=score,
            total=total,
            percentage=percentage
        )

    except Exception as e:

        db.rollback()

        print(
            "❌ SUBMIT QUIZ ERROR:",
            e
        )

        return f"""
        <h2>
            ❌ Error while submitting quiz
        </h2>

        <p>
            {e}
        </p>

        <br>

        <a href="/student_dashboard">
            ← Back to Student Dashboard
        </a>
        """

    finally:

        cursor.close()
        db.close()


# ==========================================
# MY RESULTS
# ==========================================

@student.route("/my_results")
def my_results():

    if (
    "student_id" not in session
    and not session.get("guest_mode")
    ):
        
        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    cursor.execute(
        """
        SELECT
            quizzes.title,
            results.score,
            results.percentage,
            results.submitted_at

        FROM results

        JOIN quizzes
        ON results.quiz_id =
           quizzes.quiz_id

        WHERE results.student_id=%s

        ORDER BY results.submitted_at DESC
        """,
        (
            session["student_id"],
        )
    )

    results = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template(
        "my_results.html",
        results=results
    )


# ==========================================
# LEADERBOARD
# ==========================================

@student.route("/leaderboard")
def leaderboard():

    if "student_id" not in session:
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
            ROUND(
                AVG(results.percentage),
                2
            ) AS average_percentage,
            COUNT(results.result_id)
            AS total_attempts

        FROM students

        JOIN results
        ON students.student_id =
           results.student_id

        GROUP BY
            students.student_id,
            students.full_name,
            students.roll_number

        ORDER BY
            average_percentage DESC
        """
    )

    leaderboard = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template(
        "leaderboard.html",
        leaderboard=leaderboard
    )


# ==========================================
# STUDENT PROFILE
# ==========================================

@student.route("/student_profile")
def student_profile():

    if "student_id" not in session:
        return redirect("/")

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    # ==========================================
    # STUDENT DETAILS
    # ==========================================

    cursor.execute(
        """
        SELECT
            full_name,
            roll_number,
            department,
            semester,
            section,
            created_at
        FROM students
        WHERE student_id=%s
        """,
        (
            session["student_id"],
        )
    )

    student_data = cursor.fetchone()

    # ==========================================
    # STATISTICS
    # ==========================================

    cursor.execute(
        """
        SELECT
            COUNT(*) AS total_quizzes,
            MAX(score) AS best_score,
            ROUND(
                AVG(percentage),
                2
            ) AS average_percentage
        FROM results
        WHERE student_id=%s
        """,
        (
            session["student_id"],
        )
    )

    stats = cursor.fetchone()

    cursor.close()
    db.close()

    return render_template(
        "student_profile.html",
        student=student_data,
        stats=stats
    )