import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const config = readFileSync(new URL("../src/lib/config.ts", import.meta.url), "utf8");
const chatApi = readFileSync(new URL("../src/api/chat.ts", import.meta.url), "utf8");
const dockerfile = readFileSync(new URL("../Dockerfile", import.meta.url), "utf8");
const viteEnv = readFileSync(new URL("../src/vite-env.d.ts", import.meta.url), "utf8");

assert.match(config, /VITE_CHAT_CALL_RUNTIME_BACKEND/);
assert.match(config, /CHAT_CALL_RUNTIME_BACKEND/);
assert.match(dockerfile, /ARG VITE_CHAT_CALL_RUNTIME_BACKEND/);
assert.match(viteEnv, /VITE_CHAT_CALL_RUNTIME_BACKEND/);
for (const operation of [
  "listCalls", "startCall", "getCall", "acceptCall", "declineCall", "endCall",
  "sendCallSignal", "updateCallMediaState", "sendCallHeartbeat", "getCallOrchestration",
  "getCallDiagnostics", "sendCallQualityReport", "updateCallSpeakerState",
]) {
  assert.ok(chatApi.includes(`async ${operation}`), `missing ${operation}`);
}
assert.ok(
  (chatApi.match(/CHAT_CALL_RUNTIME_BACKEND === "axum"/g) || []).length >= 13,
  "all call lifecycle and runtime operations must use the isolated call selector",
);
assert.match(chatApi, /getTurnCredentials\(\)[\s\S]*?\/chat\/calls\/turn-credentials\//);
assert.match(chatApi, /getCallingConfig\(quality\?[\s\S]*?\/chat\/calls\/config\//);

console.log("Call runtime backend source checks passed.");
