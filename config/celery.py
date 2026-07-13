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
}
