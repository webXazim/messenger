import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const config = readFileSync(new URL("../src/lib/config.ts", import.meta.url), "utf8");
const chatApi = readFileSync(new URL("../src/api/chat.ts", import.meta.url), "utf8");

assert.match(config, /VITE_CHAT_INTERACTION_BACKEND/);
assert.match(config, /CHAT_INTERACTION_BACKEND/);
assert.match(config, /: CHAT_COMMAND_BACKEND;/);

for (const route of ["mark-delivered", "mark-read", "reactions"]) {
  assert.ok(chatApi.includes(route), `missing ${route} route`);
}
assert.ok(
  (chatApi.match(/CHAT_INTERACTION_BACKEND === "axum"/g) || []).length >= 4,
  "receipt and reaction methods must use the independent interaction backend",
);
assert.match(chatApi, /CHAT_COMMAND_URL : "\/chat"/);

console.log("Message interaction backend source checks passed.");
