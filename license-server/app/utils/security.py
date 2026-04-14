import hashlib
import hmac
import secrets
import time
import base64
from collections import defaultdict, OrderedDict
from datetime import datetime, timedelta

import jwt
import bcrypt as _bcrypt

from app.config import settings


# ---------- 授权码签名（HMAC-SHA256）----------

def generate_license_code() -> str:
    """生成授权码: RMLT-XXXX-XXXX-XXXX-XXXX-XXXX"""
    prefix = "RMLT"
    random_bytes = secrets.token_bytes(12)
    random_part = base64.b32encode(random_bytes).decode('ascii')[:16]

    payload = f"{prefix}-{random_part[:4]}-{random_part[4:8]}-{random_part[8:12]}-{random_part[12:16]}"

    signature = hmac.new(
        settings.LICENSE_SIGNING_KEY.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()[:4].upper()

    return f"{payload}-{signature}"


def verify_license_code(license_code: str) -> bool:
    """验证授权码签名"""
    if not license_code or len(license_code) != 29:
        return False

    parts = license_code.split('-')
    if len(parts) != 6 or parts[0] != 'RMLT':
        return False

    payload = '-'.join(parts[:5])
    provided_signature = parts[5]

    expected_signature = hmac.new(
        settings.LICENSE_SIGNING_KEY.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()[:4].upper()

    return hmac.compare_digest(provided_signature, expected_signature)


# ---------- JWT Token ----------

def create_access_token(license_id: int, machine_id: str) -> str:
    """创建 JWT 访问令牌"""
    payload = {
        "license_id": license_id,
        "machine_id": machine_id,
        "exp": datetime.utcnow() + timedelta(hours=settings.TOKEN_EXPIRE_HOURS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """解码 JWT 令牌"""
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ---------- 密码哈希 ----------

def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode(), hashed.encode())


# ---------- Token 存储 ----------

def hash_token(token: str) -> str:
    """SHA256 哈希用于存储"""
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- 速率限制器（Phase 1 内存版）----------

class RateLimiter:
    def __init__(self):
        self._requests: dict[str, list[float]] = defaultdict(list)

    def check_rate_limit(self, key: str, max_requests: int, window_seconds: int) -> bool:
        now = time.time()
        requests = self._requests[key]
        requests[:] = [t for t in requests if now - t < window_seconds]
        if len(requests) >= max_requests:
            return False
        requests.append(now)
        return True


rate_limiter = RateLimiter()

RATE_LIMITS = {
    "/api/v1/auth/activate": {"max": 5, "window": 300},
    "/api/v1/auth/heartbeat": {"max": 20, "window": 60},
    "/api/v1/proxy/deepseek/chat": {"max": 60, "window": 60},
    "/api/v1/proxy/voice/transcribe": {"max": 30, "window": 60},
}


# ---------- Nonce 重放防护（Phase 1 内存版）----------

NONCE_CACHE: OrderedDict[str, float] = OrderedDict()
NONCE_CACHE_SIZE = 10000
NONCE_EXPIRE_SECONDS = 300


def check_nonce(nonce: str) -> bool:
    """检查 nonce 是否已使用（返回 True 表示新 nonce）"""
    _cleanup_expired_nonces()
    if nonce in NONCE_CACHE:
        return False
    NONCE_CACHE[nonce] = time.time()
    if len(NONCE_CACHE) > NONCE_CACHE_SIZE:
        NONCE_CACHE.popitem(last=False)
    return True


def _cleanup_expired_nonces():
    now = time.time()
    expired_keys = [
        nonce for nonce, ts in NONCE_CACHE.items()
        if now - ts > NONCE_EXPIRE_SECONDS
    ]
    for key in expired_keys:
        del NONCE_CACHE[key]
