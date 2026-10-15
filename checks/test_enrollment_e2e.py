"""End-to-end enrolment against the real model and real faces."""
import os, sys, io
sys.path.insert(0,'.')
os.environ['SECRET_KEY']='e2e-secret-key-for-enrolment-test-32bytes'
os.environ['LOAD_ML_ON_STARTUP']='false'
os.environ['FACE_MIN_FACE_WIDTH_PX']='40'

import cv2, insightface, numpy as np
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, Integer as SAInteger, create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause
from sqlalchemy.sql import text as st

from app.core.security import hash_password
from app.db.session import get_db
from app.models import Base
from app.models.academic import Department
from app.models.people import Student
from app.models.user import Role, User

for t in Base.metadata.tables.values():
    for c in t.columns:
        sd=c.server_default
        if isinstance(sd, DefaultClause) and "now()" in str(sd.arg).lower():
            c.server_default = DefaultClause(st("CURRENT_TIMESTAMP"))
        if c.primary_key and isinstance(c.type, BigInteger): c.type = SAInteger()
for t in Base.metadata.sorted_tables:
    t.indexes = {i for i in t.indexes if i.dialect_options.get("postgresql",{}).get("where") is None}

eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread":False}, poolclass=StaticPool)
@event.listens_for(eng,"connect")
def _fk(c,_):
    cur=c.cursor(); cur.execute("PRAGMA foreign_keys=ON"); cur.close()
Base.metadata.create_all(eng)
S = sessionmaker(bind=eng, expire_on_commit=False)

def override():
    d=S()
    try: yield d
    finally: d.close()

from app.main import app
app.dependency_overrides[get_db]=override
client = TestClient(app, raise_server_exceptions=False)

db=S()
roles={n:Role(name=n) for n in ("admin","faculty","student")}
db.add_all(roles.values()); db.commit()
dept=Department(code="CSE",name="CS"); db.add(dept); db.commit()
ua=User(email="admin@c.edu",password_hash=hash_password("Admin@1234"),full_name="Admin",role_id=roles["admin"].id)
uf=User(email="fac@c.edu",password_hash=hash_password("Fac@12345"),full_name="Fac",role_id=roles["faculty"].id)
us=User(email="stu@c.edu",password_hash=hash_password("Stu@12345"),full_name="Ravi",role_id=roles["student"].id)
db.add_all([ua,uf,us]); db.commit()
stu=Student(user_id=us.id,roll_number="2226CSE001",department_id=dept.id,semester=3)
db.add(stu); db.commit()

def login(e,p):
    r=client.post("/api/v1/auth/login",json={"email":e,"password":p})
    return {"Authorization":f"Bearer {r.json()['access_token']}"}
A=login("admin@c.edu","Admin@1234"); F=login("fac@c.edu","Fac@12345")

# Load the real model
from app.ml.engine import get_engine
get_engine().load()

# Build 8 realistic variants of ONE real face
IMG=os.path.join(os.path.dirname(insightface.__file__),'data','images')
group=cv2.imread(os.path.join(IMG,'t1.jpg'))
from app.ml.detector import ScrfdDetector
faces=ScrfdDetector().detect(group)
b=faces[4].bbox; pad=int(b.width*0.8)
crop=group[max(0,b.y1-pad):b.y2+pad, max(0,b.x1-pad):b.x2+pad]
h,w=crop.shape[:2]
def enc(im): return cv2.imencode('.jpg', im)[1].tobytes()
variants=[crop,
  cv2.convertScaleAbs(crop,alpha=1.15,beta=12), cv2.convertScaleAbs(crop,alpha=0.88,beta=-10),
  cv2.warpAffine(crop,cv2.getRotationMatrix2D((w/2,h/2),6,1.0),(w,h),borderMode=cv2.BORDER_REPLICATE),
  cv2.warpAffine(crop,cv2.getRotationMatrix2D((w/2,h/2),-6,1.0),(w,h),borderMode=cv2.BORDER_REPLICATE),
  cv2.GaussianBlur(crop,(3,3),0),
  cv2.convertScaleAbs(crop,alpha=1.05,beta=5),
  cv2.resize(crop,(int(w*1.1),int(h*1.1)))]
