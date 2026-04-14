from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import UsageLog

SERVER_TIMEZONE = ZoneInfo("Asia/Shanghai")


def get_usage_period(period: str, tz: ZoneInfo = SERVER_TIMEZONE) -> tuple[datetime, datetime]:
    """
    获取统计周期的时间范围（UTC）

    Args:
        period: 'daily' 或 'monthly'
        tz: 时区（默认北京时间 UTC+8）

    Returns:
        (start_time, end_time) UTC 时间
    """
    now = datetime.now(tz)

    if period == 'daily':
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif period == 'monthly':
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            end = start.replace(year=now.year + 1, month=1)
        else:
            end = start.replace(month=now.month + 1)
    else:
        raise ValueError(f"Unknown period: {period}")

    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def get_daily_usage(db: Session, license_id: int, service_type: str) -> int:
    """查询今日使用量"""
    start, end = get_usage_period('daily')
    result = db.query(func.count(UsageLog.id)).filter(
        UsageLog.license_id == license_id,
        UsageLog.service_type == service_type,
        UsageLog.created_at >= start,
        UsageLog.created_at < end,
        UsageLog.status == 'success'
    ).scalar()
    return result or 0


def get_monthly_usage(db: Session, license_id: int, service_type: str) -> int:
    """查询本月使用量"""
    start, end = get_usage_period('monthly')
    result = db.query(func.count(UsageLog.id)).filter(
        UsageLog.license_id == license_id,
        UsageLog.service_type == service_type,
        UsageLog.created_at >= start,
        UsageLog.created_at < end,
        UsageLog.status == 'success'
    ).scalar()
    return result or 0


def get_quota_status(db: Session, license_id: int) -> dict:
    """获取配额状态"""
    return {
        "voice_used_today": get_daily_usage(db, license_id, 'voice'),
        "deepseek_used_this_month": get_monthly_usage(db, license_id, 'deepseek'),
    }
