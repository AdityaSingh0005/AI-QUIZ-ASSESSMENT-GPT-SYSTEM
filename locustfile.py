
import os
import re
import time
import uuid
import random
import itertools
from html.parser import HTMLParser

from locust import HttpUser, task, between


# ============================================================
# CONFIGURATION
# ============================================================

QUIZ_ID = int(os.getenv("QUIZ_ID", "36"))

# Every virtual student gets a unique identity.
STUDENT_COUNTER = itertools.count(1)

RUN_ID = uuid.uuid4().hex[:8]


# ============================================================
# HTML HIDDEN INPUT PARSER
# Supports CSRF tokens if the application uses them.
# ============================================================

class HiddenInputParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.hidden_fields = {}

    def handle_starttag(self, tag, attrs):

        if tag.lower() != "input":
            return

        attrs = dict(attrs)

        if attrs.get("type", "").lower() == "hidden":

            name = attrs.get("name")
            value = attrs.get("value", "")

            if name:
                self.hidden_fields[name] = value


def extract_hidden_fields(html):

    parser = HiddenInputParser()
    parser.feed(html)

    return parser.hidden_fields


# ============================================================
# QUESTION ID EXTRACTION
# Reads IDs from rendered quiz question cards.
# ============================================================

def extract_question_ids(html):

    pattern = r'data-question-id=["\'](\d+)["\']'

    question_ids = re.findall(pattern, html)

    # Preserve order and remove accidental duplicates.
    return list(dict.fromkeys(question_ids))


# ============================================================
# VIRTUAL STUDENT
# ============================================================

class GuestStudent(HttpUser):

    wait_time = between(0.2, 0.6)

    def on_start(self):

        self.student_number = next(STUDENT_COUNTER)

        self.student_name = (
            f"LocustStudent-{RUN_ID}-{self.student_number}"
        )

        self.roll_number = (
            f"LOAD-{RUN_ID}-{self.student_number}"
        )

        self.completed = False

        self.question_ids = []

        self.quiz_hidden_fields = {}

        self.start_guest_attempt()

    # --------------------------------------------------------
    # STEP 1: Open guest registration page
    # --------------------------------------------------------

    def start_guest_attempt(self):

        start_url = f"/guest_start_quiz/{QUIZ_ID}"

        with self.client.get(
            start_url,
            name="01 - Open Guest Registration",
            catch_response=True
        ) as response:

            if response.status_code != 200:

                response.failure(
                    f"Guest page failed: {response.status_code}"
                )

                self.stop()
                return

            guest_hidden_fields = extract_hidden_fields(
                response.text
            )

            response.success()

        # ----------------------------------------------------
        # STEP 2: Register guest student
        # ----------------------------------------------------

        payload = {
            **guest_hidden_fields,
            "student_name": self.student_name,
            "roll_number": self.roll_number
        }

        with self.client.post(
            start_url,
            data=payload,
            name="02 - Guest Registration",
            allow_redirects=True,
            catch_response=True
        ) as response:

            if response.status_code != 200:

                response.failure(
                    f"Guest registration failed: {response.status_code}"
                )

                self.stop()
                return

            # The guest registration should redirect to /quiz.
            if "/quiz" not in response.url:

                response.failure(
                    "Registration did not redirect to quiz page."
                )

                self.stop()
                return

            response.success()

        # ----------------------------------------------------
        # STEP 3: Read quiz page and extract question IDs
        # ----------------------------------------------------

        self.load_quiz()

    # --------------------------------------------------------
    # LOAD QUIZ
    # --------------------------------------------------------

    def load_quiz(self):

        with self.client.get(
            "/quiz",
            name="03 - Load Quiz",
            catch_response=True
        ) as response:

            if response.status_code != 200:

                response.failure(
                    f"Quiz loading failed: {response.status_code}"
                )

                self.stop()
                return

            self.question_ids = extract_question_ids(
                response.text
            )

            self.quiz_hidden_fields = extract_hidden_fields(
                response.text
            )

            if not self.question_ids:

                response.failure(
                    "No question IDs found in quiz HTML."
                )

                self.stop()
                return

            response.success()

    # --------------------------------------------------------
    # STEP 4: Attempt all questions and submit
    # --------------------------------------------------------

    @task
    def attempt_quiz(self):

        # Prevent this virtual student from starting again.
        if self.completed:
            self.stop()
            return

        total_questions = len(self.question_ids)

        for index, question_id in enumerate(self.question_ids):

            is_last_question = (
                index == total_questions - 1
            )

            action = (
                "submit" if is_last_question else "save"
            )

            selected_option = random.choice(
                ["A", "B", "C", "D"]
            )

            payload = {
                **self.quiz_hidden_fields,
                "question_id": question_id,
                "current_index": index,
                "selected_option": selected_option,
                "action": action
            }

            with self.client.post(
                "/quiz",
                data=payload,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json"
                },
                name="04 - Save Answer / Submit",
                catch_response=True
            ) as response:

                if response.status_code != 200:

                    response.failure(
                        f"Question {index + 1}: "
                        f"HTTP {response.status_code}"
                    )

                    self.completed = True
                    self.stop()
                    return

                try:
                    result = response.json()
                except Exception:

                    response.failure(
                        f"Question {index + 1}: Invalid JSON response"
                    )

                    self.completed = True
                    self.stop()
                    return

                if not result.get("ok"):

                    response.failure(
                        f"Question {index + 1}: "
                        f"{result.get('message', 'Unknown error')}"
                    )

                    self.completed = True
                    self.stop()
                    return

                # Verify normal question progression.
                if not is_last_question:

                    if result.get("submitted"):

                        response.failure(
                            "Quiz submitted before final question."
                        )

                        self.completed = True
                        self.stop()
                        return

                    expected_index = index + 1

                    if result.get("current_index") != expected_index:

                        response.failure(
                            f"Incorrect question progression. "
                            f"Expected {expected_index}, "
                            f"received {result.get('current_index')}"
                        )

                        self.completed = True
                        self.stop()
                        return

                # Verify final submission.
                else:

                    if not result.get("submitted"):

                        response.failure(
                            "Final question did not submit the quiz."
                        )

                        self.completed = True
                        self.stop()
                        return

                response.success()

            # Small pause between answers.
            time.sleep(random.uniform(0.05, 0.15))

        # ----------------------------------------------------
        # STEP 5: Open submitted result page
        # ----------------------------------------------------

        with self.client.get(
            "/quiz",
            name="05 - Load Submitted Result",
            catch_response=True
        ) as response:

            if response.status_code != 200:

                response.failure(
                    "Could not load submitted result page."
                )

            elif "submitted" not in response.text.lower():

                response.failure(
                    "Submission confirmation not found."
                )

            else:

                response.success()

        self.completed = True

        print(
            f"Completed: {self.student_name} | "
            f"Roll: {self.roll_number} | "
            f"Questions: {total_questions}"
        )

        self.stop()

