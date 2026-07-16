import assert from "node:assert/strict";
import { rmSync } from "node:fs";
import {
  formatElapsed,
  getCallViewState,
  participantInitials,
  participantName,
} from "../.call-test-build/components/call/callPresentation.js";
import {
  calculateFloatingVideoBounds,
  clampCallValue,
  positionFromRelative,
  relativeFromPosition,
} from "../.call-test-build/components/call/callGeometry.js";
import {
  cameraFacingFromTrack,
  findPreferredCameraDevice,
  supportsMobileCameraSwitch,
} from "../.call-test-build/components/call/callCamera.js";
import { resolveVideoSenderProfile } from "../.call-test-build/components/call/callMediaProfile.js";
import {
  callDestination,
  callDirection,
  callPeerLabel,
  callStatusPresentation,
  findActiveCallForConversation,
  formatCallDuration,
} from "../.call-test-build/lib/callLifecycle.js";
import { getCallEventPresentation } from "../.call-test-build/components/messages/messagePresentation.js";

const remote = {
  id: "participant-2",
  state: "joined",
  user: { id: "2", username: "sarah", display_name: "Sarah Ahmed", is_online: true },
};

assert.equal(formatElapsed(0), "0:00");
assert.equal(formatElapsed(65.9), "1:05");
assert.equal(formatElapsed(-12), "0:00");
assert.equal(participantName(remote), "Sarah Ahmed");
assert.equal(participantInitials(remote), "SA");

const baseCall = {
  id: "call-1",
  call_type: "video",
  status: "ringing",
  started_at: new Date(0).toISOString(),
};

const ringingCallMessage = {
  id: "call-message-1",
  type: "system",
  text: "Outgoing call",
  sender: { id: "caller", username: "caller", display_name: "Caller" },
  created_at: new Date(0).toISOString(),
  attachments: [],
  call_event: {
    system_event: "call",
    call_status: "ringing",
    call_outcome: "ringing",
    call_type: "voice",
    summary_text: "Outgoing call",
    initiated_by_id: "caller",
  },
};
assert.equal(getCallEventPresentation(ringingCallMessage, "caller")?.title, "Outgoing call");
assert.equal(getCallEventPresentation(ringingCallMessage, "receiver")?.title, "Incoming call");
assert.equal(getCallEventPresentation(ringingCallMessage, "receiver")?.direction, "incoming");
const missedCallMessage = {
  ...ringingCallMessage,
  call_event: { ...ringingCallMessage.call_event, call_status: "missed", call_outcome: "missed", ringing_duration_seconds: 50 },
};
assert.equal(getCallEventPresentation(missedCallMessage, "caller")?.detail, "Voice call was not answered · Rang for 50s");

assert.equal(getCallViewState(baseCall, {
  isInitiator: true,
  remoteParticipants: [remote],
  peerState: "new",
  ringingSeconds: 4,
}).label, "Ringing…");

assert.equal(getCallViewState({ ...baseCall, status: "ongoing" }, {
  isInitiator: false,
  remoteParticipants: [remote],
  peerState: "connected",
  ringingSeconds: 0,
}).label, "Connected");

assert.equal(getCallViewState({ ...baseCall, status: "declined" }, {
  isInitiator: true,
  remoteParticipants: [remote],
  peerState: "closed",
  ringingSeconds: 10,
}).tone, "danger");

const bounds = calculateFloatingVideoBounds({
  stageWidth: 1280,
  stageHeight: 720,
  floatingWidth: 224,
  floatingHeight: 299,
});
assert.deepEqual(bounds, { minX: 12, minY: 76, maxX: 1044, maxY: 323 });

const compactBounds = calculateFloatingVideoBounds({
  stageWidth: 390,
  stageHeight: 844,
  floatingWidth: 120,
  floatingHeight: 160,
});
assert.deepEqual(compactBounds, { minX: 12, minY: 64, maxX: 258, maxY: 598 });

