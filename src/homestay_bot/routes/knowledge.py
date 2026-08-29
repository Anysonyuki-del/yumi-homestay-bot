from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import EmployeeRole, KnowledgeCandidateStatus
from homestay_bot.domain.models import AuditLog, KnowledgeCandidate, KnowledgeEntry
from homestay_bot.repositories.faq_candidates import SQLAlchemyFaqCandidateRepository
from homestay_bot.routes.employee_auth import (
    AdminCsrfServicePort,
    require_employee_session,
)
from homestay_bot.services.admin_csrf import AdminCsrfCapacityError
from homestay_bot.web import templates

router = APIRouter(prefix="/employee/knowledge")
_MAX_CSRF_TOKENS = 8
_KNOWLEDGE_CSRF_PURPOSE = "knowledge-write"


class KnowledgeAdminServicePort(Protocol):
    """定义知识管理路由所需接口。"""

    async def list_all(
        self,
        *,
        offset: int,
        limit: int,
        query: str | None = None,
        enabled: bool | None = None,
        category: str | None = None,
    ) -> list[Any]:
        """分页返回包括停用项在内的知识。"""

    async def get_detail(self, entry_id: int) -> Any:
        """按编号返回单条知识详情。"""

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

    async def list_candidates(self, *, offset: int, limit: int) -> list[Any]:
        """分页返回管理员待归纳候选。"""

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

    async def list_all(
        self,
        *,
        offset: int,
        limit: int,
        query: str | None = None,
        enabled: bool | None = None,
        category: str | None = None,
    ) -> list[KnowledgeEntry]:
        """按关键词、分类和启用状态稳定分页返回知识。"""
        statement = select(KnowledgeEntry)
        cleaned_query = (query or "").strip()[:100]
        if cleaned_query:
            escaped = (
                cleaned_query.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            pattern = f"%{escaped}%"
            statement = statement.where(
                or_(
                    KnowledgeEntry.question_zh.ilike(pattern, escape="\\"),
                    KnowledgeEntry.answer_zh.ilike(pattern, escape="\\"),
                    KnowledgeEntry.question_en.ilike(pattern, escape="\\"),
                )
            )
        if enabled is not None:
            statement = statement.where(KnowledgeEntry.is_enabled.is_(enabled))
        cleaned_category = (category or "").strip()[:64]
        if cleaned_category:
            statement = statement.where(KnowledgeEntry.category == cleaned_category)
        result = await self._session.scalars(
            statement.order_by(KnowledgeEntry.id).offset(offset).limit(limit)
        )
        return list(result.all())

    async def get_detail(self, entry_id: int) -> KnowledgeEntry:
        """按主键读取知识详情，模板不得自行访问数据库。"""
        return await self._require_entry(entry_id)

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

    async def list_candidates(
        self, *, offset: int, limit: int
    ) -> list[KnowledgeCandidate]:
        """分页返回仍开放的候选，正文仅交给管理员页面。"""
        result = await self._session.scalars(
            select(KnowledgeCandidate)
            .where(KnowledgeCandidate.status == KnowledgeCandidateStatus.OPEN)
            .order_by(KnowledgeCandidate.last_seen_at.desc(), KnowledgeCandidate.id.desc())
            .offset(offset)
            .limit(limit)
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


def _get_csrf_service(request: Request) -> AdminCsrfServicePort:
    """读取服务端 nonce 服务，避免把可覆盖的 Cookie 当作安全真值。"""
    service = getattr(request.app.state, "admin_csrf_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="表单安全服务尚未配置")
    return cast(AdminCsrfServicePort, service)


def _csrf_admin_id(request: Request) -> int:
    """读取已由员工会话复核过的管理员主体编号。"""
    admin_id = request.session.get("admin_id")
    if not isinstance(admin_id, int):
        raise HTTPException(status_code=401, detail="管理员尚未登录")
    return admin_id


async def _consume_csrf(request: Request, csrf_token: str) -> None:
    """按知识用途和管理员主体原子消费服务端一次性 nonce。"""
    consumed = await _get_csrf_service(request).consume(
        csrf_token,
        _KNOWLEDGE_CSRF_PURPOSE,
        admin_id=_csrf_admin_id(request),
    )
    # Cookie 集合只用于兼容旧页面和控制体积，绝不参与授权判断。
    tokens = _csrf_tokens(request)
    request.session["knowledge_csrf"] = [
        token for token in tokens if token != csrf_token
    ]
    if not consumed:
        raise HTTPException(status_code=409, detail="表单令牌无效或已使用")


async def _issue_csrf(request: Request) -> str:
    """签发服务端知识 nonce，并把 Cookie 兼容集合限制为最近八个。"""
    service = _get_csrf_service(request)
    admin_id = _csrf_admin_id(request)
    try:
        token = await service.issue(
            _KNOWLEDGE_CSRF_PURPOSE,
            admin_id=admin_id,
        )
    except AdminCsrfCapacityError as error:
        raise HTTPException(status_code=429, detail="表单请求过于频繁") from error
    tokens = _csrf_tokens(request)
    tokens.append(token)
    # 顺序浏览产生第九个 nonce 时同步撤销最旧项，服务端也保持同一有界窗口。
    for expired_token in tokens[:-_MAX_CSRF_TOKENS]:
        await service.consume(
            expired_token,
            _KNOWLEDGE_CSRF_PURPOSE,
            admin_id=admin_id,
        )
    request.session["knowledge_csrf"] = tokens[-_MAX_CSRF_TOKENS:]
    return token


def _csrf_tokens(request: Request) -> list[str]:
    """安全归一化新旧会话中的知识令牌，并拒绝异常会话结构。"""
    stored = request.session.get("knowledge_csrf", [])
    # 兼容升级前仍存活的单值会话，避免部署后无故使已有表单失效。
    if isinstance(stored, str):
        raw_tokens: list[object] = [stored]
    elif isinstance(stored, list):
        raw_tokens = stored
    else:
        raw_tokens = []
    return [
        token
        for token in raw_tokens
        if isinstance(token, str) and 1 <= len(token) <= 128
    ][-_MAX_CSRF_TOKENS:]


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
async def knowledge_index(
    request: Request,
    page: int = Query(1, ge=1, le=10_000),
    candidate_page: int = Query(1, ge=1, le=10_000),
    query: str | None = Query(None, max_length=100),
    enabled: Literal["enabled", "disabled"] | None = None,
    category: str | None = Query(None, max_length=64),
) -> Response:
    """允许全部已登录员工查看知识及启停状态。"""
    _, role = await require_employee_session(request)
    service = _get_service(request)
    enabled_value = None if enabled is None else enabled == "enabled"
    entries = await service.list_all(
        offset=(page - 1) * 50,
        limit=51,
        query=query,
        enabled=enabled_value,
        category=category,
    )
    candidates = (
        await service.list_candidates(
            offset=(candidate_page - 1) * 50,
            limit=51,
        )
        if role is EmployeeRole.ADMIN
        else []
    )
    csrf_token = await _issue_csrf(request) if role is EmployeeRole.ADMIN else ""
    filters = {
        key: value
        for key, value in {
            "query": query or "",
            "enabled": enabled or "",
            "category": category or "",
        }.items()
        if value
    }

    def list_url(entry_page: int, faq_page: int) -> str:
        """生成同时保留正式知识、候选页码与筛选条件的链接。"""
        return "/employee/knowledge?" + urlencode(
            {"page": entry_page, "candidate_page": faq_page, **filters}
        )

    return templates.TemplateResponse(
        request=request,
        name="knowledge/index.html",
        context={
            "entries": entries[:50],
            "candidates": candidates[:50],
            "can_edit": role is EmployeeRole.ADMIN,
            "csrf_token": csrf_token,
            "page": page,
            "candidate_page": candidate_page,
            "query": query or "",
            "enabled_filter": enabled or "",
            "category_filter": category or "",
            "previous_page": page - 1 if page > 1 else None,
            "next_page": page + 1 if len(entries) > 50 else None,
            "previous_url": (
                list_url(page - 1, candidate_page) if page > 1 else None
            ),
            "next_url": (
                list_url(page + 1, candidate_page)
                if len(entries) > 50
                else None
            ),
            "previous_candidate_page": (
                candidate_page - 1 if candidate_page > 1 else None
            ),
            "next_candidate_page": (
                candidate_page + 1 if len(candidates) > 50 else None
            ),
            "previous_candidate_url": (
                list_url(page, candidate_page - 1)
                if candidate_page > 1
                else None
            ),
            "next_candidate_url": (
                list_url(page, candidate_page + 1)
                if len(candidates) > 50
                else None
            ),
            "page_title": "民宿知识库",
            "active_nav": "knowledge",
        },
    )


@router.get("/{entry_id}", response_class=HTMLResponse)
async def knowledge_detail(request: Request, entry_id: int) -> Response:
    """允许员工只读知识详情，并向管理员提供原有编辑表单。"""
    _, role = await require_employee_session(request)
    try:
        entry = await _get_service(request).get_detail(entry_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="知识条目不存在") from error
    return templates.TemplateResponse(
        request=request,
        name="knowledge/detail.html",
        context={
            "entry": entry,
            "can_edit": role is EmployeeRole.ADMIN,
            "csrf_token": (
                await _issue_csrf(request) if role is EmployeeRole.ADMIN else ""
            ),
            "page_title": f"知识条目 #{entry_id}",
            "active_nav": "knowledge",
        },
    )


@router.post("")
async def create_knowledge(
    request: Request,
    category: str = Form(min_length=1, max_length=64),
    question_zh: str = Form(min_length=1, max_length=500),
    answer_zh: str = Form(min_length=1, max_length=10_000),
    question_en: str = Form(min_length=1, max_length=500),
    answer_en: str = Form(min_length=1, max_length=10_000),
    keywords: str = Form("", max_length=1000),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """管理员新增一条同时包含中英文内容的审核知识。"""
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
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
    category: str = Form(min_length=1, max_length=64),
    question_zh: str = Form(min_length=1, max_length=500),
    answer_zh: str = Form(min_length=1, max_length=10_000),
    question_en: str = Form(min_length=1, max_length=500),
    answer_en: str = Form(min_length=1, max_length=10_000),
    keywords: str = Form("", max_length=1000),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """管理员修改候选草稿后创建并启用正式双语知识。"""
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
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
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """管理员暂不收录候选，并关闭该主题三十天。"""
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    await _get_service(request).snooze_candidate(candidate_id, employee_id)
    return RedirectResponse(
        "/employee/knowledge", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{entry_id}/edit")
async def update_knowledge(
    request: Request,
    entry_id: int,
    category: str = Form(min_length=1, max_length=64),
    question_zh: str = Form(min_length=1, max_length=500),
    answer_zh: str = Form(min_length=1, max_length=10_000),
    question_en: str = Form(min_length=1, max_length=500),
    answer_en: str = Form(min_length=1, max_length=10_000),
    keywords: str = Form("", max_length=1000),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """管理员编辑指定双语知识。"""
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
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
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """管理员启用或停用知识，成功后返回可见的列表页面。"""
    if action not in {"enable", "disable"}:
        raise HTTPException(status_code=404, detail="未知知识操作")
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    await _get_service(request).set_enabled(
        entry_id, employee_id, enabled=action == "enable"
    )
    return RedirectResponse(
        "/employee/knowledge#knowledge-entries",
        status_code=status.HTTP_303_SEE_OTHER,
    )
