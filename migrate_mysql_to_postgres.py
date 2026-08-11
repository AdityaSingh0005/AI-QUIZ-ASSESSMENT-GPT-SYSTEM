
import os
import mysql.connector
import psycopg2


# ==========================================
# MYSQL CONNECTION
# ==========================================

mysql_conn = mysql.connector.connect(
    host=os.getenv("MYSQL_HOST", "localhost"),
    user=os.getenv("MYSQL_USER", "root"),
    password=os.getenv("MYSQL_PASSWORD"),
    database=os.getenv("MYSQL_DATABASE", "ai_quiz_system")
)

mysql_cursor = mysql_conn.cursor(dictionary=True)


# ==========================================
# POSTGRES CONNECTION
# ==========================================

postgres_url = os.getenv("DATABASE_URL")

if not postgres_url:
    raise Exception("DATABASE_URL is not set")


pg_conn = psycopg2.connect(postgres_url)
pg_cursor = pg_conn.cursor()


print("✅ Connected to MySQL")
print("✅ Connected to PostgreSQL")


# ==========================================
# DISABLE FK CHECKS / CLEAN TABLES
# ==========================================

print("\n🧹 Cleaning PostgreSQL tables...")

pg_cursor.execute("""
    TRUNCATE TABLE
        student_answers,
        results,
        questions,
        quizzes,
        students,
        teachers
    RESTART IDENTITY CASCADE
""")

pg_conn.commit()

print("✅ PostgreSQL tables cleaned")


# ==========================================
# TEACHERS
# ==========================================

print("\n📥 Migrating teachers...")

mysql_cursor.execute("""
    SELECT
        teacher_id,
        full_name,
        email,
        password,
        created_at
    FROM teachers
    ORDER BY teacher_id
""")

teachers = mysql_cursor.fetchall()

for row in teachers:

    pg_cursor.execute("""
        INSERT INTO teachers
        (
            teacher_id,
            full_name,
            email,
            password,
            created_at
        )
        VALUES (%s,%s,%s,%s,%s)
    """, (
        row["teacher_id"],
        row["full_name"],
        row["email"],
        row["password"],
        row["created_at"]
    ))

print(f"✅ Teachers migrated: {len(teachers)}")


# ==========================================
# STUDENTS
# ==========================================

print("\n📥 Migrating students...")

mysql_cursor.execute("""
    SELECT
        student_id,
        full_name,
        roll_number,
        department,
        semester,
        section,
        created_at,
        password,
        email,
        phone,
        profile_image
    FROM students
    ORDER BY student_id
""")

students = mysql_cursor.fetchall()

for row in students:

    pg_cursor.execute("""
        INSERT INTO students
        (
            student_id,
            full_name,
            roll_number,
            department,
            semester,
            section,
            created_at,
            password,
            email,
            phone,
            profile_image
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        row["student_id"],
        row["full_name"],
        row["roll_number"],
        row["department"],
        row["semester"],
        row["section"],
        row["created_at"],
        row["password"],
        row["email"],
        row["phone"],
        row["profile_image"]
    ))

print(f"✅ Students migrated: {len(students)}")


# ==========================================
# QUIZZES
# ==========================================

print("\n📥 Migrating quizzes...")

mysql_cursor.execute("""
    SELECT
        quiz_id,
        teacher_id,
        title,
        prompt,
        total_questions,
        qr_code_path,
        created_at
    FROM quizzes
    ORDER BY quiz_id
""")

quizzes = mysql_cursor.fetchall()

for row in quizzes:

    pg_cursor.execute("""
        INSERT INTO quizzes
        (
            quiz_id,
            teacher_id,
            title,
            prompt,
            total_questions,
            qr_code_path,
            created_at
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (
        row["quiz_id"],
        row["teacher_id"],
        row["title"],
        row["prompt"],
        row["total_questions"],
        row["qr_code_path"],
        row["created_at"]
    ))

print(f"✅ Quizzes migrated: {len(quizzes)}")


# ==========================================
# QUESTIONS
# ==========================================

print("\n📥 Migrating questions...")

mysql_cursor.execute("""
    SELECT
        question_id,
        quiz_id,
        question,
        option_a,
        option_b,
        option_c,
        option_d,
        correct_option,
        difficulty
    FROM questions
    ORDER BY question_id
""")

questions = mysql_cursor.fetchall()

for row in questions:

    pg_cursor.execute("""
        INSERT INTO questions
        (
            question_id,
            quiz_id,
            question,
            option_a,
            option_b,
            option_c,
            option_d,
            correct_option,
            difficulty
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        row["question_id"],
        row["quiz_id"],
        row["question"],
        row["option_a"],
        row["option_b"],
        row["option_c"],
        row["option_d"],
        row["correct_option"],
        row["difficulty"]
    ))

print(f"✅ Questions migrated: {len(questions)}")


# ==========================================
# RESULTS
# ==========================================

print("\n📥 Migrating results...")

mysql_cursor.execute("""
    SELECT
        result_id,
        student_id,
        quiz_id,
        score,
        percentage,
        submitted_at
    FROM results
    ORDER BY result_id
""")

results = mysql_cursor.fetchall()

for row in results:

    pg_cursor.execute("""
        INSERT INTO results
        (
            result_id,
            student_id,
            quiz_id,
            score,
            percentage,
            submitted_at
        )
        VALUES (%s,%s,%s,%s,%s,%s)
    """, (
        row["result_id"],
        row["student_id"],
        row["quiz_id"],
        row["score"],
        row["percentage"],
        row["submitted_at"]
    ))

print(f"✅ Results migrated: {len(results)}")


# ==========================================
# STUDENT ANSWERS
# ==========================================

print("\n📥 Migrating student answers...")

mysql_cursor.execute("""
    SELECT
        answer_id,
        student_id,
        quiz_id,
        question_id,
        selected_option
    FROM student_answers
    ORDER BY answer_id
""")

answers = mysql_cursor.fetchall()

for row in answers:

    pg_cursor.execute("""
        INSERT INTO student_answers
        (
            answer_id,
            student_id,
            quiz_id,
            question_id,
            selected_option
        )
        VALUES (%s,%s,%s,%s,%s)
    """, (
        row["answer_id"],
        row["student_id"],
        row["quiz_id"],
        row["question_id"],
        row["selected_option"]
    ))

print(f"✅ Student answers migrated: {len(answers)}")


# ==========================================
# RESET POSTGRES SEQUENCES
# ==========================================

print("\n🔧 Updating PostgreSQL sequences...")

sequence_tables = [
    ("teachers", "teacher_id"),
    ("students", "student_id"),
    ("quizzes", "quiz_id"),
    ("questions", "question_id"),
    ("results", "result_id"),
    ("student_answers", "answer_id")
]

for table, column in sequence_tables:

    pg_cursor.execute(f"""
        SELECT setval(
            pg_get_serial_sequence('{table}', '{column}'),
            COALESCE(
                (SELECT MAX({column}) FROM {table}),
                1
            ),
            true
        )
    """)


# ==========================================
# COMMIT
# ==========================================

pg_conn.commit()


# ==========================================
# CLOSE CONNECTIONS
# ==========================================

mysql_cursor.close()
mysql_conn.close()

pg_cursor.close()
pg_conn.close()


print("\n" + "=" * 50)
print("🎉 FULL DATABASE MIGRATION COMPLETED")
print("=" * 50)

