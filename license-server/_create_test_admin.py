import sys
sys.path.insert(0, r'c:\Users\a\Desktop\ai_pipeline_prototype\license-server')
from app.database import SessionLocal, init_db
from app.models import Admin
from app.utils.security import hash_password

init_db()
db = SessionLocal()
try:
    existing = db.query(Admin).filter(Admin.username == "admin").first()
    if existing:
        print("admin already exists, skipping")
    else:
        admin = Admin(
            username="admin",
            password_hash=hash_password("admin123"),
            role="super_admin"
        )
        db.add(admin)
        db.commit()
        print("Admin created: admin / admin123 (super_admin)")
finally:
    db.close()
