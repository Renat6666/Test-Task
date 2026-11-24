
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

class Base(DeclarativeBase):
    pass


class TaskStatus(str, Enum):
    BACKLOG = "backlog"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    username: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False
    )

    password: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    created_tasks: Mapped[list["Task"]] = relationship(
        back_populates="creator",
        foreign_keys=lambda: [Task.creator_id]
    )

    assigned_tasks: Mapped[list["Task"]] = relationship(
        back_populates="assignee",
        foreign_keys=lambda: [Task.assignee_id]
    )

    comments: Mapped[list["Comment"]] = relationship(
        back_populates="author"
    )

    status_changes: Mapped[list["TaskStatusHistory"]] = relationship(
        back_populates="changed_by"
    )


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(
            TaskStatus,
            name="task_status",
            values_callable=lambda enum: [
                item.value for item in enum
            ]
        ),
        default=TaskStatus.BACKLOG,
        server_default=TaskStatus.BACKLOG.value,
        nullable=False
    )

    priority: Mapped[TaskPriority] = mapped_column(
        SAEnum(
            TaskPriority,
            name="task_priority",
            values_callable=lambda enum: [
                item.value for item in enum
            ]
        ),
        default=TaskPriority.MEDIUM,
        server_default=TaskPriority.MEDIUM.value,
        nullable=False
    )

    deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    creator_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False
    )

    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )


    creator: Mapped["User"] = relationship(
        back_populates="created_tasks",
        foreign_keys=lambda: [Task.creator_id]
    )

    assignee: Mapped["User | None"] = relationship(
        back_populates="assigned_tasks",
        foreign_keys=lambda: [Task.assignee_id]
    )

    comments: Mapped[list["Comment"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True
    )

    status_history: Mapped[list["TaskStatusHistory"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True
    )

    __table_args__ = (
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_priority_deadline", "priority", "deadline"),
        Index("ix_tasks_assignee_status", "assignee_id", "status"),
        Index("ix_tasks_created_at", "created_at"),
    )


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False
    )

    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False
    )

    is_system: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    task: Mapped["Task"] = relationship(
        back_populates="comments"
    )

    author: Mapped["User"] = relationship(
        back_populates="comments"
    )

    __table_args__ = (
        Index("ix_comments_task_created", "task_id", "created_at"),
    )


class TaskStatusHistory(Base):
    __tablename__ = "task_status_history"

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False
    )

    from_status: Mapped[TaskStatus | None] = mapped_column(
        SAEnum(
            TaskStatus,
            name="task_status",
            create_type=False,
            values_callable=lambda enum: [
                item.value for item in enum
            ]
        ),
        nullable=True
    )

    to_status: Mapped[TaskStatus] = mapped_column(
        SAEnum(
            TaskStatus,
            name="task_status",
            create_type=False,
            values_callable=lambda enum: [
                item.value for item in enum
            ]
        ),
        nullable=False
    )

    changed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )


    task: Mapped["Task"] = relationship(
        back_populates="status_history"
    )

    changed_by: Mapped["User | None"] = relationship(
        back_populates="status_changes"
    )

    __table_args__ = (
        Index("ix_task_history_task_created", "task_id", "created_at"),
    )
