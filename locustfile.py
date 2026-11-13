import os
import re
import json
import time
import uuid
import random

from itertools import count
from html.parser import HTMLParser

from locust import HttpUser, task, between


# ============================================================
# CONFIGURATION
# ============================================================

QUIZ_ID = int(os.getenv("QUIZ_ID", "52"))

RUN_ID = uuid.uuid4().hex[:8]

STUDENT_COUNTER = count(1)

COURSE = "MCA"
DEPARTMENT = "Computer Science"
SEMESTER = "3"


# ============================================================
# HTML HIDDEN INPUT PARSER
# ============================================================

class HiddenInputParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.hidden_fields = {}

    def handle_starttag(self, tag, attrs):

        if tag.lower() != "input":
            return

        attrs = dict(attrs)

        input_type = attrs.get("type", "").lower()

        if input_type == "hidden":

            name = attrs.get("name")
            value = attrs.get("value", "")

            if name:
                self.hidden_fields[name] = value


# ============================================================
# QUESTION ID PARSER
# ============================================================

class QuestionIdParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.question_ids = []

    def handle_starttag(self, tag, attrs):

        attrs = dict(attrs)

        question_id = attrs.get("data-question-id")

        if question_id:

            try:
                question_id = int(question_id)

                if question_id not in self.question_ids:
                    self.question_ids.append(question_id)

            except (ValueError, TypeError):
                pass


def extract_hidden_fields(html):

    parser = HiddenInputParser()
    parser.feed(html)

    return parser.hidden_fields


def extract_question_ids(html):

    parser = QuestionIdParser()
    parser.feed(html)

    question_ids = parser.question_ids

    # Regex fallback
    if not question_ids:

        pattern = r"""data-question-id=["'](\d+)["']"""

        matches = re.findall(pattern, html)

        question_ids = list(dict.fromkeys(
            int(value) for value in matches
        ))

    return question_ids


# ============================================================
# LOCUST STUDENT
# ============================================================

