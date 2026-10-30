import re
import uuid
from locust import HttpUser, task, between

QUIZ_ID = 26


class QuizStudent(HttpUser):
    wait_time = between(0.1, 0.3)

    def on_start(self):
        self.finished = False

        unique_id = uuid.uuid4().hex[:12]
        self.student_name = f"LOAD_TEST_{unique_id}"
        self.roll_number = f"LT{unique_id}"

        with self.client.get(
            f"/guest_start_quiz/{QUIZ_ID}",
            name="1 - Open guest quiz",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(
                    f"Guest page failed: HTTP {response.status_code}"
                )
                self.finished = True
                return
            response.success()

        with self.client.post(
            f"/guest_start_quiz/{QUIZ_ID}",
            data={
                "student_name": self.student_name,
                "roll_number": self.roll_number,
            },
            name="2 - Start quiz",
            allow_redirects=True,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(
                    f"Quiz start failed: HTTP {response.status_code}"
                )
                self.finished = True
                return

            question_ids = re.findall(
                r'data-question-id=["\'](\d+)["\']',
                response.text,
            )
            self.question_ids = list(dict.fromkeys(question_ids))

            if not self.question_ids or 'id="quizForm"' not in response.text:
                response.failure(
                    "Quiz page or question IDs not found in response"
                )
                self.finished = True
                return

            response.success()

    @task
    def complete_quiz(self):
        if self.finished:
            return

        for index, question_id in enumerate(self.question_ids):
            is_last = index == len(self.question_ids) - 1

            with self.client.post(
                "/quiz",
                data={
                    "question_id": question_id,
                    "current_index": str(index),
                    "selected_option": ["A", "B", "C", "D"][index % 4],
                    "action": "submit" if is_last else "save",
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json",
                },
                name=(
                    "4 - Submit quiz"
                    if is_last
                    else "3 - Save answer and next"
                ),
                catch_response=True,
            ) as response:
                if response.status_code != 200:
                    response.failure(
                        f"Question {index + 1}: HTTP {response.status_code}"
                    )
                    self.finished = True
                    return

                try:
                    data = response.json()
                except ValueError:
                    response.failure(
                        f"Question {index + 1}: response was not JSON"
                    )
                    self.finished = True
                    return

                if not data.get("ok"):
                    response.failure(
                        f"Question {index + 1}: "
                        f"{data.get('message', 'Application error')}"
                    )
                    self.finished = True
                    return

                if is_last and not data.get("submitted"):
                    response.failure(
                        "Final request succeeded but attempt was not submitted"
                    )
                    self.finished = True
                    return

                response.success()

        self.finished = True
