"""Focused contract tests for admin student registration."""

import pytest

from app.models.people import Student
from app.schemas.student import StudentCreateRequest


V1 = "/api/v1"


def test_student_create_schema_normalizes_email_and_rejects_invalid_email(department):
    request = StudentCreateRequest(
        full_name="New Student",
        email="  NEW.STUDENT@COLLEGE.EDU  ",
        password="Temporary123!",
        roll_number="R100",
        department_id=department.id,
        semester=3,
    )
    assert str(request.email) == "new.student@college.edu"

    with pytest.raises(ValueError):
        StudentCreateRequest(
            full_name="New Student",
            email="not-an-email",
            password="Temporary123!",
            roll_number="R101",
            department_id=department.id,
            semester=3,
        )


def _payload(department_id, **overrides):
    payload = {
        "full_name": "New Student",
        "email": "new.student@college.edu",
        "password": "Temporary123!",
        "roll_number": "R100",
        "department_id": department_id,
        "semester": 3,
        "phone": "+919876543210",
        "admission_year": 2026,
    }
    payload.update(overrides)
    return payload


def test_admin_can_list_students(client, admin_headers, make_student):
    make_student("R001")

    response = client.get(f"{V1}/students", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["students"][0]["roll_number"] == "R001"


def test_admin_can_create_and_reopen_student(client, admin_headers, department, db):
    response = client.post(
        f"{V1}/students",
        headers=admin_headers,
        json=_payload(department.id),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["roll_number"] == "R100"
    assert body["student_id"] > 0
    assert "Temporary123!" not in response.text

    db.expire_all()
    stored = db.get(Student, body["student_id"])
    assert stored is not None
    assert stored.roll_number == "R100"


def test_student_creation_rejects_invalid_and_duplicate_identifiers(
    client, admin_headers, department
):
    missing = client.post(
        f"{V1}/students",
        headers=admin_headers,
        json={"full_name": "Incomplete"},
    )
    assert missing.status_code == 422

    payload = _payload(department.id)
    assert client.post(f"{V1}/students", headers=admin_headers, json=payload).status_code == 201

    duplicate_email = client.post(
        f"{V1}/students",
        headers=admin_headers,
        json=_payload(department.id, roll_number="R101"),
    )
    assert duplicate_email.status_code == 409

    duplicate_roll = client.post(
        f"{V1}/students",
        headers=admin_headers,
        json=_payload(department.id, email="other.student@college.edu", roll_number="r100"),
    )
    assert duplicate_roll.status_code == 409
