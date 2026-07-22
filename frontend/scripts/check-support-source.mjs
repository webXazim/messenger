import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const app = read("src/App.tsx");
const supportPage = read("src/pages/SupportChatPage.tsx");
const supportPlans = read("src/pages/SupportPlansPage.tsx");
const invitationPage = read("src/pages/SupportInvitationPage.tsx");
const websiteManager = read("src/components/support/SupportWebsiteManager.tsx");
const supportInbox = read("src/components/support/SupportInbox.tsx");
const supportMedia = read("src/components/support/SupportMessageMedia.tsx");
const supportTools = read("src/components/support/SupportConversationTools.tsx");
const supportWorkflowSettings = read("src/components/support/SupportWorkflowSettings.tsx");
const supportServiceSettings = read("src/components/support/SupportServiceOperationsSettings.tsx");
const supportFeedbackSettings = read("src/components/support/SupportFeedbackSettings.tsx");
const supportAnalytics = read("src/components/support/SupportAnalytics.tsx");
const supportKnowledge = read("src/components/support/SupportKnowledgeBase.tsx");
const supportGovernance = read("src/components/support/SupportDataGovernance.tsx");
const supportCall = read("src/components/support/SupportGuestCall.tsx");
const supportCallSettings = read("src/components/support/SupportCallSettings.tsx");
const supportSocket = read("src/lib/supportSocket.ts");
const supportRealtime = read("src/hooks/useSupportRealtime.ts");
const messageEntrance = read("src/hooks/useMessageEntrance.ts");
const messageMeta = read("src/components/messages/MessageMeta.tsx");
const messageCss = read("src/styles/components/messages.css");
const appShell = read("src/components/AppShell.tsx");
const widgetLoader = read("public/support-widget/v1/widget.js");
const frontendNginx = read("nginx.conf");
const dockerCompose = read("../docker-compose.yml");
const api = read("src/api/support.ts");
const css = read("src/styles/pages/support.css");
const conversationCss = read("src/styles/pages/conversation.css");
const auth = read("src/pages/AuthRedirectPage.tsx");
const returnPath = read("src/lib/returnPath.ts");
const supportViews = read("../apps/support/api/views.py");
const throttling = read("../config/throttling.py");

