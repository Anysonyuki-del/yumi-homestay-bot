"""只读输出启用知识的范围复核清单；在服务器运行，输出不得提交仓库。"""
import argparse
import asyncio
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from homestay_bot.domain.models import KnowledgeEntry


async def scope_report(session: AsyncSession) -> dict[str, object]:
    """只读取管理员复核所需的编号、类别、提问及范围，不输出答案或客人资料。"""
    rows = (await session.execute(select(
        KnowledgeEntry.id, KnowledgeEntry.category, KnowledgeEntry.question_zh,
        KnowledgeEntry.question_en, KnowledgeEntry.scope,
    ).where(KnowledgeEntry.is_enabled.is_(True)).order_by(KnowledgeEntry.id))).mappings()
    entries = [dict(row) for row in rows]
    return {"enabled": entries, "public": [row for row in entries if row["scope"] == "public"]}


async def _run(url: str) -> None:
    """使用显式指定连接只读查询，确保连接释放。"""
    engine = create_async_engine(url)
    try:
        async with AsyncSession(engine) as session:
            print(json.dumps(await scope_report(session), ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


def main() -> None:
    """不使用隐式生产连接，调用方必须明确指定复核数据库。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    asyncio.run(_run(args.database_url))


if __name__ == "__main__":
    main()
