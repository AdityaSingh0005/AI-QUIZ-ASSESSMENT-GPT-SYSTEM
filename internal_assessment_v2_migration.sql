-- AI Quiz Assessment: Internal Assessment V2 schema
-- Run this file in the Neon SQL Editor. Safe to run more than once.
-- This migration is isolated from unrelated quiz/result indexes so an old
-- duplicate record cannot prevent Internal Assessment tables from being created.

ALTER TABLE quizzes
    ADD COLUMN IF NOT EXISTS subject VARCHAR(150);

CREATE TABLE IF NOT EXISTS internal_assessments (
    assessment_id BIGSERIAL PRIMARY KEY,
    teacher_id INTEGER NOT NULL REFERENCES teachers(teacher_id) ON DELETE CASCADE,
    title VARCHAR(200) NOT NULL,
    course VARCHAR(30),
    department VARCHAR(100),
    semester VARCHAR(20),
    calculation_method VARCHAR(30) NOT NULL
        CHECK (calculation_method IN ('percentage_average', 'total_marks_average')),
    final_out_of INTEGER NOT NULL CHECK (final_out_of IN (30, 40)),
    status VARCHAR(20) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'finalized')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finalized_at TIMESTAMPTZ
);

ALTER TABLE internal_assessments ADD COLUMN IF NOT EXISTS course VARCHAR(30);
ALTER TABLE internal_assessments ADD COLUMN IF NOT EXISTS department VARCHAR(100);
ALTER TABLE internal_assessments ADD COLUMN IF NOT EXISTS semester VARCHAR(20);
ALTER TABLE internal_assessments ADD COLUMN IF NOT EXISTS calculation_method VARCHAR(30);
ALTER TABLE internal_assessments ADD COLUMN IF NOT EXISTS final_out_of INTEGER;
ALTER TABLE internal_assessments ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'draft';
ALTER TABLE internal_assessments ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE internal_assessments ADD COLUMN IF NOT EXISTS finalized_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS internal_assessment_quizzes (
    assessment_id BIGINT NOT NULL
        REFERENCES internal_assessments(assessment_id) ON DELETE CASCADE,
    quiz_id INTEGER NOT NULL
        REFERENCES quizzes(quiz_id) ON DELETE RESTRICT,
    PRIMARY KEY (assessment_id, quiz_id)
);

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

ALTER TABLE internal_assessment_participants_v2
    ADD COLUMN IF NOT EXISTS student_id INTEGER REFERENCES students(student_id) ON DELETE SET NULL;
ALTER TABLE internal_assessment_participants_v2
    ADD COLUMN IF NOT EXISTS participant_name VARCHAR(150);
ALTER TABLE internal_assessment_participants_v2
    ADD COLUMN IF NOT EXISTS roll_number VARCHAR(50);
ALTER TABLE internal_assessment_participants_v2
    ADD COLUMN IF NOT EXISTS course VARCHAR(30);
ALTER TABLE internal_assessment_participants_v2
    ADD COLUMN IF NOT EXISTS department VARCHAR(100);
ALTER TABLE internal_assessment_participants_v2
    ADD COLUMN IF NOT EXISTS semester VARCHAR(20);
ALTER TABLE internal_assessment_participants_v2
    ADD COLUMN IF NOT EXISTS participant_type VARCHAR(20) DEFAULT 'registered';

CREATE UNIQUE INDEX IF NOT EXISTS uq_ia_v2_registered_participant
    ON internal_assessment_participants_v2(assessment_id, student_id)
    WHERE student_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_ia_v2_guest_roll
    ON internal_assessment_participants_v2(assessment_id, UPPER(BTRIM(roll_number)))
    WHERE student_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_ia_v2_participant_assessment
    ON internal_assessment_participants_v2(assessment_id, participant_id);

CREATE INDEX IF NOT EXISTS idx_ia_v2_participants_assessment
    ON internal_assessment_participants_v2(assessment_id, UPPER(BTRIM(roll_number)));

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

CREATE INDEX IF NOT EXISTS idx_ia_quiz_attempts_quiz_status
    ON quiz_attempts(quiz_id, status, submitted_at DESC);


-- Preserve legacy registered-student IA records when upgrading from V1.
DO $$
BEGIN
    IF to_regclass('public.internal_assessment_students') IS NOT NULL THEN
        EXECUTE $backfill$
            INSERT INTO internal_assessment_participants_v2
                (assessment_id, student_id, participant_name, roll_number,
                 course, department, semester, participant_type)
            SELECT old.assessment_id, s.student_id, s.full_name, s.roll_number,
                   COALESCE(ia.course, s.course), COALESCE(ia.department, s.department),
                   COALESCE(ia.semester, s.semester), 'registered'
            FROM internal_assessment_students old
            JOIN students s ON s.student_id=old.student_id
            JOIN internal_assessments ia ON ia.assessment_id=old.assessment_id
            ON CONFLICT DO NOTHING
        $backfill$;
    END IF;

    IF to_regclass('public.internal_assessment_student_quizzes') IS NOT NULL THEN
        EXECUTE $backfill$
            INSERT INTO internal_assessment_participant_quizzes_v2
                (assessment_id, participant_id, quiz_id, handling_status, updated_at)
            SELECT old.assessment_id, p.participant_id, old.quiz_id,
                   old.handling_status, old.updated_at
            FROM internal_assessment_student_quizzes old
            JOIN internal_assessment_participants_v2 p
              ON p.assessment_id=old.assessment_id AND p.student_id=old.student_id
            ON CONFLICT (assessment_id, participant_id, quiz_id)
            DO UPDATE SET handling_status=EXCLUDED.handling_status,
                          updated_at=EXCLUDED.updated_at
        $backfill$;
    END IF;

    IF to_regclass('public.internal_assessment_results') IS NOT NULL THEN
        EXECUTE $backfill$
            INSERT INTO internal_assessment_participant_results_v2
                (assessment_id, participant_id, average_percentage, final_marks,
                 calculation_details, finalized_at)
            SELECT old.assessment_id, p.participant_id, old.average_percentage,
                   old.final_marks, old.calculation_details, old.finalized_at
            FROM internal_assessment_results old
            JOIN internal_assessment_participants_v2 p
              ON p.assessment_id=old.assessment_id AND p.student_id=old.student_id
            ON CONFLICT (assessment_id, participant_id) DO NOTHING
        $backfill$;
    END IF;
END $$;