assert.ok(app.includes('/support/invitations/accept'), "Public support invitation route is missing.");
assert.ok(app.includes('path="support/agents"'), "Support agent management route is missing.");
assert.ok(app.includes('path="support/inbox"'), "Support inbox route is missing.");
assert.ok(app.includes('path="support/plans"'), "Support plans route is missing.");
assert.ok(supportPlans.includes("Start 14-day trial"), "Support plan activation action is missing.");
assert.ok(supportPlans.includes("activatePlan"), "Support plan activation is not connected to the API.");
assert.ok(api.includes("/support/plans/activate/"), "Support plan activation API is missing.");
assert.ok(
  api.includes("return response.data as SupportConversationListResponse"),
  "Support conversation pagination must preserve its results object.",
);
assert.ok(
  api.includes("return response.data as SupportServiceAlertList"),
  "Support service-alert pagination must preserve its results object.",
);
assert.ok(supportPage.includes("InviteAgentSection"), "Owner agent invitation UI is missing.");
assert.ok(supportPage.includes("PendingInvitationRow"), "Pending invitation management is missing.");
assert.ok(supportPage.includes("AgentManagementCard"), "Responsive agent access management is missing.");
assert.ok(supportPage.includes("AgentAvailabilitySection"), "Agent-specific availability is missing.");
assert.ok(supportPage.includes("SupportWebsiteManager"), "Responsive website widget management is missing.");
assert.ok(supportPage.includes("Within the websites assigned to this agent"), "Conversation permission is not scoped to assigned websites.");
assert.ok(invitationPage.includes("does not create a Messenger friendship"), "Product isolation explanation is missing from invitation acceptance.");
for (const endpoint of [
  "/support/agents/invitations/",
  "/support/agents/me/availability/",
  "/support/invitations/preview/",
  "/support/invitations/accept/",
]) {
  assert.ok(api.includes(endpoint), `Support API endpoint missing: ${endpoint}`);
}
assert.ok(auth.includes("safeAppReturnPath"), "Authentication does not preserve a safe invitation return path.");
assert.ok(returnPath.includes('candidate.startsWith("//")'), "Return-path validation does not block protocol-relative redirects.");
assert.ok(websiteManager.includes("Allowed website origins"), "Website origin security controls are missing.");
assert.ok(websiteManager.includes("Regenerate site key"), "Site-key rotation UI is missing.");
assert.ok(websiteManager.includes("ms-support-widget-preview"), "Responsive widget preview is missing.");
assert.ok(widgetLoader.includes("crescentsupport.session."), "Visitor session resume storage is missing.");
assert.ok(widgetLoader.includes("Authorization"), "Widget visitor session authentication is missing.");
assert.ok(supportInbox.includes("ms-support-inbox"), "Responsive Support inbox shell is missing.");
assert.ok(supportInbox.includes("Take conversation"), "Agent conversation claiming is missing.");
assert.ok(supportInbox.includes("All websites"), "Cross-website inbox filter is missing.");
assert.ok(supportInbox.includes("refetchInterval"), "Support inbox polling fallback is missing.");
assert.ok(supportInbox.includes("ms-conversation-view"), "Support inbox does not use the Messenger conversation frame.");
assert.ok(supportInbox.includes("ChatHeader"), "Support inbox does not use the Messenger chat header.");
assert.ok(supportInbox.includes("MessengerMessageBubble"), "Support inbox does not use Messenger message bubbles.");
assert.ok(supportInbox.includes("MessageComposer"), "Support inbox does not use the Messenger composer.");
assert.ok(widgetLoader.includes("/conversation/messages/") || widgetLoader.includes('dataSessionPath("/messages/")'), "Widget conversation messaging endpoint is missing.");
assert.ok(widgetLoader.includes("cs-panel"), "Functional public widget panel is missing.");
assert.ok(css.includes(".ms-support-conversation-view.has-selection"), "Mobile queue-to-conversation switching is missing.");

assert.ok(supportInbox.includes("uploadConversationFile"), "Support team attachment uploads are missing.");
assert.ok(supportInbox.includes("onSendVoiceNote"), "Support team voice-note recording is missing.");
assert.ok(supportInbox.includes("draftInsertion"), "Support canned-reply insertion is missing.");
assert.ok(supportMedia.includes("fetchMediaBlob"), "Private Support media previews do not use authenticated delivery.");
assert.ok(supportMedia.includes("ms-support-media-video"), "Support image/video/audio rendering is missing.");
assert.ok(widgetLoader.includes("/conversation/uploads/"), "Widget visitor attachment uploads are missing.");
assert.ok(widgetLoader.includes("xhr.upload.onprogress"), "Widget attachment upload progress is missing.");
assert.ok(widgetLoader.includes("cs-upload-preview"), "Widget attachment previews are missing.");
assert.ok(widgetLoader.includes("Ready to send"), "Widget attachment readiness state is missing.");
assert.ok(widgetLoader.includes("})).then(function ()"), "Widget upload completion must clear its busy state after every upload settles.");
assert.ok(widgetLoader.includes("composerFocusRequested"), "Widget upload updates must preserve composer focus.");
assert.ok(widgetLoader.includes("setSelectionRange"), "Widget rerenders must preserve the draft cursor position.");
assert.ok(widgetLoader.includes("flex:0 0 100%"), "Widget attachment previews must fill the composer width.");
assert.ok(widgetLoader.includes("MediaRecorder"), "Widget visitor voice recording is missing.");
assert.ok(widgetLoader.includes("authorizedBlob"), "Widget private media rendering is missing.");
assert.ok(widgetLoader.includes("cs-media-grid"), "Widget image and video messages must use Messenger-style borderless media grids.");
assert.ok(widgetLoader.includes("openAttachmentViewer"), "Widget authenticated fullscreen attachment viewing is missing.");
assert.ok(widgetLoader.includes("cs-file-card"), "Widget typed file and PDF message cards are missing.");
assert.ok(widgetLoader.includes("cs-voice-message"), "Widget custom waveform voice-note player is missing.");
assert.ok(widgetLoader.includes("voiceDraft"), "Widget voice recordings must support preview, delete, and send states.");
assert.ok(widgetLoader.includes("cs-recorder-wave"), "Widget voice-note recording and preview waveform is missing.");
assert.ok(widgetLoader.includes("prefers-reduced-motion"), "Widget message and media motion must respect reduced-motion preferences.");
assert.ok(css.includes(".ms-support-composer-row"), "Responsive Support media composer layout is missing.");

