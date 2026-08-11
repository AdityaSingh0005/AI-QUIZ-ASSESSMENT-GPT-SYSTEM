import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise Exception("DATABASE_URL not found")

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True

cur = conn.cursor()

print("Connected to PostgreSQL!")

cur.execute("""
CREATE TABLE IF NOT EXISTS teachers (
    teacher_id SERIAL PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    email VARCHAR(100) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS students (
    student_id SERIAL PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    roll_number VARCHAR(30) NOT NULL UNIQUE,
    department VARCHAR(100),
    semester VARCHAR(20),
    section VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    password VARCHAR(255) NOT NULL,
    email VARCHAR(100),
    phone VARCHAR(20),
    profile_image VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS quizzes (
    quiz_id SERIAL PRIMARY KEY,
    teacher_id INTEGER,
    title VARCHAR(255),
    prompt TEXT,
    total_questions INTEGER,
    qr_code_path VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (teacher_id) REFERENCES teachers(teacher_id)
);

CREATE TABLE IF NOT EXISTS questions (
    question_id SERIAL PRIMARY KEY,
    quiz_id INTEGER,
    question TEXT,
    option_a TEXT,
    option_b TEXT,
    option_c TEXT,
    option_d TEXT,
    correct_option CHAR(1),
    difficulty VARCHAR(20) DEFAULT 'Medium',
    FOREIGN KEY (quiz_id) REFERENCES quizzes(quiz_id)
);

CREATE TABLE IF NOT EXISTS results (
    result_id SERIAL PRIMARY KEY,
    student_id INTEGER,
    quiz_id INTEGER,
    score INTEGER,
    percentage DECIMAL(5,2),
    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students(student_id),
    FOREIGN KEY (quiz_id) REFERENCES quizzes(quiz_id)
);

CREATE TABLE IF NOT EXISTS student_answers (
    answer_id SERIAL PRIMARY KEY,
    student_id INTEGER,
    quiz_id INTEGER,
    question_id INTEGER,
    selected_option CHAR(1),
    FOREIGN KEY (student_id) REFERENCES students(student_id),
    FOREIGN KEY (quiz_id) REFERENCES quizzes(quiz_id),
    FOREIGN KEY (question_id) REFERENCES questions(question_id)
);
""")

print("All 6 tables created successfully!")

cur.close()
conn.close()