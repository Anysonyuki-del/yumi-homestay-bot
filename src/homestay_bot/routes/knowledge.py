import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import EmployeeRole, KnowledgeCandidateStatus
from homestay_bot.domain.models import AuditLog, KnowledgeCandidate, KnowledgeEntry
from homestay_bot.repositories.faq_candidates import SQLAlchemyFaqCandidateRepository
from homestay_bot.routes.employee_auth import require_employee_session

router = APIRouter(prefix="/employee/knowledge")
templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "templates"
)


class KnowledgeAdminServicePort(Protocol):
    """定义知识管理路由所需接口。"""

    async def list_all(self) -> list[Any]:
        """返回包括停用项在内的全部知识。"""

    async def create(self, employee_id: int, **fields: Any) -> Any:
        """创建一条双语知识。"""

    async def update(
        self, entry_id: int, employee_id: int, **fields: Any
    ) -> Any:
        """更新一条双语知识。"""

    async def set_enabled(
        self, entry_id: int, employee_id: int, enabled: bool
    ) -> None:
        """启用或停用知识。"""

    async def list_candidates(self) -> list[Any]:
        """返回管理员待归纳候选。"""

    async def convert_candidate(
        self,
        candidate_id: int,
        employee_id: int,
        **fields: Any,
    ) -> Any:
        """把管理员修改后的草稿转换为正式知识。"""

    async def snooze_candidate(
        self,
        candidate_id: int,
        employee_id: int,
    ) -> None:
        """关闭候选三十天。"""


