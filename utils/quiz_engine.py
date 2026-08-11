
from database import get_db_connection
from psycopg2.extras import RealDictCursor


def get_quiz_questions(quiz_id):

    db = get_db_connection()

    cursor = db.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        cursor.execute(
            """
            SELECT
                question_id,
                quiz_id,
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

        return questions

    finally:

        cursor.close()
        db.close()


def calculate_score(questions, answers):

    score = 0

    for q in questions:

        q_id = str(q["question_id"])

        if q_id in answers:

            if answers[q_id] == q["correct_option"]:
                score += 1

    return score

