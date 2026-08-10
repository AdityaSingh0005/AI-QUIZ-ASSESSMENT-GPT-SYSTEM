from flask import Blueprint, render_template, redirect, session, request
from utils.quiz_engine import get_quiz_questions
from database import get_db_connection


student = Blueprint("student", __name__)


@student.route("/student_dashboard")
def student_dashboard():

    if "student_id" not in session:
        return redirect("/")

    return render_template(
        "student_dashboard.html",
        name=session["name"]
    )



@student.route("/available_quizzes")
def available_quizzes():

    if "student_id" not in session:
        return redirect("/")


    db = get_db_connection()

    cursor = db.cursor(dictionary=True)


    cursor.execute("""
        SELECT quiz_id, title, total_questions
        FROM quizzes
        ORDER BY quiz_id DESC
    """)


    quizzes = cursor.fetchall()


    return render_template(
        "available_quizzes.html",
        quizzes=quizzes
    )



@student.route("/start_quiz/<int:quiz_id>")
def start_quiz(quiz_id):

    if "student_id" not in session:
        return redirect("/")


    questions = get_quiz_questions(quiz_id)


    session["quiz_id"] = quiz_id
    session["questions"] = questions
    session["current_question"] = 0
    session["answers"] = {}


    return redirect("/quiz")



@student.route("/quiz", methods=["GET","POST"])
def quiz():

    questions = session["questions"]

    index = session["current_question"]


    if request.method == "POST":

        answer = request.form.get("answer")

        question_id = str(
            questions[index]["question_id"]
        )


        session["answers"][question_id] = answer


        if index < len(questions)-1:

            session["current_question"] = index + 1

            return redirect("/quiz")

        else:

            return redirect("/submit_quiz")



    question = questions[index]


    return render_template(
        "quiz.html",
        question=question,
        number=index+1,
        total=len(questions)
    )



@student.route("/submit_quiz")
def submit_quiz():

    if "student_id" not in session:
        return redirect("/")


    questions = session["questions"]
    answers = session["answers"]

    score = 0
    db = get_db_connection()

    cursor = db.cursor()

    for q in questions:

        q_id = q["question_id"]

        selected = answers.get(str(q_id))


        cursor.execute(
            """
            INSERT INTO student_answers
            (student_id, quiz_id, question_id, selected_option)
            VALUES(%s,%s,%s,%s)
            """,
            (
                session["student_id"],
                session["quiz_id"],
                q_id,
                selected
            )
        )


    for q in questions:

        q_id = str(q["question_id"])


        if q_id in answers:

            if answers[q_id] == q["correct_option"]:
                score += 1



    total = len(questions)


    percentage = (score / total) * 100



    


    cursor.execute(
        """
        INSERT INTO results
        (student_id, quiz_id, score, percentage)
        VALUES(%s,%s,%s,%s)
        """,
        (
            session["student_id"],
            session["quiz_id"],
            score,
            percentage
        )
    )


    db.commit()


    return render_template(
        "result.html",
        score=score,
        total=total,
        percentage=percentage
    )
    
@student.route("/my_results")
def my_results():

    if "student_id" not in session:
        return redirect("/")


    db = get_db_connection()

    cursor = db.cursor(dictionary=True)


    cursor.execute(
        """
        SELECT 
            quizzes.title,
            results.score,
            results.percentage,
            results.submitted_at

        FROM results

        JOIN quizzes
        ON results.quiz_id = quizzes.quiz_id

        WHERE results.student_id=%s

        ORDER BY results.submitted_at DESC
        """,
        (session["student_id"],)
    )


    results = cursor.fetchall()


    return render_template(
        "my_results.html",
        results=results
    )

@student.route("/leaderboard")
def leaderboard():

    if "student_id" not in session:
        return redirect("/")

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            students.full_name,
            students.roll_number,
            ROUND(AVG(results.percentage),2) AS average_percentage,
            COUNT(results.result_id) AS total_attempts

        FROM students

        JOIN results
        ON students.student_id = results.student_id

        GROUP BY students.student_id

        ORDER BY average_percentage DESC
    """)

    leaderboard = cursor.fetchall()

    return render_template(
        "leaderboard.html",
        leaderboard=leaderboard
    )
    
@student.route("/student_profile")
def student_profile():

    if "student_id" not in session:
        return redirect("/")

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # Student Details
    cursor.execute("""
        SELECT
            full_name,
            roll_number,
            department,
            semester,
            section,
            created_at
        FROM students
        WHERE student_id=%s
    """, (session["student_id"],))

    student = cursor.fetchone()

    # Statistics
    cursor.execute("""
        SELECT
            COUNT(*) AS total_quizzes,
            MAX(score) AS best_score,
            ROUND(AVG(percentage),2) AS average_percentage
        FROM results
        WHERE student_id=%s
    """, (session["student_id"],))

    stats = cursor.fetchone()

    return render_template(
        "student_profile.html",
        student=student,
        stats=stats
    )