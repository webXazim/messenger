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
  callDestination,
  callDirection,
  callPeerLabel,
  callStatusPresentation,
  findActiveCallForConversation,
  formatCallDuration,
} from "../.call-test-build/lib/callLifecycle.js";

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