class KnowledgeAdminService:
    """持久化知识管理变更并写入不含正文的审计记录。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """绑定当前数据库会话和可测试的 UTC 时钟。"""
        self._session = session
        self._now = now or (lambda: datetime.now(UTC))

    async def list_all(self) -> list[KnowledgeEntry]:
        """按主键返回全部知识，供员工审核。"""
        result = await self._session.scalars(
            select(KnowledgeEntry).order_by(KnowledgeEntry.id)
        )
        return list(result.all())

    async def create(self, employee_id: int, **fields: Any) -> KnowledgeEntry:
        """创建默认启用的双语知识，并记录最小审计信息。"""
        entry = KnowledgeEntry(**fields, is_enabled=True, updated_by=employee_id)
        self._session.add(entry)
        await self._session.flush()
        self._add_audit(employee_id, "knowledge.create", entry.id)
        await self._session.commit()
        return entry

    async def update(
        self, entry_id: int, employee_id: int, **fields: Any
    ) -> KnowledgeEntry:
        """更新允许编辑的知识字段，并记录条目级审计。"""
        entry = await self._require_entry(entry_id)
        allowed = {
            "category",
            "question_zh",
            "answer_zh",
            "question_en",
            "answer_en",
            "keywords",
        }
        for key, value in fields.items():
            if key in allowed:
                setattr(entry, key, value)
        entry.updated_by = employee_id
        self._add_audit(employee_id, "knowledge.update", entry.id)
        await self._session.commit()
        return entry

    async def set_enabled(
        self, entry_id: int, employee_id: int, enabled: bool
    ) -> None:
        """立即切换知识可用状态并写入最小审计记录。"""
        entry = await self._require_entry(entry_id)
        entry.is_enabled = enabled
        entry.updated_by = employee_id
        action = "knowledge.enable" if enabled else "knowledge.disable"
        self._add_audit(employee_id, action, entry.id)
        await self._session.commit()

    async def list_candidates(self) -> list[KnowledgeCandidate]:
        """返回仍开放的候选，正文仅交给管理员页面。"""
        result = await self._session.scalars(
            select(KnowledgeCandidate)
            .where(KnowledgeCandidate.status == KnowledgeCandidateStatus.OPEN)
            .order_by(KnowledgeCandidate.last_seen_at.desc(), KnowledgeCandidate.id.desc())
        )
        return list(result.all())

    async def convert_candidate(
        self,
        candidate_id: int,
        employee_id: int,
        **fields: Any,
    ) -> KnowledgeEntry:
        """在同一事务中创建启用知识、清理候选正文并记录最小审计。"""
        candidate = await self._require_candidate(candidate_id)
        if candidate.status is not KnowledgeCandidateStatus.OPEN:
            raise LookupError(f"FAQ 候选不可转换: {candidate_id}")
        entry = KnowledgeEntry(**fields, is_enabled=True, updated_by=employee_id)
        self._session.add(entry)
        await self._session.flush()
        await SQLAlchemyFaqCandidateRepository(self._session).convert(
            candidate_id,
            knowledge_entry_id=entry.id,
        )
        self._add_candidate_audit(
            employee_id,
            "faq_candidate.convert",
            candidate_id,
            knowledge_entry_id=entry.id,
        )
        await self._session.commit()
        return entry

    async def snooze_candidate(
        self,
        candidate_id: int,
        employee_id: int,
    ) -> None:
        """关闭候选三十天，并在同一事务删除示例和未采用草稿。"""
        candidate = await self._require_candidate(candidate_id)
        if candidate.status is not KnowledgeCandidateStatus.OPEN:
            raise LookupError(f"FAQ 候选不可关闭: {candidate_id}")
        await SQLAlchemyFaqCandidateRepository(self._session).snooze(
            candidate_id,
            until=self._now() + timedelta(days=30),
        )
        self._add_candidate_audit(
            employee_id,
            "faq_candidate.snooze",
            candidate_id,
        )
        await self._session.commit()

    async def _require_entry(self, entry_id: int) -> KnowledgeEntry:
        """读取目标知识，不存在时抛出稳定异常。"""
        entry = await self._session.get(KnowledgeEntry, entry_id)
        if entry is None:
            raise LookupError(f"知识条目不存在: {entry_id}")
        return entry

    async def _require_candidate(self, candidate_id: int) -> KnowledgeCandidate:
        """读取目标候选，不存在时抛出稳定异常。"""
        candidate = await self._session.get(KnowledgeCandidate, candidate_id)
        if candidate is None:
            raise LookupError(f"FAQ 候选不存在: {candidate_id}")
        return candidate

    def _add_audit(self, employee_id: int, action: str, entry_id: int) -> None:
        """审计只保存动作和条目 ID，不复制问题、答案或关键词。"""
        self._session.add(
            AuditLog(
                actor_employee_id=employee_id,
                action=action,
                target_type="knowledge_entry",
                target_id=str(entry_id),
                details={"entry_id": entry_id},
            )
        )

    def _add_candidate_audit(
        self,
        employee_id: int,
        action: str,
        candidate_id: int,
        *,
        knowledge_entry_id: int | None = None,
    ) -> None:
        """候选审计只保存候选和正式知识编号，不复制任何正文。"""
        details = {"candidate_id": candidate_id}
        if knowledge_entry_id is not None:
            details["knowledge_entry_id"] = knowledge_entry_id
        self._session.add(
            AuditLog(
                actor_employee_id=employee_id,
                action=action,
                target_type="knowledge_candidate",
                target_id=str(candidate_id),
                details=details,
            )
        )


def _get_service(request: Request) -> KnowledgeAdminServicePort:
    """从应用状态读取知识管理服务。"""
    service = getattr(request.app.state, "knowledge_admin_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="知识管理服务尚未配置")
    return cast(KnowledgeAdminServicePort, service)


async def _require_admin(request: Request) -> int:
    """只允许管理员修改知识。"""
    employee_id, role = await require_employee_session(request)
    if role is not EmployeeRole.ADMIN:
        raise HTTPException(status_code=403, detail="只有管理员可以修改知识")
    return employee_id


def _consume_csrf(request: Request, csrf_token: str) -> None:
    """校验并立即消耗知识管理一次性 CSRF 令牌。"""
    expected = request.session.pop("knowledge_csrf", None)
    if not isinstance(expected, str) or not secrets.compare_digest(
        expected, csrf_token
    ):
        raise HTTPException(status_code=409, detail="表单令牌无效或已使用")


def _fields(
    category: str,
    question_zh: str,
    answer_zh: str,
    question_en: str,
    answer_en: str,
    keywords: str,
) -> dict[str, Any]:
    """清理表单字段并把逗号分隔关键词转换为列表。"""
    return {
        "category": category.strip(),
        "question_zh": question_zh.strip(),
        "answer_zh": answer_zh.strip(),
        "question_en": question_en.strip(),
        "answer_en": answer_en.strip(),
        "keywords": [
            item.strip()
            for item in keywords.replace("，", ",").split(",")
            if item.strip()
        ],
    }


@router.get("", response_class=HTMLResponse)
async def knowledge_index(request: Request) -> Response:
    """允许全部已登录员工查看知识及启停状态。"""
    _, role = await require_employee_session(request)
    service = _get_service(request)
    entries = await service.list_all()
    candidates = (
        await service.list_candidates()
        if role is EmployeeRole.ADMIN
        else []
    )
    csrf_token = secrets.token_urlsafe(24)
    request.session["knowledge_csrf"] = csrf_token
    return templates.TemplateResponse(
        request=request,
        name="knowledge/index.html",
        context={
            "entries": entries,
            "candidates": candidates,
            "can_edit": role is EmployeeRole.ADMIN,
            "csrf_token": csrf_token,
        },
    )


@router.post("")
async def create_knowledge(
    request: Request,
    category: str = Form(min_length=1),
    question_zh: str = Form(min_length=1),
    answer_zh: str = Form(min_length=1),
    question_en: str = Form(min_length=1),
    answer_en: str = Form(min_length=1),
    keywords: str = Form(""),
    csrf_token: str = Form(),
) -> RedirectResponse:
    """管理员新增一条同时包含中英文内容的审核知识。"""
    employee_id = await _require_admin(request)
    _consume_csrf(request, csrf_token)
    await _get_service(request).create(
        employee_id,
        **_fields(
            category,
            question_zh,
            answer_zh,
            question_en,
            answer_en,
            keywords,
        ),
    )
    return RedirectResponse(
        "/employee/knowledge", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/candidates/{candidate_id}/convert")
async def convert_candidate(
    request: Request,
    candidate_id: int,
    category: str = Form(min_length=1),
    question_zh: str = Form(min_length=1),
    answer_zh: str = Form(min_length=1),
    question_en: str = Form(min_length=1),
    answer_en: str = Form(min_length=1),
    keywords: str = Form(""),
    csrf_token: str = Form(),
) -> RedirectResponse:
    """管理员修改候选草稿后创建并启用正式双语知识。"""
    employee_id = await _require_admin(request)
    _consume_csrf(request, csrf_token)
    await _get_service(request).convert_candidate(
        candidate_id,
        employee_id,
        **_fields(
            category,
            question_zh,
            answer_zh,
            question_en,
            answer_en,
            keywords,
        ),
    )
    return RedirectResponse(
        "/employee/knowledge", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/candidates/{candidate_id}/snooze")
async def snooze_candidate(
    request: Request,
    candidate_id: int,
    csrf_token: str = Form(),
) -> RedirectResponse:
    """管理员暂不收录候选，并关闭该主题三十天。"""
    employee_id = await _require_admin(request)
    _consume_csrf(request, csrf_token)
    await _get_service(request).snooze_candidate(candidate_id, employee_id)
    return RedirectResponse(
        "/employee/knowledge", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{entry_id}/edit")
async def update_knowledge(
    request: Request,
    entry_id: int,
    category: str = Form(min_length=1),
    question_zh: str = Form(min_length=1),
    answer_zh: str = Form(min_length=1),
    question_en: str = Form(min_length=1),
    answer_en: str = Form(min_length=1),
    keywords: str = Form(""),
    csrf_token: str = Form(),
) -> RedirectResponse:
    """管理员编辑指定双语知识。"""
    employee_id = await _require_admin(request)
    _consume_csrf(request, csrf_token)
    await _get_service(request).update(
        entry_id,
        employee_id,
        **_fields(
            category,
            question_zh,
            answer_zh,
            question_en,
            answer_en,
            keywords,
        ),
    )
    return RedirectResponse(
        "/employee/knowledge", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{entry_id}/{action}")
async def toggle_knowledge(
    request: Request,
    entry_id: int,
    action: str,
    csrf_token: str = Form(),
) -> Response:
    """管理员启用或停用知识，成功后不返回正文。"""
    if action not in {"enable", "disable"}:
        raise HTTPException(status_code=404, detail="未知知识操作")
    employee_id = await _require_admin(request)
    _consume_csrf(request, csrf_token)
    await _get_service(request).set_enabled(
        entry_id, employee_id, enabled=action == "enable"
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
