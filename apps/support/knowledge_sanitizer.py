from __future__ import annotations

import re

import bleach

ALLOWED_TAGS = {
    "p", "br", "h2", "h3", "h4", "strong", "em", "u", "s",
    "ul", "ol", "li", "blockquote", "a", "code", "pre", "hr",
    "table", "thead", "tbody", "tr", "th", "td",
}
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "target", "rel"],
    "th": ["colspan", "rowspan"],
    "td": ["colspan", "rowspan"],
}
ALLOWED_PROTOCOLS = {"https", "mailto"}


def _secure_link(attrs, new=False):
    href_key = (None, "href")
    href = attrs.get(href_key, "").strip().lower()
    if href and not (href.startswith("https://") or href.startswith("mailto:")):
        return None
    return attrs

def sanitize_knowledge_html(value: str) -> str:
    cleaned = bleach.clean(
        value or "",
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )
    cleaned = bleach.linkify(
        cleaned,
        callbacks=[_secure_link, bleach.callbacks.nofollow, bleach.callbacks.target_blank],
        skip_tags={"pre", "code"},
    )
    return cleaned.strip()


def knowledge_plain_text(value: str) -> str:
    stripped = bleach.clean(value or "", tags=set(), attributes={}, strip=True)
    return re.sub(r"\s+", " ", stripped).strip()
