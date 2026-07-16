import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const controller = read("src/pages/CallRoomPage.tsx");
const audioScreen = read("src/components/call/AudioCallScreen.tsx");
const videoScreen = read("src/components/call/VideoCallScreen.tsx");
const videoStage = read("src/components/call/VideoCallStage.tsx");
const floatingVideo = read("src/components/call/FloatingLocalVideo.tsx");
const videoCss = read("src/styles/components/video-call.css");
const audioCss = read("src/styles/pages/call-room.css");

for (const required of [
  "useCallWakeLock",
  "peerRef.current?.close()",
  "localStreamRef.current?.getTracks().forEach((track) => track.stop())",
  "remoteVideoStreamRef.current?.getTracks().forEach((track) => track.stop())",
  "remoteAudioStreamRef.current?.getTracks().forEach((track) => track.stop())",
  "AudioCallScreen",
  "VideoCallScreen",
  "facingMode: targetFacing",
  "relaxedFacingMode: true",
  "releaseCurrentBeforeAcquire: true",
  "videoActive = videoEnabled",
]) {
  assert.ok(controller.includes(required), `Missing call-controller invariant: ${required}`);
}

for (const forbidden of ["Call ID:", "remote tracks", "socket state", "Signal log", "Media plan"]) {
  assert.ok(!audioScreen.includes(forbidden), `Technical detail leaked into audio-call UI: ${forbidden}`);
  assert.ok(!videoScreen.includes(forbidden), `Technical detail leaked into video-call UI: ${forbidden}`);
}

for (const required of [
  "onToggleAudio",
  "onToggleVideo",
  "onSwitchCamera",
  "Turn camera on and switch",
  "onHangup",
  "requestFullscreen",
  "CallParticipantsDrawer",
]) {
  assert.ok(videoScreen.includes(required), `Missing video-call behavior: ${required}`);
}

assert.ok(videoCss.includes("env(safe-area-inset-bottom)"), "Video controls must respect mobile safe areas.");
assert.ok(videoCss.includes("@media (max-width: 720px)"), "Tablet/mobile video breakpoint is missing.");
assert.ok(videoCss.includes("@media (max-width: 560px)"), "Narrow mobile video breakpoint is missing.");
assert.ok(audioCss.includes("env(safe-area-inset-bottom"), "Audio controls must respect mobile safe areas.");
assert.equal((videoStage.match(/onActivate=/g) || []).length, 1, "Only the floating picture-in-picture tile may swap the main video.");
assert.ok(videoStage.includes("<FloatingLocalVideo") && videoStage.includes("swapVideos();"), "The floating video cannot switch the main video.");
assert.ok(floatingVideo.includes("setPointerCapture") && floatingVideo.includes("onLostPointerCapture"), "The floating video is not safely draggable across the full call screen.");
assert.ok(floatingVideo.includes("allowActivation && !drag.moved"), "Dragging or cancelling the floating video can accidentally switch videos.");

console.log("Call source regression checks passed.");
