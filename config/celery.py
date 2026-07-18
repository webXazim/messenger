import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("messenger_api")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

from celery.schedules import crontab

app.conf.beat_schedule = {
    "expire-stale-pending-uploads-hourly": {
        "task": "apps.chat.tasks.expire_stale_pending_uploads",
        "schedule": crontab(minute=0, hour="*"),
    },
    "deactivate-stale-devices-daily": {
        "task": "apps.chat.tasks.deactivate_stale_devices",
        "schedule": crontab(minute=15, hour=3),
    },
    "expire-stale-calls-every-minute": {
        "task": "apps.chat.tasks.expire_stale_calls",
        "schedule": crontab(minute="*"),
    },
    "expire-stale-call-participants-every-minute": {
        "task": "apps.chat.tasks.expire_stale_call_participants_task",
        "schedule": crontab(minute="*"),
    },
    "refresh-active-call-orchestration-every-minute": {
        "task": "apps.chat.tasks.refresh_active_call_orchestration_task",
        "schedule": crontab(minute="*"),
    },
    "scan-support-service-operations-every-minute": {
        "task": "apps.support.tasks.scan_support_service_operations",
        "schedule": crontab(minute="*"),
    },
    "retry-pending-support-webhooks-every-minute": {
        "task": "apps.support.tasks.retry_pending_support_webhooks",
        "schedule": crontab(minute="*"),
    },
    "maintain-support-calls-every-minute": {
        "task": "apps.support.tasks.maintain_support_calls",
        "schedule": crontab(minute="*"),
    },
    "run-support-retention-daily": {
        "task": "apps.support.tasks.run_support_retention",
        "schedule": crontab(minute=35, hour=3),
    },
}
