import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const config = readFileSync(new URL("../src/lib/config.ts", import.meta.url), "utf8");
const chatApi = readFileSync(new URL("../src/api/chat.ts", import.meta.url), "utf8");
const media = readFileSync(new URL("../src/components/AuthenticatedMedia.tsx", import.meta.url), "utf8");
const dockerfile = readFileSync(new URL("../Dockerfile", import.meta.url), "utf8");
const sendBlock = chatApi.slice(chatApi.indexOf("async sendMessage"), chatApi.indexOf("async getAttachment"));

assert.match(config, /VITE_CHAT_ATTACHMENT_BACKEND/);
assert.match(config, /CHAT_ATTACHMENT_BACKEND/);
assert.match(chatApi, /attachment-messages/);
assert.doesNotMatch(sendBlock, /django_fallback_required/);
assert.doesNotMatch(sendBlock, /catch \(error/);
assert.match(chatApi, /async getAttachment/);
assert.match(chatApi, /CHAT_ATTACHMENT_BACKEND === "axum"/);
assert.match(media, /attachments\/\$\{attachmentId\}\/media-token/);
assert.match(media, /CHAT_ATTACHMENT_BACKEND === "axum"/);
assert.match(dockerfile, /ARG VITE_CHAT_ATTACHMENT_BACKEND/);
assert.match(dockerfile, /ENV VITE_CHAT_ATTACHMENT_BACKEND/);

console.log("Attachment backend source checks passed.");
