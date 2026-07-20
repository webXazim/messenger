# Support Chat Upgrade 01 — Baseline Protection

This upgrade intentionally makes no database migration and no Inbox visual change.

## Added

- `apps.support.tests_baseline`: model and selector regression tests for Support/Messenger isolation.
- `check_support_baseline`: data-integrity command safe to run on the VPS.
- Frontend source contract preserving the current Support Inbox frame.
- One command runner: `scripts/check-support-upgrade-baseline.sh`.

## Production checks

Run before and after every Support upgrade:

```bash
python manage.py check_support_baseline
python manage.py check_support_baseline --json
```

Inside Docker:

```bash
docker compose exec backend python manage.py check_support_baseline
docker compose exec backend python manage.py test apps.support.tests_baseline

docker compose exec frontend npm run check:support-baseline
```

## Frozen boundaries

- Support visitors remain separate from Messenger users.
- Support conversations do not use Messenger participants/read state.
- Agent and website access cannot cross Support accounts.
- The existing Inbox frame, composer, message rendering, calls, and realtime contracts are unchanged.
- No Messenger model, API, route, or stylesheet was modified.
