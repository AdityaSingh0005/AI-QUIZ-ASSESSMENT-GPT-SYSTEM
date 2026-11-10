-- AI Quiz Assessment: production indexes + Internal Assessment v2
-- Run this ONCE in the Neon SQL Editor against the existing database.
-- This migration is additive; it does not drop or rewrite existing student data.

ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS subject VARCHAR(150);
ALTER TABLE internal_assessments ADD COLUMN IF NOT EXISTS course VARCHAR(30);
ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS question_order JSONB;
ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS option_order JSONB;

-- Prevent duplicate answer rows and duplicate result rows. If old duplicate
-- records exist, the migration stops with a clear message instead of deleting data.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM student_answers
        WHERE attempt_id IS NOT NULL AND question_id IS NOT NULL
        GROUP BY attempt_id, question_id HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'Duplicate student_answers exist for the same attempt/question. Resolve duplicates before rerunning this migration.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM results
        WHERE attempt_id IS NOT NULL
        GROUP BY attempt_id HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'Duplicate results exist for an attempt_id. Resolve duplicates before rerunning this migration.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM quiz_attempts
        WHERE status='in_progress' AND student_id IS NOT NULL
        GROUP BY quiz_id, student_id HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'Multiple active attempts exist for the same registered student/quiz. Resolve duplicates before rerunning this migration.';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_student_answers_attempt_question
    ON student_answers(attempt_id, question_id)
    WHERE attempt_id IS NOT NULL AND question_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_results_attempt_id
    ON results(attempt_id) WHERE attempt_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_quiz_attempt_active_student
    ON quiz_attempts(quiz_id, student_id)
    WHERE status='in_progress' AND student_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_quiz_attempts_quiz_status_submitted
    ON quiz_attempts(quiz_id, status, submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_quiz_attempts_cohort_guest
    ON quiz_attempts(course, department, semester, quiz_id)
    WHERE student_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_students_cohort
    ON students(course, department, semester);
CREATE INDEX IF NOT EXISTS idx_quizzes_teacher_subject
    ON quizzes(teacher_id, lower(btrim(subject)));
CREATE INDEX IF NOT EXISTS idx_questions_quiz_question
    ON questions(quiz_id, question_id);

-- New participant model supports both registered students and guest-only users.
CREATE TABLE IF NOT EXISTS internal_assessment_participants_v2 (
    participant_id BIGSERIAL PRIMARY KEY,
    assessment_id BIGINT NOT NULL
        REFERENCES internal_assessments(assessment_id) ON DELETE CASCADE,
    student_id INTEGER NULL
        REFERENCES students(student_id) ON DELETE SET NULL,
    participant_name VARCHAR(150) NOT NULL,
    roll_number VARCHAR(50) NOT NULL,
    course VARCHAR(30),
    department VARCHAR(100),
    semester VARCHAR(20),
    participant_type VARCHAR(20) NOT NULL
        CHECK (participant_type IN ('registered', 'guest')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ia_v2_registered_participant
    ON internal_assessment_participants_v2(assessment_id, student_id)
    WHERE student_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_ia_v2_guest_roll
    ON internal_assessment_participants_v2(assessment_id, upper(btrim(roll_number)))
    WHERE student_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_ia_v2_participants_assessment
    ON internal_assessment_participants_v2(assessment_id, upper(btrim(roll_number)));

-- Composite key required by the participant/quiz handling table.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ia_v2_participant_assessment
    ON internal_assessment_participants_v2(assessment_id, participant_id);

CREATE TABLE IF NOT EXISTS internal_assessment_participant_quizzes_v2 (
    assessment_id BIGINT NOT NULL,
    participant_id BIGINT NOT NULL,
    quiz_id INTEGER NOT NULL,
    handling_status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (handling_status IN ('pending', 'zero', 'exclude')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (assessment_id, participant_id, quiz_id),
    FOREIGN KEY (assessment_id, participant_id)
        REFERENCES internal_assessment_participants_v2(assessment_id, participant_id)
        ON DELETE CASCADE,
    FOREIGN KEY (assessment_id, quiz_id)
        REFERENCES internal_assessment_quizzes(assessment_id, quiz_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS internal_assessment_participant_results_v2 (
    assessment_id BIGINT NOT NULL,
    participant_id BIGINT NOT NULL,
    average_percentage NUMERIC(7,3) NOT NULL,
    final_marks NUMERIC(7,2) NOT NULL,
    calculation_details JSONB NOT NULL DEFAULT '[]'::jsonb,
    finalized_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (assessment_id, participant_id),
    FOREIGN KEY (assessment_id, participant_id)
        REFERENCES internal_assessment_participants_v2(assessment_id, participant_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ia_v2_results_assessment
    ON internal_assessment_participant_results_v2(assessment_id);

-- Backfill existing registered participants without deleting legacy IA rows.
INSERT INTO internal_assessment_participants_v2
    (assessment_id, student_id, participant_name, roll_number,
     course, department, semester, participant_type)
SELECT ias.assessment_id, s.student_id, s.full_name, s.roll_number,
       COALESCE(ia.course, s.course), COALESCE(ia.department, s.department),
       COALESCE(ia.semester, s.semester), 'registered'
FROM internal_assessment_students ias
JOIN students s ON s.student_id=ias.student_id
JOIN internal_assessments ia ON ia.assessment_id=ias.assessment_id
ON CONFLICT DO NOTHING;

-- Infer the course on older assessments when all linked registered students
-- belong to one course. Ambiguous assessments are intentionally left unchanged.
UPDATE internal_assessments ia
SET course = cohort.course
FROM (
    SELECT ias.assessment_id, MIN(s.course) AS course
    FROM internal_assessment_students ias
    JOIN students s ON s.student_id=ias.student_id
    GROUP BY ias.assessment_id
    HAVING COUNT(DISTINCT s.course)=1
) cohort
WHERE ia.assessment_id=cohort.assessment_id AND COALESCE(ia.course,'')='';

-- Preserve previously saved absent-handling choices.
INSERT INTO internal_assessment_participant_quizzes_v2
    (assessment_id, participant_id, quiz_id, handling_status, updated_at)
SELECT old.assessment_id, p.participant_id, old.quiz_id, old.handling_status, old.updated_at
FROM internal_assessment_student_quizzes old
JOIN internal_assessment_participants_v2 p
  ON p.assessment_id=old.assessment_id AND p.student_id=old.student_id
ON CONFLICT (assessment_id, participant_id, quiz_id)
DO UPDATE SET handling_status=EXCLUDED.handling_status, updated_at=EXCLUDED.updated_at;

-- Preserve finalized registered-student results.
INSERT INTO internal_assessment_participant_results_v2
    (assessment_id, participant_id, average_percentage, final_marks, calculation_details, finalized_at)
SELECT old.assessment_id, p.participant_id, old.average_percentage, old.final_marks,
       old.calculation_details, old.finalized_at
FROM internal_assessment_results old
JOIN internal_assessment_participants_v2 p
  ON p.assessment_id=old.assessment_id AND p.student_id=old.student_id
ON CONFLICT (assessment_id, participant_id) DO NOTHING;
