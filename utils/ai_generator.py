import json
import os
from ollama import Client


# ============================================================
# OLLAMA CLOUD CLIENT
# ============================================================

OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")

if not OLLAMA_API_KEY:
    raise Exception("OLLAMA_API_KEY environment variable is not set")


client = Client(
    host="https://ollama.com",
    headers={
        "Authorization": f"Bearer {OLLAMA_API_KEY}"
    }
)


# ============================================================
# GENERATE QUESTIONS
# ============================================================

def generate_questions(topic, easy, medium, hard):

    total = easy + medium + hard

    if total <= 0:
        raise Exception(
            "Total questions must be greater than 0"
        )


    # ========================================================
    # GENERATE QUESTIONS BY DIFFICULTY
    # ========================================================

    def generate_by_difficulty(difficulty, count):

        if count <= 0:
            return []


        prompt = f"""
Generate exactly {count} multiple-choice questions about:

{topic}

Difficulty level: {difficulty}

IMPORTANT RULES:

Return ONLY valid JSON.

The JSON must contain exactly one key:

"questions"

The value of "questions" must be an array.

Do not write explanations.
Do not write markdown.
Do not write headings.
Do not use ```.

Every question MUST contain these fields:

question
option_a
option_b
option_c
option_d
correct_option
difficulty

correct_option MUST be exactly one of:

A
B
C
D

difficulty MUST be exactly:

{difficulty}

Generate EXACTLY {count} questions.

JSON FORMAT:

{{
    "questions": [
        {{
            "question": "Example question?",
            "option_a": "Option A",
            "option_b": "Option B",
            "option_c": "Option C",
            "option_d": "Option D",
            "correct_option": "A",
            "difficulty": "{difficulty}"
        }}
    ]
}}
"""


        # ====================================================
        # RETRY 3 TIMES
        # ====================================================

        for attempt in range(1, 4):

            print(
                f"🤖 Generating {difficulty} "
                f"questions - attempt {attempt}/3"
            )


            try:

                response = client.chat(

                    # Ollama cloud model
                    model="gpt-oss:20b",

                    messages=[
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],

                    options={
                        "temperature": 0.1
                    },

                    format="json"
                )


                # =================================================
                # GET RESPONSE CONTENT
                # =================================================

                content = response["message"]["content"]

                if not content:
                    print("❌ Empty AI response")
                    continue


                content = content.strip()


                print(
                    f"\n========== "
                    f"{difficulty.upper()} RESPONSE =========="
                )

                print(content)

                print(
                    "========================================\n"
                )


                # =================================================
                # REMOVE MARKDOWN IF AI ADDS IT
                # =================================================

                content = content.replace(
                    "```json",
                    ""
                )

                content = content.replace(
                    "```",
                    ""
                )

                content = content.strip()


                # =================================================
                # PARSE JSON
                # =================================================

                try:

                    data = json.loads(content)

                except json.JSONDecodeError as e:

                    print(
                        "❌ Invalid JSON:",
                        e
                    )

                    continue


                # =================================================
                # VALIDATE OBJECT
                # =================================================

                if not isinstance(data, dict):

                    print(
                        "❌ AI response is not an object"
                    )

                    continue


                if "questions" not in data:

                    print(
                        "❌ 'questions' key missing"
                    )

                    continue


                questions = data["questions"]


                if not isinstance(
                    questions,
                    list
                ):

                    print(
                        "❌ questions is not a list"
                    )

                    continue


                # =================================================
                # CHECK QUESTION COUNT
                # =================================================

                if len(questions) != count:

                    print(
                        f"❌ Expected {count} "
                        f"questions but got "
                        f"{len(questions)}"
                    )

                    continue


                # =================================================
                # REQUIRED FIELDS
                # =================================================

                required_keys = [

                    "question",
                    "option_a",
                    "option_b",
                    "option_c",
                    "option_d",
                    "correct_option",
                    "difficulty"

                ]


                valid = True


                # =================================================
                # VALIDATE EVERY QUESTION
                # =================================================

                for index, q in enumerate(
                    questions
                ):

                    if not isinstance(
                        q,
                        dict
                    ):

                        print(
                            f"❌ Question "
                            f"{index + 1} is invalid"
                        )

                        valid = False
                        break


                    # Check required keys

                    for key in required_keys:

                        if key not in q:

                            print(
                                f"❌ Question "
                                f"{index + 1} "
                                f"missing {key}"
                            )

                            valid = False
                            break


                    if not valid:
                        break


                    # =================================================
                    # NORMALIZE CORRECT OPTION
                    # =================================================

                    q["correct_option"] = str(
                        q["correct_option"]
                    ).strip().upper()


                    if q["correct_option"] not in [

                        "A",
                        "B",
                        "C",
                        "D"

                    ]:

                        print(
                            f"❌ Question "
                            f"{index + 1} "
                            f"has invalid correct option"
                        )

                        valid = False
                        break


                    # =================================================
                    # NORMALIZE DIFFICULTY
                    # =================================================

                    q["difficulty"] = str(
                        q["difficulty"]
                    ).strip().capitalize()


                    if q["difficulty"] != difficulty:

                        print(
                            f"❌ Question "
                            f"{index + 1}: "
                            f"expected {difficulty}, "
                            f"got {q['difficulty']}"
                        )

                        valid = False
                        break


                if not valid:
                    continue


                # =================================================
                # SUCCESS
                # =================================================

                print(
                    f"✅ {difficulty}: "
                    f"{len(questions)} questions generated"
                )


                return questions


            except Exception as e:

                print(
                    f"❌ Error generating "
                    f"{difficulty}: {e}"
                )


        # ========================================================
        # ALL 3 ATTEMPTS FAILED
        # ========================================================

        raise Exception(
            f"Failed to generate "
            f"{difficulty} questions "
            f"after 3 attempts"
        )


    # ============================================================
    # GENERATE EASY
    # ============================================================

    print("\n==============================")
    print("OLLAMA CLOUD QUIZ GENERATION")
    print("==============================")

    print("Topic:", topic)
    print("Easy:", easy)
    print("Medium:", medium)
    print("Hard:", hard)
    print("Total:", total)

    print("==============================\n")


    easy_questions = generate_by_difficulty(
        "Easy",
        easy
    )


    # ============================================================
    # GENERATE MEDIUM
    # ============================================================

    medium_questions = generate_by_difficulty(
        "Medium",
        medium
    )


    # ============================================================
    # GENERATE HARD
    # ============================================================

    hard_questions = generate_by_difficulty(
        "Hard",
        hard
    )


    # ============================================================
    # COMBINE
    # ============================================================

    questions = (
        easy_questions
        + medium_questions
        + hard_questions
    )


    # ============================================================
    # FINAL CHECK
    # ============================================================

    if len(questions) != total:

        raise Exception(
            f"Expected {total} questions "
            f"but generated {len(questions)}"
        )


    print("\n==============================")
    print("✅ QUIZ GENERATION COMPLETE")
    print("==============================")

    print(
        f"Easy: {len(easy_questions)}"
    )

    print(
        f"Medium: {len(medium_questions)}"
    )

    print(
        f"Hard: {len(hard_questions)}"
    )

    print(
        f"Total: {len(questions)}"
    )

    print("==============================\n")


    return questions