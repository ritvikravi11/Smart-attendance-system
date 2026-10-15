"""
Integration tests for the authentication module.

Runs the REAL FastAPI application against an in-memory SQLite database via
TestClient -- no mocking of the auth logic. Every test exercises the full
stack: HTTP -> router -> dependency -> service -> database.

Run with:  python tests/test_auth.py
"""

import os
import sys

sys.path.insert(0, "/home/claude/backend")
os.environ["SECRET_KEY"] = "test-secret-key-for-integration-tests-only"

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, Integer as SAInteger, create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause
from sqlalchemy.sql import text as sql_text

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.base_class import Base
from app.db.session import get_db
from app.main import app
from app.models.user import RefreshToken, Role, User

GREEN, RED, RESET = "\033[92m", "\033[91m", "\033[0m"
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f" -- {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Test database: SQLite in memory, PostgreSQL-isms bridged
# ---------------------------------------------------------------------------
for _t in Base.metadata.tables.values():
    for _c in _t.columns:
        sd = _c.server_default
        if isinstance(sd, DefaultClause) and "now()" in str(sd.arg).lower():
            _c.server_default = DefaultClause(sql_text("CURRENT_TIMESTAMP"))
        if _c.primary_key and isinstance(_c.type, BigInteger):
            _c.type = SAInteger()

for _t in Base.metadata.sorted_tables:
    _t.indexes = {
        i
        for i in _t.indexes
        if i.dialect_options.get("postgresql", {}).get("where") is None
    }

# StaticPool: SQLite ':memory:' creates a SEPARATE database per connection.
# Without this, the test seeds data on one connection and the app queries a
# different, empty one -- producing "no such table". StaticPool forces every
# session to reuse a single connection, so they all see the same database.
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(engine, "connect")
def _fk_on(conn, _):
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


TestSession = sessionmaker(bind=engine, expire_on_commit=False)
Base.metadata.create_all(engine)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app, raise_server_exceptions=False)

# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
db = TestSession()
r_admin = Role(name="admin", description="Admin")
r_fac = Role(name="faculty", description="Faculty")
r_stu = Role(name="student", description="Student")
db.add_all([r_admin, r_fac, r_stu])
db.commit()

admin = User(
    email="admin@college.edu",
    password_hash=hash_password("Admin@12345"),
    full_name="System Administrator",
    role_id=r_admin.id,
)
faculty = User(
    email="das@college.edu",
    password_hash=hash_password("Faculty@123"),
    full_name="Dr. Das",
    role_id=r_fac.id,
)
inactive = User(
    email="gone@college.edu",
    password_hash=hash_password("Gone@12345"),
    full_name="Former Staff",
    role_id=r_fac.id,
    is_active=False,
)
db.add_all([admin, faculty, inactive])
db.commit()

BASE = "/api/v1/auth"

print("\n" + "=" * 72)
print("MODULE 1 -- AUTHENTICATION: INTEGRATION TESTS")
print("=" * 72)

# ---------------------------------------------------------------------------
print("\n[1] Password hashing")
# ---------------------------------------------------------------------------
h1 = hash_password("SamePassword123")
h2 = hash_password("SamePassword123")
check("Identical passwords produce different hashes", h1 != h2, "salting works")
check("Correct password verifies", verify_password("SamePassword123", h1))
check("Wrong password rejected", not verify_password("WrongPassword", h1))
check("Hash uses bcrypt 2b format", h1.startswith("$2b$12$"), h1[:7])
check("Malformed hash returns False, not crash", not verify_password("x", "garbage"))

try:
    hash_password("x" * 100)
    check("Over-72-byte password rejected", False)
except ValueError:
    check("Over-72-byte password rejected", True, "prevents silent truncation")

# ---------------------------------------------------------------------------
print("\n[2] JWT token mechanics")
# ---------------------------------------------------------------------------
access = create_access_token(user_id=42, role="faculty")
refresh = create_refresh_token(user_id=42)

payload = decode_token(access, expected_type="access")
check("Access token decodes", payload["sub"] == "42", f"sub={payload['sub']}")
check("sub is a string, per RFC 7519", isinstance(payload["sub"], str))
check("Role embedded in access token", payload["role"] == "faculty")
check("Refresh token carries no role", "role" not in decode_token(refresh, "refresh"))

# The type-confusion defence
try:
    decode_token(refresh, expected_type="access")
    check("Refresh token rejected as access token", False)
except Exception:
    check("Refresh token rejected as access token", True, "type claim enforced")

# Tampering
tampered = access[:-6] + "aaaaaa"
try:
    decode_token(tampered, expected_type="access")
    check("Tampered signature rejected", False)
except Exception:
    check("Tampered signature rejected", True)

# The alg=none attack
import base64, json

hdr = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=")
bdy = base64.urlsafe_b64encode(
    json.dumps({"sub": "1", "role": "admin", "type": "access"}).encode()
).rstrip(b"=")
forged = f"{hdr.decode()}.{bdy.decode()}."
try:
    decode_token(forged, expected_type="access")
    check("alg=none forgery rejected", False, "CRITICAL VULNERABILITY")
