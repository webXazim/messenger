import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const conversation = read("src/pages/ConversationPage.tsx");
const timeline = read("src/lib/messageTimeline.ts");
const timelineHook = read("src/hooks/useConversationTimeline.ts");
const composer = read("src/components/MessageComposer.tsx");
const meta = read("src/components/messages/MessageMeta.tsx");
const actions = read("src/components/messages/MessageActions.tsx");
const confirm = read("src/components/ConfirmDialog.tsx");
const forward = read("src/components/ForwardMessageModal.tsx");
const chatApi = read("src/api/chat.ts");
const views = read("../apps/chat/api/views.py");
const urls = read("../apps/chat/api/urls.py");

for (const required of [
  "upsertMessagePages",
  "mapMessagePages",
  "markMessageDeletedPages",
  "mergeMessageContextPages",
  "removeMessagePages",
  "TimelineMessagePage",
]) {
  assert.ok(timeline.includes(required), `Missing timeline cache invariant: ${required}`);
}

for (const required of [
  "timelineAtLatest",
  "lastDeliveredReceiptMessageRef",
  "isFetchNextPageError",
  "ConfirmDialog",
  "messageActionErrors",
  "useConversationTimeline",
  "_is_retry",
  "previousMessage",
  "resolveMessageLocalState(decryptionStates, message)",
  "resolveMessageLocalState(decryptedTexts, message)",
]) {
  assert.ok(conversation.includes(required), `Missing conversation lifecycle behavior: ${required}`);
}

for (const required of [
  "getMessageContext",
  "restoreScrollAfterOlderLoadRef",
  "mergeMessageContextPages",
  "registerMessageRef",
  "pageCount",
]) {
  assert.ok(timelineHook.includes(required), `Missing timeline hook behavior: ${required}`);
}

assert.ok(!conversation.includes("window.confirm"), "Browser confirmation prompts remain in the conversation flow.");
assert.ok(!conversation.includes("updateMessagePages"), "Legacy cache flattening still collapses message pages.");
assert.ok(composer.includes("isSubmitting"), "Composer does not prevent duplicate sends.");
assert.ok(composer.includes("submitError"), "Composer does not preserve and display failed-send errors.");
assert.ok(meta.includes("Sending…"), "Optimistic messages do not expose a sending state.");
assert.ok(actions.includes("canInteract"), "Deleted and unsent messages still expose invalid server actions.");
assert.ok(confirm.includes('role="alertdialog"'), "Destructive confirmations are not accessible dialogs.");
assert.ok(forward.includes("pendingConversationId"), "Forwarding does not prevent duplicate submissions.");
assert.ok(chatApi.includes("/context/"), "Message context API client is missing.");
assert.ok(views.includes("class MessageContextView"), "Message context backend is missing.");
assert.ok(urls.includes('name="message-context"'), "Message context route is missing.");

console.log("Message lifecycle source regression checks passed.");
