import fs from "node:fs";

const source = fs.readFileSync(new URL("../src/api/chat.ts", import.meta.url), "utf8");
const sendStart = source.indexOf("  async sendMessage(");
const sendEnd = source.indexOf("\n  async getAttachment(", sendStart);
if (sendStart < 0 || sendEnd < 0) throw new Error("Could not locate chatApi.sendMessage");
const sendBlock = source.slice(sendStart, sendEnd);

for (const forbidden of ["isDjangoFallback", "django_fallback_required", "catch ("]) {
  if (sendBlock.includes(forbidden)) {
    throw new Error(`Normal message sending still contains automatic Django fallback logic: ${forbidden}`);
  }
}
for (const required of ["CHAT_ATTACHMENT_BACKEND === \"axum\"", "/attachment-messages/", "CHAT_COMMAND_BACKEND === \"axum\"", "/messages/"]) {
  if (!sendBlock.includes(required)) throw new Error(`Universal message routing is missing: ${required}`);
}

const groupStart = source.indexOf("  async createGroupConversation(");
const groupEnd = source.indexOf("\n  async checkGroupNameAvailability(", groupStart);
const groupBlock = source.slice(groupStart, groupEnd);
if (!groupBlock.includes("isDjangoFallback")) {
  throw new Error("The deliberate centralized-billing group-create fallback was removed unexpectedly");
}

console.log("Axum hot-path frontend source checks passed.");
