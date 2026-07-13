import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const controller = read("src/pages/CallRoomPage.tsx");
const audioScreen = read("src/components/call/AudioCallScreen.tsx");
const videoScreen = read("src/components/call/VideoCallScreen.tsx");
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

console.log("Call source regression checks passed.");
