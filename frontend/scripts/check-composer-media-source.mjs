import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const composer = read("src/components/MessageComposer.tsx");
const uploadQueue = read("src/components/composer/UploadQueue.tsx");
const uploadPolicy = read("src/components/composer/uploadPolicy.ts");
const drafts = read("src/lib/conversationDrafts.ts");
const conversation = read("src/pages/ConversationPage.tsx");
const chatApi = read("src/api/chat.ts");
const media = read("src/components/AuthenticatedMedia.tsx");
const mediaMessage = read("src/components/messages/MediaMessage.tsx");
const voice = read("src/components/VoiceNoteRecorder.tsx");
const auth = read("src/contexts/AuthContext.tsx");
const views = read("../apps/chat/api/views.py");
const services = read("../apps/chat/services.py");

for (const required of [
  "AbortController",
  "maxParallelUploads",
  "hasFailedUpload",
  "pendingClientTempIdRef",
  "validateComposerUpload",
]) {
  assert.ok(composer.includes(required), `Missing composer reliability behavior: ${required}`);
}
assert.ok(uploadQueue.includes("Uploading ${progress}%"), "Upload progress is not exposed to users.");
assert.ok(uploadQueue.includes("Cancel upload"), "Active uploads cannot be cancelled clearly.");
assert.ok(uploadPolicy.includes("max_upload_bytes"), "Composer does not use backend upload limits.");
assert.ok(drafts.includes("messenger:draft:v1"), "Drafts are not scoped to account and conversation.");
assert.ok(auth.includes("clearConversationDraftsForUser"), "Logout does not clear local private drafts.");
assert.ok(chatApi.includes("onUploadProgress"), "Upload progress is not connected to Axios.");
assert.ok(chatApi.includes("metadata_source_file"), "Encrypted uploads do not inspect the original media metadata safely.");
assert.ok(conversation.includes("include_thumbnail: false"), "Encrypted media may leak an unencrypted thumbnail.");
assert.ok(conversation.includes("previewBlob: encryptedPreview"), "Encrypted attachments do not carry a compact recipient-safe preview.");
assert.ok(conversation.includes("_optimistic_attachments"), "Optimistic messages discard attachment media metadata.");
assert.match(composer, /setPendingUploads\(\[\]\);[\s\S]*setText\(""\);[\s\S]*await onSend/, "The composer attachment preview is not cleared at the optimistic send handoff.");
assert.ok(composer.includes("_optimistic_attachments: optimisticAttachments"), "The composer does not hand its visual attachment metadata to the inline optimistic message.");
assert.ok(composer.includes("width: item.width") && composer.includes("height: item.height"), "Optimistic media does not preserve the final attachment aspect ratio.");
assert.ok(conversation.includes("await sendMutation.mutateAsync(nextPayload)"), "Composer send failures are still swallowed.");
assert.ok(voice.includes("clientTempId"), "Voice-note retry cannot reuse its optimistic message identity.");
assert.match(voice, /shouldDiscard \|\| chunks\.length === 0[\s\S]*setSending\(false\)/, "Empty or discarded voice recordings can leave the composer stuck in a sending state.");
assert.ok(media.includes("hasMediaAccessToken"), "Signed media URLs are not streamed directly.");
assert.ok(media.includes('preload="metadata"'), "Video/audio still preload the complete file unnecessarily.");
assert.ok(media.includes("decryptAttachmentPreview"), "Encrypted attachment previews are not decrypted independently of full media.");
assert.ok(mediaMessage.includes("useState(false)"), "Video playback is still requested before the user presses play.");
assert.ok(mediaMessage.includes("AuthenticatedAttachmentPreview"), "Video poster rendering does not use the encrypted preview payload.");
assert.ok(mediaMessage.includes("currentUserId={currentUserId} autoPlay"), "The poster play action does not start video playback directly.");
assert.ok(views.includes("StreamingHttpResponse"), "Media byte-range responses are missing.");
assert.ok(views.includes('status=206'), "Media byte-range requests do not return partial content.");
assert.ok(views.includes('status=416'), "Invalid media ranges are not rejected correctly.");
assert.ok(services.includes("short-lived bearer capabilities"), "Signed media URLs still require exposing the account JWT to media elements.");

console.log("Composer and media source regression checks passed.");
