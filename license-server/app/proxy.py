import uuid

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import License, UsageLog
from app.auth import verify_token
from app.utils.quota import get_daily_usage, get_monthly_usage
from app.config import settings

router = APIRouter()


def log_usage(db: Session, license_id: int, service_type: str, request_id: str, **kwargs) -> bool:
    """记录使用量（幂等）"""
    try:
        log = UsageLog(
            license_id=license_id,
            service_type=service_type,
            request_id=request_id,
            **kwargs
        )
        db.add(log)
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return True  # 重复请求，忽略


# ---------- DeepSeek 代理 ----------

@router.post("/deepseek/chat")
async def proxy_deepseek(
    request: dict,
    authorization: str | None = Header(None),
    x_request_id: str | None = Header(default=None),
    db: Session = Depends(get_db)
):
    license = verify_token(db, authorization)
    if not license:
        raise HTTPException(401, detail={"code": 2001, "message": "未授权"})

    if not license.deepseek_enabled:
        raise HTTPException(403, detail={"code": 3001, "message": "当前授权未启用 DeepSeek 功能"})

    usage = get_monthly_usage(db, license.id, 'deepseek')
    if license.deepseek_monthly_quota > 0 and usage >= license.deepseek_monthly_quota:
        raise HTTPException(429, detail={"code": 3002, "message": "本月配额已用尽"})

    request_id = x_request_id or str(uuid.uuid4())

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
            json=request,
            timeout=30
        )

    if response.status_code != 200:
        raise HTTPException(502, detail={"code": 5001, "message": "DeepSeek 服务异常"})

    result = response.json()

    content = result.get('choices', [{}])[0].get('message', {}).get('content', '')

    log_usage(
        db, license.id, 'deepseek', request_id,
        input_chars=len(str(request)),
        output_chars=len(content),
    )

    return {"code": 0, "data": result}


# ---------- 讯飞语音代理 ----------

@router.post("/voice/transcribe")
async def proxy_voice(
    request: dict,
    authorization: str | None = Header(None),
    x_request_id: str | None = Header(default=None),
    db: Session = Depends(get_db)
):
    license = verify_token(db, authorization)
    if not license:
        raise HTTPException(401, detail={"code": 2001, "message": "未授权"})

    if not license.voice_enabled:
        raise HTTPException(403, detail={"code": 3001, "message": "当前授权未启用语音功能"})

    usage = get_daily_usage(db, license.id, 'voice')
    if license.voice_daily_quota > 0 and usage >= license.voice_daily_quota:
        raise HTTPException(429, detail={"code": 3002, "message": "今日语音配额已用尽"})

    request_id = x_request_id or str(uuid.uuid4())

    # 客户端发送 base64 编码的音频数据
    import base64 as b64
    audio_base64 = request.get("audio_data", "")
    audio_data = b64.b64decode(audio_base64) if audio_base64 else b""
    sample_rate = request.get("sample_rate", 16000)

    # Phase 1: 使用讯飞 SDK 在服务端转写
    audio_seconds = len(audio_data) / (sample_rate * 2) if sample_rate > 0 else 0

    # 尝试通过讯飞 WebSocket 代理转写
    text = ""
    try:
        text = await _transcribe_via_iflytek(audio_data, request.get("audio_format", "pcm"), sample_rate)
    except Exception:
        # 降级：返回空结果，记录使用量
        pass

    log_usage(
        db, license.id, 'voice', request_id,
        audio_seconds=audio_seconds,
        input_chars=len(audio_data),
    )

    return {
        "code": 0,
        "data": {
            "text": text,
            "message": "语音转写完成" if text else "语音转写完成（无识别结果）",
        }
    }


async def _transcribe_via_iflytek(audio_data: bytes, audio_format: str, sample_rate: int) -> str:
    """通过讯飞 SDK 在服务端进行语音转写"""
    import tempfile
    import os

    if not settings.IFLYTEK_APP_ID or not settings.IFLYTEK_API_KEY:
        return ""

    try:
        from xfyunsdkspeech.iat_client import IatClient
    except ImportError:
        return ""

    client = IatClient(
        app_id=settings.IFLYTEK_APP_ID,
        api_key=settings.IFLYTEK_API_KEY,
        api_secret=settings.IFLYTEK_API_SECRET,
    )

    # 保存到临时文件
    suffix = f".{audio_format}" if audio_format else ".pcm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_data)
        tmp_path = tmp.name

    try:
        chunks = []
        with open(tmp_path, "rb") as f:
            for chunk in client.stream(f):
                if isinstance(chunk, dict):
                    for ws_item in chunk.get("result", {}).get("ws", []):
                        for cw_item in ws_item.get("cw", []):
                            word = str(cw_item.get("w", "")).strip()
                            if word:
                                chunks.append(word)
                else:
                    text = str(chunk).strip()
                    if text:
                        chunks.append(text)
        return "".join(chunks).strip()
    finally:
        os.unlink(tmp_path)


def _generate_iflytek_auth() -> dict:
    """生成讯飞语音听写临时鉴权凭证"""
    import base64
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    date = now.strftime('%a, %d %b %Y %H:%M:%S GMT')

    signature_origin = f"host: iat-api.xfyun.cn\ndate: {date}\nGET /v2/iat HTTP/1.1"
    signature_sha = __import__('hmac').new(
        settings.IFLYTEK_API_SECRET.encode(),
        signature_origin.encode(),
        __import__('hashlib').sha256
    ).digest()
    signature = base64.b64encode(signature_sha).decode()

    authorization_origin = (
        f'api_key="{settings.IFLYTEK_API_KEY}", '
        f'algorithm="hmac-sha256", '
        f'headers="host date request-line", '
        f'signature="{signature}"'
    )
    authorization = base64.b64encode(authorization_origin.encode()).decode()

    return {
        "ws_url": f"wss://iat-api.xfyun.cn/v2/iat?authorization={authorization}&date={date}",
        "app_id": settings.IFLYTEK_APP_ID,
        "expires_in": 300
    }


# ---------- 配额查询 ----------

@router.get("/usage")
def get_usage(
    service: str = "all",
    authorization: str | None = Header(None),
    db: Session = Depends(get_db)
):
    license = verify_token(db, authorization)
    if not license:
        raise HTTPException(401, detail={"code": 2001, "message": "未授权"})

    from app.utils.quota import get_quota_status
    quota = get_quota_status(db, license.id)

    return {"code": 0, "data": quota}


@router.get("/remaining")
def get_remaining(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db)
):
    license = verify_token(db, authorization)
    if not license:
        raise HTTPException(401, detail={"code": 2001, "message": "未授权"})

    from app.utils.quota import get_quota_status
    quota = get_quota_status(db, license.id)

    voice_remaining = None
    if license.voice_daily_quota > 0:
        voice_remaining = max(0, license.voice_daily_quota - quota["voice_used_today"])

    deepseek_remaining = None
    if license.deepseek_monthly_quota > 0:
        deepseek_remaining = max(0, license.deepseek_monthly_quota - quota["deepseek_used_this_month"])

    return {
        "code": 0,
        "data": {
            "voice_remaining": voice_remaining,
            "deepseek_remaining": deepseek_remaining,
        }
    }
