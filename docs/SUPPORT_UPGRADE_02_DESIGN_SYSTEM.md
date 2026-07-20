# Support Upgrade 02 — Production Design System

This upgrade adds a Support-only React/CSS design system. It does not change the current Inbox frame, Messenger components, backend APIs, or database schema.

## Added

- `frontend/src/support/styles/design-system.css`
- `frontend/src/support/components/*`
- `frontend/src/support/types/ui.ts`
- `frontend/src/support/utils/classNames.ts`
- Source regression check: `npm run test:support-design-system`

## Component foundation

- Page header and surfaces
- Buttons and loading state
- Text fields, textareas, and selects
- Accessible toggles
- Status badges
- Tabs
- Generic data table
- Loading, empty, and error states
- Accessible modal/confirmation foundation

## Isolation rule

All new selectors use the `sc-` prefix and all tokens use `--sc-`. The current Inbox continues to use its existing classes and is not imported into this component system during Upgrade 02.

## Validation

```bash
cd frontend
npm ci
npm run check:support-baseline
npm run check:support-design-system
npm run build
```
