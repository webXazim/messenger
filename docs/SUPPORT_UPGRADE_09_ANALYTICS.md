# Support Chat Project Upgrade 09 — Production analytics

## Architecture
Analytics pages no longer depend on unbounded raw-message scans. Reporting uses daily, hourly, website, team, agent, and tag aggregates scoped to one Support account.

## Models
- `SupportAnalyticsDailyMetric`
- `SupportAnalyticsHourlyMetric`
- `SupportAnalyticsTagMetric`
- `SupportAnalyticsExport`

## Reporting endpoints
- `GET /api/support/analytics/v2/overview/`
- `GET /api/support/analytics/v2/volume/`
- `GET /api/support/analytics/v2/websites/`
- `GET /api/support/analytics/v2/queue-health/`
- `GET /api/support/analytics/v2/tags/`
- `GET /api/support/analytics/v2/hours/`
- `GET /api/support/analytics/v2/agents/`
- `GET/POST /api/support/analytics/v2/exports/`
- `GET /api/support/analytics/v2/exports/<id>/download/`

## Aggregation
`apps.support.tasks.aggregate_recent_support_analytics` reconciles the current and previous two days every hour. Reconciliation is transactional and idempotent.

For historical backfill, call `aggregate_support_day(account, date)` from a management command or Django shell in bounded date ranges.

## Frontend
The production page includes:
- Previous-period metric comparisons
- Conversation time-series chart
- Website distribution
- Queue health
- Top tags
- Busiest hours
- Agent performance
- CSV export queue
- Loading, error, empty, and responsive states

## Migration
`apps/support/migrations/0019_analytics_aggregates.py`


## Initial historical backfill

After migration and before enabling the Analytics page:

```bash
python manage.py backfill_support_analytics --days 90
```

A specific account or date range can be bounded explicitly:

```bash
python manage.py backfill_support_analytics \
  --account-id <uuid> \
  --start 2026-05-01 \
  --end 2026-07-20
```
