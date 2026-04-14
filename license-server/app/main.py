from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.auth import router as auth_router
from app.proxy import router as proxy_router
from app.admin import router as admin_router
from app.database import init_db
from app.utils.security import rate_limiter, RATE_LIMITS

app = FastAPI(title="RobotModbusLite License Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(proxy_router, prefix="/api/v1/proxy", tags=["proxy"])
app.include_router(admin_router, prefix="/api/v1/admin", tags=["admin"])


@app.on_event("startup")
def startup():
    init_db()


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path in RATE_LIMITS:
        limit = RATE_LIMITS[request.url.path]
        client_id = request.headers.get("X-Client-ID", request.client.host if request.client else "unknown")
        key = f"{request.url.path}:{client_id}"

        if not rate_limiter.check_rate_limit(key, limit["max"], limit["window"]):
            raise HTTPException(
                status_code=429,
                detail={
                    "code": 4001,
                    "message": "请求过于频繁，请稍后再试",
                    "retry_after": limit["window"]
                }
            )

    return await call_next(request)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/admin")
async def admin_page():
    return FileResponse("app/static/admin.html")


app.mount("/static", StaticFiles(directory="app/static"), name="static")
