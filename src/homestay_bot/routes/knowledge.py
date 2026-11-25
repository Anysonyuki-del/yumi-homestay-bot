import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
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
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import (
    EmployeeRole,
    KnowledgeCandidateStatus,
    KnowledgeReferralStatus,
)
from homestay_bot.domain.errors import OperationRefused
from homestay_bot.domain.models import (
    AuditLog,
    KnowledgeCandidate,
    KnowledgeEmbedding,
    KnowledgeEntry,
    KnowledgeImage,
    KnowledgeReferral,
    Message,
    PropertyProfile,
)
from homestay_bot.repositories.faq_candidates import SQLAlchemyFaqCandidateRepository
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.repositories.knowledge_referrals import (
    DRAFT_SOURCE_LIMIT,
    DRAFTABLE_STATUSES,
    OPEN_STATUSES,
    SQLAlchemyKnowledgeReferralRepository,
    window_start,
)
from homestay_bot.routes.admin_form_csrf import AdminCsrfServicePort
from homestay_bot.routes.employee_auth import require_employee_session
from homestay_bot.routes.page_errors import safe_return_path
from homestay_bot.routes.query_params import empty_query_to_none
from homestay_bot.services.admin_csrf import AdminCsrfCapacityError
from homestay_bot.services.knowledge_referral_service import KNOWLEDGE_REFERRAL_DRAFT_JOB_TYPE
from homestay_bot.services.knowledge_service import (
    validate_knowledge_content,
    validate_knowledge_scope,
)
from homestay_bot.services.private_file_storage import StoredPrivateFile
from homestay_bot.services.sensitive_text import contains_sensitive_text
from homestay_bot.services.task_page_service import ATTACHMENT_CLEANUP_JOB_TYPE
from homestay_bot.web import pop_page_notice, set_page_notice, templates

router = APIRouter(prefix="/employee/knowledge")
_MAX_CSRF_TOKENS = 8
_KNOWLEDGE_CSRF_PURPOSE = "knowledge-write"
# 知识配图（Spec G1、D1）：每条最多 4 张（2026-11-25 知识库重构 Spec P2-1 由 3 张放宽，
# 与机器人每轮最多发 4 张图对齐），单张不超过 5MB（用户 2026-11-03 由 2MB 放宽）。
# 只收 jpg 和 png：企业微信临时图片素材只支持这两种，webp 能存下却发不出去。
# 企业微信临时图片素材上限 10MB，5MB 留有余量；房间欢迎图片也走这个上限。
MAX_KNOWLEDGE_IMAGES = 4
# 跨房复制（Spec §3.2(4)）：一次最多复制到 100 间房；连同配图时物理拷贝的文件总数
# 不超过 36 个，避免一次请求在事务外拷贝过多文件、拖长请求并放大失败清理面。
MAX_COPY_TARGET_ROOMS = 100
MAX_COPY_IMAGE_FILES = 36
KNOWLEDGE_IMAGE_MAX_BYTES = 5 * 1024 * 1024
KNOWLEDGE_IMAGE_TYPES = frozenset({"image/png", "image/jpeg"})


# 发给客人的中英问答字段；占位符检查只看这四项。
_QA_FIELDS = ("question_zh", "answer_zh", "question_en", "answer_en")


class KnowledgeFormError(OperationRefused):
    """知识表单的范围、日期或房间填写有误；文案写给管理员看，可以原样展示。

    页面请求据此原地重新渲染表单并保留已填内容（Spec §4.3）；接口请求仍得到 422。
    """

    status_code = 422


def _validate_active_english(
    *,
    is_enabled: bool,
    scope: str,
    question_en: str | None,
    answer_en: str | None,
) -> None:
    """英文生效不变式的唯一入口（Spec §3.2(1)，用户裁决 P1=B）。

    只有「已启用且范围不是待审核」的条目会被 `list_active()` 发给机器人；这类条目
    必须有英文问答，否则外国客人会拿到空答案。停用草稿和待审核条目允许英文留空。
    编辑、启用、快捷定界、候选转换、资料核实入库全部走这里，规则只写一处。
    """
    if (
        is_enabled
        and scope != "unreviewed"
        and (not (question_en or "").strip() or not (answer_en or "").strip())
    ):
        raise KnowledgeFormError("启用中的审核知识必须填写英文问题和英文答案，请补全后再启用")


# 列表分区（Spec F1）与知识范围的对应关系；rooms 分区就是房间专属知识。
_SECTIONS = ("global", "rooms", "public", "unreviewed")
_SECTION_SCOPES = {
    "global": "global",
    "rooms": "property",
    "public": "public",
    "unreviewed": "unreviewed",
}


_SECTION_LABELS = {
    "global": "全店通用",
    "rooms": "按房间管理",
    "public": "店外周边",
    "unreviewed": "范围待审核",
}


def section_tabs(
    counts: dict[str, int],
    url_for: Callable[[str], str],
    current: str | None,
) -> list[dict[str, Any]]:
    """知识库前三个页签（全店通用、按房间管理、店外周边）及命中数。

    知识列表与资料核实页共用，保证两处页头一致（Spec F1）；current 为空表示
    当前页不在这三个分区里。
    """
    return [
        {
            "key": key,
            "label": _SECTION_LABELS[key],
            "url": url_for(key),
            "count": counts.get(key, 0),
            "current": current == key,
        }
        for key in ("global", "rooms", "public")
    ]


def _initially_enabled(fields: dict[str, Any]) -> bool:
    """新建时的启停状态：英文齐全或范围待审核才直接启用，否则存为停用草稿。"""
    if fields.get("scope", "unreviewed") == "unreviewed":
        return True
    return bool(
        str(fields.get("question_en") or "").strip()
        and str(fields.get("answer_en") or "").strip()
    )


def _normalized_question(question: str | None) -> str:
    """同名比较用：去掉全部空白并忽略大小写。"""
    return "".join((question or "").split()).casefold()


# 入库时管理员提交的全部文本字段，逐项做凭据检测（Spec 2026-10-08 fix2 §2.6(0)(5)）。
_REFERRAL_TEXT_FIELDS = ("category", *_QA_FIELDS)
_REFERRAL_SUPPRESSION = timedelta(days=30)


@dataclass(frozen=True)
class CopyImageSnapshot:
    """跨房复制第 1 步读到的一张源配图；第 3 步按 (id, file_id, sort_order) 逐项复核。"""

    id: int
    file_id: str
    sort_order: int
    content_type: str
    size: int


@dataclass(frozen=True)
class CopySnapshot:
    """跨房复制第 1 步的只读快照：源条目、去重后的目标房间与（可选）源配图。"""

    entry_id: int
    property_ids: tuple[int, ...]
    copy_images: bool
    images: tuple[CopyImageSnapshot, ...]


@dataclass(frozen=True)
class CopyResult:
    """跨房复制写入结果：新条目编号，以及目标房里已有同名问题的房间数。"""

    entry_ids: list[int]
    duplicate_rooms: int


@dataclass(frozen=True)
class ReferralStaffMessage:
    """详情页的一条管家消息：敏感消息只列出不交给模型，与转交同一秒的标注核对。"""

    message: Message
    sensitive: bool
    near_time: bool
    frozen: bool
    used: bool


