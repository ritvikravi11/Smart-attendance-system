"""
API v1 router aggregation.

WHY AN AGGREGATOR FILE
----------------------
Each endpoint module owns its own APIRouter. This file collects them into one
router that main.py mounts in a single line.

The benefit shows up as the project grows: adding students, sessions, and
recognition endpoints means three lines here and zero changes to main.py.
main.py stays a short, readable application-assembly file instead of becoming
a 200-line import list.

WHY VERSIONING FROM DAY ONE (NFR-5.4)
-------------------------------------
Every route lives under /api/v1. Right now that looks like pointless
ceremony -- there is no v2.

It is insurance. The day you need a breaking change (renaming a field,
changing a response shape), v1 clients keep working while v2 ships alongside.
Retrofitting versioning after clients exist means either breaking them or
maintaining an unversioned legacy path forever. The cost now is four
characters in a URL.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import attendance, auth, students

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(attendance.router)
api_router.include_router(students.router)

# Future modules mount here:
# api_router.include_router(students.router)
# api_router.include_router(academic.router)
# api_router.include_router(sessions.router)
# api_router.include_router(recognition.router)
# api_router.include_router(reports.router)
