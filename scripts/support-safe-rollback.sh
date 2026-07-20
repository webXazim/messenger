#!/usr/bin/env bash
set -euo pipefail

cat <<'EOF'
SAFE SUPPORT CHAT ROLLBACK

1. Disable new feature flags first:
   SUPPORT_WEBSITES_V2_ENABLED=0
   SUPPORT_TEAMS_ENABLED=0
   SUPPORT_ROUTING_ENABLED=0
   SUPPORT_KNOWLEDGE_V2_ENABLED=0
   SUPPORT_LIFECYCLE_V2_ENABLED=0
   SUPPORT_SLA_V2_ENABLED=0
   SUPPORT_ANALYTICS_V2_ENABLED=0
   SUPPORT_AUTOMATIONS_ENABLED=0
   SUPPORT_SECURITY_V2_ENABLED=0

2. Restart application and worker services.

3. Keep additive database migrations in place unless a verified backup and
   explicit rollback window exist. The migrations are backward-compatible and
   disabling flags is safer than destructive schema reversal.

4. Restore the previous application image/package.

5. Run:
   python manage.py check_support_baseline
   python manage.py check_support_production_readiness

6. Confirm the existing Inbox and Messenger still operate.
EOF
