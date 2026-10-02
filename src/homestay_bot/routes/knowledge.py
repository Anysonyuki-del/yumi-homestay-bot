import hashlib
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Annotated, Any, Literal, Protocol, cast
from urllib.parse import parse_qs, urlencode, urlparse

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BeforeValidator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import EmployeeRole, KnowledgeCandidateStatus
from homestay_bot.domain.errors import OperationRefused
from homestay_bot.domain.models import (
    AuditLog,
    KnowledgeCandidate,
    KnowledgeEmbedding,
    KnowledgeEntry,
    KnowledgeImage,
    PropertyProfile,
)
from homestay_bot.repositories.faq_candidates import SQLAlchemyFaqCandidateRepository
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.routes.admin_form_csrf import AdminCsrfServicePort
from homestay_bot.routes.employee_auth import require_employee_session
from homestay_bot.routes.page_errors import safe_return_path
from homestay_bot.routes.query_params import empty_query_to_none
from homestay_bot.services.admin_csrf import AdminCsrfCapacityError
from homestay_bot.services.knowledge_service import validate_knowledge_scope
from homestay_bot.services.private_file_storage import StoredPrivateFile
from homestay_bot.services.task_page_service import ATTACHMENT_CLEANUP_JOB_TYPE
from homestay_bot.web import pop_page_notice, set_page_notice, templates

router = APIRouter(prefix="/employee/knowledge")
_MAX_CSRF_TOKENS = 8
_KNOWLEDGE_CSRF_PURPOSE = "knowledge-write"
# 知识配图（Spec G1、D1）：每条最多 3 张，单张不超过 2MB。只收 jpg 和 png：
# 企业微信临时图片素材只支持这两种，webp 能存下却发不出去。
MAX_KNOWLEDGE_IMAGES = 3
KNOWLEDGE_IMAGE_MAX_BYTES = 2 * 1024 * 1024
KNOWLEDGE_IMAGE_TYPES = frozenset({"image/png", "image/jpeg"})


