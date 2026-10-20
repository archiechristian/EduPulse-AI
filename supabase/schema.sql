-- EduPulse AI schema for Supabase (Postgres).
-- Generated from backend/app/models.py by backend/scripts/export_schema.py.
-- Run in the Supabase SQL editor, or simply point DATABASE_URL at Supabase: the API creates
-- these tables automatically on startup.

CREATE TABLE IF NOT EXISTS email_logs (
	id SERIAL NOT NULL, 
	to_email VARCHAR(255) NOT NULL, 
	subject VARCHAR(255) NOT NULL, 
	body TEXT NOT NULL, 
	kind VARCHAR(40) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	error TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_email_logs_to_email ON email_logs (to_email);
CREATE INDEX IF NOT EXISTS ix_email_logs_created_at ON email_logs (created_at);

CREATE TABLE IF NOT EXISTS grading_scales (
	id SERIAL NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	bands JSON NOT NULL, 
	is_default BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS users (
	id SERIAL NOT NULL, 
	email VARCHAR(255) NOT NULL, 
	full_name VARCHAR(255) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	phone VARCHAR(40), 
	is_active BOOLEAN NOT NULL, 
	email_notifications BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	last_login_at TIMESTAMP WITHOUT TIME ZONE, 
	reset_token VARCHAR(128), 
	reset_token_expires TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email);
CREATE INDEX IF NOT EXISTS ix_users_role ON users (role);
CREATE INDEX IF NOT EXISTS ix_users_reset_token ON users (reset_token);

CREATE TABLE IF NOT EXISTS teachers (
	id SERIAL NOT NULL, 
	user_id INTEGER, 
	full_name VARCHAR(255) NOT NULL, 
	email VARCHAR(255), 
	employee_code VARCHAR(50), 
	department VARCHAR(120), 
	phone VARCHAR(40), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (user_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_teachers_email ON teachers (email);

CREATE TABLE IF NOT EXISTS classes (
	id SERIAL NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	section VARCHAR(20), 
	grade_level INTEGER, 
	academic_year VARCHAR(20), 
	class_teacher_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(class_teacher_id) REFERENCES teachers (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS exams (
	id SERIAL NOT NULL, 
	name VARCHAR(160) NOT NULL, 
	class_id INTEGER, 
	exam_type VARCHAR(40) NOT NULL, 
	date DATE, 
	max_marks FLOAT NOT NULL, 
	weight FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(class_id) REFERENCES classes (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_exams_class_id ON exams (class_id);

CREATE TABLE IF NOT EXISTS students (
	id SERIAL NOT NULL, 
	user_id INTEGER, 
	full_name VARCHAR(255) NOT NULL, 
	email VARCHAR(255), 
	roll_no VARCHAR(40), 
	class_id INTEGER, 
	parent_user_id INTEGER, 
	gender VARCHAR(20), 
	date_of_birth DATE, 
	guardian_name VARCHAR(255), 
	guardian_email VARCHAR(255), 
	guardian_phone VARCHAR(40), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (user_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL, 
	FOREIGN KEY(class_id) REFERENCES classes (id) ON DELETE SET NULL, 
	FOREIGN KEY(parent_user_id) REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_students_roll_no ON students (roll_no);
CREATE INDEX IF NOT EXISTS ix_students_email ON students (email);
CREATE INDEX IF NOT EXISTS ix_students_parent_user_id ON students (parent_user_id);
CREATE INDEX IF NOT EXISTS ix_students_class_id ON students (class_id);

CREATE TABLE IF NOT EXISTS subjects (
	id SERIAL NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	code VARCHAR(30), 
	class_id INTEGER, 
	teacher_id INTEGER, 
	pass_mark FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(class_id) REFERENCES classes (id) ON DELETE CASCADE, 
	FOREIGN KEY(teacher_id) REFERENCES teachers (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_subjects_teacher_id ON subjects (teacher_id);
CREATE INDEX IF NOT EXISTS ix_subjects_class_id ON subjects (class_id);

CREATE TABLE IF NOT EXISTS alerts (
	id SERIAL NOT NULL, 
	student_id INTEGER, 
	level VARCHAR(20) NOT NULL, 
	category VARCHAR(30) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	message TEXT NOT NULL, 
	is_read BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_alerts_created_at ON alerts (created_at);
CREATE INDEX IF NOT EXISTS ix_alerts_student_id ON alerts (student_id);

CREATE TABLE IF NOT EXISTS assignments (
	id SERIAL NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	description TEXT, 
	class_id INTEGER, 
	subject_id INTEGER, 
	teacher_id INTEGER, 
	due_date DATE, 
	max_score FLOAT NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(class_id) REFERENCES classes (id) ON DELETE CASCADE, 
	FOREIGN KEY(subject_id) REFERENCES subjects (id) ON DELETE SET NULL, 
	FOREIGN KEY(teacher_id) REFERENCES teachers (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_assignments_subject_id ON assignments (subject_id);
CREATE INDEX IF NOT EXISTS ix_assignments_class_id ON assignments (class_id);

CREATE TABLE IF NOT EXISTS attendance (
	id SERIAL NOT NULL, 
	student_id INTEGER NOT NULL, 
	class_id INTEGER, 
	date DATE NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	note VARCHAR(255), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_attendance UNIQUE (student_id, date), 
	FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE, 
	FOREIGN KEY(class_id) REFERENCES classes (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_attendance_date ON attendance (date);
CREATE INDEX IF NOT EXISTS ix_attendance_student_id ON attendance (student_id);
CREATE INDEX IF NOT EXISTS ix_attendance_class_id ON attendance (class_id);

CREATE TABLE IF NOT EXISTS remarks (
	id SERIAL NOT NULL, 
	student_id INTEGER NOT NULL, 
	author_user_id INTEGER, 
	category VARCHAR(30) NOT NULL, 
	text TEXT NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE, 
	FOREIGN KEY(author_user_id) REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_remarks_student_id ON remarks (student_id);

CREATE TABLE IF NOT EXISTS topics (
	id SERIAL NOT NULL, 
	subject_id INTEGER NOT NULL, 
	name VARCHAR(160) NOT NULL, 
	description TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(subject_id) REFERENCES subjects (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_topics_subject_id ON topics (subject_id);

CREATE TABLE IF NOT EXISTS marks (
	id SERIAL NOT NULL, 
	student_id INTEGER NOT NULL, 
	exam_id INTEGER NOT NULL, 
	subject_id INTEGER NOT NULL, 
	topic_id INTEGER, 
	score FLOAT NOT NULL, 
	max_score FLOAT NOT NULL, 
	remarks TEXT, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_mark UNIQUE (student_id, exam_id, subject_id, topic_id), 
	FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE, 
	FOREIGN KEY(exam_id) REFERENCES exams (id) ON DELETE CASCADE, 
	FOREIGN KEY(subject_id) REFERENCES subjects (id) ON DELETE CASCADE, 
	FOREIGN KEY(topic_id) REFERENCES topics (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_marks_student_id ON marks (student_id);
CREATE INDEX IF NOT EXISTS ix_marks_topic_id ON marks (topic_id);
CREATE INDEX IF NOT EXISTS ix_marks_subject_id ON marks (subject_id);
CREATE INDEX IF NOT EXISTS ix_marks_exam_id ON marks (exam_id);

CREATE TABLE IF NOT EXISTS submissions (
	id SERIAL NOT NULL, 
	assignment_id INTEGER NOT NULL, 
	student_id INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	score FLOAT, 
	feedback TEXT, 
	submitted_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_submission UNIQUE (assignment_id, student_id), 
	FOREIGN KEY(assignment_id) REFERENCES assignments (id) ON DELETE CASCADE, 
	FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_submissions_assignment_id ON submissions (assignment_id);
CREATE INDEX IF NOT EXISTS ix_submissions_student_id ON submissions (student_id);

-- The FastAPI backend connects with the service connection string and enforces role-based
-- access itself. Enable RLS so the tables are not exposed through Supabase's public REST API.
ALTER TABLE email_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE grading_scales ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE teachers ENABLE ROW LEVEL SECURITY;
ALTER TABLE classes ENABLE ROW LEVEL SECURITY;
ALTER TABLE exams ENABLE ROW LEVEL SECURITY;
ALTER TABLE students ENABLE ROW LEVEL SECURITY;
ALTER TABLE subjects ENABLE ROW LEVEL SECURITY;
ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE attendance ENABLE ROW LEVEL SECURITY;
ALTER TABLE remarks ENABLE ROW LEVEL SECURITY;
ALTER TABLE topics ENABLE ROW LEVEL SECURITY;
ALTER TABLE marks ENABLE ROW LEVEL SECURITY;
ALTER TABLE submissions ENABLE ROW LEVEL SECURITY;
