"""Student registration and listing operations."""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import hash_password
from app.models.academic import Department
from app.models.enums import RoleName
from app.models.people import Student
from app.models.user import Role, User
from app.schemas.student import StudentCreateRequest


class StudentService:
    def __init__(self, db: Session):
        self.db = db

    def list_students(self) -> tuple[list[Student], int]:
        query = (
            select(Student)
            .options(joinedload(Student.user), joinedload(Student.department))
            .where(Student.is_active.is_(True))
            .order_by(Student.roll_number)
        )
        students = self.db.execute(query).scalars().all()
        total = self.db.scalar(
            select(func.count(Student.id)).where(Student.is_active.is_(True))
        ) or 0
        return students, total

    def create(self, payload: StudentCreateRequest) -> Student:
        email = str(payload.email).lower()
        roll_number = payload.roll_number.strip()

        if self.db.scalar(select(User.id).where(User.email.ilike(email))) is not None:
            raise ConflictError("A user with this email already exists.", error_code="DUPLICATE_EMAIL")
        if self.db.scalar(
            select(Student.id).where(Student.roll_number == roll_number)
        ) is not None:
            raise ConflictError(
                "A student with this roll number already exists.",
                error_code="DUPLICATE_ROLL_NUMBER",
            )

        role = self.db.scalar(select(Role).where(Role.name == RoleName.STUDENT.value))
        if role is None:
            raise NotFoundError("Student role is not configured.")
        if self.db.get(Department, payload.department_id) is None:
            raise NotFoundError("Department not found.")

        user = User(
            email=email,
            password_hash=hash_password(payload.password),
            full_name=payload.full_name.strip(),
            role_id=role.id,
        )
        student = Student(
            user=user,
            roll_number=roll_number,
            department_id=payload.department_id,
            semester=payload.semester,
            phone=payload.phone.strip() if payload.phone else None,
            admission_year=payload.admission_year,
        )
        self.db.add(student)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ConflictError(
                "A student with this email or roll number already exists.",
                error_code="DUPLICATE_STUDENT",
            ) from exc

        self.db.refresh(student)
        return student

    def list_departments(self) -> list[Department]:
        return list(
            self.db.scalars(
                select(Department)
                .where(Department.is_active.is_(True))
                .order_by(Department.name)
            )
        )
