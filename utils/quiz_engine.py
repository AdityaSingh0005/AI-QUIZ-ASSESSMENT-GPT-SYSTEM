from database import get_db_connection
from psycopg2.extras import RealDictCursor

def get_quiz_questions(quiz_id):
    """Load quiz questions directly from PostgreSQL."""
    db = get_db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)

    try:
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
                difficulty,
                explanation
            FROM questions
            WHERE quiz_id = %s
            ORDER BY question_id
            """,
            (quiz_id,)
        )

        return cursor.fetchall()

    finally:
        cursor.close()
        db.close()


def get_attempt_answers(attempt_id):
    """Retrieve saved answers for one quiz attempt."""
    if not attempt_id:
        return {}

    db = get_db_connection()
    cursor = db.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """
            SELECT question_id, selected_option
            FROM student_answers
            WHERE attempt_id = %s
            ORDER BY question_id
            """,
            (attempt_id,)
        )

        rows = cursor.fetchall()

        answers = {}

        for row in rows:
            question_id = str(row["question_id"])
            answers[question_id] = row["selected_option"]

        return answers

    finally:
        cursor.close()
        db.close()


def calculate_score(questions, answers):
    """Calculate the score from question records and saved answers."""
    score = 0

    for question in questions:
        question_id = str(question["question_id"])
        selected = answers.get(question_id)

        if selected is None:
            continue

        selected = str(selected).strip().upper()
        correct = str(
            question["correct_option"]
        ).strip().upper()

        if selected == correct:
            score += 1

    return score