@dataclass(frozen=True)
class ReferralListItem:
    """列表一行：转交记录与按窗口实时计算的管家回复条数。"""

    referral: KnowledgeReferral
    reply_count: int


@dataclass(frozen=True)
class ReferralDetail:
    """资料核实详情页需要的全部数据，模板不自行访问数据库。"""

    referral: KnowledgeReferral
    property: PropertyProfile | None
    messages: list[ReferralStaffMessage] = field(default_factory=list)


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
        section: str | None = None,
    ) -> list[Any]:
        """分页返回包括停用项在内的知识。"""

    async def section_counts(
        self,
        *,
        query: str | None = None,
        enabled: bool | None = None,
        category: str | None = None,
    ) -> dict[str, int]:
        """当前筛选下四个分区的命中数，另含全库待审核总数 unreviewed_total。"""

    async def room_entry_counts(
        self,
        *,
        query: str | None = None,
        enabled: bool | None = None,
        category: str | None = None,
    ) -> dict[int, int]:
        """当前筛选下各房间的专属知识条数。"""

    async def list_categories(
        self, *, section: str | None = None, room: str | None = None
    ) -> list[tuple[str, int]]:
        """已有分类及条数，供筛选下拉与表单补全。"""

    async def image_counts_for_entries(self, entry_ids: list[int]) -> dict[int, int]:
        """一次查询本页条目的配图数量。"""

    async def list_properties(self) -> list[Any]:
        """提供房间选项，避免管理员手填内部编号。"""

    async def get_detail(self, entry_id: int) -> Any:
        """按编号返回单条知识详情。"""

    async def create(self, employee_id: int, **fields: Any) -> Any:
        """创建一条双语知识。"""

    async def create_for_rooms(
        self, employee_id: int, property_ids: list[int], **fields: Any
    ) -> list[Any]:
        """同一事务为多个在用房间各建一条房间专属知识。"""

    async def set_scope(self, entry_id: int, employee_id: int, target_scope: str) -> Any:
        """把范围待审核的条目快捷定界为全店通用或店外周边。"""

    async def copy_to_rooms(
        self,
        entry_id: int,
        employee_id: int,
        property_ids: list[int],
        *,
        copy_images: bool,
    ) -> CopyResult:
        """把一条知识复制到其他房间，结果均为停用草稿。"""

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

    async def list_referrals(self, *, limit: int = 50, offset: int = 0) -> list[Any]:
        """资料核实列表。"""

    async def count_open_referrals(self) -> int:
        """待处理的资料核实数量。"""

    async def referral_detail(self, referral_id: int) -> Any:
        """资料核实详情。"""

    async def start_referral_draft(self, referral_id: int, employee_id: int) -> None:
        """管理员点「整理草稿」：换代并冻结本代次的管家消息。"""

    async def convert_referral(
        self,
        referral_id: int,
        employee_id: int,
        *,
        generation: int,
        reviewed: bool,
        **fields: Any,
    ) -> Any:
        """把已整理的草稿核对后入库。"""

    async def close_referral(self, referral_id: int, employee_id: int) -> None:
        """关闭为「不入库」并写入 30 天抑制。"""


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
        section: str | None = None,
    ) -> list[KnowledgeEntry]:
        """按关键词、分类、启用状态、分区和房间稳定分页返回知识。

        `section`（2026-11-25 知识库重构 Spec F1）：按范围分区过滤，空为全部。
        `room`（Spec F6，借鉴世界书按角色分组）：空为全部；`shared` 为不指定房间的
        本店通用与店外公开知识；数字为指定房间的专属知识。
        """
        statement = self._filtered(select(KnowledgeEntry), query, enabled, category)
        if section in _SECTION_SCOPES:
            statement = statement.where(KnowledgeEntry.scope == _SECTION_SCOPES[section])
        cleaned_room = (room or "").strip()
        if cleaned_room == "shared":
            statement = statement.where(KnowledgeEntry.property_id.is_(None))
        elif cleaned_room.isdigit():
            statement = statement.where(KnowledgeEntry.property_id == int(cleaned_room))
        result = await self._session.scalars(
            statement.order_by(KnowledgeEntry.id).offset(offset).limit(limit)
        )
        return list(result.all())

    @staticmethod
    def _filtered(
        statement: Any,
        query: str | None,
        enabled: bool | None,
        category: str | None,
    ) -> Any:
        """列表与各类计数共用的关键词、启用状态、分类过滤，保证页签数字与列表一致。"""
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
        return statement

    async def section_counts(
        self,
        *,
        query: str | None = None,
        enabled: bool | None = None,
        category: str | None = None,
    ) -> dict[str, int]:
        """一次 GROUP BY 得到当前筛选下各分区命中数（Spec F1 跨区搜索发现性）。

        另返回不受筛选影响的全库待审核总数 `unreviewed_total`：提醒横幅只在存量
        真的存在时出现，不因关键词过滤而消失。
        """
        rows = (
            await self._session.execute(
                self._filtered(
                    select(KnowledgeEntry.scope, func.count(KnowledgeEntry.id)),
                    query,
                    enabled,
                    category,
                ).group_by(KnowledgeEntry.scope)
            )
        ).all()
        by_scope = {scope: count for scope, count in rows}
        counts = {
            section: int(by_scope.get(scope, 0))
            for section, scope in _SECTION_SCOPES.items()
        }
        counts["unreviewed_total"] = int(
            await self._session.scalar(
                select(func.count(KnowledgeEntry.id)).where(
                    KnowledgeEntry.scope == "unreviewed"
                )
            )
            or 0
        )
        return counts

    async def room_entry_counts(
        self,
        *,
        query: str | None = None,
        enabled: bool | None = None,
        category: str | None = None,
    ) -> dict[int, int]:
        """当前筛选下各房间的专属知识条数，房间目录据此显示数量胶囊。"""
        rows = (
            await self._session.execute(
                self._filtered(
                    select(KnowledgeEntry.property_id, func.count(KnowledgeEntry.id)),
                    query,
                    enabled,
                    category,
                )
                .where(
                    KnowledgeEntry.scope == "property",
                    KnowledgeEntry.property_id.is_not(None),
                )
                .group_by(KnowledgeEntry.property_id)
            )
        ).all()
        return {int(property_id): int(count) for property_id, count in rows}

    async def list_categories(
        self, *, section: str | None = None, room: str | None = None
    ) -> list[tuple[str, int]]:
        """已有分类及条数；不带参数时为全库去重分类（表单补全用）。"""
        statement = select(KnowledgeEntry.category, func.count(KnowledgeEntry.id))
        if section in _SECTION_SCOPES:
            statement = statement.where(KnowledgeEntry.scope == _SECTION_SCOPES[section])
        cleaned_room = (room or "").strip()
        if cleaned_room.isdigit():
            statement = statement.where(KnowledgeEntry.property_id == int(cleaned_room))
        rows = (
            await self._session.execute(
                statement.group_by(KnowledgeEntry.category).order_by(KnowledgeEntry.category)
            )
        ).all()
        return [(str(category), int(count)) for category, count in rows]

    async def image_counts_for_entries(self, entry_ids: list[int]) -> dict[int, int]:
        """单次 GROUP BY 批量查配图数，列表卡片的角标不产生 N+1 查询。"""
        if not entry_ids:
            return {}
        rows = (
            await self._session.execute(
                select(KnowledgeImage.knowledge_entry_id, func.count(KnowledgeImage.id))
                .where(KnowledgeImage.knowledge_entry_id.in_(entry_ids))
                .group_by(KnowledgeImage.knowledge_entry_id)
            )
        ).all()
        return {int(entry_id): int(count) for entry_id, count in rows}

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

    @staticmethod
    def _validate_content(fields: dict[str, Any]) -> None:
        """写入前拒绝仍带草稿占位符的问答，候选页预填的草稿必须先核实替换。"""
        try:
            validate_knowledge_content(
                *(fields.get(name) for name in _QA_FIELDS)
            )
        except ValueError as error:
            raise KnowledgeFormError(str(error)) from error

    async def get_detail(self, entry_id: int) -> KnowledgeEntry:
        """按主键读取知识详情，模板不得自行访问数据库。"""
        return await self._require_entry(entry_id)

    async def create(self, employee_id: int, **fields: Any) -> KnowledgeEntry:
        """创建一条知识并记录最小审计信息。

        英文齐全（或范围待审核）时直接启用；缺英文时存为停用草稿而不打回表单
        （Spec §3.2(1) 新建特殊规则），英文生效不变式因此天然成立。
        """
        self._validate_content(fields)
        await self._validate_scope(fields)
        entry = KnowledgeEntry(
            **fields, is_enabled=_initially_enabled(fields), updated_by=employee_id
        )
        self._session.add(entry)
        await self._session.flush()
        self._add_audit(employee_id, "knowledge.create", entry.id)
        await self._session.commit()
        return entry

    async def create_for_rooms(
        self, employee_id: int, property_ids: list[int], **fields: Any
    ) -> list[KnowledgeEntry]:
        """同一事务为多个在用房间各建一条房间专属知识（Spec §3.2(2)）。

        先校验全部房间存在且在用，再逐条写入、逐条审计，最后只提交一次：任何一步
        失败整批回滚，不会出现「建了一半」。路由已去重并保证编号为正整数。
        """
        if fields.get("scope") != "property":
            raise KnowledgeFormError("全店通用 / 店外周边不能指定房间")
        if not property_ids:
            raise KnowledgeFormError("请至少选择一个房间")
        self._validate_content(fields)
        # 日期与范围约束与单条一致；房间存在性在下面按整批核对。
        try:
            validate_knowledge_scope(
                "property",
                property_ids[0],
                fields.get("valid_from"),
                fields.get("valid_until"),
            )
        except ValueError as error:
            raise KnowledgeFormError(str(error)) from error
        rooms = {
            room.id: room
            for room in (
                await self._session.scalars(
                    select(PropertyProfile).where(PropertyProfile.id.in_(property_ids))
                )
            ).all()
        }
        missing = [str(room_id) for room_id in property_ids if room_id not in rooms]
        if missing:
            raise KnowledgeFormError(
                f"所选房间不存在（编号 {'、'.join(missing)}），请刷新页面后重新选择"
            )
        if any(not rooms[room_id].is_active for room_id in property_ids):
            raise KnowledgeFormError("不能为已停用房间新增知识")
        is_enabled = _initially_enabled(fields)
        entries: list[KnowledgeEntry] = []
        try:
            for room_id in property_ids:
                entry = KnowledgeEntry(
                    **{**fields, "property_id": room_id},
                    is_enabled=is_enabled,
                    updated_by=employee_id,
                )
                self._session.add(entry)
                await self._session.flush()
                self._add_audit(employee_id, "knowledge.create", entry.id)
                entries.append(entry)
            await self._session.commit()
        except BaseException:
            await self._session.rollback()
            raise
        return entries

    async def update(self, entry_id: int, employee_id: int, **fields: Any) -> KnowledgeEntry:
        """更新允许编辑的知识字段，并记录条目级审计。"""
        entry = await self._require_entry(entry_id)
        # 只改了部分字段时，按改后的完整问答核对，未改的字段取库里的现值。
        self._validate_content(
            {name: fields.get(name, getattr(entry, name)) for name in _QA_FIELDS}
        )
        await self._validate_scope(
            {
                name: fields.get(name, getattr(entry, name))
                for name in ("scope", "property_id", "valid_from", "valid_until")
            }
        )
        # 编辑不改变启停状态：启用中的审核知识清空英文会让外国客人拿到空答案，拒绝。
        _validate_active_english(
            is_enabled=entry.is_enabled,
            scope=fields.get("scope", entry.scope),
            question_en=fields.get("question_en", entry.question_en),
            answer_en=fields.get("answer_en", entry.answer_en),
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
            "show_in_miniprogram",
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
        if enabled:
            # 启用等于把问答重新发给客人：占位符拦截上线前存下的历史条目也要先核实。
            # 停用不校验，含占位符的已启用条目必须能随时止损下线。
            self._validate_content({name: getattr(entry, name) for name in _QA_FIELDS})
            _validate_active_english(
                is_enabled=True,
                scope=entry.scope,
                question_en=entry.question_en,
                answer_en=entry.answer_en,
            )
        entry.is_enabled = enabled
        entry.updated_by = employee_id
        action = "knowledge.enable" if enabled else "knowledge.disable"
        self._add_audit(employee_id, action, entry.id)
        await self._session.commit()

    async def set_scope(
        self, entry_id: int, employee_id: int, target_scope: str
    ) -> KnowledgeEntry:
        """把范围待审核的条目快捷定界为全店通用或店外周边（Spec §3.2(3)）。

        只接受原范围为待审核的条目，防止误把房间知识一键变成全店通用；启停状态与
        小程序勾选保持原值。定界后条目可能立即参与回答，因此复用占位符与英文不变式。
        """
        entry = await self._require_entry(entry_id)
        if entry.scope != "unreviewed":
            raise KnowledgeFormError("仅范围待审核的条目可使用快捷定界")
        if target_scope not in {"global", "public"}:
            raise KnowledgeFormError("快捷定界只能设为全店通用或店外周边")
        await self._validate_scope(
            {
                "scope": target_scope,
                "property_id": entry.property_id,
                "valid_from": entry.valid_from,
                "valid_until": entry.valid_until,
            }
        )
        self._validate_content({name: getattr(entry, name) for name in _QA_FIELDS})
        _validate_active_english(
            is_enabled=entry.is_enabled,
            scope=target_scope,
            question_en=entry.question_en,
            answer_en=entry.answer_en,
        )
        entry.scope = target_scope
        entry.updated_by = employee_id
        self._add_audit(employee_id, "knowledge.set_scope", entry.id)
        await self._session.commit()
        return entry

    async def copy_snapshot(
        self, entry_id: int, property_ids: list[int], *, copy_images: bool
    ) -> CopySnapshot:
        """跨房复制第 1 步：只读校验源条目与目标房间，并记下源配图快照。

        只允许复制房间专属或全店通用知识；目标去重后须为 1–100 间在用房间且不含源
        房间本身；连同配图时文件总数不超过 36。这里不写库，后面的物理拷贝在事务外。
        """
        entry = await self._require_entry(entry_id)
        if entry.scope not in {"property", "global"}:
            raise KnowledgeFormError("只有房间专属或全店通用的知识可以复制到其他房间")
        targets = tuple(dict.fromkeys(property_ids))
        if not targets:
            raise KnowledgeFormError("请至少选择一个目标房间")
        if len(targets) > MAX_COPY_TARGET_ROOMS:
            raise KnowledgeFormError(
                f"一次最多复制到 {MAX_COPY_TARGET_ROOMS} 间房，请分批操作"
            )
        if entry.property_id is not None and entry.property_id in targets:
            raise KnowledgeFormError("不能复制到这条知识原本所在的房间")
        rooms = {
            room.id: room
            for room in (
                await self._session.scalars(
                    select(PropertyProfile).where(PropertyProfile.id.in_(targets))
                )
            ).all()
        }
        missing = [str(room_id) for room_id in targets if room_id not in rooms]
        if missing:
            raise KnowledgeFormError(
                f"目标房间不存在（编号 {'、'.join(missing)}），请刷新页面后重新选择"
            )
        if any(not rooms[room_id].is_active for room_id in targets):
            raise KnowledgeFormError("不能复制到已停用的房间")
        images: tuple[CopyImageSnapshot, ...] = ()
        if copy_images:
            images = tuple(
                CopyImageSnapshot(
                    id=image.id,
                    file_id=image.file_id,
                    sort_order=image.sort_order,
                    content_type=image.content_type,
                    size=image.size,
                )
                for image in await self.list_images(entry_id)
            )
            total = len(targets) * len(images)
            if total > MAX_COPY_IMAGE_FILES:
                raise KnowledgeFormError(
                    f"连同配图一次最多复制 {MAX_COPY_IMAGE_FILES} 个图片文件（本次需要 "
                    f"{total} 个），请减少目标房间分批复制，或取消「同时复制配图」"
                )
        return CopySnapshot(
            entry_id=entry_id,
            property_ids=targets,
            copy_images=copy_images,
            images=images,
        )

    async def prepare_copies(
        self,
        snapshot: CopySnapshot,
        copied_files: list[list[str]],
        employee_id: int,
    ) -> CopyResult:
        """跨房复制第 3 步：锁住源条目复核快照，写入停用草稿与配图记录，不提交。

        `copied_files[i]` 是第 i 个目标房按快照顺序拷出的新文件编号。源条目被删、
        范围变化或配图与快照不一致时抛 409，由调用方回滚并删除拷出的文件；提交与
        提交失败的处理也交给调用方（Spec §3.2(4) Step 3–4）。
        """
        changed = KnowledgeFormError(
            "源条目或配图已变化，请刷新后重试", status_code=status.HTTP_409_CONFLICT
        )
        entry = await self._session.scalar(
            select(KnowledgeEntry)
            .where(KnowledgeEntry.id == snapshot.entry_id)
            .with_for_update()
        )
        if (
            entry is None
            or entry.scope not in {"property", "global"}
            or (entry.property_id is not None and entry.property_id in snapshot.property_ids)
        ):
            raise changed
        if snapshot.copy_images:
            current = [
                (image.id, image.file_id, image.sort_order)
                for image in await self.list_images(snapshot.entry_id)
            ]
            expected = [
                (image.id, image.file_id, image.sort_order) for image in snapshot.images
            ]
            if current != expected:
                raise changed
        if len(copied_files) != len(snapshot.property_ids) or any(
            len(files) != len(snapshot.images) for files in copied_files
        ):
            raise ValueError("拷贝文件与目标房间、源配图数量不一致")
        # 同名提醒：去掉空白并忽略大小写后比较中文问题，只计数不阻止。
        source_question = _normalized_question(entry.question_zh)
        existing = (
            await self._session.execute(
                select(KnowledgeEntry.property_id, KnowledgeEntry.question_zh).where(
                    KnowledgeEntry.scope == "property",
                    KnowledgeEntry.property_id.in_(snapshot.property_ids),
                )
            )
        ).all()
        duplicate_rooms = len(
            {
                property_id
                for property_id, question in existing
                if _normalized_question(question) == source_question
            }
        )
        entry_ids: list[int] = []
        for room_id, files in zip(snapshot.property_ids, copied_files, strict=True):
            copy = KnowledgeEntry(
                category=entry.category,
                question_zh=entry.question_zh,
                answer_zh=entry.answer_zh,
                question_en=entry.question_en,
                answer_en=entry.answer_en,
                keywords=list(entry.keywords or []),
                scope="property",
                property_id=room_id,
                valid_from=entry.valid_from,
                valid_until=entry.valid_until,
                trigger_any=list(entry.trigger_any) if entry.trigger_any else None,
                trigger_exclude=(
                    list(entry.trigger_exclude) if entry.trigger_exclude else None
                ),
                # 复制结果一律停用草稿、不进小程序：管理员在目标房核对后再手动启用。
                is_enabled=False,
                show_in_miniprogram=False,
                updated_by=employee_id,
            )
            self._session.add(copy)
            await self._session.flush()
            for image, file_id in zip(snapshot.images, files, strict=True):
                self._session.add(
                    KnowledgeImage(
                        knowledge_entry_id=copy.id,
                        file_id=file_id,
                        content_type=image.content_type,
                        size=image.size,
                        sort_order=image.sort_order,
                        created_by=employee_id,
                    )
                )
            # 审计只记新旧条目编号与配图数量，不复制问答正文。
            self._session.add(
                AuditLog(
                    actor_employee_id=employee_id,
                    action="knowledge.copy",
                    target_type="knowledge_entry",
                    target_id=str(copy.id),
                    details={
                        "entry_id": copy.id,
                        "source_entry_id": snapshot.entry_id,
                        "image_count": len(files),
                    },
                )
            )
            entry_ids.append(copy.id)
        await self._session.flush()
        return CopyResult(entry_ids=entry_ids, duplicate_rooms=duplicate_rooms)

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
        self._validate_content(fields)
        await self._validate_scope(fields)
        # 候选转换直接建启用知识，英文生效不变式同样适用。
        _validate_active_english(
            is_enabled=True,
            scope=fields.get("scope", "unreviewed"),
            question_en=fields.get("question_en"),
            answer_en=fields.get("answer_en"),
        )
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
        """锁定条目后登记配图，并发上传也不会超过每条 MAX_KNOWLEDGE_IMAGES 张。"""
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

    # ---- 资料核实（修复 Spec 2026-10-08 fix2 §2.6(3)(5)） ----

    async def list_referrals(
        self, *, limit: int = 50, offset: int = 0,
    ) -> list[ReferralListItem]:
        """列表与「管家已回复 N 条」，N 按管家消息窗口实时计算。"""
        repository = SQLAlchemyKnowledgeReferralRepository(self._session)
        return [
            ReferralListItem(
                referral=referral,
                reply_count=len(await repository.servicer_messages(referral)),
            )
            for referral in await repository.list_recent(limit=limit, offset=offset)
        ]

    async def count_open_referrals(self) -> int:
        """待处理数量（D-G17：不催办，只在后台显示）。"""
        return await SQLAlchemyKnowledgeReferralRepository(self._session).count_open()

    async def referral_detail(self, referral_id: int) -> ReferralDetail:
        """详情：原问、房源、窗口内管家消息（管理员已鉴权，可看原文）与草稿。"""
        repository = SQLAlchemyKnowledgeReferralRepository(self._session)
        referral = await self._require_referral(referral_id)
        draft = referral.draft or {}
        used = set(draft.get("used_message_ids", []))
        frozen = set(referral.source_message_ids or [])
        start = window_start(referral.created_at)
        messages = [
            ReferralStaffMessage(
                message=message,
                sensitive=contains_sensitive_text(message.content or ""),
                near_time=message.sent_at.replace(microsecond=0) == start,
                frozen=message.id in frozen,
                used=message.id in used,
            )
            for message in await repository.servicer_messages(referral)
        ]
        return ReferralDetail(
            referral=referral,
            property=(
                await self._session.get(PropertyProfile, referral.property_id)
                if referral.property_id is not None
                else None
            ),
            messages=messages,
        )

    async def start_referral_draft(self, referral_id: int, employee_id: int) -> None:
        """一个事务内：锁定记录、代次加一、改为整理中、清空旧草稿、冻结来源并入队。

        冻结窗口内最新的 5 条非敏感管家消息编号（按发送时间、消息编号排序），只存编号不复制
        原文；之后新到的消息需要再次点击、进入新代次。窗口内没有可用消息时直接记为「没找到
        答案」，不调用模型。整理中不能强制重开（V8-R2）。
        """
        repository = SQLAlchemyKnowledgeReferralRepository(self._session)
        referral = await repository.get(referral_id, lock=True)
        if referral is None:
            raise LookupError(f"资料核实不存在: {referral_id}")
        if referral.status not in DRAFTABLE_STATUSES:
            raise KnowledgeFormError("这条资料核实当前不能整理草稿，请刷新页面查看最新状态")
        candidates = [
            message
            for message in await repository.servicer_messages(referral)
            if not contains_sensitive_text(message.content or "")
        ]
        frozen = [message.id for message in candidates[-DRAFT_SOURCE_LIMIT:]]
        generation = referral.generation
        try:
            # SQLite 不提供行锁；状态和代次必须在同一 UPDATE 中争取资格。
            result = cast(CursorResult[Any], await self._session.execute(
                update(KnowledgeReferral).where(
                    KnowledgeReferral.id == referral_id,
                    KnowledgeReferral.status.in_(DRAFTABLE_STATUSES),
                    KnowledgeReferral.generation == generation,
                ).values(
                    generation=generation + 1, draft=None, source_message_ids=frozen,
                    status=(KnowledgeReferralStatus.DRAFTING if frozen
                            else KnowledgeReferralStatus.NO_ANSWER),
                ).execution_options(synchronize_session=False)
            ))
            if result.rowcount != 1:
                raise KnowledgeFormError("草稿已变化，请刷新页面查看最新状态")
            if frozen:
                await SQLAlchemyJobRepository(self._session).enqueue(
                    KNOWLEDGE_REFERRAL_DRAFT_JOB_TYPE,
                    {"referral_id": referral_id, "generation": generation + 1},
                    dedupe_key=f"referral-draft:{referral_id}:{generation + 1}",
                )
            self._add_referral_audit(employee_id, "knowledge_referral.draft", referral_id)
            await self._session.commit()
        except BaseException:
            await self._session.rollback()
            raise

    async def convert_referral(
        self,
        referral_id: int,
        employee_id: int,
        *,
        generation: int,
        reviewed: bool,
        **fields: Any,
    ) -> KnowledgeEntry:
        """一个事务完成入库，仿照 `convert_candidate`，不调用会自行提交的 `create`。

        锁定记录，核对「状态 = 已整理、代次 = 页面携带的代次、草稿完整」；服务端校验
        「已核对」勾选；全部提交字段做凭据检测与占位符拦截，按范围校验后建启用知识；只结束
        实际采用的这条记录。中途失败全部回滚，不留下孤立的启用知识（V6-R6、V7-R4）。
        """
        repository = SQLAlchemyKnowledgeReferralRepository(self._session)
        referral = await repository.get(referral_id, lock=True)
        if referral is None:
            raise LookupError(f"资料核实不存在: {referral_id}")
        draft = referral.draft or {}
        if (
            referral.status is not KnowledgeReferralStatus.DRAFTED
            or referral.generation != generation
            or not all(str(draft.get(name, "")).strip() for name in _QA_FIELDS)
        ):
            raise KnowledgeFormError("草稿已变化或尚未整理完成，请刷新页面后重新核对")
        if not reviewed:
            raise KnowledgeFormError("请先核对中英答案与适用范围，并勾选「已核对」")
        if fields.get("scope") not in {"property", "global"}:
            raise KnowledgeFormError("请选择「仅此房间」或「全店」")
        texts = [str(fields.get(name) or "") for name in _REFERRAL_TEXT_FIELDS]
        texts.extend(str(item) for item in fields.get("keywords") or [])
        if any(contains_sensitive_text(text) for text in texts):
            raise KnowledgeFormError(
                "内容含密码、验证码、链接或客人隐私信息，请删除后再入库"
            )
        self._validate_content(fields)
        await self._validate_scope(fields)
        _validate_active_english(
            is_enabled=True,
            scope=str(fields.get("scope")),
            question_en=fields.get("question_en"),
            answer_en=fields.get("answer_en"),
        )
        try:
            # 先原子取得入库资格，清空或换代先提交时不能再创建孤立知识。
            result = cast(CursorResult[Any], await self._session.execute(
                update(KnowledgeReferral).where(
                    KnowledgeReferral.id == referral_id,
                    KnowledgeReferral.status == KnowledgeReferralStatus.DRAFTED,
                    KnowledgeReferral.generation == generation,
                ).values(status=KnowledgeReferralStatus.CONVERTED)
                .execution_options(synchronize_session=False)
            ))
            if result.rowcount != 1:
                raise KnowledgeFormError("草稿已变化或尚未整理完成，请刷新页面后重新核对")
            entry = KnowledgeEntry(**fields, is_enabled=True, updated_by=employee_id)
            self._session.add(entry)
            await self._session.flush()
            await self._session.execute(
                update(KnowledgeReferral).where(KnowledgeReferral.id == referral_id)
                .values(knowledge_entry_id=entry.id).execution_options(synchronize_session=False)
            )
            self._add_referral_audit(
                employee_id, "knowledge_referral.convert", referral_id, knowledge_entry_id=entry.id
            )
            await self._session.commit()
        except BaseException:
            await self._session.rollback()
            raise
        return entry

    async def close_referral(self, referral_id: int, employee_id: int) -> None:
        """关闭为「不入库」：同一问题与房源 30 天内不再转交（D-G16），在途草稿写回失配。"""
        repository = SQLAlchemyKnowledgeReferralRepository(self._session)
        referral = await repository.get(referral_id, lock=True)
        if referral is None:
            raise LookupError(f"资料核实不存在: {referral_id}")
        if referral.status not in OPEN_STATUSES:
            raise KnowledgeFormError("这条资料核实已结束，请刷新页面查看最新状态")
        try:
            result = cast(CursorResult[Any], await self._session.execute(
                update(KnowledgeReferral).where(
                    KnowledgeReferral.id == referral_id,
                    KnowledgeReferral.status.in_(OPEN_STATUSES),
                ).values(status=KnowledgeReferralStatus.CLOSED)
                .execution_options(synchronize_session=False)
            ))
            if result.rowcount != 1:
                raise KnowledgeFormError("这条资料核实已结束，请刷新页面查看最新状态")
            if referral.normalized_question:
                await repository.suppress(
                    referral.normalized_question, referral.property_id,
                    until=self._now() + _REFERRAL_SUPPRESSION, closed_by=employee_id,
                )
            self._add_referral_audit(employee_id, "knowledge_referral.close", referral_id)
            await self._session.commit()
        except BaseException:
            await self._session.rollback()
            raise

    async def _require_referral(self, referral_id: int) -> KnowledgeReferral:
        """读取资料核实记录，不存在时抛出稳定异常。"""
        referral = await SQLAlchemyKnowledgeReferralRepository(self._session).get(referral_id)
        if referral is None:
            raise LookupError(f"资料核实不存在: {referral_id}")
        return referral

    def _add_referral_audit(
        self,
        employee_id: int,
        action: str,
        referral_id: int,
        *,
        knowledge_entry_id: int | None = None,
    ) -> None:
        """审计只记编号与动作，不复制原问、管家消息或草稿。"""
        details = {"referral_id": referral_id}
        if knowledge_entry_id is not None:
            details["knowledge_entry_id"] = knowledge_entry_id
        self._session.add(
            AuditLog(
                actor_employee_id=employee_id,
                action=action,
                target_type="knowledge_referral",
                target_id=str(referral_id),
                details=details,
            )
        )

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
    show_in_miniprogram: str = "",
) -> dict[str, Any]:
    """清理表单并复用所有入口一致的范围、日期约束。

    小程序展示开关是复选框：未勾选时浏览器不提交该字段，按「不展示」保存。
    """
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
        "show_in_miniprogram": show_in_miniprogram == "1",
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


