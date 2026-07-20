# Support Chat Project Upgrade 08 — Production SLA engine

## Added
- Account SLA controls for pause and escalation
- Website- and team-scoped SLA overrides
- Website precedence over team precedence over account defaults
- Business-minute pause/resume calculations
- Waiting-for-customer SLA pause
- Snoozed-conversation SLA pause
- Persistent pause duration and recalculation timestamps
- One-time breach escalation
- Escalation-team notifications
- Conversation SLA snapshot/recalculation API
- Owner SLA policy management UI

## New API routes
- `GET/POST /api/support/sla-policies/`
- `PATCH/DELETE /api/support/sla-policies/<policy_id>/`
- `GET/POST /api/support/conversations/<conversation_id>/sla/`

## Migration
- `apps/support/migrations/0018_production_sla.py`

## Deployment
Run `./scripts/check-support-upgrade-08.sh`, then apply migrations before restarting workers.
