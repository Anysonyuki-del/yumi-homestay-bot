import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.routes.approvals import router as approvals_router
from homestay_bot.routes.employee_auth import router as employee_auth_router
from homestay_bot.routes.wecom_callback import router as wecom_callback_router

app = FastAPI(title="武汉民宿客服机器人")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "local-development-only"),
    https_only=os.environ.get("SESSION_COOKIE_HTTPS_ONLY", "0") == "1",
    same_site="lax",
)
app.include_router(wecom_callback_router)
app.include_router(employee_auth_router)
app.include_router(approvals_router)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "static"),
    name="static",
)


@app.get("/health")
async def health() -> dict[str, str]:
    """返回进程级健康状态，供本地联调和容器检查使用。"""
    return {"status": "ok"}
