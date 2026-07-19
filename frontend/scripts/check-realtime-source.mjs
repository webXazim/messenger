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
const activeConversationView = read("src/lib/activeConversationView.ts");
const cache = read("src/lib/realtimeCache.ts");
const services = read("../apps/chat/services.py");
const axumSocket = read("../realtime/src/websocket.rs");
const realtimePresence = read("../apps/common/realtime_presence.py");
const realtimeAuth = read("../apps/common/realtime_auth.py");
const realtimeCredentials = read("src/lib/realtimeCredentials.ts");
const callRoom = read("src/pages/CallRoomPage.tsx");
const realtimePublisher = read("../apps/common/realtime.py");
const serializers = read("../apps/chat/api/serializers.py");
const views = read("../apps/chat/api/views.py");
const devicePresence = read("src/lib/devicePresence.ts");
const personPresentation = read("src/lib/personPresentation.ts");

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
  "socket.split()",
  "high_queue_capacity",
  "low_queue_capacity",
  '"typing.start" | "typing.stop"',
  '"presence.ping"',
  '"call.signal"',
  "validate_grant",
]) {
  assert.ok(axumSocket.includes(required), `Missing Axum realtime hardening: ${required}`);
}
for (const required of ["issue_user_realtime_ticket", "issue_audience_grant", "issue_call_grant", "realtime_call_grant", "RS256"]) {
  assert.ok(realtimeAuth.includes(required), `Missing realtime credential protection: ${required}`);
}

for (const required of [
  "requestRealtimeCallGrant",
  'call_grant: callGrant.grant',
  "clearRealtimeCallGrant",
]) {
  assert.ok(`${realtimeCredentials}\n${callRoom}`.includes(required), `Missing active-call signaling grant: ${required}`);
}
assert.ok(axumSocket.includes("validate_call_grant"), "Axum does not validate active-call signaling grants.");
assert.ok(axumSocket.includes("REALTIME_MAX_CONNECTION_AGE_SECONDS") || read("../realtime/src/config.rs").includes("REALTIME_MAX_CONNECTION_AGE_SECONDS"), "Realtime connections are not periodically re-authorized.");
assert.ok(read("../realtime/src/config.rs").includes("REALTIME_CONNECTION_REFRESH_JITTER_SECONDS"), "Credential refresh jitter is missing.");
assert.ok(realtimePublisher.includes('redis_stream') && realtimePublisher.includes('Axum realtime event delivery failed'), "Django realtime publishing is not transport-neutral.");


