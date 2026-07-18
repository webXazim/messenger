# Upgrade 10 — Support Knowledge Base

This release adds Support knowledge categories, published and draft articles, website-scoped visibility, visitor widget self-service, article feedback, and agent reply insertion.

## Deploy

1. Back up PostgreSQL and the current deployed source.
2. Build and restart the existing Messenger deployment.
3. Run `python manage.py migrate` inside the web container.
4. Open **Support Chat → Knowledge** and configure the first published articles.
5. Confirm each website's allowed origins before enabling widget self-service.

Migration: `support.0009_knowledge_base_self_service`

No new environment variables are required.
