from utils.ai_generator import generate_questions


questions = generate_questions(
    "Data Structures and Algorithms",
    2,
    2,
    1
)


for i, q in enumerate(questions, 1):

    print(
        f"{i}. "
        f"[{q['difficulty']}] "
        f"{q['question']}"
    )