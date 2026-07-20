# Support Chat — Final production handoff

## Completion status
All 11 planned upgrades are included.

1. Baseline and isolation protection
2. Support-only design system
3. Websites and widget management
4. Teams and agent permissions
5. Routing and assignment
6. Knowledge base
7. Conversation lifecycle and collaboration
8. SLA engine
9. Aggregated analytics
10. Automations, privacy, and security
11. Production QA and rollout controls

## Non-negotiable isolation
- Existing Inbox frame remains the visual source of truth.
- Messenger models, routes, conversations, and UI remain independent.
- Support visitors never become Messenger contacts automatically.
- All Support queries must remain scoped by Support account and website access.

## Production sequence

```bash
./scripts/support-predeploy.sh
./scripts/support-production-deploy.sh
./scripts/support-postdeploy-smoke.sh
```

## Recommended rollout order
1. `SUPPORT_WEBSITES_V2_ENABLED`
2. `SUPPORT_TEAMS_ENABLED`
3. `SUPPORT_ROUTING_ENABLED`
4. `SUPPORT_KNOWLEDGE_V2_ENABLED`
5. `SUPPORT_LIFECYCLE_V2_ENABLED`
6. `SUPPORT_SLA_V2_ENABLED`
7. `SUPPORT_ANALYTICS_V2_ENABLED`
8. `SUPPORT_SECURITY_V2_ENABLED`
9. `SUPPORT_AUTOMATIONS_ENABLED`

Enable one flag, run smoke tests, observe logs and metrics, then proceed.

## Required infrastructure
- PostgreSQL
- Redis/cache
- Django API/web services
- Celery workers
- Celery beat
- Realtime service
- HTTPS reverse proxy
- Persistent media or object storage
- Database backups
- Centralized logs

## Migration policy
Migrations 0015–0020 are additive. Apply them before enabling the corresponding feature flags. Do not destructively reverse production migrations merely to disable a feature; disable its flag and restore the previous application image.

## Initial analytics backfill

```bash
python manage.py backfill_support_analytics --days 90
```

## Readiness command

```bash
python manage.py check_support_production_readiness --strict
```

## Rollback

```bash
./scripts/support-safe-rollback.sh
```
