from app.schemas.auth import *
from app.schemas.attendance import *
from app.schemas.attendance_photos import *
from app.schemas.student_photos import *
try:
    from app.schemas.students import *
except ImportError:
    pass
try:
    from app.schemas.student import *
except ImportError:
    pass
