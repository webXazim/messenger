# Support Chat private media

Support Chat reuses Messenger's proven pending-upload, scanning, metadata,
thumbnail, private-download, and attachment-finalization infrastructure. Product
ownership remains explicit so Support files cannot be attached to personal
Messenger messages and Messenger files cannot enter Support conversations.

## Scope and storage

Every pending upload has a `purpose` of `messenger` or `support`. Support uploads
also have a `SupportPendingUpload` ownership record containing the hidden Support
account, website, conversation, and either the authenticated team member or the
signed visitor session.

Support object keys are namespaced as:

```text
support/<support-account-id>/<website-id>/pending/<year>/<month>/...
support/<support-account-id>/<website-id>/<conversation-id>/attachments/<year>/<month>/...
support/<support-account-id>/<website-id>/<conversation-id>/thumbnails/<year>/<month>/...
```

Existing Messenger storage keys remain under `chat/`.

## Authorization

Team media routes require an authenticated owner or agent with current access to
the conversation's website. Widget media routes require the matching site key,
origin-bound visitor session, bearer token, website, visitor, and conversation.
Media is streamed through protected endpoints; bucket object URLs are not exposed.

## Upload and send lifecycle

1. The client uploads a file to the selected Support conversation.
2. The backend normalizes type and name, stores it privately, creates Support
   ownership metadata, and dispatches or performs the existing malware scan.
3. Sending a message locks and revalidates every upload, checks ownership,
   website, conversation, scan state, attachment count, and combined size.
4. Clean uploads are copied into immutable `MessageAttachment` records and the
   pending records become attached.
5. Images, videos, audio, voice notes, PDFs, and documents are returned through
   Support-only preview and download routes.

A voice note must contain exactly one audio upload and is marked separately from
a normal audio attachment.

## Configuration

```env
SUPPORT_MAX_ATTACHMENTS_PER_MESSAGE=8
# 0 uses MAX_UPLOAD_BYTES multiplied by the attachment count limit.
SUPPORT_MAX_MESSAGE_UPLOAD_BYTES=0
SUPPORT_UPLOAD_CREATE_RATE=20/min
SUPPORT_WIDGET_UPLOAD_RATE=12/min
```

`SUPPORT_WIDGET_ENABLED` and each website's `allow_attachments` setting must both
permit public visitor uploads.

## Responsive behavior

The authenticated Support inbox uses Messenger's existing breakpoints and voice
recorder. Desktop, tablet, and mobile layouts support upload progress, removable
attachment chips, private image/video/audio previews, document downloads, and
voice recording. The public widget uses compact equivalents and retains realtime
with polling fallback.
