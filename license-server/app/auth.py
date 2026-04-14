from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import License, AccessToken
from app.schemas import ActivateRequest, HeartbeatRequest
from app.utils.security import (
    verify_license_code,
    create_access_token,
    decode_access_token,
    hash_token,
)
from app.utils.quota import get_quota_status
from app.config import settings

router = APIRouter()


def verify_token(db: Session, authorization: str | None) -> License | None:
    """验证 Bearer Token 并返回 License 对象"""
    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization[7:]
    payload = decode_access_token(token)
    if not payload:
        return None

    license_id = payload.get("license_id")
    machine_id = payload.get("machine_id")

    license = db.query(License).filter(
        License.id == license_id,
        License.status == 'active'
    ).first()

    if not license:
        return None

    # 验证 machine_id 匹配
    if license.machine_id != machine_id:
        return None

    return license


def build_license_info(license: License) -> dict:
    """构建授权信息响应"""
    days_remaining = None
    if license.expires_at:
        delta = license.expires_at - datetime.utcnow()
        days_remaining = max(0, delta.days)

    return {
        "type": license.license_type,
        "voice_enabled": license.voice_enabled,
        "deepseek_enabled": license.deepseek_enabled,
        "voice_daily_quota": license.voice_daily_quota,
        "deepseek_monthly_quota": license.deepseek_monthly_quota,
        "expires_at": license.expires_at.isoformat() if license.expires_at else None,
        "days_remaining": days_remaining,
    }


# ---------- 激活授权码 ----------

@router.post("/activate")
def activate_license(req: ActivateRequest, db: Session = Depends(get_db)):
    # 验证授权码签名
    if not verify_license_code(req.license_code):
        raise HTTPException(400, detail={"code": 1001, "message": "授权码无效"})

    license = db.query(License).filter(
        License.license_code == req.license_code
    ).first()

    if not license:
        raise HTTPException(400, detail={"code": 1001, "message": "授权码无效"})

    if license.status == 'revoked':
        raise HTTPException(403, detail={"code": 1004, "message": "授权已被吊销"})

    if license.status == 'active' and license.machine_id != req.machine_id:
        raise HTTPException(403, detail={"code": 1005, "message": "授权码已在其他设备激活"})

    if license.expires_at and license.expires_at < datetime.utcnow():
        raise HTTPException(403, detail={"code": 1003, "message": "授权已过期"})

    # 激活
    license.status = 'active'
    license.machine_id = req.machine_id
    license.machine_name = req.machine_name
    license.activated_at = datetime.utcnow()

    # 创建 Token
    access_token = create_access_token(license.id, req.machine_id)
    token_hash = hash_token(access_token)

    # 存储 Token 哈希
    db_token = AccessToken(
        license_id=license.id,
        token_hash=token_hash,
        machine_id=req.machine_id,
        expires_at=datetime.utcnow() + timedelta(hours=settings.TOKEN_EXPIRE_HOURS),
    )
    db.add(db_token)
    db.commit()

    return {
        "code": 0,
        "data": {
            "access_token": access_token,
            "token_expires_in": settings.TOKEN_EXPIRE_HOURS * 3600,
            "license": build_license_info(license),
            "quota": get_quota_status(db, license.id)
        }
    }


# ---------- 解绑设备 ----------

@router.post("/deactivate")
def deactivate_license(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db)
):
    license = verify_token(db, authorization)
    if not license:
        raise HTTPException(401, detail={"code": 2001, "message": "未授权"})

    old_machine_id = license.machine_id

    license.status = 'inactive'
    license.machine_id = None
    license.machine_name = None

    # 撤销所有 Token
    db.query(AccessToken).filter(
        AccessToken.license_id == license.id
    ).update({"is_revoked": True})

    db.commit()

    return {"code": 0, "message": "解绑成功"}


# ---------- 刷新 Token ----------

@router.post("/refresh")
def refresh_token(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db)
):
    license = verify_token(db, authorization)
    if not license:
        raise HTTPException(401, detail={"code": 2001, "message": "未授权"})

    # 撤销旧 Token
    if authorization:
        old_token = authorization[7:]
        old_hash = hash_token(old_token)
        db.query(AccessToken).filter(
            AccessToken.token_hash == old_hash
        ).update({"is_revoked": True})

    # 创建新 Token
    new_token = create_access_token(license.id, license.machine_id)
    token_hash = hash_token(new_token)

    db.add(AccessToken(
        license_id=license.id,
        token_hash=token_hash,
        machine_id=license.machine_id,
        expires_at=datetime.utcnow() + timedelta(hours=settings.TOKEN_EXPIRE_HOURS),
    ))
    db.commit()

    return {
        "code": 0,
        "data": {
            "access_token": new_token,
            "token_expires_in": settings.TOKEN_EXPIRE_HOURS * 3600,
        }
    }


# ---------- 查询授权状态 ----------

@router.get("/status")
def get_status(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db)
):
    license = verify_token(db, authorization)
    if not license:
        return {"code": 2001, "message": "授权无效", "data": None}

    return {
        "code": 0,
        "data": {
            "license": build_license_info(license),
            "quota": get_quota_status(db, license.id)
        }
    }


# ---------- 心跳上报 ----------

@router.post("/heartbeat")
def heartbeat(
    req: HeartbeatRequest,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db)
):
    license = verify_token(db, authorization)
    if not license:
        return {"code": 2001, "message": "授权无效", "data": None}

    if license.machine_id != req.machine_id:
        return {"code": 1005, "message": "设备不匹配", "data": None}

    return {
        "code": 0,
        "data": {
            "valid": True,
            "quota": get_quota_status(db, license.id)
        }
    }