def _submitted_form(**raw: Any) -> SimpleNamespace:
    """把刚提交的原始字段整理成表单宏能读的对象，只用于失败后回填。

    这里不做校验也不写库：值来自本次已认证请求，仅原样还给同一位管理员核对修改，
    模板照常转义。房间编号能解析成整数时才回选，避免把任意文本当成下拉选项；
    新建页的多房间勾选（property_ids）同样只保留能解析的编号。
    """
    property_id = str(raw.get("property_id", "")).strip()
    property_ids = [
        int(value)
        for value in raw.get("property_ids") or []
        if _positive_id(str(value).strip()) is not None
    ]
    return SimpleNamespace(
        scope=raw.get("scope", "unreviewed"),
        property_id=_positive_id(property_id),
        property_ids=property_ids,
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
        show_in_miniprogram=raw.get("show_in_miniprogram", "") == "1",
    )


def _positive_id(value: str) -> int | None:
    """把表单里的房间编号解析为正整数；空串、非 ASCII 数字或过长时返回空值。"""
    if not value or len(value) > 18 or not (value.isascii() and value.isdigit()):
        return None
    number = int(value)
    return number if number > 0 else None


def _parse_room_ids(property_ids: list[str], property_id: str, scope: str) -> list[int]:
    """解析新建表单的房间参数（Spec §3.2(2) 校验矩阵），全部错误走可读 422。

    新表单提交多选 `property_ids`，旧调用方提交单值 `property_id`，两者只能用其一；
    重复编号去重保序；非房间范围带了房间直接拒绝，绝不静默丢弃。
    """
    single = property_id.strip()
    if property_ids and single:
        raise KnowledgeFormError("请只用一种方式选择房间，不要同时提交单选和多选房间")
    values = property_ids if property_ids else ([single] if single else [])
    room_ids: list[int] = []
    for value in values:
        parsed = _positive_id(value.strip())
        if parsed is None:
            raise KnowledgeFormError("房间编号无效，请刷新页面后重新选择")
        room_ids.append(parsed)
    room_ids = list(dict.fromkeys(room_ids))
    if scope != "property" and room_ids:
        raise KnowledgeFormError("全店通用 / 店外周边不能指定房间")
    if scope == "property" and not room_ids:
        raise KnowledgeFormError("选择「指定房间」时请至少勾选一个房间")
    if len(room_ids) > MAX_COPY_TARGET_ROOMS:
        raise KnowledgeFormError(f"一次最多为 {MAX_COPY_TARGET_ROOMS} 间房新增，请分批操作")
    return room_ids


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
    if query.get("section") in _SECTIONS:
        params["section"] = query["section"]
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
    section: Annotated[
        Literal["global", "rooms", "public", "unreviewed"] | None,
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
        section=section,
    )