const relative = { x: 0.72, y: 0.25 };
const position = positionFromRelative(relative, compactBounds);
const roundTrip = relativeFromPosition(position, compactBounds);
assert.ok(Math.abs(roundTrip.x - relative.x) < 0.000001);
assert.ok(Math.abs(roundTrip.y - relative.y) < 0.000001);
assert.equal(clampCallValue(20, 0, 10), 10);
assert.equal(clampCallValue(-2, 0, 10), 0);

const frontCamera = { deviceId: "front", label: "Front Camera" };
const rearCamera = { deviceId: "rear", label: "Back Camera" };
assert.equal(findPreferredCameraDevice([frontCamera, rearCamera], "environment", "front")?.deviceId, "rear");
assert.equal(findPreferredCameraDevice([frontCamera, rearCamera], "user", "rear")?.deviceId, "front");
assert.equal(findPreferredCameraDevice([{ deviceId: "one", label: "" }, { deviceId: "two", label: "" }], "environment", ""), null);
assert.equal(cameraFacingFromTrack({ label: "Rear camera", getSettings: () => ({}) }), "environment");
assert.equal(cameraFacingFromTrack({ label: "", getSettings: () => ({ facingMode: "user" }) }), "user");
assert.equal(supportsMobileCameraSwitch({ facingModeSupported: true, maxTouchPoints: 5, userAgent: "" }), true);
assert.equal(supportsMobileCameraSwitch({ facingModeSupported: true, maxTouchPoints: 0, userAgent: "Desktop" }), false);

assert.deepEqual(resolveVideoSenderProfile({ mode: "standard", videoActive: true, compact: false }), {
  active: true,
  maxBitrate: undefined,
  maxFramerate: undefined,
  scaleResolutionDownBy: 1,
  reduced: false,
});
assert.deepEqual(resolveVideoSenderProfile({ mode: "standard", videoActive: true, compact: true }), {
  active: true,
  maxBitrate: undefined,
  maxFramerate: undefined,
  scaleResolutionDownBy: 1,
  reduced: false,
});
assert.deepEqual(resolveVideoSenderProfile({ mode: "standard", videoActive: true, compact: false, remotePreferredVideoQuality: "low" }), {
  active: true,
  maxBitrate: 250_000,
  maxFramerate: 12,
  scaleResolutionDownBy: 2,
  reduced: true,
});
assert.deepEqual(resolveVideoSenderProfile({ mode: "low_bandwidth_video", videoActive: true, compact: false }), {
  active: true,
  maxBitrate: 250_000,
  maxFramerate: 12,
  scaleResolutionDownBy: 2,
  reduced: true,
});
assert.deepEqual(resolveVideoSenderProfile({ mode: "audio_only", videoActive: true, compact: true }), {
  active: true,
  maxBitrate: 40_000,
  maxFramerate: 4,
  scaleResolutionDownBy: 4,
  reduced: true,
});


const me = { id: "1", username: "me" };
const historyCall = {
  ...baseCall,
  conversation: "conversation-1",
  initiated_by: me,
  participants: [
    { id: "self", state: "joined", user: me },
    remote,
  ],
};
assert.equal(callDirection(historyCall, me), "outgoing");
assert.equal(callPeerLabel(historyCall, me), "Sarah Ahmed");
assert.equal(callDestination(historyCall, me), "/calls/call-1");
assert.equal(callStatusPresentation({ ...historyCall, status: "missed" }, me).label, "No answer");
assert.equal(callStatusPresentation({ ...historyCall, status: "ended", duration_seconds: 125 }, me).label, "Completed · 2:05");
assert.equal(callDestination({ ...historyCall, status: "ended" }, me), "/chat/conversation-1");
assert.equal(findActiveCallForConversation([historyCall], "conversation-1", me)?.id, "call-1");
assert.equal(formatCallDuration(3661), "1:01:01");

rmSync(new URL("../.call-test-build", import.meta.url), { recursive: true, force: true });
console.log("Call UI core tests passed.");
