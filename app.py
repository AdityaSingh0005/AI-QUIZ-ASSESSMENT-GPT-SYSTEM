from flask import Flask
from routes.auth import auth
from routes.teacher import teacher
from routes.student import student


app = Flask(__name__)

app.secret_key = "ai_quiz_secret_key"

app.register_blueprint(auth)
app.register_blueprint(teacher)
app.register_blueprint(student)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )