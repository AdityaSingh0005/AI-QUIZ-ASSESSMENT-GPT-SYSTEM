import json
import os
from ollama import Client


# ============================================================
# OLLAMA CLOUD CLIENT
# ============================================================

OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")

if not OLLAMA_API_KEY:
    raise Exception(
        "OLLAMA_API_KEY environment variable is not set"
    )


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

    # ========================================================
    # TOTAL
    # ========================================================

    total = easy + medium + hard

    if total <= 0:
        raise Exception(
            "Total questions must be greater than 0"
        )


    print("\n")
    print("=" * 70)
    print("🤖 OLLAMA CLOUD QUIZ GENERATION")
    print("=" * 70)

    print("📌 Topic:", topic)
    print("📌 Easy:", easy)
    print("📌 Medium:", medium)
    print("📌 Hard:", hard)
    print("📌 Total:", total)

    print("=" * 70)


    # ========================================================
    # GENERATION PROMPT
    # ========================================================

    prompt = f"""
Generate exactly {total} multiple-choice questions about:

{topic}

DIFFICULTY DISTRIBUTION:

Easy: {easy}
Medium: {medium}
Hard: {hard}

IMPORTANT RULES:

1. Return ONLY valid JSON.
2. Do not write markdown.
3. Do not use ```json.
4. Do not write explanations outside JSON.
5. Generate EXACTLY {total} questions.
6. The difficulty distribution MUST be EXACTLY:

Easy = {easy}
Medium = {medium}
Hard = {hard}

7. Every question MUST contain exactly these fields:

question
option_a
option_b
option_c
option_d
correct_option
difficulty
explanation

8. correct_option MUST be exactly one of:

A
B
C
D

9. difficulty MUST be exactly one of:

Easy
Medium
Hard

10. Every question must have four different options.
11. Exactly one option must be correct.
12. Questions must be relevant to the requested topic.
13. Easy questions should test basic concepts.
14. Medium questions should test understanding and application.
15. Hard questions should test deeper understanding and reasoning.
16. Do not duplicate questions.
17. Do not create empty fields.
18. Keep the explanation concise and relevant.
19. Do not generate extra questions.

JSON FORMAT:

{{
    "questions": [
        {{
            "question": "Question text?",
            "option_a": "Option A",
            "option_b": "Option B",
            "option_c": "Option C",
            "option_d": "Option D",
            "correct_option": "A",
            "difficulty": "Easy",
            "explanation": "Option A is correct because it represents the fundamental concept being tested."
        }}
    ]
}}

REMEMBER:

Generate exactly {total} questions.

Easy: exactly {easy}
Medium: exactly {medium}
Hard: exactly {hard}
"""


    # ========================================================
    # RETRY
    # ========================================================

    max_attempts = 3


    for attempt in range(1, max_attempts + 1):

        print("\n")
        print("=" * 70)

        print(
            f"🤖 GENERATING COMPLETE QUIZ "
            f"- ATTEMPT {attempt}/{max_attempts}"
        )

        print("=" * 70)


        try:

            # ==================================================
            # OLLAMA REQUEST
            # ==================================================

            response = client.chat(

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


            # ==================================================
            # GET CONTENT
            # ==================================================

            content = response["message"]["content"]


            if not content:

                print(
                    "❌ Ollama returned empty response."
                )

                continue


            content = content.strip()


            print("\n")
            print("=" * 70)
            print("========== AI RESPONSE ==========")
            print("=" * 70)

            print(content)

            print("=" * 70)


            # ==================================================
            # REMOVE MARKDOWN IF PRESENT
            # ==================================================

            if content.startswith("```json"):

                content = content[
                    len("```json"):
                ]


            if content.startswith("```"):

                content = content[
                    len("```"):
                ]


            if content.endswith("```"):

                content = content[
                    :-3
                ]


            content = content.strip()


            # ==================================================
            # PARSE JSON
            # ==================================================

            try:

                data = json.loads(content)

            except json.JSONDecodeError as e:

                print(
                    "❌ Invalid JSON:",
                    e
                )

                continue


            # ==================================================
            # CHECK OBJECT
            # ==================================================

            if not isinstance(data, dict):

                print(
                    "❌ AI response is not a JSON object."
                )

                continue


            # ==================================================
            # CHECK QUESTIONS KEY
            # ==================================================

            if "questions" not in data:

                print(
                    "❌ 'questions' key missing."
                )

                continue


            questions = data["questions"]


            # ==================================================
            # CHECK LIST
            # ==================================================

            if not isinstance(
                questions,
                list
            ):

                print(
                    "❌ 'questions' is not a list."
                )

                continue


            # ==================================================
            # REQUIRED FIELDS
            # ========================================================

            required_fields = [
                "question",
                "option_a",
                "option_b",
                "option_c",
                "option_d",
                "correct_option",
                "difficulty",
                "explanation"
            ]


            # ==================================================
            # HANDLE EXTRA QUESTIONS
            # ==================================================

            if len(questions) > total:

                print(
                    f"⚠️ AI generated extra questions."
                )

                print(
                    f"Expected: {total}"
                )

                print(
                    f"Received: {len(questions)}"
                )

                print(
                    "🔧 Attempting to safely remove extra questions "
                    "while preserving difficulty distribution..."
                )


                # ----------------------------------------------
                # SEPARATE QUESTIONS BY DIFFICULTY
                # ----------------------------------------------

                easy_questions = []

                medium_questions = []

                hard_questions = []


                for q in questions:

                    if not isinstance(q, dict):

                        continue


                    difficulty = str(
                        q.get("difficulty", "")
                    ).strip().capitalize()


                    if difficulty == "Easy":

                        easy_questions.append(q)


                    elif difficulty == "Medium":

                        medium_questions.append(q)


                    elif difficulty == "Hard":

                        hard_questions.append(q)


                # ----------------------------------------------
                # CHECK WHETHER EACH DIFFICULTY HAS ENOUGH
                # QUESTIONS
                # ----------------------------------------------

                if (
                    len(easy_questions) >= easy
                    and
                    len(medium_questions) >= medium
                    and
                    len(hard_questions) >= hard
                ):

                    # ------------------------------------------
                    # SELECT EXACT REQUIRED NUMBER
                    # ------------------------------------------

                    selected_easy = easy_questions[:easy]

                    selected_medium = medium_questions[:medium]

                    selected_hard = hard_questions[:hard]


                    # ------------------------------------------
                    # COMBINE
                    # ------------------------------------------

                    questions = (
                        selected_easy
                        + selected_medium
                        + selected_hard
                    )


                    print(
                        "✅ Extra questions removed successfully."
                    )

                    print(
                        f"✅ Easy selected: {len(selected_easy)}"
                    )

                    print(
                        f"✅ Medium selected: {len(selected_medium)}"
                    )

                    print(
                        f"✅ Hard selected: {len(selected_hard)}"
                    )

                    print(
                        f"✅ Final question count: {len(questions)}"
                    )


                else:

                    print(
                        "❌ Cannot safely remove extra questions."
                    )

                    print(
                        "❌ Difficulty distribution is insufficient."
                    )

                    print(
                        f"Available Easy: {len(easy_questions)} "
                        f"(Required: {easy})"
                    )

                    print(
                        f"Available Medium: {len(medium_questions)} "
                        f"(Required: {medium})"
                    )

                    print(
                        f"Available Hard: {len(hard_questions)} "
                        f"(Required: {hard})"
                    )

                    continue


            # ==================================================
            # CHECK TOTAL COUNT AFTER NORMALIZATION
            # ==================================================

            if len(questions) != total:

                print(
                    "❌ Question count mismatch."
                )

                print(
                    f"Expected: {total}"
                )

                print(
                    f"Received: {len(questions)}"
                )

                continue


            # ==================================================
            # VALIDATION
            # ==================================================

            valid = True


            # ==================================================
            # TRACK DUPLICATE QUESTIONS
            # ==================================================

            question_texts = set()


            # ==================================================
            # VALIDATE QUESTIONS
            # ==================================================

            for index, q in enumerate(
                questions,
                start=1
            ):

                print(
                    f"\n🔍 Validating Question {index}"
                )


                # ----------------------------------------------
                # OBJECT CHECK
                # ----------------------------------------------

                if not isinstance(
                    q,
                    dict
                ):

                    print(
                        f"❌ Question {index} "
                        f"is not an object."
                    )

                    valid = False
                    break


                # ----------------------------------------------
                # REQUIRED FIELD CHECK
                # ----------------------------------------------

                for field in required_fields:

                    if field not in q:

                        print(
                            f"❌ Question {index} "
                            f"missing field: {field}"
                        )

                        valid = False
                        break


                    if q[field] is None:

                        print(
                            f"❌ Question {index} "
                            f"has empty field: {field}"
                        )

                        valid = False
                        break


                    if str(
                        q[field]
                    ).strip() == "":

                        print(
                            f"❌ Question {index} "
                            f"has blank field: {field}"
                        )

                        valid = False
                        break


                if not valid:

                    break


                # ----------------------------------------------
                # NORMALIZE QUESTION TEXT
                # ----------------------------------------------

                q["question"] = str(
                    q["question"]
                ).strip()


                # ----------------------------------------------
                # DUPLICATE QUESTION CHECK
                # ----------------------------------------------

                question_key = q[
                    "question"
                ].lower()


                if question_key in question_texts:

                    print(
                        f"❌ Question {index} "
                        f"is a duplicate question."
                    )

                    valid = False
                    break


                question_texts.add(
                    question_key
                )


                # ----------------------------------------------
                # NORMALIZE CORRECT OPTION
                # ----------------------------------------------

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
                        f"❌ Question {index} "
                        f"has invalid correct option:"
                        f" {q['correct_option']}"
                    )

                    valid = False
                    break


                # ----------------------------------------------
                # NORMALIZE DIFFICULTY
                # ----------------------------------------------

                q["difficulty"] = str(
                    q["difficulty"]
                ).strip().capitalize()


                if q["difficulty"] not in [

                    "Easy",
                    "Medium",
                    "Hard"

                ]:

                    print(
                        f"❌ Question {index} "
                        f"has invalid difficulty:"
                        f" {q['difficulty']}"
                    )

                    valid = False
                    break


                # ----------------------------------------------
                # NORMALIZE OPTIONS
                # ----------------------------------------------

                q["option_a"] = str(
                    q["option_a"]
                ).strip()

                q["option_b"] = str(
                    q["option_b"]
                ).strip()

                q["option_c"] = str(
                    q["option_c"]
                ).strip()

                q["option_d"] = str(
                    q["option_d"]
                ).strip()


                # ----------------------------------------------
                # CHECK OPTIONS ARE DIFFERENT
                # ----------------------------------------------

                options = [

                    q["option_a"],

                    q["option_b"],

                    q["option_c"],

                    q["option_d"]

                ]


                if len(
                    set(
                        option.lower()
                        for option in options
                    )
                ) != 4:

                    print(
                        f"❌ Question {index} "
                        f"contains duplicate options."
                    )

                    valid = False
                    break


                # ----------------------------------------------
                # NORMALIZE EXPLANATION
                # ----------------------------------------------

                q["explanation"] = str(
                    q["explanation"]
                ).strip()


            # ==================================================
            # INVALID QUESTION DATA
            # ==================================================

            if not valid:

                print(
                    "❌ Question validation failed."
                )

                continue


            # ==================================================
            # DIFFICULTY COUNT
            # ==================================================

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


            print("\n")
            print("=" * 70)
            print("📊 DIFFICULTY VALIDATION")
            print("=" * 70)

            print(
                f"Requested Easy:   {easy}"
            )

            print(
                f"Generated Easy:   {generated_easy}"
            )

            print()

            print(
                f"Requested Medium: {medium}"
            )

            print(
                f"Generated Medium: {generated_medium}"
            )

            print()

            print(
                f"Requested Hard:   {hard}"
            )

            print(
                f"Generated Hard:   {generated_hard}"
            )

            print("=" * 70)


            # ==================================================
            # EXACT DISTRIBUTION CHECK
            # ==================================================

            if generated_easy != easy:

                print(
                    "❌ Easy distribution mismatch."
                )

                continue


            if generated_medium != medium:

                print(
                    "❌ Medium distribution mismatch."
                )

                continue


            if generated_hard != hard:

                print(
                    "❌ Hard distribution mismatch."
                )

                continue


            # ==================================================
            # FINAL COUNT CHECK
            # ==================================================

            if len(questions) != total:

                print(
                    "❌ Final question count is incorrect."
                )

                continue


            # ==================================================
            # FINAL SUCCESS
            # ==================================================

            print("\n")
            print("=" * 70)
            print("🎉 QUIZ GENERATION SUCCESSFUL")
            print("=" * 70)

            print(
                f"✅ Easy: {generated_easy}"
            )

            print(
                f"✅ Medium: {generated_medium}"
            )

            print(
                f"✅ Hard: {generated_hard}"
            )

            print(
                f"✅ Total: {len(questions)}"
            )

            print("=" * 70)


            return questions


        # ======================================================
        # EXCEPTION
        # ======================================================

        except Exception as e:

            print("\n")
            print("=" * 70)

            print(
                "❌ OLLAMA GENERATION ERROR"
            )

            print(
                "Error type:",
                type(e).__name__
            )

            print(
                "Error:",
                str(e)
            )

            print("=" * 70)


    # ========================================================
    # ALL RETRIES FAILED
    # ========================================================

    raise Exception(
        "AI failed to generate a valid quiz "
        f"after {max_attempts} attempts."
    )