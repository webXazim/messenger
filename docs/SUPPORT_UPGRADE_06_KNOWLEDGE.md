# Support Upgrade 06 — Production Knowledge Base

Adds article SEO/language fields, immutable revisions, revision restore, archive restore, related articles, website scoping, widget-safe public search, and owner-only management APIs.

## Validation

```bash
python manage.py migrate
python manage.py test apps.support.tests_knowledge apps.support.tests_knowledge_production
cd frontend && npm run typecheck && npm run build
```

The Inbox layout and Messenger domain remain unchanged. Existing knowledge endpoints stay backward compatible.
