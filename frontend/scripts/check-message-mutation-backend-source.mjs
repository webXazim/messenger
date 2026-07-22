import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const config = readFileSync(new URL("../src/lib/config.ts", import.meta.url), "utf8");
const chatApi = readFileSync(new URL("../src/api/chat.ts", import.meta.url), "utf8");
const conversation = readFileSync(new URL("../src/pages/ConversationPage.tsx", import.meta.url), "utf8");
const actions = readFileSync(new URL("../src/components/messages/MessageActions.tsx", import.meta.url), "utf8");
const types = readFileSync(new URL("../src/types/chat.ts", import.meta.url), "utf8");

assert.match(config, /VITE_CHAT_MESSAGE_MUTATION_BACKEND/);
assert.match(config, /CHAT_MESSAGE_MUTATION_BACKEND/);
assert.match(chatApi, /function messageMutationPath/);
for (const operation of ["editMessage", "deleteMessage", "restoreMessage", "retryMessage"]) {
  assert.ok(chatApi.includes(`async ${operation}`), `missing ${operation}`);
}
assert.ok(
  (chatApi.match(/messageMutationPath\(/g) || []).length >= 5,
  "edit, delete, restore, and retry must use the independent message mutation backend",
);
for (const event of ["message.updated", "message.restored", "message.retried"]) {
  assert.ok(conversation.includes(event), `missing realtime ${event} handling`);
}
assert.match(conversation, /handleRestore/);
assert.match(actions, /message\.can_restore !== false/);
assert.match(types, /can_restore\?: boolean/);

console.log("Message mutation backend source checks passed.");
