import mysql.connector


def get_db_connection():
    connection = mysql.connector.connect(
        host="localhost",
        user="root",
        password="rootpassword@45",
        database="ai_quiz_system"
    )

    return connection