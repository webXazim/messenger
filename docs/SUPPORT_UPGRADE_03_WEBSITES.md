# Support Upgrade 03 — Websites and widget management

This upgrade replaces the previous expanding website cards with the approved production Websites workspace.

## Safety
- Inbox component and layout are unchanged.
- Messenger models, routes, selectors, and styles are unchanged.
- Existing widget script and site keys remain backward compatible.
- Site-key rotation remains an explicit destructive action.
- All website APIs remain account-scoped and owner-protected for writes.

## Added
- Searchable website table and selected website workspace.
- Setup, Appearance, Behavior, Access, and Usage tabs.
- Real installation code, allowed origins, widget preview, and call/upload controls.
- Bounded current-day website usage endpoint.
- Source checks and combined validation script.
