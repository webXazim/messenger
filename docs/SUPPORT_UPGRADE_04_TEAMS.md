# Support Chat Upgrade 04 — Teams and agent permissions

This upgrade adds Support-only operational teams, team membership, website-team access, agent capacity, and granular permissions. It does not modify Messenger or the existing Support Inbox frame.

## Database
- `SupportTeam`
- `SupportTeamMembership`
- `SupportWebsiteTeam`
- `SupportAgentInvitationTeam`
- Granular permission fields on agents and invitations

## Safety
- Every team relation validates the same Support account.
- Owners remain the only account administrators.
- Deactivating an agent preserves history and returns active assigned conversations to the unassigned queue.
- Website access, team membership, and permissions update in one transaction.

## API
- `GET/POST /api/support/teams/`
- `PATCH/DELETE /api/support/teams/<team_id>/`
- Agent invitation/update payloads accept `team_ids` and granular permissions.
- Support bootstrap now includes `teams`.

## Frontend
The Agents page now provides a searchable agent table, team filters, workload/capacity, website access, granular permissions, team creation, and safe deactivation.

## Deployment
```bash
python manage.py migrate
python manage.py test apps.support.tests_teams apps.support.tests_baseline
cd frontend && npm ci && npm run typecheck && npm run build
```
