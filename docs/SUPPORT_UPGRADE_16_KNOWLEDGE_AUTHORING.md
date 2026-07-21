# Support Upgrade 16 — Production knowledge authoring

## Editor
The article composer now provides structured rich-text authoring for headings, paragraphs, emphasis, lists, quotations, secure links, undo, redo, and formatting cleanup.

CKEditor 5 was not bundled automatically because current self-hosted releases require an appropriate GPL or commercial license. The included editor keeps the project license-neutral. It can be replaced with a licensed CKEditor 5 build later without changing the article API or stored sanitized HTML format.

## Security
All article HTML is sanitized on the backend with Bleach before storage. The allowlist excludes scripts, event handlers, iframes, embedded HTML, arbitrary styles, and unsafe URL protocols. Read APIs sanitize historical content again before returning it.

## Permissions
The Support owner and agents granted `can_manage_knowledge` can create, edit, publish, archive, restore, categorize, and revise articles. Other agents retain read-only access to published material permitted for their websites.

## Business-ready publishing
The editor separates customer-facing content, publishing status, language, category, website availability, featured placement, related articles, and internal version notes.