assert.ok(supportInbox.includes("SupportConversationTools"), "Support-only conversation workflow tools are missing from the inbox.");
assert.ok(supportInbox.includes("support-saved-views"), "Personal saved Support inbox filters are missing.");
assert.ok(supportInbox.includes("listCannedReplies"), "Canned replies are not connected to the Support composer.");
assert.ok(supportTools.includes("Never shown to visitors"), "Internal-note privacy is not explained in the Support UI.");
assert.ok(supportTools.includes("updateConversationTags"), "Support conversation tag assignment is missing.");
assert.ok(supportTools.includes("support-conversation-activity"), "Support audit activity query is missing.");
assert.ok(supportWorkflowSettings.includes("Canned replies"), "Owner canned-reply management is missing.");
assert.ok(supportWorkflowSettings.includes("<h2>Tags</h2>"), "Owner Support-tag management is missing.");
assert.ok(supportRealtime.includes("support-conversation-activity"), "Support realtime does not refresh private notes and audit activity.");
assert.ok(css.includes(".ms-support-conversation-tools"), "Responsive Support workflow detail layout is missing.");
assert.ok(css.includes(".ms-support-workflow-grid"), "Responsive Support workflow settings grid is missing.");

assert.ok(supportInbox.includes('{ value: "overdue", label: "Overdue" }'), "Support overdue queue is missing.");
assert.ok(supportInbox.includes('{ value: "follow_up", label: "Follow-ups" }'), "Support follow-up queue is missing.");
assert.ok(supportInbox.includes("support-service-alerts"), "Persistent Support service alerts are not connected to the inbox.");
assert.ok(supportInbox.includes("follow_up_at"), "Conversation follow-up scheduling is missing.");
assert.ok(supportServiceSettings.includes("Response targets and business hours"), "Owner service-operation settings are missing.");
assert.ok(supportServiceSettings.includes("Count targets during business hours only"), "Business-hour controls are missing.");
assert.ok(supportServiceSettings.includes("Alert assigned agent"), "Assigned-agent SLA alerts are missing.");
assert.ok(api.includes("/support/service-settings/"), "Support service settings API is missing.");
assert.ok(api.includes("/support/service-alerts/"), "Support service alerts API is missing.");
assert.ok(supportRealtime.includes("support.service.alert"), "Support service alerts are not connected to realtime notifications.");
assert.ok(css.includes(".ms-support-service-target-grid"), "Responsive service-target settings are missing.");
assert.ok(css.includes(".ms-support-business-day"), "Responsive business-hour rows are missing.");


assert.ok(app.includes('path="support/analytics"'), "Support analytics route is missing.");
assert.ok(supportPage.includes("SupportAnalytics"), "Responsive Support analytics page is not connected.");
assert.ok(supportPage.includes("SupportFeedbackSettings"), "Customer feedback settings are not connected.");
assert.ok(supportAnalytics.includes("Conversations by website"), "Website-level Support reporting is missing.");
assert.ok(supportAnalytics.includes("Agent performance"), "Agent workload reporting is missing.");
assert.ok(supportAnalytics.includes("CSAT"), "CSAT reporting is missing.");
assert.ok(supportFeedbackSettings.includes("Request automatically"), "Automatic CSAT controls are missing.");
assert.ok(supportInbox.includes("SupportCSATPanel"), "Conversation feedback status is missing from the Support inbox.");
assert.ok(api.includes("/support/analytics/overview/"), "Support analytics API is missing.");
assert.ok(api.includes("/support/feedback-settings/"), "Support feedback settings API is missing.");
assert.ok(api.includes("/csat/"), "Support CSAT API is missing.");
assert.ok(widgetLoader.includes("renderCSAT"), "Public widget satisfaction prompt is missing.");
assert.ok(widgetLoader.includes("submitFeedback"), "Public widget feedback submission is missing.");
assert.ok(css.includes(".ms-support-analytics-metrics"), "Responsive analytics metrics are missing.");
assert.ok(css.includes(".ms-support-report-row"), "Responsive analytics report rows are missing.");
assert.ok(css.includes(".ms-support-csat-panel"), "Responsive conversation CSAT panel is missing.");