files=[("images",(f"i{i}.jpg", io.BytesIO(enc(v)), "image/jpeg")) for i,v in enumerate(variants)]

P="\033[92mPASS\033[0m"; FL="\033[91mFAIL\033[0m"
res=[]
def ck(n,ok,d=""):
    res.append(ok); print(f"  [{P if ok else FL}] {n}"+(f" -- {d}" if d else ""))

print("\n"+"="*70); print("END-TO-END: ENROLMENT API"); print("="*70)

print("\n[1] Authorization")
r=client.post(f"/api/v1/students/{stu.id}/faces", files=files)
ck("Unauthenticated enrolment -> 401", r.status_code==401, f"got {r.status_code}")
r=client.post(f"/api/v1/students/{stu.id}/faces", headers=F, files=files)
ck("Faculty enrolment -> 403 (admin only)", r.status_code==403, f"got {r.status_code}")
r=client.get(f"/api/v1/students/{stu.id}/faces/status", headers=F)
ck("Faculty status -> 403", r.status_code==403, f"got {r.status_code}")

print("\n[2] Consent gate")
r=client.post(f"/api/v1/students/{stu.id}/faces", headers=A, files=files)
ck("*** Enrolment WITHOUT consent -> 403 ***", r.status_code==403, f"got {r.status_code}")
ck("error_code is CONSENT_REQUIRED", r.json().get("error_code")=="CONSENT_REQUIRED", r.json().get("error_code",""))

r=client.get(f"/api/v1/students/{stu.id}/faces/status", headers=A)
ck("Status before consent", r.json()["has_valid_consent"] is False and r.json()["face_enrolled"] is False)

r=client.post(f"/api/v1/students/{stu.id}/consent", headers=A, json={"granted":True,"note":"form signed"})
ck("Grant consent -> 200", r.status_code==200, f"got {r.status_code}")
ck("has_valid_consent now true", r.json()["has_valid_consent"] is True)

print("\n[3] Validation")
r=client.post(f"/api/v1/students/{stu.id}/faces", headers=A, files=files[:2])
ck("Too few images -> 400", r.status_code==400, f"got {r.status_code}")
r=client.post(f"/api/v1/students/999/consent", headers=A, json={"granted":True})
ck("Unknown student -> 404", r.status_code==404, f"got {r.status_code}")

# Mixed people -> consistency rejection
mixed=[]
for i,f in enumerate(faces[:6]):
    bb=f.bbox; pd=int(bb.width*0.8)
    c=group[max(0,bb.y1-pd):bb.y2+pd, max(0,bb.x1-pd):bb.x2+pd]
    mixed.append(("images",(f"m{i}.jpg", io.BytesIO(enc(c)), "image/jpeg")))
r=client.post(f"/api/v1/students/{stu.id}/faces", headers=A, files=mixed)
ck("*** Six DIFFERENT people -> rejected ***", r.status_code==400, f"got {r.status_code}: {r.json().get('error_code')}")

print("\n[4] Successful enrolment")
files=[("images",(f"i{i}.jpg", io.BytesIO(enc(v)), "image/jpeg")) for i,v in enumerate(variants)]
r=client.post(f"/api/v1/students/{stu.id}/faces", headers=A, files=files)
ck("Enrolment -> 201 Created", r.status_code==201, f"got {r.status_code}: {r.text[:180]}")
if r.status_code==201:
    body=r.json()
    print(f"\n       accepted={body['images_accepted']}/{body['images_submitted']} "
          f"quality={body['quality_score']} consistency={body['consistency_score']} "
          f"index={body['index_size']}\n")
    ck("enrolled = true", body["enrolled"] is True)
    ck("*** raw_images_retained = false ***", body["raw_images_retained"] is False)
    ck("Matcher index refreshed", body["index_size"]==1, f"index_size={body['index_size']}")
    ck("Model recorded", body["model_name"]=="buffalo_l")
    ck("No embedding in response", "embedding" not in r.text)

