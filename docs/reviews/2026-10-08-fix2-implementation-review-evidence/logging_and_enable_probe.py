"""离线审查反例：退出 0 表示现有缺陷成立，不能作为修复通过证明。"""

import asyncio
import io
import logging
import re
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from test_knowledge_routes import build_client

from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.errors import OperationRefused
from homestay_bot.domain.models import Base, Employee, KnowledgeEntry
from homestay_bot.routes.knowledge import KnowledgeAdminService
from homestay_bot.routes.page_errors import handle_operation_refused


def probe_logging() -> None:
    """按 Uvicorn 先配置日志、再导入应用的顺序，捕获合成秘密的真实输出。"""
    captured = io.StringIO()
    with redirect_stderr(captured):
        uvicorn.Config("homestay_bot.main:app").load()
        logger = logging.getLogger("homestay_bot.review")
        logger.warning(
            "synthetic_headers=%s status=%s",
            {"Authorization": "Bearer SYNTHETIC_ONLY_TOKEN"},
            401,
        )
        try:
            raise RuntimeError({"api_key": "SYNTHETIC_ONLY_KEY"})
        except RuntimeError:
            logger.exception("synthetic_request_failed")
    actual = captured.getvalue()
    assert "SYNTHETIC_ONLY_TOKEN" in actual
    assert "SYNTHETIC_ONLY_KEY" in actual
    assert "Traceback" in actual and "RuntimeError" in actual
    print("LOGGING quoted_mapping_secret_visible=True exception_secret_visible=True")


async def seed(factory) -> None:
    """只在临时库写合成管理员与旧占位符知识，不读取真实配置或数据。"""
    async with factory() as session:
        session.add(Employee(id=1, wecom_userid="synthetic-admin", name="审查管理员",
                             role=EmployeeRole.ADMIN))
        session.add(KnowledgeEntry(
            id=1, category="审查", scope="global", is_enabled=False,
            question_zh="早餐几点？", answer_zh="【待管理员确认】",
            question_en="Breakfast?", answer_en="To be confirmed.", keywords=[],
        ))
        await session.commit()


def probe_enable() -> None:
    """通过实际路由、实际全局错误处理器和真实启用服务核对 HTML/JSON 的落点。"""
    with TemporaryDirectory(prefix="fix2-review-enable-") as temp:
        engine = create_engine(f"sqlite+aiosqlite:///{Path(temp) / 'review.db'}")
        factory = create_session_factory(engine)

        async def setup() -> None:
            """建立测试元数据；生产迁移验收与此独立。"""
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            await seed(factory)

        asyncio.run(setup())
        client, listing = build_client(EmployeeRole.ADMIN)

        class RealToggle:
            """只替换写操作为真实服务；列表与登录复用已有测试装配。"""

            def __getattr__(self, name):
                """把页面读取交给既有列表夹具。"""
                return getattr(listing, name)

            async def set_enabled(self, entry_id, employee_id, enabled):
                """实际执行占位符校验，不由替身模拟拒绝。"""
                async with factory() as session:
                    await KnowledgeAdminService(session).set_enabled(
                        entry_id, employee_id, enabled,
                    )

        client.app.state.knowledge_admin_service = RealToggle()
        client.app.add_exception_handler(OperationRefused, handle_operation_refused)
        # build_client 已发登录请求；重新装配测试中刚注册的真实错误处理器。
        client.app.middleware_stack = None
        for accept, expected_status in (("text/html", 303), ("application/json", 422)):
            page = client.get("/employee/knowledge")
            token = re.search(r'name="csrf_token" value="([^\"]+)"', page.text).group(1)
            response = client.post(
                "/employee/knowledge/1/enable",
                data={"csrf_token": token,
                      "return_to": "/employee/knowledge?enabled=false#knowledge-entries"},
                headers={"Accept": accept}, follow_redirects=False,
            )
            assert response.status_code == expected_status
            if accept == "text/html":
                assert response.headers["location"] == "/employee/tasks"
            print("ENABLE", accept, response.status_code,
                  response.headers.get("location", response.json().get("detail")
                                       if accept == "application/json" else ""))

        async def finish() -> None:
            """复核拒绝未改变启用状态并释放临时连接。"""
            async with factory() as session:
                assert not (await session.get(KnowledgeEntry, 1)).is_enabled
            await engine.dispose()

        asyncio.run(finish())
        client.close()


if __name__ == "__main__":
    probe_logging()
    probe_enable()
