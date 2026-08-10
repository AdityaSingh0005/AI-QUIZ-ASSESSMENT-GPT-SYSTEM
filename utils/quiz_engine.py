from database import get_db_connection



def get_quiz_questions(quiz_id):

    db = get_db_connection()

    cursor = db.cursor(dictionary=True)


    cursor.execute(
        """
        SELECT *
        FROM questions
        WHERE quiz_id=%s
        ORDER BY question_id
        """,
        (quiz_id,)
    )


    questions = cursor.fetchall()


    return questions



def calculate_score(questions, answers):

    score = 0


    for q in questions:

        q_id = str(q["question_id"])


        if q_id in answers:

            if answers[q_id] == q["correct_option"]:
                score += 1


    return score