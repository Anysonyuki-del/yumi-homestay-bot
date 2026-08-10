"""补充运行配置候选生命周期、基线和状态约束。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_runtime_config_lifecycle"
down_revision: str | None = "0016_admin_dashboard_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加候选测试元数据，并初始化受约束的单例激活状态。"""
    with op.batch_alter_table("runtime_config_versions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(length=32),
                server_default="candidate",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "test_results",
                sa.JSON(),
                server_default=sa.text("'{}'"),
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("failure_code", sa.String(length=64)))
        batch_op.add_column(sa.Column("based_on_version_id", sa.Integer()))
        batch_op.add_column(
            sa.Column(
                "based_on_revision",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("activated_at", sa.DateTime(timezone=True)))
        batch_op.create_foreign_key(
            "fk_runtime_config_versions_based_on",
            "runtime_config_versions",
            ["based_on_version_id"],
            ["id"],
        )

    with op.batch_alter_table("runtime_config_state") as batch_op:
        batch_op.create_check_constraint(
            "ck_runtime_config_state_revision_nonnegative",
            "revision >= 0",
        )
        batch_op.create_check_constraint(
            "ck_runtime_config_state_distinct_pointers",
            "active_version_id IS NULL OR previous_version_id IS NULL "
            "OR active_version_id <> previous_version_id",
        )
    op.execute(
        sa.text(
            "INSERT INTO runtime_config_state (id, revision) VALUES (1, 0) "
            "ON CONFLICT (id) DO NOTHING"
        )
    )


def downgrade() -> None:
    """移除生命周期字段和新增约束，保留批次一基础版本表。"""
    with op.batch_alter_table("runtime_config_state") as batch_op:
        batch_op.drop_constraint(
            "ck_runtime_config_state_distinct_pointers",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_runtime_config_state_revision_nonnegative",
            type_="check",
        )
    with op.batch_alter_table("runtime_config_versions") as batch_op:
        batch_op.drop_constraint(
            "fk_runtime_config_versions_based_on",
            type_="foreignkey",
        )
        batch_op.drop_column("activated_at")
        batch_op.drop_column("based_on_revision")
        batch_op.drop_column("based_on_version_id")
        batch_op.drop_column("failure_code")
        batch_op.drop_column("test_results")
        batch_op.drop_column("status")
