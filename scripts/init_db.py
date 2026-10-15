"""
Database seeding: roles, a default admin, and optional demo data.

WHY A SEED SCRIPT EXISTS
------------------------
A freshly migrated database has correct tables and zero rows. That is
unusable: there are no roles, so no user can be created, so nobody can log in
and create anything. The system cannot bootstrap itself.

Seeding solves the chicken-and-egg problem by inserting the minimum viable
state directly.

IDEMPOTENCY IS THE KEY PROPERTY
-------------------------------
This script can be run repeatedly without creating duplicates or errors. Every
insert checks for existence first.

That matters because you WILL run it more than once -- after a schema reset,
after a teammate pulls the repo, as part of a deployment step. A seed script
that explodes on second run is a seed script people stop trusting.

Usage:
    python -m scripts.init_db             # roles + admin only
    python -m scripts.init_db --demo      # also inserts demo data
"""

import argparse
import logging
import sys
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.academic import Department, Enrollment, Subject, SubjectOffering
from app.models.enums import RoleName
from app.models.people import Faculty, Student
from app.models.user import Role, User

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
logger = logging.getLogger("seed")


ROLE_DEFINITIONS = [
    (RoleName.ADMIN, "Full system access; manages all master data"),
    (RoleName.FACULTY, "Conducts sessions and manages attendance for own subjects"),
    (RoleName.STUDENT, "Read-only access to own attendance data"),
]


def seed_roles(db: Session) -> dict[str, Role]:
    """
    Insert the three roles if absent.

    Roles are referenced by every user, so this must run before any user
    creation. Returns a name->Role map so callers avoid re-querying.
    """
    roles: dict[str, Role] = {}
    for name, description in ROLE_DEFINITIONS:
        existing = db.execute(
            select(Role).where(Role.name == name.value)
        ).scalar_one_or_none()

        if existing:
            roles[name.value] = existing
            logger.info("Role '%s' already present", name.value)
        else:
            role = Role(name=name.value, description=description)
            db.add(role)
            db.flush()  # populates role.id without committing yet
            roles[name.value] = role
            logger.info("Created role '%s'", name.value)

    db.commit()
    return roles


def seed_admin(db: Session, roles: dict[str, Role]) -> User:
    """
    Create the bootstrap administrator.

    SECURITY NOTE ON THE DEFAULT PASSWORD
    -------------------------------------
    The default below is intentionally obvious and intentionally logged as a
    warning. Weak bootstrap credentials that nobody notices are how systems
    get compromised -- the classic "admin/admin left in production" failure.

    Two mitigations: it is loud, and it is overridable via the environment.
    In any real deployment, set SEED_ADMIN_PASSWORD and change it immediately
    after first login.
    """
    import os

    admin_email = os.getenv("SEED_ADMIN_EMAIL", "admin@college.edu")
    admin_password = os.getenv("SEED_ADMIN_PASSWORD", "Admin@12345")

    existing = db.execute(
        select(User).where(User.email == admin_email)
    ).scalar_one_or_none()

    if existing:
        logger.info("Admin '%s' already present", admin_email)
        return existing

    admin = User(
        email=admin_email,
        password_hash=hash_password(admin_password),
        full_name="System Administrator",
        role_id=roles[RoleName.ADMIN.value].id,
        is_active=True,
    )
    db.add(admin)
    db.commit()

    logger.info("Created admin: %s", admin_email)
    if admin_password == "Admin@12345":
        logger.warning("=" * 62)
        logger.warning("DEFAULT ADMIN PASSWORD IN USE: %s", admin_password)
        logger.warning("Change it immediately via POST /api/v1/auth/change-password")
        logger.warning("=" * 62)

    return admin


def seed_demo_data(db: Session, roles: dict[str, Role]) -> None:
    """
    Insert a small realistic dataset for development and demos.

    Creates: one department, one faculty member, one subject, one offering,
    and three students enrolled in it. Enough to exercise every relationship
    in the schema without hand-crafting rows through the API.
    """
    dept = db.execute(
        select(Department).where(Department.code == "CSE")
    ).scalar_one_or_none()

    if dept is None:
        dept = Department(code="CSE", name="Computer Science and Engineering")
        db.add(dept)
        db.flush()
        logger.info("Created department CSE")

    # ---- Faculty ----
    fac_email = "das@college.edu"
    fac_user = db.execute(
        select(User).where(User.email == fac_email)
    ).scalar_one_or_none()

    if fac_user is None:
        fac_user = User(
            email=fac_email,
            password_hash=hash_password("Faculty@123"),
            full_name="Dr. Subhransu Das",
            role_id=roles[RoleName.FACULTY.value].id,
        )
        db.add(fac_user)
        db.flush()

        faculty = Faculty(
            user_id=fac_user.id,
            employee_code="EMP001",
            department_id=dept.id,
            designation="Assistant Professor",
        )
        db.add(faculty)
        db.flush()
        logger.info("Created faculty %s / Faculty@123", fac_email)
    else:
        faculty = db.execute(
            select(Faculty).where(Faculty.user_id == fac_user.id)
        ).scalar_one()

    # ---- Subject and offering ----
    subject = db.execute(
        select(Subject).where(Subject.code == "CS301")
    ).scalar_one_or_none()

    if subject is None:
        subject = Subject(
            code="CS301",
            name="Data Structures and Algorithms",
            department_id=dept.id,
            semester=3,
            credits=4,
        )
        db.add(subject)
        db.flush()
        logger.info("Created subject CS301")

    offering = db.execute(
        select(SubjectOffering).where(
            SubjectOffering.subject_id == subject.id,
            SubjectOffering.section == "A",
            SubjectOffering.academic_year == "2026-2027",
        )
    ).scalar_one_or_none()

    if offering is None:
        offering = SubjectOffering(
            subject_id=subject.id,
            faculty_id=faculty.id,
            section="A",
            academic_year="2026-2027",
        )
        db.add(offering)
        db.flush()
        logger.info("Created offering CS301-A 2026-2027")

    # ---- Students ----
    demo_students = [
        ("2226CSE001", "Ravi Kumar Sahu", "ravi@college.edu"),
        ("2226CSE002", "Priya Mohanty", "priya@college.edu"),
        ("2226CSE003", "Ankit Behera", "ankit@college.edu"),
    ]

    for roll, name, email in demo_students:
        existing = db.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()
        if existing:
            continue

        stu_user = User(
            email=email,
            password_hash=hash_password("Student@123"),
            full_name=name,
            role_id=roles[RoleName.STUDENT.value].id,
        )
        db.add(stu_user)
        db.flush()

        student = Student(
            user_id=stu_user.id,
            roll_number=roll,
            department_id=dept.id,
            semester=3,
            phone="9876543210",
            admission_year=2022,
            consent_given_at=datetime.now(timezone.utc),
        )
        db.add(student)
        db.flush()

        db.add(Enrollment(student_id=student.id, offering_id=offering.id))
        logger.info("Created student %s (%s) / Student@123", roll, email)

    db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the attendance database")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Also insert demo department, faculty, subject, and students",
    )
    args = parser.parse_args()

    logger.info("Seeding database: %s", settings.postgres_db)

    db = SessionLocal()
    try:
        roles = seed_roles(db)
        seed_admin(db, roles)
        if args.demo:
            seed_demo_data(db, roles)
        logger.info("Seeding complete.")
        return 0
    except Exception:
        db.rollback()
        logger.exception("Seeding failed; rolled back.")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