print("\n[5] Status after enrolment")
r=client.get(f"/api/v1/students/{stu.id}/faces/status", headers=A)
s=r.json()
ck("face_enrolled true", s["face_enrolled"] is True)
ck("*** in_matcher_index true ***", s["in_matcher_index"] is True, "index is live, not stale")
ck("source_image_count recorded", (s["source_image_count"] or 0)>0, str(s["source_image_count"]))

print("\n[6] Re-enrolment replaces, not duplicates")
files=[("images",(f"i{i}.jpg", io.BytesIO(enc(v)), "image/jpeg")) for i,v in enumerate(variants)]
r=client.post(f"/api/v1/students/{stu.id}/faces", headers=A, files=files)
ck("Re-enrolment -> 201", r.status_code==201, f"got {r.status_code}")
from app.models.face import FaceEmbedding
d2=S()
active=d2.query(FaceEmbedding).filter_by(student_id=stu.id,is_active=True,is_primary=True).count()
total=d2.query(FaceEmbedding).filter_by(student_id=stu.id).count()
ck("*** Exactly ONE active primary ***", active==1, f"{active} active, {total} total rows")

print("\n[7] The whole point: recognition now works")
from app.ml.matcher import get_matcher
from app.ml.embedder import ArcFaceEmbedder
from app.ml.preprocessing import apply_clahe
m=get_matcher()
ck("Index contains the student", m.contains(stu.id), f"size={m.size}")
enh=apply_clahe(crop)
det=ScrfdDetector().detect(enh)
q=ArcFaceEmbedder().embed(enh, det[0])
match=m.search(q)
ck("*** Enrolled face RECOGNISED ***", match.student_id==stu.id and not match.is_unknown,
   f"confidence {match.confidence:.4f}")
# An unenrolled person must be UNKNOWN
bb=faces[1].bbox; pd=int(bb.width*0.8)
other=group[max(0,bb.y1-pd):bb.y2+pd, max(0,bb.x1-pd):bb.x2+pd]
oe=apply_clahe(other); od=ScrfdDetector().detect(oe)
if od:
    om=m.search(ArcFaceEmbedder().embed(oe, od[0]))
    ck("*** Unenrolled person -> UNKNOWN ***", om.is_unknown, f"best {om.confidence:.4f}")

print("\n[8] Erasure")
r=client.delete(f"/api/v1/students/{stu.id}/faces", headers=A)
ck("Delete -> 200", r.status_code==200, f"got {r.status_code}")
ck("Embeddings removed", r.json()["embeddings_deleted"]>0, str(r.json()["embeddings_deleted"]))
ck("attendance preserved flag", r.json()["attendance_records_preserved"] is True)
ck("Index shrank to 0", r.json()["index_size"]==0)
d3=S()
ck("*** Hard delete: zero rows remain ***", d3.query(FaceEmbedding).filter_by(student_id=stu.id).count()==0)

print("\n[9] Consent withdrawal erases data")
client.post(f"/api/v1/students/{stu.id}/consent", headers=A, json={"granted":True})
files=[("images",(f"i{i}.jpg", io.BytesIO(enc(v)), "image/jpeg")) for i,v in enumerate(variants)]
client.post(f"/api/v1/students/{stu.id}/faces", headers=A, files=files)
r=client.post(f"/api/v1/students/{stu.id}/consent", headers=A, json={"granted":False})
ck("Withdraw consent -> 200", r.status_code==200)
ck("face_enrolled cleared", r.json()["face_enrolled"] is False)
r=client.get(f"/api/v1/students/{stu.id}/faces/status", headers=A)
ck("*** Withdrawal removed from index ***", r.json()["in_matcher_index"] is False)
r=client.post(f"/api/v1/students/{stu.id}/faces", headers=A, files=files)
ck("Enrolment blocked after withdrawal", r.status_code==403)

print("\n"+"="*70)
p=sum(res); print(f"RESULT: {p}/{len(res)} checks passed"); print("="*70+"\n")
sys.exit(0 if p==len(res) else 1)
