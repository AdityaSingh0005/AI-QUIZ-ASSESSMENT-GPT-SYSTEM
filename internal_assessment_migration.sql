-- Internal Assessment feature (safe to run more than once)
CREATE TABLE IF NOT EXISTS internal_assessments (
    assessment_id BIGSERIAL PRIMARY KEY,
    teacher_id INTEGER NOT NULL REFERENCES teachers(teacher_id) ON DELETE CASCADE,
    title VARCHAR(200) NOT NULL,
    department VARCHAR(100),
    semester VARCHAR(20),
    section VARCHAR(20),
    calculation_method VARCHAR(30) NOT NULL
        CHECK (calculation_method IN ('percentage_average', 'total_marks_average')),
    final_out_of INTEGER NOT NULL CHECK (final_out_of IN (30, 40)),
    status VARCHAR(20) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'finalized')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finalized_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS internal_assessment_quizzes (
    assessment_id BIGINT NOT NULL REFERENCES internal_assessments(assessment_id) ON DELETE CASCADE,
    quiz_id INTEGER NOT NULL REFERENCES quizzes(quiz_id) ON DELETE RESTRICT,
    PRIMARY KEY (assessment_id, quiz_id)
);

CREATE TABLE IF NOT EXISTS internal_assessment_students (
    assessment_id BIGINT NOT NULL REFERENCES internal_assessments(assessment_id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(student_id) ON DELETE RESTRICT,
    PRIMARY KEY (assessment_id, student_id)
);

-- An override is stored only when the teacher changes the default Pending state.
CREATE TABLE IF NOT EXISTS internal_assessment_student_quizzes (
    assessment_id BIGINT NOT NULL,
    student_id INTEGER NOT NULL,
    quiz_id INTEGER NOT NULL,
    handling_status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (handling_status IN ('pending', 'zero', 'exclude')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (assessment_id, student_id, quiz_id),
    FOREIGN KEY (assessment_id, student_id)
        REFERENCES internal_assessment_students(assessment_id, student_id)
        ON DELETE CASCADE,
    FOREIGN KEY (assessment_id, quiz_id)
        REFERENCES internal_assessment_quizzes(assessment_id, quiz_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS internal_assessment_results (
    assessment_id BIGINT NOT NULL REFERENCES internal_assessments(assessment_id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(student_id) ON DELETE RESTRICT,
    average_percentage NUMERIC(7,3) NOT NULL,
    final_marks NUMERIC(7,2) NOT NULL,
    calculation_details JSONB NOT NULL DEFAULT '[]'::jsonb,
    finalized_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (assessment_id, student_id)
);

CREATE INDEX IF NOT EXISTS idx_internal_assessments_teacher
    ON internal_assessments(teacher_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_internal_assessment_quizzes_quiz
    ON internal_assessment_quizzes(quiz_id);
