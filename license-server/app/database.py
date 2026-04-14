from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

connect_args = {}
_db_url = settings.DATABASE_URL
if _db_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
    # 将相对路径转为基于 license-server 根目录的绝对路径
    if ":///./" in _db_url or ":///:" not in _db_url and ":///" in _db_url:
        rel = _db_url.split("///", 1)[1]
        if not Path(rel).is_absolute():
            abs_path = Path(__file__).resolve().parent.parent / rel
            _db_url = f"sqlite:///{abs_path}"

engine = create_engine(_db_url, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def init_db():
    """初始化数据库表结构"""
    from app.models import Base  # noqa: F811
    Base.metadata.create_all(bind=engine)


def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