assert.ok(app.includes('path="support/knowledge"'), "Support knowledge route is missing.");
assert.ok(supportPage.includes("SupportKnowledgeBase"), "Support knowledge page is not connected.");
assert.ok(supportKnowledge.includes("Show in widget"), "Visitor self-service settings are missing.");
assert.ok(supportKnowledge.includes("Available on all websites"), "Per-website article isolation is missing.");
assert.ok(supportInbox.includes("Insert knowledge answer"), "Agent knowledge answer insertion is missing.");
assert.ok(api.includes("/support/knowledge/articles/"), "Support knowledge article API is missing.");
assert.ok(widgetLoader.includes("renderKnowledge"), "Public widget knowledge search is missing.");
assert.ok(widgetLoader.includes("submitKnowledgeFeedback"), "Public article feedback is missing.");
assert.ok(css.includes(".ms-support-kb-editor__grid"), "Responsive knowledge editor layout is missing.");
assert.ok(css.includes(".ms-support-kb-summary"), "Responsive knowledge summary is missing.");


assert.ok(supportInbox.includes("SupportGuestCall"), "Support guest-call overlay is not connected to the inbox.");
assert.ok(supportInbox.includes("audioCallsEnabled"), "Independent Support audio-call gating is missing.");
assert.ok(supportInbox.includes("videoCallsEnabled"), "Independent Support video-call gating is missing.");
assert.ok(supportCall.includes("getCallTurnCredentials"), "Support calls do not use authenticated TURN credentials.");
assert.ok(supportCall.includes("RTCPeerConnection"), "Support WebRTC orchestration is missing.");
assert.ok(supportCall.includes("playsInline"), "Responsive Support inline video playback is missing.");
assert.ok(supportCallSettings.includes("Maximum call duration"), "Owner Support call limits are missing.");
assert.ok(api.includes("/support/calls/") || api.includes("supportDataPath(`/conversations/${conversationId}/calls/`)"), "Support call APIs are missing.");
assert.ok(api.includes("/support/calls/active/") || api.includes('supportDataPath("/calls/active/")'), "Global Support call recovery is missing.");
assert.ok(widgetLoader.includes("function renderCall"), "Widget incoming-call experience is missing.");
assert.ok(widgetLoader.includes("RTCPeerConnection"), "Widget WebRTC guest calling is missing.");
assert.ok(widgetLoader.includes("/calls/turn-credentials/"), "Widget TURN credential retrieval is missing.");
assert.ok(widgetLoader.includes("function acquireCallMedia"), "Widget calls must acquire browser media before ringing support.");
const visitorCallCreateIndex = Math.max(
  widgetLoader.indexOf('request(sessionPath("/calls/")'),
  widgetLoader.indexOf('dataRequest(sessionPath("/calls/")'),
);
assert.ok(
  visitorCallCreateIndex >= 0 && widgetLoader.indexOf("acquireCallMedia(callType)") < visitorCallCreateIndex,
  "Visitor calls must obtain camera or microphone access before creating a ringing call.",
);
const visitorCallAcceptIndex = Math.max(
  widgetLoader.indexOf('request(callPath(incomingCall.id, "/accept/")'),
  widgetLoader.indexOf('dataRequest(callPath(incomingCall.id, "/accept/")'),
);
assert.ok(
  visitorCallAcceptIndex >= 0 && widgetLoader.indexOf("acquireCallMedia(incomingCall.call_type)") < visitorCallAcceptIndex,
  "Incoming calls must obtain camera or microphone access before being accepted.",
);
assert.ok(widgetLoader.includes("lastReceiptAckStatus"), "Widget receipt acknowledgement deduplication is missing.");
assert.ok(widgetLoader.includes("function updateLauncherUnread"), "Widget unread badge must update without rebuilding the launcher.");
assert.ok(
  widgetLoader.includes('!state.open && !state.closing && shadow.querySelector(".cs-launcher")'),
  "Closed widget renders must preserve the existing launcher element.",
);
assert.ok(!widgetLoader.includes('.cs-launcher{animation:'), "Widget launcher must not replay an entrance animation during background updates.");
assert.ok(!widgetLoader.includes("animation:cs-panel-in"), "Open widget renders must not replay the panel entrance animation.");
assert.ok(
  !widgetLoader.includes('payload.event === "support.message.created" || payload.event === "support.conversation.updated"'),
  "Background conversation updates must not be treated as new visitor-visible messages.",
);
assert.ok(
  frontendNginx.includes('add_header Cache-Control "no-cache, must-revalidate"'),
  "The fixed widget loader URL must revalidate instead of serving stale code.",
);
assert.ok(widgetLoader.includes(".cs-bubble-text{display:inline}"), "Widget message metadata must remain inline with text.");
assert.ok(dockerCompose.includes("SUPPORT_CALLS_ENABLED: ${SUPPORT_CALLS_ENABLED:-true}"), "Production Support calls are not enabled.");
assert.ok(css.includes(".ms-support-call-overlay"), "Responsive Support call overlay styles are missing.");
assert.ok(css.includes(".ms-support-call-media"), "Responsive Support video layout is missing.");

