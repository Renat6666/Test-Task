"""Celery application. Broker and result backend are both Redis."""

from celery import Celery

from core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "task_management",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["workers.quotes"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_max_tasks_per_child=200,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
)
