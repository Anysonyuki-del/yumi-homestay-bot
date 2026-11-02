"""小程序住客自助的公开只读内容：入住指南与房间介绍（小程序住客自助 Spec F1、F3）。

这里的结果会返回给任何打开小程序的人，因此只做白名单式输出：每个函数只取
Spec 列出的字段，不把 ORM 对象或运营字段交给路由。
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from homestay_bot.domain.models import (
    KnowledgeEntry,
    PropertyProfile,
    RoomShowcase,
    RoomShowcasePhoto,
)
from homestay_bot.services.private_file_storage import PrivateFileStorage, StoredPrivateFile

# 入住指南最多返回的条目数；超出部分截断不报错，避免公开接口返回无界列表。
GUIDE_MAX_ITEMS = 200
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _today_in_shanghai() -> date:
    """有效期按民宿所在时区的当天判断，与知识检索的日期口径一致。"""
    return datetime.now(_SHANGHAI).date()


@dataclass(frozen=True)
class GuideItem:
    """入住指南里的一条问答，只含中文问题与答案。"""

    question: str
    answer: str


@dataclass(frozen=True)
class GuideSection:
    """同一分类下的问答，分类顺序取该分类最早条目的编号。"""

    category: str
    items: tuple[GuideItem, ...]


@dataclass(frozen=True)
class RoomCard:
    """房间列表的一项；照片只给编号，由路由拼成公开地址。"""

    id: int
    name: str
    bed_type: str | None
    max_guests: int | None
    area_sqm: int | None
    cover_photo_id: int | None


@dataclass(frozen=True)
class RoomDetail:
    """房间详情：列表字段加简介、设施和全部照片编号。"""

    card: RoomCard
    intro: str | None
    facilities: tuple[str, ...]
    photo_ids: tuple[int, ...]


def _visible_rooms() -> Select[tuple[RoomShowcase, str]]:
    """可见房间的基础查询：勾选展示且房间仍在用，两者缺一就当作不存在。"""
    return (
        select(RoomShowcase, PropertyProfile.title)
        .join(PropertyProfile, PropertyProfile.id == RoomShowcase.property_id)
        .where(RoomShowcase.is_visible.is_(True), PropertyProfile.is_active.is_(True))
    )


class MiniProgramContentService:
    """为每次公开内容请求创建独立只读事务。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        storage: PrivateFileStorage,
        today: Callable[[], date] = _today_in_shanghai,
    ) -> None:
        """保存数据库会话工厂与私有文件存储；日期来源可注入，便于测试有效期边界。"""
        self._factory = factory
        self._storage = storage
        self._today = today

    async def guide(self) -> list[GuideSection]:
        """返回勾选展示、本店通用、已启用且今天在有效期内的知识，按分类分组。

        只取「本店通用」：指定房间的知识可能含房间专属信息，店外公开知识不是本店须知，
        待审核的知识不能为事实背书。
        """
        today = self._today()
        statement = (
            select(KnowledgeEntry.category, KnowledgeEntry.question_zh, KnowledgeEntry.answer_zh)
            .where(
                KnowledgeEntry.is_enabled.is_(True),
                KnowledgeEntry.scope == "global",
                KnowledgeEntry.show_in_miniprogram.is_(True),
                or_(KnowledgeEntry.valid_from.is_(None), KnowledgeEntry.valid_from <= today),
                or_(KnowledgeEntry.valid_until.is_(None), KnowledgeEntry.valid_until >= today),
            )
            .order_by(KnowledgeEntry.id)
            .limit(GUIDE_MAX_ITEMS)
        )
        async with self._factory() as session:
            rows = (await session.execute(statement)).all()
        # 按编号顺序遍历，字典保持插入顺序，分类自然按其最早条目排序。
        grouped: dict[str, list[GuideItem]] = {}
        for category, question, answer in rows:
            grouped.setdefault(category, []).append(GuideItem(question=question, answer=answer))
        return [
            GuideSection(category=category, items=tuple(items))
            for category, items in grouped.items()
        ]

    async def rooms(self) -> list[RoomCard]:
        """可见房间列表：排序号升序、未填排最后，再按房间编号。"""
        statement = _visible_rooms().order_by(
            RoomShowcase.sort_order.is_(None),
            RoomShowcase.sort_order,
            RoomShowcase.property_id,
        )
        async with self._factory() as session:
            rows = (await session.execute(statement)).all()
            covers = await self._photo_ids(session, [showcase.property_id for showcase, _ in rows])
        return [
            self._card(showcase, title, covers.get(showcase.property_id, ()))
            for showcase, title in rows
        ]

    async def room(self, property_id: int) -> RoomDetail | None:
        """可见房间的详情；不可见与不存在同样返回 None。"""
        async with self._factory() as session:
            row = (
                await session.execute(
                    _visible_rooms().where(RoomShowcase.property_id == property_id)
                )
            ).one_or_none()
            if row is None:
                return None
            showcase, title = row
            photo_ids = (await self._photo_ids(session, [property_id])).get(property_id, ())
        return RoomDetail(
            card=self._card(showcase, title, photo_ids),
            intro=showcase.intro,
            facilities=tuple(showcase.facilities or ()),
            photo_ids=photo_ids,
        )

    async def photo(self, property_id: int, photo_id: int) -> StoredPrivateFile | None:
        """只按「房间可见 → 照片属于该房间」的关系读取，绝不按外部传入的文件编号读文件。"""
        statement = (
            select(RoomShowcasePhoto.file_id)
            .join(RoomShowcase, RoomShowcase.property_id == RoomShowcasePhoto.property_id)
            .join(PropertyProfile, PropertyProfile.id == RoomShowcasePhoto.property_id)
            .where(
                RoomShowcasePhoto.id == photo_id,
                RoomShowcasePhoto.property_id == property_id,
                RoomShowcase.is_visible.is_(True),
                PropertyProfile.is_active.is_(True),
            )
        )
        async with self._factory() as session:
            file_id = await session.scalar(statement)
        if file_id is None:
            return None
        try:
            return self._storage.open_for_read(file_id)
        except LookupError:
            # 记录还在但文件已被清理（如删除后的清理任务先跑完），按不存在处理。
            return None

    @staticmethod
    async def _photo_ids(
        session: AsyncSession, property_ids: list[int]
    ) -> dict[int, tuple[int, ...]]:
        """按房间取照片编号，组内按排序号、编号排序。"""
        if not property_ids:
            return {}
        rows = (
            await session.execute(
                select(RoomShowcasePhoto.property_id, RoomShowcasePhoto.id)
                .where(RoomShowcasePhoto.property_id.in_(property_ids))
                .order_by(RoomShowcasePhoto.sort_order, RoomShowcasePhoto.id)
            )
        ).all()
        grouped: dict[int, list[int]] = {}
        for property_id, photo_id in rows:
            grouped.setdefault(property_id, []).append(photo_id)
        return {key: tuple(value) for key, value in grouped.items()}

    @staticmethod
    def _card(showcase: RoomShowcase, title: str, photo_ids: tuple[int, ...]) -> RoomCard:
        """组装列表项；展示名称留空时用房间标题（用户 2026-11-03 确认）。"""
        return RoomCard(
            id=showcase.property_id,
            name=showcase.display_name or title,
            bed_type=showcase.bed_type,
            max_guests=showcase.max_guests,
            area_sqm=showcase.area_sqm,
            cover_photo_id=photo_ids[0] if photo_ids else None,
        )