assert.ok(supportPage.includes("SupportDataGovernance"), "Support data-governance settings are not connected.");
assert.ok(supportGovernance.includes("Allow visitor deletion requests"), "Visitor deletion controls are missing.");
assert.ok(supportGovernance.includes("HTTPS endpoint"), "Signed webhook management is missing.");
assert.ok(supportGovernance.includes("Download"), "Private Support export downloads are missing.");
assert.ok(supportTools.includes("Delete visitor data"), "Owner visitor-data deletion action is missing.");
assert.ok(api.includes("/support/privacy/settings/"), "Support privacy settings API is missing.");
assert.ok(api.includes("/support/webhooks/"), "Support webhook API is missing.");
assert.ok(api.includes("/support/exports/"), "Support export API is missing.");
assert.ok(api.includes("/support/privacy/visitors/"), "Owner visitor deletion API is missing.");
assert.ok(widgetLoader.includes("requestDataDeletion"), "Widget visitor self-deletion API is missing.");
assert.ok(widgetLoader.includes("Delete my support data"), "Widget visitor deletion control is missing.");
assert.ok(css.includes(".ms-support-governance-grid"), "Responsive Support governance grid is missing.");
assert.ok(css.includes(".ms-support-governance-row"), "Responsive Support governance list is missing.");

