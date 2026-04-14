from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import License, User, Admin, UsageLog
from app.schemas import (
    AdminLoginRequest, LicenseCreateRequest, LicenseUpdateRequest,
    LicenseListItem, StatisticsResponse,
)
from app.utils.security import (
    generate_license_code,
    verify_password,
    hash_password,
    create_access_token,
    decode_access_token,
)
from app.utils.quota import get_quota_status
from app.config import settings

router = APIRouter()


# ---------- 管理员认证 ----------

def verify_admin(authorization: str | None, db: Session) -> Admin:
    """验证管理员 Token"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")

    token = authorization[7:]
    payload = decode_access_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(401, "Token 无效")

    admin = db.query(Admin).filter(Admin.id == payload.get("admin_id")).first()
    if not admin:
        raise HTTPException(401, "管理员不存在")
    return admin


@router.post("/login")
def admin_login(req: AdminLoginRequest, db: Session = Depends(get_db)):
    admin = db.query(Admin).filter(Admin.username == req.username).first()
    if not admin or not verify_password(req.password, admin.password_hash):
        raise HTTPException(401, "用户名或密码错误")

    # 更新最后登录时间
    admin.last_login_at = datetime.utcnow()
    db.commit()

    # 创建管理员 Token
    token = create_access_token(admin.id, "admin")
    # 覆写 JWT payload 以区分管理员和授权 Token
    import jwt
    payload = {
        "admin_id": admin.id,
        "role": "admin",
        "exp": datetime.utcnow() + timedelta(hours=settings.TOKEN_EXPIRE_HOURS),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

    return {"code": 0, "data": {"access_token": token, "role": admin.role}}


# ---------- 授权管理 ----------

@router.get("/licenses")
def list_licenses(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db)
):
    verify_admin(authorization, db)

    licenses = db.query(License).order_by(License.created_at.desc()).all()

    result = []
    for lic in licenses:
        result.append({
            "id": lic.id,
            "license_code": lic.license_code,
            "license_type": lic.license_type,
            "status": lic.status,
            "voice_enabled": lic.voice_enabled,
            "deepseek_enabled": lic.deepseek_enabled,
            "voice_daily_quota": lic.voice_daily_quota,
            "deepseek_monthly_quota": lic.deepseek_monthly_quota,
            "machine_id": lic.machine_id,
            "machine_name": lic.machine_name,
            "user_id": lic.user_id,
            "expires_at": lic.expires_at.isoformat() if lic.expires_at else None,
            "created_at": lic.created_at.isoformat() if lic.created_at else None,
            "activated_at": lic.activated_at.isoformat() if lic.activated_at else None,
        })

    return {"code": 0, "data": result}


@router.post("/licenses/create")
def create_license(
    req: LicenseCreateRequest,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db)
):
    verify_admin(authorization, db)

    code = generate_license_code()

    expires_at = None
    if req.validity_days > 0:
        expires_at = datetime.utcnow() + timedelta(days=req.validity_days)

    license = License(
        license_code=code,
        license_type=req.license_type,
        user_id=req.user_id,
        voice_enabled=req.voice_enabled,
        deepseek_enabled=req.deepseek_enabled,
        voice_daily_quota=req.voice_daily_quota,
        deepseek_monthly_quota=req.deepseek_monthly_quota,
        expires_at=expires_at,
    )
    db.add(license)
    db.commit()
    db.refresh(license)

    return {
        "code": 0,
        "data": {
            "id": license.id,
            "license_code": code,
            "license_type": license.license_type,
            "expires_at": license.expires_at.isoformat() if license.expires_at else None,
        }
    }


@router.put("/licenses/{license_id}")
def update_license(
    license_id: int,
    req: LicenseUpdateRequest,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db)
):
    verify_admin(authorization, db)

    license = db.query(License).filter(License.id == license_id).first()
    if not license:
        raise HTTPException(404, "授权不存在")

    if req.voice_enabled is not None:
        license.voice_enabled = req.voice_enabled
    if req.deepseek_enabled is not None:
        license.deepseek_enabled = req.deepseek_enabled
    if req.voice_daily_quota is not None:
        license.voice_daily_quota = req.voice_daily_quota
    if req.deepseek_monthly_quota is not None:
        license.deepseek_monthly_quota = req.deepseek_monthly_quota
    if req.expires_at is not None:
        license.expires_at = req.expires_at
    if req.status is not None:
        license.status = req.status

    db.commit()

    return {"code": 0, "message": "更新成功"}


@router.delete("/licenses/{license_id}")
def revoke_license(
    license_id: int,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db)
):
    verify_admin(authorization, db)

    license = db.query(License).filter(License.id == license_id).first()
    if not license:
        raise HTTPException(404, "授权不存在")

    license.status = 'revoked'
    db.commit()

    return {"code": 0, "message": "授权已吊销"}


# ---------- 用户管理 ----------

@router.get("/users")
def list_users(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db)
):
    verify_admin(authorization, db)

    users = db.query(User).order_by(User.created_at.desc()).all()
    result = []
    for u in users:
        result.append({
            "id": u.id,
            "email": u.email,
            "phone": u.phone,
            "contact_name": u.contact_name,
            "company_name": u.company_name,
            "status": u.status,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        })

    return {"code": 0, "data": result}


# ---------- 统计数据 ----------

@router.get("/statistics")
def get_statistics(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db)
):
    verify_admin(authorization, db)

    from app.utils.quota import get_usage_period

    total_licenses = db.query(func.count(License.id)).scalar() or 0
    active_licenses = db.query(func.count(License.id)).filter(
        License.status == 'active'
    ).scalar() or 0
    total_users = db.query(func.count(User.id)).scalar() or 0

    start, end = get_usage_period('daily')
    deepseek_calls_today = db.query(func.count(UsageLog.id)).filter(
        UsageLog.service_type == 'deepseek',
        UsageLog.created_at >= start,
        UsageLog.created_at < end,
    ).scalar() or 0

    voice_calls_today = db.query(func.count(UsageLog.id)).filter(
        UsageLog.service_type == 'voice',
        UsageLog.created_at >= start,
        UsageLog.created_at < end,
    ).scalar() or 0

    return {
        "code": 0,
        "data": {
            "total_licenses": total_licenses,
            "active_licenses": active_licenses,
            "total_users": total_users,
            "deepseek_calls_today": deepseek_calls_today,
            "voice_calls_today": voice_calls_today,
        }
    }
