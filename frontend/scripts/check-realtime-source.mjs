import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const socket = read("src/lib/chatSocket.ts");
const hook = read("src/hooks/useChatSocket.ts");
const tokenStore = read("src/lib/tokenStore.ts");
const appShell = read("src/components/AppShell.tsx");
const conversation = read("src/pages/ConversationPage.tsx");
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
  "patchMessageCache",
]) {
  assert.ok(`${appShell}\n${cache}`.includes(required), `Missing reconnect reconciliation: ${required}`);
}

for (const required of [
  "typingExpiryTimersRef",
  "expires_at",
  "setTypingUsers({})",
  'payload.event === "message.reaction_updated"',
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
assert.ok(cache.includes('{ queryKey: ["conversation-route"] }'), "Named conversation routes do not receive presence updates.");
assert.ok(appShell.includes('["call.heartbeat", "call.media_state", "call.quality_report"]'), "Active call traffic does not reconcile global user presence.");
assert.ok(serializers.includes("return getattr(obj, \"last_seen_at\", None) if self._presence_is_visible(obj) else None"), "Private last-seen data is still exposed.");
assert.ok(views.includes('if getattr(participant, "_read_changed", False)'), "Unchanged read receipts are still broadcast repeatedly.");
assert.ok(views.includes("_broadcast_presence_update(request.user, snapshot)"), "REST presence changes are not propagated to peers.");

console.log("Realtime source regression checks passed.");
