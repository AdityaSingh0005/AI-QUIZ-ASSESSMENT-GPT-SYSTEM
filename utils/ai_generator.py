
import json
import ollama


def generate_questions(topic, easy, medium, hard):

    total = easy + medium + hard

    if total <= 0:
        raise Exception("Total questions must be greater than 0")


    def generate_by_difficulty(difficulty, count):

        if count <= 0:
            return []


        prompt = f"""
Generate exactly {count} multiple-choice questions about:

{topic}

Difficulty level: {difficulty}

IMPORTANT:

Return ONLY a JSON object containing a key called "questions".

The value of "questions" MUST be an array.

Do not write explanations.
Do not write markdown.
Do not write headings.

Every question must contain:

question
option_a
option_b
option_c
option_d
correct_option
difficulty

correct_option must be exactly:
A, B, C, or D

difficulty must be exactly:
{difficulty}

Generate exactly {count} questions.

Example format:

{{
    "questions": [
        {{
            "question": "What is a stack?",
            "option_a": "LIFO data structure",
            "option_b": "FIFO data structure",
            "option_c": "Database",
            "option_d": "Operating System",
            "correct_option": "A",
            "difficulty": "{difficulty}"
        }}
    ]
}}
"""


        for attempt in range(1, 4):

            print(
                f"🤖 Generating {difficulty} "
                f"questions - attempt {attempt}/3"
            )


            try:

                response = ollama.chat(

                    model="llama3.2:3b",

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


                content = response[
                    "message"
                ][
                    "content"
                ].strip()


                print(
                    f"\n========== {difficulty.upper()} RESPONSE =========="
                )

                print(content)

                print(
                    "========================================\n"
                )


                # Remove markdown if present

                content = content.replace(
                    "```json",
                    ""
                )

                content = content.replace(
                    "```",
                    ""
                ).strip()


                try:

                    data = json.loads(content)

                except json.JSONDecodeError:

                    print(
                        "❌ Invalid JSON"
                    )

                    continue


                # We asked for:
                #
                # {
                #   "questions": [...]
                # }

                if not isinstance(data, dict):

                    print(
                        "❌ Response is not JSON object"
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


                if len(questions) != count:

                    print(
                        f"❌ Expected {count} "
                        f"questions but got "
                        f"{len(questions)}"
                    )

                    continue


                # Validate questions

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


                for index, q in enumerate(
                    questions
                ):

                    if not isinstance(
                        q,
                        dict
                    ):

                        valid = False

                        print(
                            f"❌ Question "
                            f"{index + 1} invalid"
                        )

                        break


                    for key in required_keys:

                        if key not in q:

                            valid = False

                            print(
                                f"❌ Question "
                                f"{index + 1} "
                                f"missing {key}"
                            )

                            break


                    if not valid:
                        break


                    # Normalize correct option

                    q["correct_option"] = str(
                        q["correct_option"]
                    ).strip().upper()


                    if q[
                        "correct_option"
                    ] not in [

                        "A",
                        "B",
                        "C",
                        "D"

                    ]:

                        valid = False

                        print(
                            "❌ Invalid "
                            "correct option"
                        )

                        break


                    # Normalize difficulty

                    q["difficulty"] = str(
                        q["difficulty"]
                    ).strip().capitalize()


                    if q[
                        "difficulty"
                    ] != difficulty:

                        valid = False

                        print(
                            f"❌ Expected "
                            f"{difficulty} "
                            f"question but got "
                            f"{q['difficulty']}"
                        )

                        break


                if not valid:

                    continue


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


        raise Exception(
            f"Failed to generate "
            f"{difficulty} questions "
            f"after 3 attempts"
        )


    # =====================================
    # Generate separately
    # =====================================

    print("\n==============================")
    print("OLLAMA QUIZ GENERATION")
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


    medium_questions = generate_by_difficulty(
        "Medium",
        medium
    )


    hard_questions = generate_by_difficulty(
        "Hard",
        hard
    )


    # Combine all questions

    questions = (
        easy_questions
        + medium_questions
        + hard_questions
    )


    # Final safety check

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

