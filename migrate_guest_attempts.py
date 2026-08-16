import os
import psycopg2


DATABASE_URL = os.environ.get("DATABASE_URL")


if not DATABASE_URL:
    raise Exception("DATABASE_URL not found")


conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True

cur = conn.cursor()

print("Connected to PostgreSQL!")


# ============================================================
# 1. QUIZ ATTEMPTS TABLE
# ============================================================

cur.execute("""
CREATE TABLE IF NOT EXISTS quiz_attempts (

    attempt_id BIGSERIAL PRIMARY KEY,

    quiz_id INTEGER NOT NULL,

    student_id INTEGER,

    student_name VARCHAR(100) NOT NULL,

    roll_number VARCHAR(30) NOT NULL,

    attempt_mode VARCHAR(10) NOT NULL
        CHECK (attempt_mode IN ('login', 'guest')),

    started_at TIMESTAMPTZ
        DEFAULT CURRENT_TIMESTAMP,

    submitted_at TIMESTAMPTZ,

    status VARCHAR(20) NOT NULL
        DEFAULT 'in_progress'
        CHECK (
            status IN (
                'in_progress',
                'submitted',
                'expired'
            )
        ),

    score INTEGER,

    percentage DECIMAL(5,2),

    CONSTRAINT quiz_attempts_quiz_fk
        FOREIGN KEY (quiz_id)
        REFERENCES quizzes(quiz_id)
        ON DELETE CASCADE,

    CONSTRAINT quiz_attempts_student_fk
        FOREIGN KEY (student_id)
        REFERENCES students(student_id)
        ON DELETE SET NULL
);
""")


print("✓ quiz_attempts table ready")


# ============================================================
# 2. ADD ATTEMPT ID TO RESULTS
# ============================================================

cur.execute("""
ALTER TABLE results
ADD COLUMN IF NOT EXISTS attempt_id BIGINT;
""")


# Add FK only if it does not already exist
cur.execute("""
DO $$
BEGIN

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'results_attempt_fk'
    ) THEN

        ALTER TABLE results
        ADD CONSTRAINT results_attempt_fk
        FOREIGN KEY (attempt_id)
        REFERENCES quiz_attempts(attempt_id)
        ON DELETE SET NULL;

    END IF;

END
$$;
""")


print("✓ results.attempt_id ready")


# ============================================================
# 3. ADD ATTEMPT ID TO STUDENT ANSWERS
# ============================================================

cur.execute("""
ALTER TABLE student_answers
ADD COLUMN IF NOT EXISTS attempt_id BIGINT;
""")


cur.execute("""
DO $$
BEGIN

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'student_answers_attempt_fk'
    ) THEN

        ALTER TABLE student_answers
        ADD CONSTRAINT student_answers_attempt_fk
        FOREIGN KEY (attempt_id)
        REFERENCES quiz_attempts(attempt_id)
        ON DELETE SET NULL;

    END IF;

END
$$;
""")


print("✓ student_answers.attempt_id ready")


# ============================================================
# 4. ONE ATTEMPT PER ROLL NUMBER FOR EACH QUIZ
# ============================================================

cur.execute("""
CREATE UNIQUE INDEX IF NOT EXISTS
unique_quiz_roll_attempt
ON quiz_attempts (
    quiz_id,
    LOWER(roll_number)
);
""")


print("✓ Duplicate attempt protection ready")


# ============================================================
# FINISHED
# ============================================================

print()
print("==========================================")
print("Guest Quiz Attempt Migration Completed!")
print("==========================================")


cur.close()
conn.close()