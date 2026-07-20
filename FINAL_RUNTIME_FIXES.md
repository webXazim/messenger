# Final runtime fixes

Fixed source-level runtime failures found during validation.

## Backend model import

`apps/support/models.py` now imports `F`:

```python
from django.db.models import F, Q
```

This fixes the startup error raised by the knowledge related-article check constraint.

## Chat service compatibility dependencies

`apps/chat/services.py` now imports the transport-neutral realtime helpers using compatibility aliases:

```python
from apps.common.realtime import (
    make_realtime_event as build_realtime_event,
    make_realtime_safe as normalize_realtime_value,
)
```

It also defines the voice upload MIME aliases and extensions used by `is_voice_like_upload()`.

## Validation performed

- Python compile-all passed for `apps` and `config`
- Static undefined-name scan passed with no remaining undefined names
- No `replaceAll()` or other identified modern-only string/array calls remain in frontend source
- Existing Inbox source was not changed by these fixes
