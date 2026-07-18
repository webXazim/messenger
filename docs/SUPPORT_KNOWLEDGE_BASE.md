# Support Knowledge Base and Visitor Self-Service

Upgrade 10 adds a Support-only knowledge base without changing personal Messenger behavior.

## Product boundary

- Knowledge categories, articles, public search, article feedback, and agent answer insertion belong only to Support Chat.
- Personal Messenger conversations, contacts, calls, E2EE, search, media, unread counts, and realtime rooms are unchanged.
- Published articles are always evaluated against the active Support account and website before being returned.

## Article visibility

An article is either:

- available to every active website in its Support account; or
- limited to an explicit set of websites in the same Support account.

Draft and archived articles are never returned to public widget endpoints. Agents only receive published articles for websites they are assigned to. The owner can manage all article states.

## Visitor self-service

The widget can show featured or matching articles before the visitor starts a conversation. Visitors can:

- search article titles, summaries, and bodies;
- browse active categories;
- open a complete article;
- mark an article helpful or not helpful; and
- continue to the normal Support Chat form when an answer is insufficient.

Article feedback uses a random browser key stored by the widget. Only its SHA-256 hash is stored by the server. Repeated feedback from the same browser updates the existing response rather than increasing counts repeatedly.

## Agent workflow

The Support inbox reply composer includes a Knowledge selector. It lists only published articles available to the conversation's website. Choosing an article inserts its approved body into the reply draft, where the agent can review or edit it before sending.

## Endpoints

Authenticated Support team:

- `GET/PATCH /api/v1/support/knowledge/settings/`
- `GET/POST /api/v1/support/knowledge/categories/`
- `PATCH/DELETE /api/v1/support/knowledge/categories/<category-id>/`
- `GET/POST /api/v1/support/knowledge/articles/`
- `GET/PATCH/DELETE /api/v1/support/knowledge/articles/<article-id>/`

Public website widget:

- `GET /api/v1/support/widget/<site-key>/knowledge/`
- `GET /api/v1/support/widget/<site-key>/knowledge/articles/<article-id>/`
- `POST /api/v1/support/widget/<site-key>/knowledge/articles/<article-id>/feedback/`

Every public endpoint enforces the registered website origin.

## Deployment

Apply migration `support.0009_knowledge_base_self_service` after backing up PostgreSQL:

```bash
docker compose build
docker compose up -d
docker compose exec web python manage.py migrate
```

No new environment variables are required.