class KnowledgeFormError(OperationRefused):
    """知识表单的范围、日期或房间填写有误；文案写给管理员看，可以原样展示。

    页面请求据此原地重新渲染表单并保留已填内容（Spec §4.3）；接口请求仍得到 422。
    """

    status_code = 422


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
        room: str | None = None,
    ) -> list[Any]:
        """分页返回包括停用项在内的知识。"""

    async def list_properties(self) -> list[Any]:
        """提供房间选项，避免管理员手填内部编号。"""

    async def get_detail(self, entry_id: int) -> Any:
        """按编号返回单条知识详情。"""

    async def create(self, employee_id: int, **fields: Any) -> Any:
        """创建一条双语知识。"""

    async def update(self, entry_id: int, employee_id: int, **fields: Any) -> Any:
        """更新一条双语知识。"""

    async def set_enabled(self, entry_id: int, employee_id: int, enabled: bool) -> None:
        """启用或停用知识。"""

    async def delete_entry(self, entry_id: int, employee_id: int) -> None:
        """永久删除一条知识及其配图、向量，并把来源候选改回待处理。"""

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

    async def list_images(self, entry_id: int) -> list[Any]:
        """按发送顺序返回条目配图。"""

    async def upload_image(
        self,
        entry_id: int,
        employee_id: int,
        stream: Any,
        content_type: str,
    ) -> Any:
        """校验并保存一张配图。"""

    async def delete_image(self, entry_id: int, image_id: int, employee_id: int) -> None:
        """删除一张配图并登记文件清理。"""

    async def move_image(
        self, entry_id: int, image_id: int, direction: str, employee_id: int
    ) -> None:
        """把配图前移或后移一位。"""

    async def image_file(self, entry_id: int, image_id: int) -> StoredPrivateFile:
        """返回配图私有文件供后台预览。"""


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
        room: str | None = None,
    ) -> list[KnowledgeEntry]:
        """按关键词、分类、启用状态和房间稳定分页返回知识。

        `room`（Spec F6，借鉴世界书按角色分组）：空为全部；`shared` 为不指定房间的
        本店通用与店外公开知识；数字为指定房间的专属知识。
        """
        statement = select(KnowledgeEntry)
        cleaned_query = (query or "").strip()[:100]
        if cleaned_query:
            escaped = cleaned_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
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
        cleaned_room = (room or "").strip()
        if cleaned_room == "shared":
            statement = statement.where(KnowledgeEntry.property_id.is_(None))
        elif cleaned_room.isdigit():
            statement = statement.where(KnowledgeEntry.property_id == int(cleaned_room))
        result = await self._session.scalars(
            statement.order_by(KnowledgeEntry.id).offset(offset).limit(limit)
        )
        return list(result.all())

    async def list_properties(self) -> list[PropertyProfile]:
        """包含停用房间，编辑已有知识时仍能准确显示原选择。"""
        return list(
            (
                await self._session.scalars(
                    select(PropertyProfile).order_by(
                        PropertyProfile.room_number, PropertyProfile.id
                    )
                )
            ).all()
        )

    async def _validate_scope(self, fields: dict[str, Any]) -> None:
        """写入前统一复核范围和房间存在性，返回可读的表单错误。"""
        try:
            validate_knowledge_scope(
                fields.get("scope", "unreviewed"),
                fields.get("property_id"),
                fields.get("valid_from"),
                fields.get("valid_until"),
            )
        except ValueError as error:
            raise KnowledgeFormError(str(error)) from error
        if (
            fields.get("property_id") is not None
            and await self._session.get(PropertyProfile, fields["property_id"]) is None
        ):
            raise KnowledgeFormError("所选房间不存在，请重新选择")

    async def get_detail(self, entry_id: int) -> KnowledgeEntry:
        """按主键读取知识详情，模板不得自行访问数据库。"""
        return await self._require_entry(entry_id)

    async def create(self, employee_id: int, **fields: Any) -> KnowledgeEntry:
        """创建默认启用的双语知识，并记录最小审计信息。"""
        await self._validate_scope(fields)
        entry = KnowledgeEntry(**fields, is_enabled=True, updated_by=employee_id)
        self._session.add(entry)
        await self._session.flush()
        self._add_audit(employee_id, "knowledge.create", entry.id)
        await self._session.commit()
        return entry

    async def update(self, entry_id: int, employee_id: int, **fields: Any) -> KnowledgeEntry:
        """更新允许编辑的知识字段，并记录条目级审计。"""
        entry = await self._require_entry(entry_id)
        await self._validate_scope(
            {
                name: fields.get(name, getattr(entry, name))
                for name in ("scope", "property_id", "valid_from", "valid_until")
            }
        )
        allowed = {
            "category",
            "question_zh",
            "answer_zh",
            "question_en",
            "answer_en",
            "keywords",
            "scope",
            "property_id",
            "valid_from",
            "valid_until",
            "trigger_any",
            "trigger_exclude",
        }
        for key, value in fields.items():
            if key in allowed:
                setattr(entry, key, value)
        entry.updated_by = employee_id
        self._add_audit(employee_id, "knowledge.update", entry.id)
        await self._session.commit()
        return entry

    async def set_enabled(self, entry_id: int, employee_id: int, enabled: bool) -> None:
        """立即切换知识可用状态并写入最小审计记录。"""
        entry = await self._require_entry(entry_id)
        entry.is_enabled = enabled
        entry.updated_by = employee_id
        action = "knowledge.enable" if enabled else "knowledge.disable"
        self._add_audit(employee_id, action, entry.id)
        await self._session.commit()

    async def delete_entry(self, entry_id: int, employee_id: int) -> None:
        """永久删除一条知识（用户确认的 D3，2026-10-01），全部在同一事务内完成。

        先按 add_image 同样的方式锁住条目：并发上传要么先完成（其配图在这里一并
        清理），要么在删除后拿不到条目而失败，不会留下没有父条目的配图。
        配图与向量表虽声明了级联删除，这里仍显式删除：不依赖 SQLite 是否开启
        外键，两种数据库行为一致。私有文件在提交后由已有的附件清理任务删除；
        事务失败时清理任务随之回滚，文件保持原样。审计只记编号和配图数量。
        """
        entry = await self._session.scalar(
            select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id).with_for_update()
        )
        if entry is None:
            raise LookupError(f"知识条目不存在: {entry_id}")
        file_ids = list(
            (
                await self._session.scalars(
                    select(KnowledgeImage.file_id).where(
                        KnowledgeImage.knowledge_entry_id == entry_id
                    )
                )
            ).all()
        )
        candidate_id = await self._session.scalar(
            select(KnowledgeCandidate.id).where(
                KnowledgeCandidate.knowledge_entry_id == entry_id
            )
        )
        if candidate_id is not None:
            # 必须先解除引用再删条目：候选外键没有级联，顺序反了会撞外键约束。
            await SQLAlchemyFaqCandidateRepository(self._session).reopen_after_entry_deleted(
                candidate_id
            )
            self._add_candidate_audit(
                employee_id,
                "faq_candidate.reopen",
                candidate_id,
                knowledge_entry_id=entry_id,
            )
        await self._session.execute(
            delete(KnowledgeImage).where(KnowledgeImage.knowledge_entry_id == entry_id)
        )
        await self._session.execute(
            delete(KnowledgeEmbedding).where(KnowledgeEmbedding.entry_id == entry_id)
        )
        await self._session.delete(entry)
        if file_ids:
            # 去重键带上这批文件的摘要：SQLite 主键没有 AUTOINCREMENT，删掉最大编号
            # 后会被新条目复用，只用条目编号做键时，第二次删除会撞上旧任务、漏登记
            # 新文件（Codex M4）。摘要对排序后的文件编号取 SHA-256，同一批文件重试
            # 仍得到同一个键，长度固定，远在 jobs.dedupe_key 的 128 字符以内。
            digest = hashlib.sha256(",".join(sorted(file_ids)).encode()).hexdigest()
            await SQLAlchemyJobRepository(self._session).enqueue(
                ATTACHMENT_CLEANUP_JOB_TYPE,
                {"file_ids": file_ids},
                dedupe_key=f"knowledge-entry-cleanup:{entry_id}:{digest}",
            )
        self._session.add(
            AuditLog(
                actor_employee_id=employee_id,
                action="knowledge.delete",
                target_type="knowledge_entry",
                target_id=str(entry_id),
                details={"entry_id": entry_id, "image_count": len(file_ids)},
            )
        )
        await self._session.commit()

    async def list_candidates(self, *, offset: int, limit: int) -> list[KnowledgeCandidate]:
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
        await self._validate_scope(fields)
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

    async def list_images(self, entry_id: int) -> list[KnowledgeImage]:
        """按发送顺序返回条目配图。"""
        return list(
            (
                await self._session.scalars(
                    select(KnowledgeImage)
                    .where(KnowledgeImage.knowledge_entry_id == entry_id)
                    .order_by(KnowledgeImage.sort_order, KnowledgeImage.id)
                )
            ).all()
        )

    async def add_image(
        self,
        entry_id: int,
        employee_id: int,
        *,
        file_id: str,
        content_type: str,
        size: int,
    ) -> KnowledgeImage:
        """锁定条目后登记配图，并发上传也不会超过每条 3 张。"""
        entry = await self._session.scalar(
            select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id).with_for_update()
        )
        if entry is None:
            raise LookupError(f"知识条目不存在: {entry_id}")
        count, last_order = (
            await self._session.execute(
                select(func.count(KnowledgeImage.id), func.max(KnowledgeImage.sort_order)).where(
                    KnowledgeImage.knowledge_entry_id == entry_id
                )
            )
        ).one()
        if count >= MAX_KNOWLEDGE_IMAGES:
            raise ValueError(f"每条知识最多 {MAX_KNOWLEDGE_IMAGES} 张图片")
        image = KnowledgeImage(
            knowledge_entry_id=entry_id,
            file_id=file_id,
            content_type=content_type,
            size=size,
            sort_order=(last_order or 0) + 1,
            created_by=employee_id,
        )
        self._session.add(image)
        await self._session.flush()
        self._add_audit(employee_id, "knowledge.image_add", entry_id, image_id=image.id)
        await self._session.commit()
        return image

    async def delete_image(self, entry_id: int, image_id: int, employee_id: int) -> None:
        """删除配图记录，并在同一事务登记私有文件清理（提交后由 worker 删除）。"""
        image = await self._require_image(entry_id, image_id)
        await self._session.delete(image)
        await SQLAlchemyJobRepository(self._session).enqueue(
            ATTACHMENT_CLEANUP_JOB_TYPE,
            {"file_ids": [image.file_id]},
            dedupe_key=f"knowledge-image-cleanup:{image_id}",
        )
        self._add_audit(employee_id, "knowledge.image_delete", entry_id, image_id=image_id)
        await self._session.commit()

    async def move_image(
        self, entry_id: int, image_id: int, direction: str, employee_id: int
    ) -> None:
        """与相邻配图交换顺序；已在最前或最后时不变。"""
        if direction not in {"up", "down"}:
            raise ValueError("未知的移动方向")
        await self._session.scalar(
            select(KnowledgeEntry.id).where(KnowledgeEntry.id == entry_id).with_for_update()
        )
        images = await self.list_images(entry_id)
        index = next((i for i, item in enumerate(images) if item.id == image_id), None)
        if index is None:
            raise LookupError(f"配图不存在: {image_id}")
        target = index - 1 if direction == "up" else index + 1
        if 0 <= target < len(images):
            # 先按当前顺序重排成连续序号，再交换，历史空洞或重复序号都不影响结果。
            for order, item in enumerate(images, start=1):
                item.sort_order = order
            images[index].sort_order, images[target].sort_order = (
                images[target].sort_order,
                images[index].sort_order,
            )
            self._add_audit(employee_id, "knowledge.image_move", entry_id, image_id=image_id)
        await self._session.commit()

    async def get_image(self, entry_id: int, image_id: int) -> KnowledgeImage:
        """读取属于该条目的配图。"""
        return await self._require_image(entry_id, image_id)

    async def _require_image(self, entry_id: int, image_id: int) -> KnowledgeImage:
        """配图必须属于地址里的条目，防止跨条目操作。"""
        image = await self._session.get(KnowledgeImage, image_id)
        if image is None or image.knowledge_entry_id != entry_id:
            raise LookupError(f"配图不存在: {image_id}")
        return image

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

    def _add_audit(
        self, employee_id: int, action: str, entry_id: int, *, image_id: int | None = None
    ) -> None:
        """审计只保存动作、条目和配图编号，不复制问题、答案、关键词或图片内容。"""
        details: dict[str, int] = {"entry_id": entry_id}
        if image_id is not None:
            details["image_id"] = image_id
        self._session.add(
            AuditLog(
                actor_employee_id=employee_id,
                action=action,
                target_type="knowledge_entry",
                target_id=str(entry_id),
                details=details,
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
    request.session["knowledge_csrf"] = [token for token in tokens if token != csrf_token]
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
            # 会话集合上限与服务端作用域上限同为 8，而裁剪发生在签发之后：不开淘汰
            # 时第九次打开知识页会先被作用域拒绝，页面直接 429，下面的裁剪永远执行
            # 不到。由服务端淘汰最旧 nonce 才能让这段有界窗口真正生效。
            evict_oldest_in_scope=True,
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
    return [token for token in raw_tokens if isinstance(token, str) and 1 <= len(token) <= 128][
        -_MAX_CSRF_TOKENS:
    ]


def _fields(
    category: str,
    question_zh: str,
    answer_zh: str,
    question_en: str,
    answer_en: str,
    keywords: str,
    scope: str = "unreviewed",
    property_id: str = "",
    valid_from: str = "",
    valid_until: str = "",
    trigger_any: str = "",
    trigger_exclude: str = "",
) -> dict[str, Any]:
    """清理表单并复用所有入口一致的范围、日期约束。"""
    try:
        room = int(property_id) if property_id.strip() else None
        start = date.fromisoformat(valid_from) if valid_from else None
        end = date.fromisoformat(valid_until) if valid_until else None
        validate_knowledge_scope(scope, room, start, end)
    except ValueError as error:
        raise KnowledgeFormError(f"知识适用范围或日期有误：{error}") from error
    return {
        "scope": scope,
        "property_id": room,
        "valid_from": start,
        "valid_until": end,
        "category": category.strip(),
        "question_zh": question_zh.strip(),
        "answer_zh": answer_zh.strip(),
        "question_en": question_en.strip(),
        "answer_en": answer_en.strip(),
        "keywords": _word_list(keywords) or [],
        # Spec F5：空表示不限制，存为空值而不是空列表，便于区分「没配置」。
        "trigger_any": _word_list(trigger_any),
        "trigger_exclude": _word_list(trigger_exclude),
    }


def _word_list(value: str) -> list[str] | None:
    """把逗号分隔（中英文逗号均可）的词表清理成列表；没有有效词时返回空值。"""
    words = [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]
    return words or None


_VIEWS = ("entries", "candidates")
_ENABLED_FILTERS = ("enabled", "disabled")


def _wants_html(request: Request) -> bool:
    """表单提交期望页面；接口调用继续得到 JSON。"""
    return "text/html" in request.headers.get("accept", "")


def _submitted_form(**raw: str) -> SimpleNamespace:
    """把刚提交的原始字段整理成表单宏能读的对象，只用于失败后回填。

    这里不做校验也不写库：值来自本次已认证请求，仅原样还给同一位管理员核对修改，
    模板照常转义。房间编号能解析成整数时才回选，避免把任意文本当成下拉选项。
    """
    property_id = raw.get("property_id", "").strip()
    return SimpleNamespace(
        scope=raw.get("scope", "unreviewed"),
        property_id=int(property_id) if property_id.isdigit() else None,
        valid_from=raw.get("valid_from", ""),
        valid_until=raw.get("valid_until", ""),
        trigger_any=_word_list(raw.get("trigger_any", "")),
        trigger_exclude=_word_list(raw.get("trigger_exclude", "")),
        category=raw.get("category", ""),
        question_zh=raw.get("question_zh", ""),
        answer_zh=raw.get("answer_zh", ""),
        question_en=raw.get("question_en", ""),
        answer_en=raw.get("answer_en", ""),
        keywords=_word_list(raw.get("keywords", "")) or [],
    )


def _index_params(return_to: str) -> dict[str, Any]:
    """从列表页带来的来源地址还原分页、筛选和分区，供失败后在原处重新渲染。

    来源先经 safe_return_path 限定在站内；每个参数再按列表路由同样的边界收窄，
    不合法的值直接丢弃，退回默认视图。
    """
    target = urlparse(safe_return_path(return_to, fallback="/employee/knowledge"))
    if target.path != "/employee/knowledge":
        return {}
    query = {key: values[0] for key, values in parse_qs(target.query).items() if values}
    params: dict[str, Any] = {}
    for name in ("page", "candidate_page"):
        value = query.get(name, "")
        if value.isdigit() and 1 <= int(value) <= 10_000:
            params[name] = int(value)
    if query.get("enabled") in _ENABLED_FILTERS:
        params["enabled"] = query["enabled"]
    if query.get("view") in _VIEWS:
        params["view"] = query["view"]
    for name, limit in (("query", 100), ("category", 64), ("room", 20)):
        if query.get(name):
            params[name] = query[name][:limit]
    return params


@router.get("", response_class=HTMLResponse)
async def knowledge_index(
    request: Request,
    page: int = Query(1, ge=1, le=10_000),
    candidate_page: int = Query(1, ge=1, le=10_000),
    query: str | None = Query(None, max_length=100),
    enabled: Annotated[
        Literal["enabled", "disabled"] | None,
        BeforeValidator(empty_query_to_none),
    ] = None,
    category: str | None = Query(None, max_length=64),
    room: str | None = Query(None, max_length=20),
    view: Annotated[
        Literal["entries", "candidates"] | None,
        BeforeValidator(empty_query_to_none),
    ] = None,
) -> Response:
    """允许全部已登录员工查看知识及启停状态；管理员另有待审核候选分区。"""
    return await _render_index(
        request,
        page=page,
        candidate_page=candidate_page,
        query=query,
        enabled=enabled,
        category=category,
        room=room,
        view=view,
    )


async def _render_index(
    request: Request,
    *,
    page: int = 1,
    candidate_page: int = 1,
    query: str | None = None,
    enabled: str | None = None,
    category: str | None = None,
    room: str | None = None,
    view: str | None = None,
    status_code: int = status.HTTP_200_OK,
    error: str | None = None,
    failed_form: dict[str, Any] | None = None,
) -> Response:
    """渲染知识列表；failed_form 给出时把出错的新建或候选表单展开并回填。

    列表分「现有知识／待审核候选」两个分区（F11），默认现有知识，只加载当前分区：
    候选的长表单不再把现有知识挤到页面底部。普通员工没有候选分区，
    传入 view=candidates 也只看到现有知识。
    """
    _, role = await require_employee_session(request)
    is_admin = role is EmployeeRole.ADMIN
    current = "candidates" if view == "candidates" and is_admin else "entries"
    service = _get_service(request)
    enabled_value = None if enabled is None else enabled == "enabled"
    entries = (
        await service.list_all(
            offset=(page - 1) * 50,
            limit=51,
            query=query,
            enabled=enabled_value,
            category=category,
            room=room,
        )
        if current == "entries"
        else []
    )
    candidates = (
        await service.list_candidates(
            offset=(candidate_page - 1) * 50,
            limit=51,
        )
        if current == "candidates"
        else []
    )
    csrf_token = await _issue_csrf(request) if is_admin else ""
    filters = {
        key: value
        for key, value in {
            "query": query or "",
            "enabled": enabled or "",
            "category": category or "",
            "room": room or "",
        }.items()
        if value
    }

    def list_url(entry_page: int, faq_page: int, target_view: str = current) -> str:
        """生成同时保留分区、两个页码与筛选条件的链接。"""
        params: dict[str, Any] = {"page": entry_page, "candidate_page": faq_page, **filters}
        if target_view == "candidates":
            params["view"] = "candidates"
        return "/employee/knowledge?" + urlencode(params)

    current_view = list_url(page, candidate_page)
    return templates.TemplateResponse(
        request=request,
        name="knowledge/index.html",
        status_code=status_code,
        context={
            # 当前视图原样带给每个写操作表单与详情链接：回来时分区、筛选、两个
            # 分页都还在。回跳时仍由 safe_return_path 复核，不接受站外目标。
            "current_view": current_view,
            "view": current,
            "entries_url": list_url(page, candidate_page, "entries"),
            "candidates_url": list_url(page, candidate_page, "candidates"),
            "properties": await service.list_properties(),
            "entries": entries[:50],
            "candidates": candidates[:50],
            "can_edit": is_admin,
            "csrf_token": csrf_token,
            "page": page,
            "candidate_page": candidate_page,
            "query": query or "",
            "enabled_filter": enabled or "",
            "category_filter": category or "",
            "room_filter": room or "",
            "previous_url": (list_url(page - 1, candidate_page) if page > 1 else None),
            "next_url": (list_url(page + 1, candidate_page) if len(entries) > 50 else None),
            "previous_candidate_url": (
                list_url(page, candidate_page - 1) if candidate_page > 1 else None
            ),
            "next_candidate_url": (
                list_url(page, candidate_page + 1) if len(candidates) > 50 else None
            ),
            "error": error,
            "failed_form": failed_form,
            "notice": pop_page_notice(request) or None,
            "page_title": "民宿知识库",
            "active_nav": "knowledge",
        },
    )


@router.get("/{entry_id}", response_class=HTMLResponse)
async def knowledge_detail(
    request: Request,
    entry_id: int,
    return_to: Annotated[str, Query(max_length=200)] = "",
) -> Response:
    """允许员工只读知识详情，并向管理员提供原有编辑表单。"""
    return await _render_detail(request, entry_id, return_to=return_to)


async def _render_detail(
    request: Request,
    entry_id: int,
    *,
    return_to: str = "",
    status_code: int = status.HTTP_200_OK,
    error: str | None = None,
    submitted: SimpleNamespace | None = None,
) -> Response:
    """渲染知识详情；submitted 给出时编辑表单回填刚提交的内容而不是库里的旧值。"""
    _, role = await require_employee_session(request)
    try:
        entry = await _get_service(request).get_detail(entry_id)
    except LookupError as error_:
        raise HTTPException(status_code=404, detail="知识条目不存在") from error_
    return templates.TemplateResponse(
        request=request,
        name="knowledge/detail.html",
        status_code=status_code,
        context={
            "entry": entry,
            "form_entry": submitted or entry,
            "images": await _get_service(request).list_images(entry_id),
            "max_images": MAX_KNOWLEDGE_IMAGES,
            "properties": await _get_service(request).list_properties(),
            "can_edit": role is EmployeeRole.ADMIN,
            "csrf_token": (await _issue_csrf(request) if role is EmployeeRole.ADMIN else ""),
            # 返回与写操作都回到来源列表；来源不在本站时退回知识列表。
            "return_to": safe_return_path(return_to, fallback="/employee/knowledge"),
            "error": error,
            "page_title": f"知识条目 #{entry_id}",
            "active_nav": "knowledge",
        },
    )


def _form_error_response(request: Request, error: KnowledgeFormError) -> None:
    """接口请求保持原来的 422 JSON；页面请求由调用方原地重新渲染。"""
    if not _wants_html(request):
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error


@router.post("")
async def create_knowledge(
    request: Request,
    category: str = Form(min_length=1, max_length=64),
    question_zh: str = Form(min_length=1, max_length=500),
    answer_zh: str = Form(min_length=1, max_length=10_000),
    question_en: str = Form(min_length=1, max_length=500),
    answer_en: str = Form(min_length=1, max_length=10_000),
    keywords: str = Form("", max_length=1000),
    scope: str = Form("unreviewed", max_length=16),
    property_id: str = Form("", max_length=20),
    valid_from: str = Form("", max_length=10),
    valid_until: str = Form("", max_length=10),
    trigger_any: str = Form("", max_length=1000),
    trigger_exclude: str = Form("", max_length=1000),
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
) -> Response:
    """管理员新增一条同时包含中英文内容的审核知识。"""
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    raw = {
        "category": category, "question_zh": question_zh, "answer_zh": answer_zh,
        "question_en": question_en, "answer_en": answer_en, "keywords": keywords,
        "scope": scope, "property_id": property_id, "valid_from": valid_from,
        "valid_until": valid_until, "trigger_any": trigger_any,
        "trigger_exclude": trigger_exclude,
    }
    try:
        await _get_service(request).create(employee_id, **_fields(**raw))
    except KnowledgeFormError as error:
        _form_error_response(request, error)
        return await _render_index(
            request,
            **{**_index_params(return_to), "view": "entries"},
            status_code=error.status_code,
            error=str(error),
            failed_form={"kind": "create", "values": _submitted_form(**raw)},
        )
    return RedirectResponse(
        safe_return_path(return_to, fallback="/employee/knowledge"),
        status_code=status.HTTP_303_SEE_OTHER,
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
    scope: str = Form("unreviewed", max_length=16),
    property_id: str = Form("", max_length=20),
    valid_from: str = Form("", max_length=10),
    valid_until: str = Form("", max_length=10),
    trigger_any: str = Form("", max_length=1000),
    trigger_exclude: str = Form("", max_length=1000),
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
) -> Response:
    """管理员修改候选草稿后创建并启用正式双语知识。"""
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    raw = {
        "category": category, "question_zh": question_zh, "answer_zh": answer_zh,
        "question_en": question_en, "answer_en": answer_en, "keywords": keywords,
        "scope": scope, "property_id": property_id, "valid_from": valid_from,
        "valid_until": valid_until, "trigger_any": trigger_any,
        "trigger_exclude": trigger_exclude,
    }
    try:
        await _get_service(request).convert_candidate(
            candidate_id, employee_id, **_fields(**raw)
        )
    except KnowledgeFormError as error:
        _form_error_response(request, error)
        return await _render_index(
            request,
            **{**_index_params(return_to), "view": "candidates"},
            status_code=error.status_code,
            error=str(error),
            failed_form={
                "kind": "candidate",
                "candidate_id": candidate_id,
                "values": _submitted_form(**raw),
            },
        )
    return RedirectResponse(
        safe_return_path(return_to, fallback="/employee/knowledge?view=candidates"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/candidates/{candidate_id}/snooze")
async def snooze_candidate(
    request: Request,
    candidate_id: int,
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
) -> RedirectResponse:
    """管理员暂不收录候选，并关闭该主题三十天。"""
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    await _get_service(request).snooze_candidate(candidate_id, employee_id)
    return RedirectResponse(
        safe_return_path(return_to, fallback="/employee/knowledge?view=candidates"),
        status_code=status.HTTP_303_SEE_OTHER,
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
    scope: str = Form("unreviewed", max_length=16),
    property_id: str = Form("", max_length=20),
    valid_from: str = Form("", max_length=10),
    valid_until: str = Form("", max_length=10),
    trigger_any: str = Form("", max_length=1000),
    trigger_exclude: str = Form("", max_length=1000),
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
) -> Response:
    """管理员编辑指定双语知识。"""
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    raw = {
        "category": category, "question_zh": question_zh, "answer_zh": answer_zh,
        "question_en": question_en, "answer_en": answer_en, "keywords": keywords,
        "scope": scope, "property_id": property_id, "valid_from": valid_from,
        "valid_until": valid_until, "trigger_any": trigger_any,
        "trigger_exclude": trigger_exclude,
    }
    try:
        await _get_service(request).update(entry_id, employee_id, **_fields(**raw))
    except KnowledgeFormError as error:
        _form_error_response(request, error)
        return await _render_detail(
            request,
            entry_id,
            return_to=return_to,
            status_code=error.status_code,
            error=str(error),
            submitted=_submitted_form(**raw),
        )
    return RedirectResponse(
        safe_return_path(return_to, fallback="/employee/knowledge"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


# 删除必须注册在 `/{entry_id}/{action}` 启停路由之前：FastAPI 按注册顺序匹配，
# 注册在后面的话 POST /1/delete 会先被启停路由截走并返回 404（Codex 探针 E9）。
@router.post("/{entry_id}/delete")
async def delete_knowledge(
    request: Request,
    entry_id: int,
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
) -> RedirectResponse:
    """管理员永久删除一条知识；删除后回到来源列表并提示结果。"""
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    try:
        await _get_service(request).delete_entry(entry_id, employee_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="知识条目不存在或已删除") from error
    set_page_notice(request, f"已永久删除知识 #{entry_id}")
    target = safe_return_path(return_to, fallback="/employee/knowledge")
    # 从详情页删除时来源是详情页本身，删完它已不存在，改回知识列表。
    if urlparse(target).path == f"/employee/knowledge/{entry_id}":
        target = "/employee/knowledge"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


def _images_anchor(entry_id: int) -> str:
    """配图操作完成后回到详情页配图区。"""
    return f"/employee/knowledge/{entry_id}#knowledge-images"


@router.get("/{entry_id}/images/{image_id}")
async def knowledge_image(request: Request, entry_id: int, image_id: int) -> Response:
    """员工可预览配图；只按条目内编号取文件，不接受文件名。"""
    await require_employee_session(request)
    try:
        stored = await _get_service(request).image_file(entry_id, image_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="配图不存在") from error
    response = FileResponse(stored.path, media_type=stored.content_type, filename=None)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.post("/{entry_id}/images/upload")
async def upload_knowledge_image(
    request: Request,
    entry_id: int,
    image: Annotated[UploadFile, File()],
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """管理员给条目加一张配图：限 JPG、PNG，单张 2MB，每条最多 3 张。"""
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    try:
        await _get_service(request).upload_image(
            entry_id,
            employee_id,
            image.file,
            image.content_type or "application/octet-stream",
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail="知识条目不存在") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        await image.close()
    return RedirectResponse(_images_anchor(entry_id), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{entry_id}/images/{image_id}/delete")
async def delete_knowledge_image(
    request: Request,
    entry_id: int,
    image_id: int,
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """管理员删除一张配图。"""
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    try:
        await _get_service(request).delete_image(entry_id, image_id, employee_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="配图不存在") from error
    return RedirectResponse(_images_anchor(entry_id), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{entry_id}/images/{image_id}/move")
async def move_knowledge_image(
    request: Request,
    entry_id: int,
    image_id: int,
    direction: Literal["up", "down"] = Form(),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """管理员调整配图发送顺序。"""
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    try:
        await _get_service(request).move_image(entry_id, image_id, direction, employee_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="配图不存在") from error
    return RedirectResponse(_images_anchor(entry_id), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{entry_id}/{action}")
async def toggle_knowledge(
    request: Request,
    entry_id: int,
    action: str,
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
) -> RedirectResponse:
    """管理员启用或停用知识，成功后返回可见的列表页面。"""
    if action not in {"enable", "disable"}:
        raise HTTPException(status_code=404, detail="未知知识操作")
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    await _get_service(request).set_enabled(entry_id, employee_id, enabled=action == "enable")
    # 回到来源视图；来源里没带锚点时才补上条目区锚点。
    target = safe_return_path(return_to, fallback="/employee/knowledge")
    if "#" not in target:
        target = f"{target}#knowledge-entries"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