assert.ok(supportSocket.includes("support.ping"), "Support realtime heartbeat is missing.");
assert.ok(supportSocket.includes("scheduleReconnect"), "Support realtime reconnect recovery is missing.");
assert.ok(supportRealtime.includes("support-unread-summary"), "Cross-website Support unread summary is missing.");
assert.ok(supportRealtime.includes("showSupportBrowserNotification"), "Background Support notifications are missing.");
assert.ok(supportRealtime.includes("SOCKET_AUTH_FAILED_EVENT"), "Support socket token refresh is not connected to the existing auth lifecycle.");
assert.ok(supportRealtime.includes("code === 4403"), "Revoked Support access does not stop its socket without disturbing Messenger authentication.");
assert.ok(appShell.includes("supportRealtime.socketStatus"), "Support and Messenger socket states are not isolated in the application shell.");
assert.ok(supportInbox.includes('socketStatus === "open" ? false'), "Support polling fallback does not stop while realtime is healthy.");
assert.ok(widgetLoader.includes("connectRealtime"), "Public widget realtime delivery is missing.");
assert.ok(widgetLoader.includes("scheduleRealtimeReconnect"), "Public widget reconnect recovery is missing.");
assert.ok(widgetLoader.includes("socketConnectTimer"), "Public widget can remain stuck in WebSocket CONNECTING.");
assert.ok(widgetLoader.includes("Date.now() - state.lastPongAt > 55000"), "Public widget does not recover half-open WebSockets.");
assert.ok(widgetLoader.includes("state.pollInFlight"), "Public widget fallback polling can overlap and overload the API.");
assert.ok(widgetLoader.includes("sendMessageRequest(messagePayload, 0)"), "Public widget message sends lack bounded idempotent recovery.");
assert.ok(supportSocket.includes('payload.event === "connection.ready"'), "Support sockets do not translate Axum readiness into recovery refreshes.");
assert.ok(supportSocket.includes("Date.now() - this.lastPongAt > 55000"), "Support agent sockets do not recover half-open connections.");
assert.ok(supportRealtime.includes('payload.event === "support.ready"'), "Support reconnects do not invalidate durable state.");
assert.ok(supportInbox.includes("client_temp_id: clientTempId"), "Support agent optimistic IDs are not persisted for idempotency.");
assert.ok(widgetLoader.includes("client_temp_id: clientTempId"), "Support visitor optimistic IDs are not persisted for idempotency.");
assert.ok(widgetLoader.includes("payload.data.message_id || payload.data.id"), "Widget realtime delivery receipts do not resolve the authoritative message ID.");
assert.ok(supportViews.includes("throttle_classes = [UnsafeScopedRateThrottle]"), "Support message polling still consumes the visitor send-rate bucket.");
assert.ok(supportViews.includes('throttle_scope = "support_message_send"'), "Agent Support sends do not have an isolated throttle budget.");
assert.ok(supportRealtime.includes('payload.event === "support.message.delivered"'), "Support delivery receipts still trigger message-history refetches.");
assert.ok(supportRealtime.includes('payload.event === "support.message.read"'), "Support read receipts still trigger message-history refetches.");
assert.ok(!supportInbox.includes("supportApi.markConversationDelivered(selectedId"), "Support Inbox refetches still emit redundant delivery receipts.");
assert.ok(supportViews.includes('"conversation_id": str(conversation_id or "")'), "Widget tickets do not report whether their conversation audience is ready.");
assert.ok(throttling.includes("class UnsafeScopedRateThrottle"), "Unsafe-only scoped throttling is missing.");
assert.ok(widgetLoader.includes("sendQueue: Promise.resolve()"), "Widget sends are not serialized.");
assert.ok(widgetLoader.includes("visitorTyping: false"), "Widget typing presence is not edge-triggered.");
assert.ok(widgetLoader.includes("state.messages.splice(replacementIndex, 1, payload.message)"), "Widget optimistic messages are not replaced in place.");
assert.ok(widgetLoader.includes("state.renderedMessageKeys[renderKey]"), "Widget message confirmations can replay entrance motion.");
assert.ok(widgetLoader.includes(".cs-message.is-grouped{margin-top:2px}"), "Widget mobile message grouping does not match Messenger density.");
assert.ok(supportInbox.includes("createSerializedTaskQueue"), "Support Inbox sends are not serialized.");
assert.ok(supportInbox.includes("structuralSharing: mergeSupportMessages"), "Support Inbox refetches can discard optimistic messages.");
assert.ok(supportInbox.includes("stableMessageRenderKey(message)"), "Support Inbox confirmation can remount and blink optimistic messages.");
assert.ok(messageEntrance.includes("client_temp_id"), "Message rendering does not preserve optimistic identity.");
assert.ok(messageMeta.includes('hour: "numeric"'), "Message timestamps still show a leading zero.");
assert.ok(messageCss.includes(".ms-message-block.is-message-entering"), "Messenger and Support messages have no shared entrance motion.");
assert.ok(conversationCss.includes(".ms-chat-composer-dock > .ms-typing-indicator"), "Hidden typing presence still reserves message timeline space.");


assert.ok(css.includes("@media (max-width: 760px)"), "Support responsive tablet/mobile layout is missing.");
assert.ok(css.includes("@media (max-width: 480px)"), "Support compact mobile layout is missing.");
assert.ok(css.includes("env(safe-area-inset-bottom)"), "Invitation acceptance does not respect mobile safe areas.");
assert.ok(css.includes("grid-template-columns: minmax(0, 1fr)"), "Support mobile controls do not collapse to one column.");

console.log("Support Chat workflow, realtime, media, service operations, analytics, CSAT, knowledge self-service, data governance, guest calls, widget fallback, and responsive source regression checks passed.");
