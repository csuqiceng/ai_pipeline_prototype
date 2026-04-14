"""创建管理员账户脚本"""
from getpass import getpass


def main():
    import sys
    sys.path.insert(0, ".")

    from app.database import SessionLocal
    from app.models import Admin
    from app.utils.security import hash_password

    print("=== 创建管理员账户 ===")
    username = input("管理员用户名: ").strip()
    if not username:
        print("用户名不能为空")
        return

    password = getpass("密码: ")
    if len(password) < 6:
        print("密码至少6位")
        return

    confirm = getpass("确认密码: ")
    if password != confirm:
        print("两次密码不一致")
        return

    role = input("角色 (super_admin/admin/operator) [admin]: ").strip() or "admin"
    if role not in ("super_admin", "admin", "operator"):
        print("无效角色")
        return

    db = SessionLocal()
    try:
        existing = db.query(Admin).filter(Admin.username == username).first()
        if existing:
            print(f"用户名 {username} 已存在")
            return

        admin = Admin(
            username=username,
            password_hash=hash_password(password),
            role=role
        )
        db.add(admin)
        db.commit()
        print(f"✓ 管理员 {username} 创建成功，角色: {role}")
    except Exception as e:
        db.rollback()
        print(f"✗ 创建失败: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