class GuestStudent(HttpUser):

    wait_time = between(0.2, 0.5)

    def on_start(self):

        self.student_number = next(STUDENT_COUNTER)

        self.unique_id = uuid.uuid4().hex[:8]

        self.student_name = (
            f"Locust Student {RUN_ID}-{self.student_number}"
        )

        self.roll_number = (
            f"LOAD-{RUN_ID}-{self.student_number}-{self.unique_id}"
        )

        self.question_ids = []

        self.quiz_started = False

        self.start_guest_attempt()


    # ========================================================
    # START GUEST ATTEMPT
    # ========================================================

    def start_guest_attempt(self):

        start_url = f"/guest_start_quiz/{QUIZ_ID}"

        # ----------------------------------------------------
        # STEP 1: OPEN GUEST REGISTRATION PAGE
        # ----------------------------------------------------

        with self.client.get(
            start_url,
            name="/guest_start_quiz/[quiz_id]",
            catch_response=True
        ) as response:

            if response.status_code != 200:

                print("\n========== GUEST PAGE ERROR ==========")
                print("Status:", response.status_code)
                print("URL:", response.url)
                print("Body:", response.text[:2000])
                print("======================================\n")

                response.failure(
                    f"Guest page failed: {response.status_code}"
                )

                self.stop()
                return

            guest_html = response.text

            response.success()


        # ----------------------------------------------------
        # STEP 2: EXTRACT HIDDEN INPUTS
        # ----------------------------------------------------

        hidden_fields = extract_hidden_fields(guest_html)


        # ----------------------------------------------------
        # STEP 3: FILL ALL STUDENT DETAILS
        # ----------------------------------------------------

        payload = {

            **hidden_fields,

            "quiz_id": str(QUIZ_ID),

            "student_name": self.student_name,

            "roll_number": self.roll_number,

            "course": COURSE,

            "department": DEPARTMENT,

            "semester": SEMESTER
        }


        # ----------------------------------------------------
        # STEP 4: SUBMIT ACTUAL HTML FORM
        # ----------------------------------------------------

        with self.client.post(
            "/guest_start",
            data=payload,
            name="/guest_start",
            catch_response=True,
            allow_redirects=True
        ) as response:

            if response.status_code != 200:

                print("\n========== REGISTRATION ERROR ==========")

                print("Status:", response.status_code)

                print("URL:", response.url)

                print("Student:", self.student_name)

                print("Roll:", self.roll_number)

                print("Course:", COURSE)

                print("Department:", DEPARTMENT)

                print("Semester:", SEMESTER)

                print("Response:", response.text[:3000])

                print("========================================\n")

                response.failure(
                    f"Registration failed: "
                    f"{response.status_code} | "
                    f"{response.text[:500]}"
                )

                self.stop()
                return

            if not response.url.rstrip("/").endswith("/quiz"):

                print("\n========== REGISTRATION NOT COMPLETED ==========")

                print("Final URL:", response.url)

                print("Response:", response.text[:2000])

                print("================================================\n")

                response.failure(
                    "Registration did not redirect to quiz page."
                )

                self.stop()
                return

            response.success()


        # ----------------------------------------------------
        # STEP 5: OPEN QUIZ PAGE
        # ----------------------------------------------------

        with self.client.get(
            "/quiz",
            name="/quiz",
            catch_response=True
        ) as response:

            if response.status_code != 200:

                print("\n========== QUIZ PAGE ERROR ==========")

                print("Status:", response.status_code)

                print("Response:", response.text[:2000])

                print("=====================================\n")

                response.failure(
                    f"Quiz page failed: {response.status_code}"
                )

                self.stop()
                return

            quiz_html = response.text

            response.success()


        # ----------------------------------------------------
        # STEP 6: EXTRACT QUESTION IDS
        # ----------------------------------------------------

        self.question_ids = extract_question_ids(quiz_html)

        if not self.question_ids:

            print("\n========== QUESTION EXTRACTION ERROR ==========")

            print("Student:", self.student_name)

            print("Quiz ID:", QUIZ_ID)

            print("No question IDs found in quiz.html.")

            print("HTML:", quiz_html[:3000])

            print("================================================\n")

            self.environment.runner.stats.log_error(
                "Question Extraction",
                "No data-question-id found in quiz HTML"
            )

            self.stop()
            return


        self.quiz_started = True

        print(
            f"[QUIZ STARTED] "
            f"{self.student_name} | "
            f"{self.roll_number} | "
            f"Questions: {len(self.question_ids)}"
        )


    # ========================================================
    # ATTEMPT QUIZ
    # ========================================================

    @task
    def attempt_quiz(self):

        if not self.quiz_started:

            self.stop()
            return


        total_questions = len(self.question_ids)


        for index, question_id in enumerate(self.question_ids):

            is_last_question = (
                index == total_questions - 1
            )


            # ------------------------------------------------
            # RANDOM ANSWER
            # ------------------------------------------------

            selected_option = random.choice(
                ["A", "B", "C", "D"]
            )


            action = (
                "submit" if is_last_question else "save"
            )


            payload = {

                "question_id": question_id,

                "current_index": index,

                "selected_option": selected_option,

                "action": action
            }


            headers = {

                "X-Requested-With": "XMLHttpRequest"
            }


            # ------------------------------------------------
            # SAVE ANSWER / SUBMIT QUIZ
            # ------------------------------------------------

            with self.client.post(
                "/quiz",
                data=payload,
                headers=headers,
                name="/quiz [answer]",
                catch_response=True
            ) as response:


                if response.status_code != 200:

                    print("\n========== ANSWER SUBMISSION ERROR ==========")

                    print("Student:", self.student_name)

                    print("Question:", question_id)

                    print("Status:", response.status_code)

                    print("Response:", response.text[:2000])

                    print("=============================================\n")

                    response.failure(
                        f"Answer submission failed: "
                        f"{response.status_code}"
                    )

                    self.stop()
                    return


                # --------------------------------------------
                # PARSE JSON RESPONSE
                # --------------------------------------------

                try:

                    result = response.json()

                except (ValueError, json.JSONDecodeError):

                    response.failure(
                        "Invalid JSON response from quiz endpoint."
                    )

                    self.stop()
                    return


                if not result.get("ok"):

                    print("\n========== QUIZ SAVE ERROR ==========")

                    print("Student:", self.student_name)

                    print("Question:", question_id)

                    print("Response:", result)

                    print("=====================================\n")

                    response.failure(
                        result.get("message", "Answer could not be saved.")
                    )

                    self.stop()
                    return


                # --------------------------------------------
                # VERIFY QUESTION PROGRESSION
                # --------------------------------------------

                if is_last_question:

                    if not result.get("submitted"):

                        response.failure(
                            "Final quiz submission was not confirmed."
                        )

                        self.stop()
                        return

                else:

                    returned_index = result.get("current_index")

                    if returned_index != index + 1:

                        response.failure(
                            f"Question progression mismatch. "
                            f"Expected {index + 1}, "
                            f"received {returned_index}"
                        )

                        self.stop()
                        return


                response.success()


        # ====================================================
        # STEP 7: VERIFY RESULT PAGE
        # ====================================================

        with self.client.get(
            "/quiz",
            name="/quiz [result]",
            catch_response=True
        ) as response:

            if response.status_code != 200:

                print("\n========== RESULT PAGE ERROR ==========")

                print("Student:", self.student_name)

                print("Status:", response.status_code)

                print("Response:", response.text[:2000])

                print("=======================================\n")

                response.failure(
                    f"Result page failed: {response.status_code}"
                )

                self.stop()
                return


            # Submitted attempt should not redirect back to registration.
            if "/guest_start" in response.url:

                response.failure(
                    "Result page redirected to guest registration."
                )

                self.stop()
                return


            response.success()


        print(
            f"[QUIZ COMPLETED] "
            f"{self.student_name} | "
            f"{self.roll_number} | "
            f"Questions: {total_questions}"
        )

        self.stop()