import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const callsPage = read("src/pages/CallsPage.tsx");
const conversationPage = read("src/pages/ConversationPage.tsx");
const callRoom = read("src/pages/CallRoomPage.tsx");
const appShell = read("src/components/AppShell.tsx");
const banner = read("src/components/IncomingCallBanner.tsx");
const overlay = read("src/components/IncomingCallOverlay.tsx");
const media = read("src/lib/mediaPermissions.ts");
const coordination = read("src/lib/callCoordination.ts");
const lifecycle = read("src/lib/callLifecycle.ts");
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
assert.ok(appShell.includes("handleIncomingCallAction"), "Incoming banner and overlay do not share one guarded action path.");
assert.ok(appShell.includes("claimCallAction"), "Incoming acceptance is not coordinated across tabs.");
assert.ok(appShell.includes('message.metadata?.system_event === "call"'), "Call timeline messages can still appear as generic receiver toasts.");
assert.ok(callRoom.indexOf("lastOfferSentAtRef.current = Date.now()") > callRoom.indexOf('await sendSignal("offer"'), "Initial offers are marked sent before signaling succeeds.");
assert.ok(callRoom.includes("if (!sent) offerSentRef.current = false"), "A throttled or unsent initial offer can still block handshake retries.");
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
