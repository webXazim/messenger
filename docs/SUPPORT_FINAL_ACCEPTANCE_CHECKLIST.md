# Support Chat — Final acceptance checklist

## Existing product protection
- [ ] Existing Inbox structure is unchanged
- [ ] Existing message composer works
- [ ] Attachments and voice messages work
- [ ] Calls work when enabled
- [ ] Typing, presence, delivery, and read events work
- [ ] Messenger behavior and data are unchanged

## Tenant and permission safety
- [ ] Account A cannot access account B
- [ ] Agents only see authorized websites
- [ ] Owner-only settings reject agents
- [ ] Analytics/export permissions are enforced
- [ ] Automation rules cannot cross account boundaries

## Operational modules
- [ ] Websites and widget scripts work
- [ ] Teams and permissions work
- [ ] Routing respects availability and capacity
- [ ] Knowledge publishing and widget search work
- [ ] Lifecycle transitions and snoozing work
- [ ] SLA pause, breach, and escalation work
- [ ] Analytics backfill and hourly reconciliation work
- [ ] Automation idempotency and limits work
- [ ] Privacy exports/deletion workflows work
- [ ] Webhook signing and rotation work

## UX and accessibility
- [ ] Desktop, tablet, and mobile layouts pass
- [ ] Keyboard navigation works
- [ ] Focus is visible
- [ ] Modals trap and restore focus
- [ ] Loading, empty, error, and success states work
- [ ] Reduced-motion preference is respected
- [ ] RTL content does not break layouts

## Deployment
- [ ] Database backup completed
- [ ] Pre-deploy script passed
- [ ] Migrations applied
- [ ] Analytics backfill completed
- [ ] Workers and beat restarted
- [ ] Post-deploy smoke tests passed
- [ ] Feature flags enabled gradually
- [ ] Rollback package and prior image are available