for (const required of [
  'TURN_PROVIDER", "legacy',
  "CLOUDFLARE_TURN_KEY_ID",
  "generate-ice-servers",
  '"provider": "cloudflare"',
]) {
  assert.ok(services.includes(required), `Missing Cloudflare TURN credential boundary: ${required}`);
}
assert.ok(serializers.includes("_presence_is_visible"), "Chat participant serializers still expose private presence.");
assert.ok(cache.includes("mergeConversationPreservingPresence"), "Conversation refreshes can still overwrite newer presence state.");
assert.ok(cache.includes("mergeConversationReceipts(current, incoming)"), "Conversation refreshes can still regress delivery and read receipts.");
assert.ok(cache.includes("reconcileConversationPresence"), "Conversation updates do not reconcile a peer already known to be online.");
assert.ok(cache.includes('{ queryKey: ["conversation-route"] }'), "Named conversation routes do not receive presence updates.");
assert.ok(appShell.includes('["call.heartbeat", "call.media_state", "call.quality_report"]'), "Active call traffic does not reconcile global user presence.");
assert.match(appShell, /payload\.event === "message\.created"[\s\S]*chatApi\.markConversationDelivered/, "Messages received outside the open chat are not acknowledged as delivered through Django.");
assert.ok(appShell.includes('message.conversation_id || payload.data?.conversation_id || payload.data?.conversation'), "Global delivery acknowledgement can lose the conversation id.");
assert.ok(appShell.includes("decryptMessageTextResult"), "Foreground message notifications do not decrypt their preview locally.");
assert.ok(appShell.includes("isConversationActivelyViewedAtLatest(conversationId)"), "Foreground notifications do not respect the exact chat's latest-view state.");
assert.ok(activeConversationView.includes("activeConversationView.conversationId !== conversationId"), "Active chat notification suppression is not keyed by the resolved conversation id.");
assert.ok(activeConversationView.includes("activeConversationView.atLatest"), "Active chat notification suppression does not require the latest-message viewpoint.");
assert.ok(conversation.includes("setActiveConversationView({ conversationId, atLatest: timelineAtLatest, visible: pageVisible })"), "Conversation pages do not publish their live latest-view state.");
assert.match(conversation, /shouldReadImmediately[\s\S]*markConversationReadInCaches\(queryClient, conversationId\)[\s\S]*acknowledgeConversationRead/, "Visible latest-view messages are not cleared from unread state immediately.");
assert.match(appShell, /chatIsOpenAtLatest[\s\S]*chatApi\.markConversationRead/, "The global realtime listener does not persist an immediate read receipt through Django.");
assert.ok(conversation.includes("new IntersectionObserver"), "Rendered message visibility is not observed for immediate read receipts.");
assert.ok(cache.includes("applyActiveConversationReadState(reconcileConversationPresence"), "Realtime conversation updates can restore an unread badge for the actively viewed latest chat.");
assert.ok(!appShell.includes("New chat activity"), "Foreground message notifications still expose the generic activity label.");
assert.ok(appShell.includes('navigate(`/chat/${toast.conversationId}?reply=1`)'), "In-app message notifications do not offer a reply handoff.");
assert.ok(pushNotifications.includes('{ action: "reply", title: "Reply" }'), "Browser message notifications do not expose a Reply action.");
assert.ok(pushNotifications.includes('`/chat/${data.conversation_id}?reply=1`'), "Direct browser notification clicks do not focus reply mode.");
assert.ok(messagingServiceWorker.includes('event.action === "reply"'), "Background notification Reply actions are not handled.");
assert.ok(conversation.includes('get("reply") === "1"'), "Notification reply routes are not recognized by the conversation page.");
assert.ok(composer.includes("if (autoFocus) focusTextarea()"), "Notification replies do not focus the message composer.");
assert.ok(serializers.includes("if not self._presence_is_visible(obj)") && serializers.includes('self._presence_snapshot(obj).get("last_seen_at")'), "Private or stale last-seen data is still exposed.");
assert.ok(cache.includes("mergeNewestPresence"), "Older API presence can still overwrite newer realtime state.");
assert.ok(services.includes("read_many_user_last_seen"), "Conversation serialization does not batch current Axum last-seen timestamps.");
assert.ok(realtimePresence.includes("LAST_SEEN_KEY_PREFIX"), "Shared presence storage does not retain Axum last-seen timestamps.");
assert.ok(axumSocket.includes('if !data.contains_key("last_seen_at")'), "Axum presence fanout still discards its current last-seen timestamp.");
assert.ok(views.includes('if getattr(participant, "_read_changed", False)'), "Unchanged read receipts are still broadcast repeatedly.");
assert.ok(services.includes("effective_read_message"), "Duplicate read acknowledgements do not repair their delivered cursor.");
assert.ok(views.includes("_broadcast_presence_update(request.user, snapshot)"), "REST presence changes are not propagated to peers.");
assert.ok(devicePresence.includes("PRESENCE_IDLE_AFTER_MS") && devicePresence.includes("detectPresenceDeviceType"), "The client does not classify broad device type or define an idle threshold.");
assert.ok(socket.includes("presence_status") && socket.includes("device_type") && socket.includes("visibilitychange"), "Socket heartbeats do not report activity and device presence.");
assert.ok(!conversation.includes('socket.send({ event: "message.read"'), "Read receipts must use the durable Django HTTP path.");
assert.ok(!conversation.includes('socket.send({ event: "message.delivered"'), "Delivery receipts must use the durable Django HTTP path.");
assert.ok(services.includes("_presence_snapshot_from_devices") && services.includes('presence_status = "active" if actively_used else "idle"'), "Multi-device presence does not aggregate active and idle sessions.");
assert.ok(personPresentation.includes('"Idle"') && personPresentation.includes("deviceLabel"), "Presence labels do not display idle state and device type.");
assert.ok(conversation.includes("presenceAwareConversation") && conversation.includes("applyKnownOnlinePresence([conversation], friends)"), "The open chat header and details do not share the inbox presence snapshot.");

console.log("Realtime source regression checks passed.");
