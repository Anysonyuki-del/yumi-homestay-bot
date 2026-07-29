from fastapi import FastAPI

from homestay_bot.routes.wecom_callback import router as wecom_callback_router

app = FastAPI(title="武汉民宿客服机器人")
app.include_router(wecom_callback_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """返回进程级健康状态，供本地联调和容器检查使用。"""
    return {"status": "ok"}
