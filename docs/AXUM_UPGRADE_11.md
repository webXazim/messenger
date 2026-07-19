# Upgrade 11 — selective read models and safe caching

This upgrade avoids a global serializer rewrite. It optimizes only flat, read-heavy Support endpoints while preserving the existing API schema.

## Read projections

The tag, canned-reply, and knowledge-category list endpoints now use `values()` projections plus explicit read serializers. Create/update endpoints continue to use the model-backed serializers and their validation.

## Public knowledge cache

Public knowledge suggestion responses use a short-lived shared-cache entry keyed by website, normalized query, category, limit, and an account-scoped version. Changes to knowledge settings, categories, articles, or article/website assignments increment the version after the database transaction commits.

The cache does not cover:

- Messenger or Support messages
- permissions or grants
- presence
- read/delivery receipts
- calls
- article detail requests or view counters

Set `SUPPORT_PUBLIC_KB_CACHE_TTL_SECONDS=60` in production. Set it to a lower value for very frequently edited knowledge bases.
