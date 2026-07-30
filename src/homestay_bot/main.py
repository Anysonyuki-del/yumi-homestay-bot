import os
import secrets
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.application import application_lifespan
from homestay_bot.config import Settings
from homestay_bot.logging import configure_logging_redaction
from homestay_bot.routes.approvals import router as approvals_router
from homestay_bot.routes.employee_auth import router as employee_auth_router
from homestay_bot.routes.health import router as health_router
from homestay_bot.routes.hostex_webhook import router as hostex_webhook_router
from homestay_bot.routes.knowledge import router as knowledge_router
from homestay_bot.routes.private_files import router as private_files_router
from homestay_bot.routes.properties import router as properties_router
from homestay_bot.routes.tasks import router as tasks_router
from homestay_bot.routes.wecom_callback import router as wecom_callback_router


def _session_configuration() -> tuple[str, bool]:
    """优先读取完整配置；未配置时使用进程级随机密钥而非公开默认值。"""
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError:
        secret = os.environ.get("SESSION_SECRET") or secrets.token_urlsafe(48)
        https_only = os.environ.get("SESSION_COOKIE_HTTPS_ONLY", "0") == "1"
        return secret, https_only
    https_override = os.environ.get("SESSION_COOKIE_HTTPS_ONLY")
    https_only = (
        https_override == "1"
        if https_override is not None
        else settings.public_base_url.startswith("https://")
    )
    return settings.session_secret, https_only


session_secret, session_https_only = _session_configuration()
configure_logging_redaction()

app = FastAPI(
    title="武汉民宿客服机器人",
    lifespan=application_lifespan,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    https_only=session_https_only,
    same_site="lax",
)
app.include_router(wecom_callback_router)
app.include_router(hostex_webhook_router)
app.include_router(employee_auth_router)
app.include_router(approvals_router)
app.include_router(knowledge_router)
app.include_router(tasks_router)
app.include_router(private_files_router)
app.include_router(properties_router)
app.include_router(health_router)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "static"),
    name="static",
)
