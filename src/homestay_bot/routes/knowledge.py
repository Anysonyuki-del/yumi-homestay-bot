from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import AuditLog, KnowledgeEntry

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


class KnowledgeAdminService:
    """持久化知识管理变更并写入不含正文的审计记录。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前数据库会话。"""
        self._session = session

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

    async def _require_entry(self, entry_id: int) -> KnowledgeEntry:
        """读取目标知识，不存在时抛出稳定异常。"""
        entry = await self._session.get(KnowledgeEntry, entry_id)
        if entry is None:
            raise LookupError(f"知识条目不存在: {entry_id}")
        return entry

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


def _get_service(request: Request) -> KnowledgeAdminServicePort:
    """从应用状态读取知识管理服务。"""
    service = getattr(request.app.state, "knowledge_admin_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="知识管理服务尚未配置")
    return cast(KnowledgeAdminServicePort, service)


def _require_employee(request: Request) -> tuple[int, EmployeeRole]:
    """读取签名员工会话，并拒绝未登录或无效角色。"""
    employee_id = request.session.get("employee_id")
    role = request.session.get("employee_role")
    if not isinstance(employee_id, int) or not isinstance(role, str):
        raise HTTPException(status_code=401, detail="员工尚未登录")
    try:
        return employee_id, EmployeeRole(role)
    except ValueError as error:
        raise HTTPException(status_code=401, detail="员工角色无效") from error


def _require_admin(request: Request) -> int:
    """只允许管理员修改知识。"""
    employee_id, role = _require_employee(request)
    if role is not EmployeeRole.ADMIN:
        raise HTTPException(status_code=403, detail="只有管理员可以修改知识")
    return employee_id


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
    _, role = _require_employee(request)
    entries = await _get_service(request).list_all()
    return templates.TemplateResponse(
        request=request,
        name="knowledge/index.html",
        context={"entries": entries, "can_edit": role is EmployeeRole.ADMIN},
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
) -> RedirectResponse:
    """管理员新增一条同时包含中英文内容的审核知识。"""
    employee_id = _require_admin(request)
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
) -> RedirectResponse:
    """管理员编辑指定双语知识。"""
    employee_id = _require_admin(request)
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
    request: Request, entry_id: int, action: str
) -> Response:
    """管理员启用或停用知识，成功后不返回正文。"""
    if action not in {"enable", "disable"}:
        raise HTTPException(status_code=404, detail="未知知识操作")
    employee_id = _require_admin(request)
    await _get_service(request).set_enabled(
        entry_id, employee_id, enabled=action == "enable"
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
