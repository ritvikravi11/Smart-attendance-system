-- =====================================================================
-- AI Attendance Management System -- PostgreSQL Schema
-- Generated from SQLAlchemy models. Do not edit by hand;
-- change the models and regenerate.
-- =====================================================================

-- --------------------------------------------------------------------
-- DEPARTMENTS
-- --------------------------------------------------------------------
CREATE TABLE departments (
	id SERIAL NOT NULL, 
	code VARCHAR(10) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code), 
	UNIQUE (name)
);

CREATE INDEX ix_departments_is_active ON departments (is_active);

-- --------------------------------------------------------------------
-- ROLES
-- --------------------------------------------------------------------
CREATE TABLE roles (
	id SERIAL NOT NULL, 
	name VARCHAR(20) NOT NULL, 
	description VARCHAR(200), 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

-- --------------------------------------------------------------------
-- SUBJECTS -- Catalogue of courses; the syllabus definition
-- --------------------------------------------------------------------
CREATE TABLE subjects (
	id SERIAL NOT NULL, 
	code VARCHAR(20) NOT NULL, 
	name VARCHAR(150) NOT NULL, 
	department_id INTEGER NOT NULL, 
	semester SMALLINT NOT NULL, 
	credits SMALLINT, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_subject_semester_range CHECK (semester BETWEEN 1 AND 8), 
	CONSTRAINT ck_subject_credits_range CHECK (credits IS NULL OR credits BETWEEN 1 AND 10), 
	UNIQUE (code), 
	FOREIGN KEY(department_id) REFERENCES departments (id) ON DELETE RESTRICT
);

CREATE INDEX idx_subjects_dept_sem ON subjects (department_id, semester) WHERE is_active = true;
CREATE INDEX ix_subjects_is_active ON subjects (is_active);

-- --------------------------------------------------------------------
-- USERS -- Authentication identity for every human in the system
-- --------------------------------------------------------------------
CREATE TABLE users (
	id BIGSERIAL NOT NULL, 
	email VARCHAR(150) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	full_name VARCHAR(150) NOT NULL, 
	role_id INTEGER NOT NULL, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	failed_login_count INTEGER DEFAULT '0' NOT NULL, 
	locked_until TIMESTAMP WITH TIME ZONE, 
	last_login_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (email), 
	FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX idx_users_email_lower ON users (lower(email));
CREATE INDEX idx_users_role_active ON users (role_id) WHERE is_active = true;

-- --------------------------------------------------------------------
-- AUDIT_LOGS -- Immutable trail of state-changing actions
-- --------------------------------------------------------------------
CREATE TABLE audit_logs (
	id BIGSERIAL NOT NULL, 
	user_id BIGINT, 
	action VARCHAR(50) NOT NULL, 
	entity_type VARCHAR(50) NOT NULL, 
	entity_id BIGINT, 
	details JSONB, 
	ip_address VARCHAR(45), 
	user_agent VARCHAR(255), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX idx_audit_action ON audit_logs (action);
CREATE INDEX idx_audit_created ON audit_logs (created_at DESC);
CREATE INDEX idx_audit_entity ON audit_logs (entity_type, entity_id);
CREATE INDEX idx_audit_user ON audit_logs (user_id);

-- --------------------------------------------------------------------
-- FACULTY -- Teaching staff profile; 1:1 with users
-- --------------------------------------------------------------------
CREATE TABLE faculty (
	id BIGSERIAL NOT NULL, 
	user_id BIGINT NOT NULL, 
	employee_code VARCHAR(30) NOT NULL, 
	department_id INTEGER NOT NULL, 
	designation VARCHAR(60), 
	phone VARCHAR(15), 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (user_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	UNIQUE (employee_code), 
	FOREIGN KEY(department_id) REFERENCES departments (id) ON DELETE RESTRICT
);

CREATE INDEX idx_faculty_dept ON faculty (department_id) WHERE is_active = true;
CREATE INDEX ix_faculty_is_active ON faculty (is_active);

-- --------------------------------------------------------------------
-- REFRESH_TOKENS -- Revocable refresh tokens; enables real logout
-- --------------------------------------------------------------------
CREATE TABLE refresh_tokens (
	id BIGSERIAL NOT NULL, 
	user_id BIGINT NOT NULL, 
	token_hash VARCHAR(255) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	user_agent VARCHAR(255), 
	ip_address VARCHAR(45), 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	UNIQUE (token_hash)
);

CREATE INDEX idx_refresh_expires ON refresh_tokens (expires_at);
CREATE INDEX idx_refresh_user_valid ON refresh_tokens (user_id) WHERE revoked_at IS NULL;

-- --------------------------------------------------------------------
-- STUDENTS -- Learner profile; 1:1 with users
-- --------------------------------------------------------------------
CREATE TABLE students (
	id BIGSERIAL NOT NULL, 
	user_id BIGINT NOT NULL, 
	roll_number VARCHAR(30) NOT NULL, 
	department_id INTEGER NOT NULL, 
	semester SMALLINT NOT NULL, 
	phone VARCHAR(15), 
	admission_year SMALLINT, 
	consent_given_at TIMESTAMP WITH TIME ZONE, 
	consent_revoked_at TIMESTAMP WITH TIME ZONE, 
	face_enrolled BOOLEAN DEFAULT 'false' NOT NULL, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_student_semester_range CHECK (semester BETWEEN 1 AND 8), 
	CONSTRAINT ck_student_consent_order CHECK (consent_revoked_at IS NULL OR consent_given_at IS NOT NULL), 
	UNIQUE (user_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	UNIQUE (roll_number), 
	FOREIGN KEY(department_id) REFERENCES departments (id) ON DELETE RESTRICT
);

CREATE INDEX idx_students_dept_sem ON students (department_id, semester) WHERE is_active = true;
CREATE INDEX idx_students_not_enrolled ON students (id) WHERE face_enrolled = false AND is_active = true;
CREATE INDEX idx_students_roll ON students (roll_number);
CREATE INDEX ix_students_is_active ON students (is_active);

-- --------------------------------------------------------------------
-- FACE_EMBEDDINGS -- Biometric templates. Most sensitive table in the system.
-- --------------------------------------------------------------------
CREATE TABLE face_embeddings (
	id BIGSERIAL NOT NULL, 
	student_id BIGINT NOT NULL, 
	embedding BYTEA NOT NULL, 
	model_name VARCHAR(50) NOT NULL, 
	model_version VARCHAR(20) NOT NULL, 
	quality_score FLOAT, 
	is_primary BOOLEAN DEFAULT 'false' NOT NULL, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	source_image_count INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_embedding_byte_length CHECK (octet_length(embedding) = 2048), 
	CONSTRAINT ck_embedding_quality_range CHECK (quality_score IS NULL OR quality_score BETWEEN 0 AND 1), 
	FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE
);

CREATE INDEX idx_embeddings_active_primary ON face_embeddings (student_id, model_name) WHERE is_active = true AND is_primary = true;
CREATE UNIQUE INDEX uq_embedding_one_primary_per_student ON face_embeddings (student_id) WHERE is_primary = true AND is_active = true;

-- --------------------------------------------------------------------
-- SUBJECT_OFFERINGS -- One teaching instance: subject + faculty + section + year. Attendance attaches HERE, never directly to a subject.
-- --------------------------------------------------------------------
CREATE TABLE subject_offerings (
	id BIGSERIAL NOT NULL, 
	subject_id INTEGER NOT NULL, 
	faculty_id BIGINT NOT NULL, 
	section VARCHAR(10) DEFAULT 'A' NOT NULL, 
	academic_year VARCHAR(9) NOT NULL, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_offering_subject_section_year UNIQUE (subject_id, section, academic_year), 
	CONSTRAINT ck_offering_year_format CHECK (academic_year LIKE '____-____'), 
	FOREIGN KEY(subject_id) REFERENCES subjects (id) ON DELETE RESTRICT, 
	FOREIGN KEY(faculty_id) REFERENCES faculty (id) ON DELETE RESTRICT
);

CREATE INDEX idx_offerings_faculty ON subject_offerings (faculty_id) WHERE is_active = true;
CREATE INDEX idx_offerings_subject_year ON subject_offerings (subject_id, academic_year);
CREATE INDEX ix_subject_offerings_is_active ON subject_offerings (is_active);

-- --------------------------------------------------------------------
-- ATTENDANCE_SESSIONS -- One occurrence of a class; groups attendance records
-- --------------------------------------------------------------------
CREATE TABLE attendance_sessions (
	id BIGSERIAL NOT NULL, 
	offering_id BIGINT NOT NULL, 
	session_date DATE NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	ended_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(15) DEFAULT 'open' NOT NULL, 
	room VARCHAR(30), 
	late_threshold_minutes INTEGER, 
	created_by BIGINT NOT NULL, 
	notes VARCHAR(255), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_session_status_valid CHECK (status IN ('open', 'closed', 'cancelled')), 
	CONSTRAINT ck_session_ended_matches_status CHECK ((status = 'open'  AND ended_at IS NULL) OR (status <> 'open' AND ended_at IS NOT NULL)), 
	CONSTRAINT ck_session_end_after_start CHECK (ended_at IS NULL OR ended_at >= started_at), 
	FOREIGN KEY(offering_id) REFERENCES subject_offerings (id) ON DELETE RESTRICT, 
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX idx_sessions_date ON attendance_sessions (session_date);
CREATE INDEX idx_sessions_offering_date ON attendance_sessions (offering_id, session_date);
CREATE INDEX idx_sessions_open ON attendance_sessions (offering_id) WHERE status = 'open';

-- --------------------------------------------------------------------
-- ENROLLMENTS -- Junction: resolves many-to-many students <-> offerings
-- --------------------------------------------------------------------
CREATE TABLE enrollments (
	id BIGSERIAL NOT NULL, 
	student_id BIGINT NOT NULL, 
	offering_id BIGINT NOT NULL, 
	enrolled_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_enrollment_student_offering UNIQUE (student_id, offering_id), 
	FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE, 
	FOREIGN KEY(offering_id) REFERENCES subject_offerings (id) ON DELETE CASCADE
);

CREATE INDEX idx_enroll_offering ON enrollments (offering_id) WHERE is_active = true;
CREATE INDEX idx_enroll_student ON enrollments (student_id) WHERE is_active = true;
CREATE INDEX ix_enrollments_is_active ON enrollments (is_active);

-- --------------------------------------------------------------------
-- ATTENDANCE_RECORDS -- One student's status in one session. UNIQUE(session_id, student_id) is the duplicate-prevention guarantee.
-- --------------------------------------------------------------------
CREATE TABLE attendance_records (
	id BIGSERIAL NOT NULL, 
	session_id BIGINT NOT NULL, 
	student_id BIGINT NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	method VARCHAR(10) NOT NULL, 
	confidence FLOAT, 
	needs_review BOOLEAN DEFAULT 'false' NOT NULL, 
	marked_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	marked_by BIGINT, 
	overridden_by BIGINT, 
	overridden_at TIMESTAMP WITH TIME ZONE, 
	notes VARCHAR(255), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_attendance_session_student UNIQUE (session_id, student_id), 
	CONSTRAINT ck_record_status_valid CHECK (status IN ('present', 'absent', 'late', 'excused')), 
	CONSTRAINT ck_record_method_valid CHECK (method IN ('face', 'manual')), 
	CONSTRAINT ck_record_confidence_range CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1), 
	CONSTRAINT ck_record_method_requires_provenance CHECK ((method = 'face'   AND confidence IS NOT NULL) OR (method = 'manual' AND marked_by  IS NOT NULL)), 
	CONSTRAINT ck_record_override_complete CHECK ((overridden_by IS NULL AND overridden_at IS NULL) OR (overridden_by IS NOT NULL AND overridden_at IS NOT NULL)), 
	FOREIGN KEY(session_id) REFERENCES attendance_sessions (id) ON DELETE CASCADE, 
	FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE RESTRICT, 
	FOREIGN KEY(marked_by) REFERENCES users (id) ON DELETE SET NULL, 
	FOREIGN KEY(overridden_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX idx_records_needs_review ON attendance_records (session_id) WHERE needs_review = true;
CREATE INDEX idx_records_session ON attendance_records (session_id);
CREATE INDEX idx_records_student ON attendance_records (student_id);
CREATE INDEX idx_records_student_status ON attendance_records (student_id, status);

-- --------------------------------------------------------------------
-- RECOGNITION_LOGS -- Every recognition attempt. The measurement instrument for threshold calibration and accuracy reporting.
-- --------------------------------------------------------------------
CREATE TABLE recognition_logs (
	id BIGSERIAL NOT NULL, 
	session_id BIGINT, 
	matched_student_id BIGINT, 
	confidence FLOAT, 
	is_unknown BOOLEAN DEFAULT 'false' NOT NULL, 
	was_marked BOOLEAN DEFAULT 'false' NOT NULL, 
	detection_score FLOAT, 
	face_width_px INTEGER, 
	processing_ms INTEGER, 
	captured_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_reclog_confidence_range CHECK (confidence IS NULL OR confidence BETWEEN -1 AND 1), 
	CONSTRAINT ck_reclog_unknown_consistency CHECK ((is_unknown = true  AND matched_student_id IS NULL) OR (is_unknown = false AND matched_student_id IS NOT NULL)), 
	FOREIGN KEY(session_id) REFERENCES attendance_sessions (id) ON DELETE SET NULL, 
	FOREIGN KEY(matched_student_id) REFERENCES students (id) ON DELETE SET NULL
);

CREATE INDEX idx_reclog_captured ON recognition_logs (captured_at);
CREATE INDEX idx_reclog_session ON recognition_logs (session_id);
CREATE INDEX idx_reclog_unknown ON recognition_logs (captured_at) WHERE is_unknown = true;

-- =====================================================================
-- Tables:            14
-- Indexes:           35  (of which 14 are partial)
-- Foreign keys:      21
-- CHECK constraints: 17
-- UNIQUE constraints:13
-- =====================================================================
