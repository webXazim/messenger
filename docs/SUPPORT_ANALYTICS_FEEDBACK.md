# Support analytics and customer feedback

Upgrade 09 adds Support-only service reporting and customer satisfaction (CSAT)
without changing personal Messenger conversations, unread state, notifications,
WebSockets, E2EE, calls, or media behavior.

## Access model

- The Support owner can view analytics across every website in the Support account.
- An agent must have `can_view_analytics` enabled.
- Agent analytics are limited to websites assigned to that agent.
- Agent workload reporting exposes only that agent's own row.
- Website filters are checked by the backend; frontend filters are not treated as
  authorization.
- Messenger data is never included in Support analytics.

## Analytics endpoint

Authenticated Support members use:

```text
GET /api/v1/support/analytics/overview/
```

Supported query parameters:

```text
start=YYYY-MM-DD
end=YYYY-MM-DD
website=<website UUID>
```

The maximum reporting period is 366 days. The response includes:

- conversation volume and resolution rate
- currently open, unassigned, and overdue conversations
- median first-response and resolution times
- response and resolution service-target breach rates
- inbound and outbound Support message counts
- CSAT response count, average score, and rating distribution
- daily created/resolved volume
- per-website performance
- scoped agent workload

All aggregates are calculated from Support-scoped records and permission-filtered
websites. For significantly larger installations, these live aggregates can later
be replaced by periodic snapshots without changing the API contract.

## Feedback settings

The Support owner manages feedback through:

```text
GET   /api/v1/support/feedback-settings/
PATCH /api/v1/support/feedback-settings/
```

Settings include:

- enable or disable CSAT
- automatically request feedback when a conversation is resolved
- allow or disallow comments
- survey expiry in days

Agents cannot change account-wide feedback settings.

## CSAT lifecycle

Each Support conversation can have one survey. A survey may be:

```text
pending
submitted
dismissed
expired
```

When automatic requests are enabled, the transition to `resolved` creates or
reactivates a pending survey. Closing an already-resolved conversation does not
create duplicate surveys.

Authorized Support members use:

```text
GET    /api/v1/support/conversations/<conversation UUID>/csat/
POST   /api/v1/support/conversations/<conversation UUID>/csat/
DELETE /api/v1/support/conversations/<conversation UUID>/csat/
```

Manual requests are allowed only for resolved or closed conversations. Submitted
surveys are immutable from the Support interface.

## Website visitor submission

The origin-bound visitor session uses:

```text
GET  /api/v1/support/widget/<site key>/sessions/<session UUID>/conversation/csat/
POST /api/v1/support/widget/<site key>/sessions/<session UUID>/conversation/csat/
```

A visitor can submit one rating from 1 through 5 and, when enabled, an optional
comment. The endpoint verifies the site key, visitor session, token, origin,
website, and conversation relationship on every request. A visitor cannot access
or answer another website's or another visitor's survey.

CSAT updates are delivered through the existing isolated Support realtime groups.
The widget retains REST polling as a fallback when WebSockets are unavailable.

## Responsive interface

The analytics and feedback interfaces reuse Messenger's existing application
shell, breakpoints, spacing tokens, focus behavior, and mobile safe-area handling.

- Desktop shows KPI cards, trend visualization, website reports, and workload.
- Tablet collapses wide report sections without horizontal page overflow.
- Mobile uses single-column KPI cards, compact trend rows, and card-based website
  reporting rather than forcing desktop tables into the viewport.
- Widget feedback controls remain touch-friendly and fit the existing widget panel.

## Deployment

Apply the committed migration:

```bash
docker compose exec web python manage.py migrate
```

Migration:

```text
support.0008_analytics_and_customer_feedback
```

No new environment variables are required. Support Chat and the public widget must
still be enabled through their existing feature flags before these surfaces are
available.
