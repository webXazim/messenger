# Support Chat Project Upgrade 10 — Settings, automations, privacy, and security

## Added
- Owner notification preferences
- Daily summary scheduling controls
- Bounded Support security settings
- Attachment size and extension policies
- Verified-visitor controls for sensitive workflows
- Audit retention controls
- Agent Support session timeout controls
- Webhook failure thresholds
- Safe automation rules
- Automation execution history
- Idempotency keys
- Per-rule action limits
- Event-level rule limits
- Duplicate-action suppression
- Execution timeout
- Stop-processing control
- Dry-run API support
- No arbitrary code execution

## Automation triggers
- Conversation created
- Visitor message
- Agent message
- Status changed
- Assignment changed
- Tag added
- SLA due soon
- SLA breached
- Follow-up due

## Conditions
- Website
- Team
- Priority
- Status
- Tag
- Business-hours state
- Verified visitor
- Assigned or unassigned state

## Actions
- Request routing
- Assign team
- Set priority
- Add tag
- Request an approved response
- Notify owner
- Notify assigned agent
- Set follow-up
- Trigger an approved webhook

## Existing production foundations retained
This upgrade builds on the project’s existing privacy settings, temporary data exports, visitor deletion requests, webhook signing and rotation, webhook delivery logs, and Support audit events.

## Migration
`apps/support/migrations/0020_automations_security.py`
