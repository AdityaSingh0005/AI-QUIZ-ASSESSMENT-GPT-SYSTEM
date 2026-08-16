from flask import Blueprint, render_template, redirect, session, request
from utils.quiz_engine import get_quiz_questions
from database import get_db_connection
from psycopg2.extras import RealDictCursor
import time


student = Blueprint("student", __name__)


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

    cursor.execute("""
        SELECT
            quiz_id,
            title,
            total_questions,
            duration_minutes,
            question_time_seconds
        FROM quizzes
        ORDER BY quiz_id DESC
    """)

    quizzes = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template(
        "available_quizzes.html",
        quizzes=quizzes
    )


# ==========================================
# START QUIZ
# ==========================================

@student.route("/start_quiz/<int:quiz_id>")
def start_quiz(quiz_id):

    # Student is not logged in
    if "student_id" not in session:

        # Remember which quiz QR requested
        session["pending_quiz_id"] = quiz_id

        return redirect("/")

    # ==================================
    # GET QUIZ SETTINGS
    # ==================================

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    cursor.execute(
        """
        SELECT
            quiz_id,
            title,
            duration_minutes,
            question_time_seconds
        FROM quizzes
        WHERE quiz_id=%s
        """,
        (quiz_id,)
    )

    quiz = cursor.fetchone()

    cursor.close()
    db.close()

    if not quiz:
        return "Quiz not found."

    # ==================================
    # GET QUESTIONS
    # ==================================

    questions = get_quiz_questions(quiz_id)

    if not questions:
        return "Quiz not found or no questions available."

    # ==================================
    # START QUIZ SESSION
    # ==================================

    session["quiz_id"] = quiz_id

    session["questions"] = questions

    session["current_question"] = 0

    session["answers"] = {}

    # ==================================
    # TIMER SETTINGS
    # ==================================

    session["quiz_duration_minutes"] = (
        quiz["duration_minutes"] or 30
    )

    session["question_time_seconds"] = (
        quiz["question_time_seconds"] or 60
    )

    # ==================================
    # QUIZ START TIME
    # ==================================

    session["quiz_start_time"] = time.time()

    return redirect("/quiz")


# ==========================================
# QUIZ
# ==========================================

@student.route("/quiz", methods=["GET", "POST"])
def quiz():

    if "student_id" not in session:
        return redirect("/")

    questions = session.get(
        "questions",
        []
    )

    if not questions:
        return redirect("/available_quizzes")

    index = session.get(
        "current_question",
        0
    )

    # ==================================
    # SAFETY CHECK
    # ==================================

    if index >= len(questions):

        return redirect("/submit_quiz")

    # ==================================
    # OVERALL QUIZ TIMER
    # ==================================

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

    # ==================================
    # CHECK TOTAL QUIZ TIME
    # ==================================

    if start_time:

        elapsed_time = (
            time.time() - start_time
        )

        if elapsed_time >= total_duration:

            return redirect(
                "/submit_quiz"
            )

    # ==================================
    # POST ANSWER
    # ==================================

    if request.method == "POST":

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

        answers[question_id] = answer

        session["answers"] = answers

        # ==================================
        # NEXT QUESTION
        # ==================================

        if index < len(questions) - 1:

            session["current_question"] = (
                index + 1
            )

            return redirect("/quiz")

        # ==================================
        # LAST QUESTION
        # ==================================

        else:

            return redirect(
                "/submit_quiz"
            )

    # ==================================
    # CURRENT QUESTION
    # ==================================

    question = questions[index]

    # ==================================
    # REMAINING QUIZ TIME
    # ==================================

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

    # ==================================
    # QUESTION TIMER
    # ==================================

    question_time_seconds = session.get(
        "question_time_seconds",
        60
    )

    # ==================================
    # RENDER QUIZ
    # ==================================

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
        )
    )


# ==========================================
# SUBMIT QUIZ
# ==========================================

@student.route("/submit_quiz")
def submit_quiz():

    if "student_id" not in session:
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

        # ==================================
        # SAVE STUDENT ANSWERS
        # ==================================

        for q in questions:

            q_id = q["question_id"]

            selected = answers.get(
                str(q_id)
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
                VALUES (%s,%s,%s,%s)
                """,
                (
                    session["student_id"],
                    session["quiz_id"],
                    q_id,
                    selected
                )
            )

        # ==================================
        # CALCULATE SCORE
        # ==================================

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

        # ==================================
        # CALCULATE PERCENTAGE
        # ==================================

        total = len(questions)

        percentage = (
            (score / total) * 100
        )

        # ==================================
        # SAVE RESULT
        # ==================================

        cursor.execute(
            """
            INSERT INTO results
            (
                student_id,
                quiz_id,
                score,
                percentage
            )
            VALUES (%s,%s,%s,%s)
            """,
            (
                session["student_id"],
                session["quiz_id"],
                score,
                percentage
            )
        )

        db.commit()

        # ==================================
        # CLEAR QUIZ SESSION
        # ==================================

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
        <h2>❌ Error while submitting quiz</h2>

        <p>{e}</p>

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

    if "student_id" not in session:
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

    # ==================================
    # STUDENT DETAILS
    # ==================================

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

    # ==================================
    # STATISTICS
    # ==================================

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