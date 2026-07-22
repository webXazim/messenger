import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const config = readFileSync(new URL("../src/lib/config.ts", import.meta.url), "utf8");
const chatApi = readFileSync(new URL("../src/api/chat.ts", import.meta.url), "utf8");
const appShell = readFileSync(new URL("../src/components/AppShell.tsx", import.meta.url), "utf8");
const dockerfile = readFileSync(new URL("../Dockerfile", import.meta.url), "utf8");

assert.match(config, /VITE_CHAT_CONVERSATION_COMMAND_BACKEND/);
assert.match(config, /CHAT_CONVERSATION_COMMAND_BACKEND/);
assert.match(chatApi, /conversationCommandPath/);
assert.match(chatApi, /createDirectConversation/);
assert.match(chatApi, /createGroupConversation/);
assert.match(chatApi, /getConversationDraft/);
assert.match(chatApi, /saveConversationDraft/);
assert.match(chatApi, /transferGroupOwnership/);
assert.match(chatApi, /CHAT_CONVERSATION_COMMAND_BACKEND === "axum"/);
assert.match(chatApi, /django_fallback_required/); // group creation only, while centralized billing is synchronous
for (const method of ["toggleConversationArchive", "removeGroupParticipant", "banGroupParticipant", "leaveConversation"]) {
  const start = chatApi.indexOf(`async ${method}`);
  const end = chatApi.indexOf("\n  async ", start + 10);
  const block = chatApi.slice(start, end < 0 ? undefined : end);
  assert.match(block, /conversationCommandPath/);
  assert.doesNotMatch(block, /django_fallback_required/);
  assert.doesNotMatch(block, /catch \(error/);
}
assert.match(appShell, /conversation\.participants_added/);
assert.match(appShell, /user\.blocked/);
assert.match(dockerfile, /ARG VITE_CHAT_CONVERSATION_COMMAND_BACKEND/);
assert.match(dockerfile, /ENV VITE_CHAT_CONVERSATION_COMMAND_BACKEND/);

console.log("Conversation command backend source checks passed.");
