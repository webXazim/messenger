import os

from celery import Celery
from celery.schedules import crontab
from django.conf import settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("messenger_api")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "aggregate-recent-support-analytics-hourly": {
        "task": "apps.support.tasks.aggregate_recent_support_analytics",
        "schedule": 3600.0,
        "args": [3],
    },
    "wake-snoozed-support-conversations-every-minute": {
        "task": "apps.support.tasks.wake_snoozed_support_conversations",
        "schedule": 60.0,
    },
    "delete-old-realtime-outbox-daily": {
        "task": "apps.common.tasks.delete_old_realtime_outbox_events",
        "schedule": crontab(minute=10, hour=4),
    },
    "monitor-realtime-pipeline-every-five-minutes": {
        "task": "apps.common.tasks.monitor_realtime_pipeline",
        "schedule": crontab(minute="*/5"),
    },
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
    "reassign-offline-support-conversations-every-minute": {
        "task": "apps.support.tasks.reassign_offline_support_conversations",
        "schedule": crontab(minute="*"),
    },
    "run-support-retention-daily": {
        "task": "apps.support.tasks.run_support_retention",
        "schedule": crontab(minute=35, hour=3),
    },
}

# The transactional outbox is transport-neutral. Keep a periodic recovery
# sweep enabled whenever durable realtime delivery is enabled, including the NATS-primary configuration.
if getattr(settings, "REALTIME_OUTBOX_ENABLED", False):
    recovery_interval = float(getattr(settings, "REALTIME_OUTBOX_RECOVERY_INTERVAL_SECONDS", 15))
    app.conf.beat_schedule["recover-realtime-outbox"] = {
        "task": "apps.common.tasks.publish_realtime_outbox_events",
        "schedule": recovery_interval,
    }