except Exception:
    check("alg=none forgery rejected", True, "algorithms= pinning works")

# ---------------------------------------------------------------------------
print("\n[3] Login endpoint")
# ---------------------------------------------------------------------------
r = client.post(BASE + "/login", json={"email": "admin@college.edu", "password": "Admin@12345"})
check("Valid login returns 200", r.status_code == 200, f"got {r.status_code}")
body = r.json()
check("Response contains access_token", "access_token" in body)
check("Response contains refresh_token", "refresh_token" in body)
check("token_type is bearer", body.get("token_type") == "bearer")
check("expires_in is seconds", body.get("expires_in") == 1800, f"{body.get('expires_in')}")

admin_access = body["access_token"]
admin_refresh = body["refresh_token"]

r = client.post(BASE + "/login", json={"email": "ADMIN@College.EDU", "password": "Admin@12345"})
check("Email lookup is case-insensitive", r.status_code == 200, f"got {r.status_code}")

r = client.post(BASE + "/login", json={"email": "admin@college.edu", "password": "wrong"})
check("Wrong password returns 401", r.status_code == 401)
check(
    "Error message does not reveal account existence",
    "Incorrect email or password" in r.json()["detail"],
    "prevents enumeration",
)

r2 = client.post(BASE + "/login", json={"email": "nobody@college.edu", "password": "wrong"})
check(
    "Unknown email gives IDENTICAL message to wrong password",
    r2.json()["detail"] == r.json()["detail"],
)

r = client.post(BASE + "/login", json={"email": "gone@college.edu", "password": "Gone@12345"})
check("Deactivated account rejected", r.status_code == 403, f"got {r.status_code}")

r = client.post(BASE + "/login", json={"email": "not-an-email", "password": "x"})
check("Malformed email returns 422", r.status_code == 422)
check("Validation error uses standard envelope", r.json().get("error_code") == "VALIDATION_ERROR")

# ---------------------------------------------------------------------------
print("\n[4] Account lockout")
# ---------------------------------------------------------------------------
locked_user = User(
    email="target@college.edu",
    password_hash=hash_password("Target@123"),
    full_name="Lockout Target",
    role_id=r_fac.id,
)
db.add(locked_user)
db.commit()

codes = []
for _ in range(5):
    rr = client.post(BASE + "/login", json={"email": "target@college.edu", "password": "bad"})
    codes.append(rr.status_code)
check("First 5 bad attempts all return 401", codes == [401] * 5, str(codes))

rr = client.post(BASE + "/login", json={"email": "target@college.edu", "password": "bad"})
check("6th attempt returns 423 Locked", rr.status_code == 423, f"got {rr.status_code}")

rr = client.post(BASE + "/login", json={"email": "target@college.edu", "password": "Target@123"})
check("CORRECT password still blocked while locked", rr.status_code == 423, "lockout precedes password check")

db.refresh(locked_user)
locked_user.locked_until = None
locked_user.failed_login_count = 0
db.commit()
rr = client.post(BASE + "/login", json={"email": "target@college.edu", "password": "Target@123"})
check("Login works after lock expires", rr.status_code == 200)

# ---------------------------------------------------------------------------
print("\n[5] Protected endpoints and RBAC")
# ---------------------------------------------------------------------------
r = client.get(BASE + "/me")
check("No token returns 401", r.status_code == 401)

r = client.get(BASE + "/me", headers={"Authorization": "Bearer not.a.token"})
check("Garbage token returns 401", r.status_code == 401)

r = client.get(BASE + "/me", headers={"Authorization": f"Bearer {admin_access}"})
check("Valid token returns 200", r.status_code == 200, f"got {r.status_code}")
me = r.json()
check("Returns correct user", me.get("email") == "admin@college.edu")
check("Returns nested role", me.get("role", {}).get("name") == "admin")
check("*** password_hash NEVER exposed ***", "password_hash" not in me)
check("failed_login_count not exposed", "failed_login_count" not in me)
check("locked_until not exposed", "locked_until" not in me)

expired = create_access_token(user_id=admin.id, role="admin")
import jwt as pyjwt
from app.core.config import settings as cfg

expired_token = pyjwt.encode(
    {
        "sub": str(admin.id),
        "role": "admin",
        "type": "access",
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        "iat": datetime.now(timezone.utc) - timedelta(hours=1),
    },
    cfg.secret_key,
    algorithm=cfg.algorithm,
)
r = client.get(BASE + "/me", headers={"Authorization": f"Bearer {expired_token}"})
check("Expired token returns 401", r.status_code == 401)

r = client.get(BASE + "/me", headers={"Authorization": f"Bearer {admin_refresh}"})
check("Refresh token rejected on protected route", r.status_code == 401, "type confusion blocked")

# Deactivation takes effect immediately, mid-session
fac_login = client.post(BASE + "/login", json={"email": "das@college.edu", "password": "Faculty@123"}).json()
fac_access = fac_login["access_token"]
r = client.get(BASE + "/me", headers={"Authorization": f"Bearer {fac_access}"})
check("Faculty token works before deactivation", r.status_code == 200)

