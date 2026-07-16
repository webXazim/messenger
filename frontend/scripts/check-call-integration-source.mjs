import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const callsPage = read("src/pages/CallsPage.tsx");
const conversationPage = read("src/pages/ConversationPage.tsx");
const callRoom = read("src/pages/CallRoomPage.tsx");
const appShell = read("src/components/AppShell.tsx");
const activeCallContext = read("src/contexts/ActiveCallContext.tsx");
const app = read("src/App.tsx");
const callRoomCss = read("src/styles/pages/call-room.css");
const callMediaProfile = read("src/components/call/callMediaProfile.ts");
const banner = read("src/components/IncomingCallBanner.tsx");
const overlay = read("src/components/IncomingCallOverlay.tsx");
const media = read("src/lib/mediaPermissions.ts");
const coordination = read("src/lib/callCoordination.ts");
const lifecycle = read("src/lib/callLifecycle.ts");
const messageBubble = read("src/components/MessageBubble.tsx");
const callEventMessage = read("src/components/messages/CallEventMessage.tsx");
const services = read("../apps/chat/services.py");
const views = read("../apps/chat/api/views.py");
const tests = read("../apps/chat/tests.py");

for (const required of [
  "callPeerLabel",
  "callDirection",
  "callStatusPresentation",
  "callDestination",
  "findActiveCallForConversation",
  "preflightCallMedia",
  "Return to call",
]) {
  assert.ok(callsPage.includes(required), `Calls history/launcher is missing: ${required}`);
}
assert.ok(callsPage.includes('to={callDestination(call, currentIdentity)}'), "Ended call history does not return to its conversation.");
assert.ok(conversationPage.includes("findActiveCallForUser"), "Conversation call buttons do not guard an already-active call.");
assert.ok(conversationPage.includes("preflightCallMedia"), "Conversation call buttons do not validate media before creating a call.");
assert.ok(callRoom.includes("canInitializeLocalMedia"), "Incoming call routes can still open camera or microphone before acceptance.");
assert.ok(callRoom.includes("declineMutation"), "Incoming call-room decline still uses the generic end action.");
assert.ok(callRoom.includes("patchCallCaches(queryClient, updated)"), "Declined calls can remain active in the frontend cache.");
assert.ok(callRoom.includes("isTerminalCall"), "Ended call URLs are not redirected back to their conversation.");
assert.ok(callRoom.includes('if (call.status === "missed")'), "Unanswered calls can remain open on the call screen.");
assert.ok(appShell.includes("handleIncomingCallAction"), "Incoming banner and overlay do not share one guarded action path.");
assert.ok(appShell.includes("claimCallAction"), "Incoming acceptance is not coordinated across tabs.");
assert.ok(appShell.includes("activeCallId") && appShell.includes("<CallRoomPage"), "The live call controller is not mounted above child routes.");
assert.ok(appShell.includes("expandedCallId") && appShell.includes("displayMode={activeCallIsExpanded ? \"full\" : \"compact\"}"), "New and incoming calls can still open as a compact bar instead of the full call screen.");
assert.ok(activeCallContext.includes("activateCall") && activeCallContext.includes("expectOutgoingCall"), "Call launchers cannot atomically activate the persistent call controller.");
assert.ok(conversationPage.includes("expectOutgoingCall(conversationId)") && callsPage.includes("expectOutgoingCall(conversationId)"), "Outgoing call launchers can still miss the server's started event.");
assert.ok(appShell.includes("outgoingCallConversationRef.current === conversationId") && appShell.includes("activateCall(call.id)"), "The caller is not activated from the matching realtime started event.");
assert.ok(appShell.includes("rememberActiveCallId") && appShell.includes("sessionStorage"), "An active call cannot be restored after an in-tab reload.");
assert.ok(appShell.includes("presentIncomingCall") && appShell.includes("isForegroundBrowserTab"), "The foreground recipient is not brought into the incoming call room.");
assert.ok(appShell.includes('payload.event === "call.accepted"') && appShell.includes('navigate(`/calls/${callPayload.id}`)'), "The connected caller is not restored to the shared call room after acceptance.");
assert.ok(app.includes('path="calls/:callId" element={<></>}'), "The call route can mount a second competing call controller.");
assert.ok(callRoom.includes('displayMode?: "full" | "compact"'), "The call controller cannot switch between full and persistent modes.");
assert.ok(callRoom.includes("ms-active-call-bar") && callRoom.includes("onLeave={minimizeCall}"), "The persistent call bar or minimize behavior is missing.");
const minimizeCallBlock = callRoom.slice(callRoom.indexOf("const minimizeCall"), callRoom.indexOf("const compactDisplayName"));
assert.ok(minimizeCallBlock.includes("onCallMinimize?.(call.id)") && !minimizeCallBlock.includes("declineMutation"), "Minimizing an incoming full-screen call can still decline it.");
assert.ok(callRoom.includes("onCallFinished?.(call.id)"), "Terminal calls do not clear the persistent call session.");
assert.ok(callRoomCss.includes("Visually hide the full call surface without unmounting its audio/video nodes"), "Compact mode can unmount or pause the live media surface.");
assert.ok(callRoom.includes("resolveVideoSenderProfile") && callRoom.includes('displayMode === "compact" ? "low"'), "Persistent calls do not advertise and apply their reduced video profile.");
assert.ok(callMediaProfile.includes("PERSISTENT_MAX_BITRATE_BPS") && callMediaProfile.includes("remotePreferredVideoQuality"), "Persistent video efficiency caps are missing or not shared with the peer.");
assert.ok(appShell.includes('message.metadata?.system_event === "call"'), "Call timeline messages can still appear as generic receiver toasts.");
assert.ok(callRoom.indexOf("lastOfferSentAtRef.current = Date.now()") > callRoom.indexOf('await sendSignal("offer"'), "Initial offers are marked sent before signaling succeeds.");
assert.ok(callRoom.includes("if (!sent) offerSentRef.current = false"), "A throttled or unsent initial offer can still block handshake retries.");
assert.ok(services.includes("target_message.sender = call.initiated_by"), "Call-card ownership can still flip to the participant who answers or declines.");
assert.ok(conversationPage.includes("message.call_event?.initiated_by_id"), "Call-card receipts still derive ownership from the mutable sender.");
assert.ok(callEventMessage.includes("ms-call-message__meta"), "Call-card time and receipts are still outside the card.");
assert.ok(!banner.includes("Call ID:"), "Incoming banner still exposes a technical call ID.");
assert.ok(overlay.includes("onMinimize"), "Incoming overlay cannot be minimized into the banner.");
assert.ok(media.includes("requestRequiredCallMedia"), "Strict call-media permission validation is missing.");
assert.ok(media.includes("preflightCallMedia"), "Call media preflight helper is missing.");
assert.ok(coordination.includes("BroadcastChannel"), "Cross-tab incoming-call coordination is missing.");
assert.ok(coordination.includes("sessionStorage"), "Call actions do not share a stable owner inside one browser tab.");
assert.ok(lifecycle.includes("Completed ·"), "Completed call duration is not presented in user-facing history.");
assert.ok(services.includes("actor_active_call"), "Backend still allows one caller to start multiple simultaneous calls.");
assert.ok(views.includes('"active_call_exists" if exc.actor_busy'), "Backend does not distinguish the caller's existing active call.");
assert.ok(tests.includes("test_caller_cannot_start_a_second_active_call"), "Backend regression coverage for duplicate caller calls is missing.");
assert.ok(tests.includes("test_direct_call_decline_closes_call_and_redial_creates_fresh_session"), "Decline/redial regression coverage is missing.");

console.log("Call lifecycle integration source checks passed.");
