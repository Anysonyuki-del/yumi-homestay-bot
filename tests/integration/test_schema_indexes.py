import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from homestay_bot.domain.models import Base


@pytest.mark.asyncio
async def test_high_frequency_queries_have_composite_indexes() -> None:
    """高频消息、订单、任务和合并查询必须有覆盖排序条件的复合索引。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

        def read_indexes(sync_connection) -> dict[str, set[tuple[str, ...]]]:
            """读取各业务表实际生成的索引列顺序。"""
            result: dict[str, set[tuple[str, ...]]] = {}
            for table in (
                "messages",
                "stay_orders",
                "business_tasks",
                "customer_merge_suggestions",
                "jobs",
                "admin_credentials",
            ):
                result[table] = {
                    tuple(index["column_names"])
                    for index in inspect(sync_connection).get_indexes(table)
                }
            return result

        indexes = await connection.run_sync(read_indexes)

    assert ("conversation_id", "message_type", "id") in indexes["messages"]
    assert ("customer_id", "status", "check_in_date") in indexes["stay_orders"]
    assert ("status", "assigned_employee_id", "service_date") in indexes["business_tasks"]
    assert (
        "source_customer_id",
        "target_customer_id",
        "status",
    ) in indexes["customer_merge_suggestions"]
    assert ("status", "job_type", "available_at") in indexes["jobs"]
    assert ("username",) in indexes["admin_credentials"]

    await engine.dispose()
