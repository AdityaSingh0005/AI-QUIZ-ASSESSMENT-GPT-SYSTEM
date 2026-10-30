-- =====================================================
-- AI QUIZ ASSESSMENT GPT SYSTEM
-- Fresh PostgreSQL Database Schema for Neon
-- =====================================================

-- 1. TEACHERS
CREATE TABLE IF NOT EXISTS teachers (
    teacher_id SERIAL PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    email VARCHAR(100) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


-- 2. STUDENTS
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


-- 3. QUIZZES
CREATE TABLE IF NOT EXISTS quizzes (
    quiz_id SERIAL PRIMARY KEY,

    teacher_id INTEGER
        REFERENCES teachers(teacher_id),

    title VARCHAR(255),
    prompt TEXT,
    total_questions INTEGER,
    qr_code_path VARCHAR(255),

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    question_time_seconds INTEGER DEFAULT 60,

    available_from TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

    available_until TIMESTAMPTZ,

    duration_minutes INTEGER DEFAULT 10
);


-- 4. QUESTIONS
CREATE TABLE IF NOT EXISTS questions (
    question_id SERIAL PRIMARY KEY,

    quiz_id INTEGER
        REFERENCES quizzes(quiz_id)
        ON DELETE CASCADE,

    question TEXT,
    option_a TEXT,
    option_b TEXT,
    option_c TEXT,
    option_d TEXT,

    correct_option CHAR(1),

    difficulty VARCHAR(20) DEFAULT 'Medium',

    explanation TEXT
);


-- 5. RESULTS
CREATE TABLE IF NOT EXISTS results (
    result_id SERIAL PRIMARY KEY,

    student_id INTEGER
        REFERENCES students(student_id),

    quiz_id INTEGER
        REFERENCES quizzes(quiz_id)
        ON DELETE CASCADE,

    score INTEGER,

    percentage DECIMAL(5,2),

    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    attempt_id BIGINT
);


-- 6. STUDENT ANSWERS
CREATE TABLE IF NOT EXISTS student_answers (
    answer_id SERIAL PRIMARY KEY,

    student_id INTEGER
        REFERENCES students(student_id),

    quiz_id INTEGER
        REFERENCES quizzes(quiz_id)
        ON DELETE CASCADE,

    question_id INTEGER
        REFERENCES questions(question_id)
        ON DELETE CASCADE,

    selected_option CHAR(1),

    attempt_id BIGINT
);


-- 7. QUIZ ATTEMPTS
CREATE TABLE IF NOT EXISTS quiz_attempts (
    attempt_id BIGSERIAL PRIMARY KEY,

    quiz_id INTEGER NOT NULL
        REFERENCES quizzes(quiz_id)
        ON DELETE CASCADE,

    student_id INTEGER
        REFERENCES students(student_id)
        ON DELETE SET NULL,

    student_name VARCHAR(100) NOT NULL,

    roll_number VARCHAR(30) NOT NULL,

    attempt_mode VARCHAR(10) NOT NULL
        CHECK (attempt_mode IN ('login', 'guest')),

    started_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

    submitted_at TIMESTAMPTZ,

    status VARCHAR(20) NOT NULL DEFAULT 'in_progress'
        CHECK (
            status IN (
                'in_progress',
                'submitted',
                'expired'
            )
        ),

    score INTEGER,

    percentage DECIMAL(5,2)
);


-- Add foreign keys for attempt tracking.
-- Run after quiz_attempts exists.

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


-- Helpful indexes
CREATE INDEX IF NOT EXISTS idx_quizzes_teacher
    ON quizzes(teacher_id);

CREATE INDEX IF NOT EXISTS idx_questions_quiz
    ON questions(quiz_id);

CREATE INDEX IF NOT EXISTS idx_results_student
    ON results(student_id);

CREATE INDEX IF NOT EXISTS idx_results_quiz
    ON results(quiz_id);

CREATE INDEX IF NOT EXISTS idx_attempts_quiz_student
    ON quiz_attempts(quiz_id, student_id);

CREATE INDEX IF NOT EXISTS idx_student_answers_attempt
    ON student_answers(attempt_id);