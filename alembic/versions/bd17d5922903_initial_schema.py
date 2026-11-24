"""initial schema

Revision ID: bd17d5922903
Revises:
Create Date: 2026-11-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "bd17d5922903"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

task_status = postgresql.ENUM(
    "backlog",
    "in_progress",
    "review",
    "done",
    "cancelled",
    name="task_status",
    create_type=False,
)
task_priority = postgresql.ENUM(
    "low",
    "medium",
    "high",
    name="task_priority",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    task_status.create(bind, checkfirst=True)
    task_priority.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("username"),
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", task_status, server_default="backlog", nullable=False),
        sa.Column("priority", task_priority, server_default="medium", nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("creator_id", sa.Integer(), nullable=False),
        sa.Column("assignee_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_assignee_status", "tasks", ["assignee_id", "status"])
    op.create_index("ix_tasks_created_at", "tasks", ["created_at"])
    op.create_index("ix_tasks_priority_deadline", "tasks", ["priority", "deadline"])
    op.create_index("ix_tasks_status", "tasks", ["status"])

    op.create_table(
        "comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_comments_task_created", "comments", ["task_id", "created_at"])

    op.create_table(
        "task_status_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("from_status", task_status, nullable=True),
        sa.Column("to_status", task_status, nullable=False),
        sa.Column("changed_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["changed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_task_history_task_created", "task_status_history", ["task_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_task_history_task_created", table_name="task_status_history")
    op.drop_table("task_status_history")
    op.drop_index("ix_comments_task_created", table_name="comments")
    op.drop_table("comments")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_priority_deadline", table_name="tasks")
    op.drop_index("ix_tasks_created_at", table_name="tasks")
    op.drop_index("ix_tasks_assignee_status", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("users")

    bind = op.get_bind()
    task_priority.drop(bind, checkfirst=True)
    task_status.drop(bind, checkfirst=True)