def _resolve_section(section: str | None, room: str | None) -> tuple[str, int | None]:
    """按 Spec F1 的确定性规则解析分区与选中房间。

    显式 section 优先；否则数字 room 进入「按房间管理」并选中该房；room=shared 与
    其余情况落到默认的「全店通用」。room 只在「按房间管理」分区内生效。
    """
    room_id = _positive_id((room or "").strip())
    if section is None:
        section = "rooms" if room_id is not None else "global"
    return section, (room_id if section == "rooms" else None)


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
    section: str | None = None,
    status_code: int = status.HTTP_200_OK,
    error: str | None = None,
    failed_form: dict[str, Any] | None = None,
) -> Response:
    """渲染知识列表；failed_form 给出时把出错的候选表单展开并回填。

    知识分区按范围切成全店通用、按房间管理、店外周边（另有待审核范围的集中定界
    视图），只加载当前分区；管理员另有待审核候选分区。普通员工传入
    view=candidates 也只看到知识分区。
    """
    _, role = await require_employee_session(request)
    is_admin = role is EmployeeRole.ADMIN
    current = "candidates" if view == "candidates" and is_admin else "entries"
    current_section, selected_room = _resolve_section(section, room)
    service = _get_service(request)
    enabled_value = None if enabled is None else enabled == "enabled"
    entries = (
        await service.list_all(
            offset=(page - 1) * 50,
            limit=51,
            query=query,
            enabled=enabled_value,
            category=category,
            room=str(selected_room) if selected_room is not None else None,
            section=current_section,
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
    # 资料核实待处理数量只给管理员看（D-G17：不催办，只在后台显示）。
    referral_open_count = await service.count_open_referrals() if is_admin else 0
    # 页签命中数与列表使用同一组筛选条件（Spec F1 跨区搜索发现性）。
    counts = await service.section_counts(
        query=query, enabled=enabled_value, category=category
    )
    filters = {
        key: value
        for key, value in {
            "query": query or "",
            "enabled": enabled or "",
            "category": category or "",
        }.items()
        if value
    }

    def list_url(
        entry_page: int,
        faq_page: int,
        target_view: str = current,
        target_section: str = current_section,
        target_room: int | None = selected_room,
    ) -> str:
        """生成同时保留分区、两个页码与筛选条件的链接。

        默认分区（全店通用）且未选房间时不写 section，原有分页链接形式保持不变。
        """
        params: dict[str, Any] = {"page": entry_page, "candidate_page": faq_page}
        if target_view != "candidates":
            if target_section != "global" or target_room is not None:
                params["section"] = target_section
            if target_room is not None:
                params["room"] = target_room
        params.update(filters)
        if target_view == "candidates":
            params["view"] = "candidates"
        return "/employee/knowledge?" + urlencode(params)

    tabs = section_tabs(
        counts,
        lambda key: list_url(1, candidate_page, "entries", key, None),
        current_section if current == "entries" else None,
    )
    # 当前分区零命中而其他分区有命中时，空状态直接给出跨区链接。
    other_hits = [
        tab for tab in tabs if not tab["current"] and tab["count"]
    ] + (
        [
            {
                "key": "unreviewed",
                "label": _SECTION_LABELS["unreviewed"],
                "url": list_url(1, candidate_page, "entries", "unreviewed", None),
                "count": counts.get("unreviewed", 0),
            }
        ]
        if current_section != "unreviewed" and counts.get("unreviewed", 0)
        else []
    )
    properties = await service.list_properties()
    room_counts = (
        await service.room_entry_counts(query=query, enabled=enabled_value, category=category)
        if current == "entries" and current_section == "rooms"
        else {}
    )
    selected_property = next(
        (item for item in properties if item.id == selected_room), None
    )
    page_entries = entries[:50]
    current_view = list_url(page, candidate_page)
    new_params: dict[str, Any] = {
        "scope": {"rooms": "property", "public": "public"}.get(current_section, "global")
    }
    if selected_room is not None:
        new_params["property_id"] = selected_room
    new_params["return_to"] = current_view
    return templates.TemplateResponse(
        request=request,
        name="knowledge/index.html",
        status_code=status_code,
        context={
            # 当前视图原样带给每个写操作表单与详情链接：回来时分区、筛选、两个
            # 分页都还在。回跳时仍由 safe_return_path 复核，不接受站外目标。
            "current_view": current_view,
            "view": current,
            "section": current_section,
            "section_label": _SECTION_LABELS[current_section],
            "tabs": tabs,
            "section_counts": counts,
            "other_hits": other_hits,
            "unreviewed_url": list_url(1, candidate_page, "entries", "unreviewed", None),
            "rooms_url": list_url(1, candidate_page, "entries", "rooms", None),
            "room_url": {
                item.id: list_url(1, candidate_page, "entries", "rooms", item.id)
                for item in properties
            },
            "room_counts": room_counts,
            "selected_room": selected_room,
            "selected_property": selected_property,
            "new_url": "/employee/knowledge/new?" + urlencode(new_params),
            "entries_url": list_url(page, candidate_page, "entries"),
            "candidates_url": list_url(page, candidate_page, "candidates"),
            "properties": properties,
            "entries": page_entries,
            "image_counts": await service.image_counts_for_entries(
                [entry.id for entry in page_entries]
            ),
            "categories": (
                await service.list_categories(
                    section=current_section,
                    room=str(selected_room) if selected_room is not None else None,
                )
                if current == "entries"
                else await service.list_categories()
            ),
            "candidates": candidates[:50],
            "can_edit": is_admin,
            "csrf_token": csrf_token,
            "referral_open_count": referral_open_count,
            "page": page,
            "candidate_page": candidate_page,
            "query": query or "",
            "enabled_filter": enabled or "",
            "category_filter": category or "",
            "room_filter": str(selected_room) if selected_room is not None else "",
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


# 新建页必须注册在 `/{entry_id}` 详情路由之前：FastAPI 按注册顺序匹配，
# 否则 GET /new 会被详情路由当成编号解析并返回 422（知识库重构 Spec §3.1）。
@router.get("/new", response_class=HTMLResponse)
async def knowledge_new(
    request: Request,
    scope: Annotated[
        Literal["global", "property", "public"] | None,
        BeforeValidator(empty_query_to_none),
    ] = None,
    property_id: Annotated[str, Query(max_length=20)] = "",
    return_to: Annotated[str, Query(max_length=200)] = "",
) -> Response:
    """管理员的独立新建页，可由列表按当前分区与房间预设范围。"""
    await _require_admin(request)
    preset_room = _positive_id(property_id.strip())
    return await _render_new(
        request,
        return_to=return_to,
        values=_submitted_form(
            scope=scope or "global",
            property_ids=[str(preset_room)] if preset_room is not None else [],
        ),
    )


async def _render_new(
    request: Request,
    *,
    return_to: str = "",
    values: SimpleNamespace,
    status_code: int = status.HTTP_200_OK,
    error: str | None = None,
) -> Response:
    """渲染新建页；失败重绘时 values 是刚提交的内容，并换发新的一次性令牌。"""
    service = _get_service(request)
    return templates.TemplateResponse(
        request=request,
        name="knowledge/new.html",
        status_code=status_code,
        context={
            "values": values,
            "properties": await service.list_properties(),
            "categories": await service.list_categories(),
            "csrf_token": await _issue_csrf(request),
            "return_to": safe_return_path(return_to, fallback="/employee/knowledge"),
            "error": error,
            "page_title": "新增知识",
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
    is_admin = role is EmployeeRole.ADMIN
    service = _get_service(request)
    try:
        entry = await service.get_detail(entry_id)
    except LookupError as error_:
        raise HTTPException(status_code=404, detail="知识条目不存在") from error_
    properties = await service.list_properties()
    safe_return = safe_return_path(return_to, fallback="/employee/knowledge")
    # 详情页启停与快捷定界后需回到本页状态区，同时保留原列表 return_to，
    # 避免切换状态后「返回知识列表」退回默认全店页；若编码后超出表单 200 字符上限则回退不内嵌。
    status_return = f"/employee/knowledge/{entry_id}#entry-status"
    if safe_return != "/employee/knowledge":
        candidate_return = (
            f"/employee/knowledge/{entry_id}?"
            + urlencode({"return_to": safe_return})
            + "#entry-status"
        )
        if len(candidate_return) <= 200:
            status_return = candidate_return
    return templates.TemplateResponse(
        request=request,
        name="knowledge/detail.html",
        status_code=status_code,
        context={
            "entry": entry,
            "form_entry": submitted or entry,
            "images": await service.list_images(entry_id),
            "max_images": MAX_KNOWLEDGE_IMAGES,
            "properties": properties,
            # 跨房复制只列在用房间，并排除源房间本身（Spec F4.5）。
            "copy_targets": [
                item
                for item in properties
                if item.is_active and item.id != entry.property_id
            ],
            "can_copy": is_admin and entry.scope in {"property", "global"},
            "categories": await service.list_categories() if is_admin else [],
            "can_edit": is_admin,
            "csrf_token": (await _issue_csrf(request) if is_admin else ""),
            # 返回与写操作都回到来源列表；来源不在本站时退回知识列表。
            "return_to": safe_return,
            "status_return": status_return,
            "error": error,
            "notice": pop_page_notice(request) or None,
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
    # 英文可留空：新建缺英文时存为停用草稿（知识库重构 Spec §3.2(1)，用户裁决 P1=B）。
    question_en: str = Form("", max_length=500),
    answer_en: str = Form("", max_length=10_000),
    keywords: str = Form("", max_length=1000),
    scope: str = Form("unreviewed", max_length=16),
    property_id: str = Form("", max_length=20),
    # 多房间勾选按字符串列表接收、自行解析：声明成整数列表时坏值会变成框架的
    # 字段校验 JSON，页面无法原地重绘（Spec §3.2(2)）。
    property_ids: Annotated[list[str] | None, Form()] = None,
    valid_from: str = Form("", max_length=10),
    valid_until: str = Form("", max_length=10),
    trigger_any: str = Form("", max_length=1000),
    trigger_exclude: str = Form("", max_length=1000),
    show_in_miniprogram: str = Form("", max_length=1),
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
) -> Response:
    """管理员新增知识：单条，或「指定房间」时为多个在用房间各建一条（同一事务）。"""
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    raw = {
        "category": category, "question_zh": question_zh, "answer_zh": answer_zh,
        "question_en": question_en, "answer_en": answer_en, "keywords": keywords,
        "scope": scope, "property_id": property_id, "valid_from": valid_from,
        "valid_until": valid_until, "trigger_any": trigger_any,
        "trigger_exclude": trigger_exclude, "show_in_miniprogram": show_in_miniprogram,
    }
    service = _get_service(request)
    try:
        room_ids = _parse_room_ids(property_ids or [], property_id, scope)
        fields = _fields(
            **{**raw, "property_id": str(room_ids[0]) if room_ids else ""}
        )
        if scope == "property":
            entries = await service.create_for_rooms(employee_id, room_ids, **fields)
        else:
            entries = [await service.create(employee_id, **fields)]
    except KnowledgeFormError as error:
        _form_error_response(request, error)
        return await _render_new(
            request,
            return_to=return_to,
            values=_submitted_form(**raw, property_ids=property_ids or []),
            status_code=error.status_code,
            error=str(error),
        )
    count = len(entries)
    if entries and not entries[0].is_enabled:
        prefix = f"已为 {count} 间房各" if count > 1 else "已"
        set_page_notice(request, f"{prefix}保存为停用草稿，补全英文后才能启用")
    else:
        set_page_notice(
            request, f"已为 {count} 间房各新增一条知识" if count > 1 else "已新增知识"
        )
    # 回到新条目所在分区：多房统一回「按房间管理」，单房回该房间，其余回所属分区。
    if scope == "property":
        target = "/employee/knowledge?section=rooms" + (
            f"&room={room_ids[0]}" if count == 1 else ""
        )
    elif scope == "global":
        target = "/employee/knowledge"
    else:
        target = f"/employee/knowledge?section={scope}"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/candidates/{candidate_id}/convert")
async def convert_candidate(
    request: Request,
    candidate_id: int,
    category: str = Form(min_length=1, max_length=64),
    question_zh: str = Form(min_length=1, max_length=500),
    answer_zh: str = Form(min_length=1, max_length=10_000),
    # 英文不在表单层强制：是否必填由服务层英文生效不变式按启用与范围判断。
    question_en: str = Form("", max_length=500),
    answer_en: str = Form("", max_length=10_000),
    keywords: str = Form("", max_length=1000),
    scope: str = Form("unreviewed", max_length=16),
    property_id: str = Form("", max_length=20),
    valid_from: str = Form("", max_length=10),
    valid_until: str = Form("", max_length=10),
    trigger_any: str = Form("", max_length=1000),
    trigger_exclude: str = Form("", max_length=1000),
    show_in_miniprogram: str = Form("", max_length=1),
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
        "trigger_exclude": trigger_exclude, "show_in_miniprogram": show_in_miniprogram,
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
    # 英文不在表单层强制：是否必填由服务层英文生效不变式按启用与范围判断。
    question_en: str = Form("", max_length=500),
    answer_en: str = Form("", max_length=10_000),
    keywords: str = Form("", max_length=1000),
    scope: str = Form("unreviewed", max_length=16),
    property_id: str = Form("", max_length=20),
    valid_from: str = Form("", max_length=10),
    valid_until: str = Form("", max_length=10),
    trigger_any: str = Form("", max_length=1000),
    trigger_exclude: str = Form("", max_length=1000),
    show_in_miniprogram: str = Form("", max_length=1),
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
        "trigger_exclude": trigger_exclude, "show_in_miniprogram": show_in_miniprogram,
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


@router.post("/{entry_id}/copy")
async def copy_knowledge(
    request: Request,
    entry_id: int,
    property_ids: Annotated[list[str] | None, Form()] = None,
    copy_images: str = Form("", max_length=1),
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
) -> RedirectResponse:
    """管理员把房间或全店知识复制到其他房间，结果均为停用草稿（Spec F4.5）。

    必须注册在 `/{entry_id}/{action}` 启停路由之前。失败抛 KnowledgeFormError，
    由全局处理器写页面提示并回到详情页复制区（接口调用得到 422/409 JSON）。
    """
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    safe_return = safe_return_path(return_to, fallback="/employee/knowledge")
    back = f"/employee/knowledge/{entry_id}?" + urlencode({"return_to": safe_return})
    room_ids: list[int] = []
    try:
        for value in property_ids or []:
            parsed = _positive_id(value.strip())
            if parsed is None:
                raise KnowledgeFormError("房间编号无效，请刷新页面后重新选择")
            room_ids.append(parsed)
        result = await _get_service(request).copy_to_rooms(
            entry_id, employee_id, room_ids, copy_images=copy_images == "1"
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail="知识条目不存在") from error
    except KnowledgeFormError as error:
        error.return_to = f"{back}#entry-copy"
        raise
    message = (
        f"已复制到 {len(result.entry_ids)} 间房，均为停用草稿，请到各房间核对后再启用"
    )
    if result.duplicate_rooms:
        message += f"；其中 {result.duplicate_rooms} 间房已有同名问题，请注意合并"
    set_page_notice(request, message)
    return RedirectResponse(f"{back}#entry-copy", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{entry_id}/scope")
async def set_knowledge_scope(
    request: Request,
    entry_id: int,
    scope: str = Form(min_length=1, max_length=16),
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
) -> RedirectResponse:
    """管理员把范围待审核的条目快捷定界为全店通用或店外周边（Spec §3.2(3)）。

    必须注册在 `/{entry_id}/{action}` 启停路由之前。
    """
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    target = safe_return_path(return_to, fallback="/employee/knowledge?section=unreviewed")
    try:
        await _get_service(request).set_scope(entry_id, employee_id, scope)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="知识条目不存在") from error
    except KnowledgeFormError as error:
        error.return_to = target
        raise
    label = "全店通用" if scope == "global" else "店外周边"
    set_page_notice(request, f"已将知识 #{entry_id} 设为{label}")
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
    """管理员给条目加一张配图：限 JPG、PNG，单张 5MB，每条最多 MAX_KNOWLEDGE_IMAGES 张。"""
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
    """管理员启用或停用知识，成功后回到来源页面并提示结果。"""
    if action not in {"enable", "disable"}:
        raise HTTPException(status_code=404, detail="未知知识操作")
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    # 回到来源视图；只有来源是知识列表且没带锚点时才补上条目区锚点，
    # 详情页来源自带 #entry-status，不能被改写到不存在的列表锚点。
    target = safe_return_path(return_to, fallback="/employee/knowledge")
    if urlparse(target).path == "/employee/knowledge" and "#" not in target:
        target = f"{target}#knowledge-entries"
    enabled = action == "enable"
    try:
        await _get_service(request).set_enabled(entry_id, employee_id, enabled=enabled)
    except KnowledgeFormError as error:
        error.return_to = target
        raise
    set_page_notice(request, f"已{'启用' if enabled else '停用'}知识 #{entry_id}")
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
