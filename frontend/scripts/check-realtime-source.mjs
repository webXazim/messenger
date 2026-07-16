import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const socket = read("src/lib/chatSocket.ts");
const hook = read("src/hooks/useChatSocket.ts");
const tokenStore = read("src/lib/tokenStore.ts");
const appShell = read("src/components/AppShell.tsx");
const conversation = read("src/pages/ConversationPage.tsx");
const composer = read("src/components/MessageComposer.tsx");
const pushNotifications = read("src/lib/pushNotifications.ts");
const messagingServiceWorker = read("public/firebase-messaging-sw.js");
const cache = read("src/lib/realtimeCache.ts");
const services = read("../apps/chat/services.py");
const consumers = read("../apps/chat/consumers.py");
const serializers = read("../apps/chat/api/serializers.py");
const views = read("../apps/chat/api/views.py");

for (const required of [
  "event_id?: string",
  "incomingEventKey",
  "credentialsChanged",
  "SOCKET_AUTH_FAILED_EVENT",
  "replaceConnection",
  "isDuplicateIncomingEvent",
]) {
  assert.ok(socket.includes(required), `Missing socket stability behavior: ${required}`);
}

for (const required of [
  "AUTH_TOKEN_UPDATED_EVENT",
  "ensureSocketConnection(true)",
  "jwtExpiresSoon",
  "visibilitychange",
]) {
  assert.ok(`${hook}\n${tokenStore}`.includes(required), `Missing token refresh behavior: ${required}`);
}

for (const required of [
  "chatApi.sync",
  "mergeChatSync",
  "getRealtimeSyncMarker",
  "patchConversationCaches",
  "patchConversationReceiptCaches",
  "patchMessageCache",
]) {
  assert.ok(`${appShell}\n${cache}`.includes(required), `Missing reconnect reconciliation: ${required}`);
}

for (const required of [
  "typingExpiryTimersRef",
  "expires_at",
  "setTypingUsers({})",
  'payload.event === "message.reaction_updated"',
  "acknowledgeConversationRead(conversationId, normalized.id)",
  "timelineAtLatestRef.current",
  'socket.send({ event: "message.read"',
  'socket.send({ event: "message.delivered"',
  'applyParticipantReceiptInCache(targetConversationId, "message.read", receipt, queryClient)',
  'refetchInterval:',
]) {
  assert.ok(conversation.includes(required), `Missing typing/realtime UI protection: ${required}`);
}

for (const required of [
  "make_realtime_event",
  "_active_presence_devices",
  "get_public_presence_snapshot",
  "presence_recipient_ids",
]) {
  assert.ok(services.includes(required), `Missing backend realtime primitive: ${required}`);
}

for (const required of [
  "_stop_all_typing",
  "_broadcast_typing_event",
  'event.get("event_id")',
  "_public_presence_snapshot",
]) {
  assert.ok(consumers.includes(required), `Missing WebSocket consumer hardening: ${required}`);
}

assert.ok(serializers.includes("_presence_is_visible"), "Chat participant serializers still expose private presence.");
assert.ok(cache.includes("mergeConversationPreservingPresence"), "Conversation refreshes can still overwrite newer presence state.");
assert.ok(cache.includes("mergeConversationReceipts(current, incoming)"), "Conversation refreshes can still regress delivery and read receipts.");
assert.ok(cache.includes("reconcileConversationPresence"), "Conversation updates do not reconcile a peer already known to be online.");
assert.ok(cache.includes('{ queryKey: ["conversation-route"] }'), "Named conversation routes do not receive presence updates.");
assert.ok(appShell.includes('["call.heartbeat", "call.media_state", "call.quality_report"]'), "Active call traffic does not reconcile global user presence.");
assert.match(appShell, /payload\.event === "message\.created"[\s\S]*socket\.send\(\{ event: "message\.delivered"/, "Messages received outside the open chat are not acknowledged as delivered.");
assert.ok(appShell.includes('message.conversation_id || payload.data?.conversation_id || payload.data?.conversation'), "Global delivery acknowledgement can lose the conversation id.");
assert.ok(appShell.includes("decryptMessageTextResult"), "Foreground message notifications do not decrypt their preview locally.");
assert.ok(!appShell.includes("New chat activity"), "Foreground message notifications still expose the generic activity label.");
assert.ok(appShell.includes('navigate(`/chat/${toast.conversationId}?reply=1`)'), "In-app message notifications do not offer a reply handoff.");
assert.ok(pushNotifications.includes('{ action: "reply", title: "Reply" }'), "Browser message notifications do not expose a Reply action.");
assert.ok(pushNotifications.includes('`/chat/${data.conversation_id}?reply=1`'), "Direct browser notification clicks do not focus reply mode.");
assert.ok(messagingServiceWorker.includes('event.action === "reply"'), "Background notification Reply actions are not handled.");
assert.ok(conversation.includes('get("reply") === "1"'), "Notification reply routes are not recognized by the conversation page.");
assert.ok(composer.includes("if (autoFocus) focusTextarea()"), "Notification replies do not focus the message composer.");
assert.ok(serializers.includes("return getattr(obj, \"last_seen_at\", None) if self._presence_is_visible(obj) else None"), "Private last-seen data is still exposed.");
assert.ok(views.includes('if getattr(participant, "_read_changed", False)'), "Unchanged read receipts are still broadcast repeatedly.");
assert.ok(services.includes("effective_read_message"), "Duplicate read acknowledgements do not repair their delivered cursor.");
assert.ok(views.includes("_broadcast_presence_update(request.user, snapshot)"), "REST presence changes are not propagated to peers.");
assert.match(consumers, /_subscribe[\s\S]*group_send\(group_name, self\._event_payload\("message\.delivered"/, "Subscription delivery receipts are not broadcast to the sender.");

console.log("Realtime source regression checks passed.");
