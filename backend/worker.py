from celery import Celery
from backend.config import settings

# Celery handles background task processing
# FastAPI queues jobs here instantly, worker processes them separately
# Flow: webhook received → job queued in Redis → worker picks it up → runs AI review
celery_app = Celery(
    "code_reviewer",
    broker=settings.redis_url,    # Redis receives tasks from FastAPI
    backend=settings.redis_url,   # Redis stores task results
    include=["backend.tasks"]     # where task functions are defined
)

celery_app.conf.update(
    task_serializer="json",
    result_expires=3600,           # clear results after 1 hour
    worker_prefetch_multiplier=1,  # one task at a time per worker
    task_acks_late=True,           # retry if worker crashes mid-task
    task_reject_on_worker_lost=True,
)