db.refresh(faculty)
faculty.is_active = False
db.commit()
r = client.get(BASE + "/me", headers={"Authorization": f"Bearer {fac_access}"})
check(
    "*** Deactivation blocks an UNEXPIRED token immediately ***",
    r.status_code == 403,
    "this is why get_current_user hits the DB",
)
faculty.is_active = True
db.commit()

# ---------------------------------------------------------------------------
print("\n[6] Refresh and rotation")
# ---------------------------------------------------------------------------
r = client.post(BASE + "/refresh", json={"refresh_token": admin_refresh})
check("Refresh returns 200", r.status_code == 200, f"got {r.status_code}")
rotated = r.json()
check("New access token issued", rotated["access_token"] != admin_access)
check("*** Refresh token ROTATED ***", rotated["refresh_token"] != admin_refresh)

r = client.post(BASE + "/refresh", json={"refresh_token": admin_refresh})
check("Old refresh token now revoked", r.status_code == 401, "rotation prevents replay")

new_refresh = rotated["refresh_token"]
new_access = rotated["access_token"]
r = client.get(BASE + "/me", headers={"Authorization": f"Bearer {new_access}"})
check("Rotated access token works", r.status_code == 200)

r = client.post(BASE + "/refresh", json={"refresh_token": "garbage"})
check("Invalid refresh token returns 401", r.status_code == 401)

stored_count = db.execute(
    select(RefreshToken).where(RefreshToken.user_id == admin.id)
).scalars().all()
check(
    "Refresh tokens stored HASHED, never raw",
    all(t.token_hash != new_refresh and len(t.token_hash) == 64 for t in stored_count),
    "SHA-256 hex = 64 chars",
)

# ---------------------------------------------------------------------------
print("\n[7] Logout")
# ---------------------------------------------------------------------------
r = client.post(
    BASE + "/logout",
    json={"refresh_token": new_refresh},
    headers={"Authorization": f"Bearer {new_access}"},
)
check("Logout returns 200", r.status_code == 200)

r = client.post(BASE + "/refresh", json={"refresh_token": new_refresh})
check("Revoked token cannot refresh", r.status_code == 401)

r = client.post(
    BASE + "/logout",
    json={"refresh_token": new_refresh},
    headers={"Authorization": f"Bearer {new_access}"},
)
check("Logout is idempotent", r.status_code == 200, "double logout is not an error")

# ---------------------------------------------------------------------------
print("\n[8] Password change")
# ---------------------------------------------------------------------------
tok = client.post(BASE + "/login", json={"email": "admin@college.edu", "password": "Admin@12345"}).json()
acc, ref = tok["access_token"], tok["refresh_token"]

r = client.post(
    BASE + "/change-password",
    json={"current_password": "WrongOne123", "new_password": "NewPass@456"},
    headers={"Authorization": f"Bearer {acc}"},
)
check("Wrong current password rejected", r.status_code == 401, "stops session hijack")

r = client.post(
    BASE + "/change-password",
    json={"current_password": "Admin@12345", "new_password": "short"},
    headers={"Authorization": f"Bearer {acc}"},
)
check("Weak new password rejected", r.status_code == 422)

r = client.post(
    BASE + "/change-password",
    json={"current_password": "Admin@12345", "new_password": "Admin@12345"},
    headers={"Authorization": f"Bearer {acc}"},
)
check("Reusing same password rejected", r.status_code == 400)

r = client.post(
    BASE + "/change-password",
    json={"current_password": "Admin@12345", "new_password": "NewPass@456"},
    headers={"Authorization": f"Bearer {acc}"},
)
check("Valid change returns 200", r.status_code == 200, f"got {r.status_code}")

r = client.post(BASE + "/refresh", json={"refresh_token": ref})
check("*** All sessions revoked after change ***", r.status_code == 401)

r = client.post(BASE + "/login", json={"email": "admin@college.edu", "password": "NewPass@456"})
check("New password works", r.status_code == 200)
r = client.post(BASE + "/login", json={"email": "admin@college.edu", "password": "Admin@12345"})
check("Old password rejected", r.status_code == 401)

# ---------------------------------------------------------------------------
print("\n[9] System endpoints")
# ---------------------------------------------------------------------------
r = client.get("/health")
check("Health check unauthenticated", r.status_code == 200 and r.json()["status"] == "ok")
r = client.get("/openapi.json")
check("OpenAPI spec generated", r.status_code == 200)
paths = r.json().get("paths", {})
check("All auth routes documented", len([p for p in paths if "/auth/" in p]) == 6, f"{len([p for p in paths if '/auth/' in p])} routes")

# ---------------------------------------------------------------------------
print("\n" + "=" * 72)
passed = sum(1 for _, ok in results if ok)
total = len(results)
colour = GREEN if passed == total else RED
print(f"{colour}RESULT: {passed}/{total} checks passed{RESET}")
print("=" * 72 + "\n")

db.close()
sys.exit(0 if passed == total else 1)
