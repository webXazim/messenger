import assert from "node:assert/strict";
import { rmSync } from "node:fs";
import {
  flattenMessagePages,
  mapMessagePages,
  markMessageDeletedPages,
  mergeMessageContextPages,
  removeMessagePages,
  upsertMessagePages,
} from "../.timeline-test-build/lib/messageTimeline.js";

const user = { id: "user-1", username: "user", display_name: "User" };
const makeMessage = (id, createdAt, patch = {}) => ({
  id,
  type: "text",
  text: id,
  sender: user,
  created_at: createdAt,
  attachments: [],
  reactions: [],
  reaction_summary: {},
  ...patch,
});

const optimistic = makeMessage("temp-client-1", "2026-07-13T10:00:00Z", {
  client_temp_id: "client-1",
  delivery_status: "sending",
});
const older = makeMessage("older", "2026-07-12T10:00:00Z");
const oldest = makeMessage("oldest", "2026-07-11T10:00:00Z");
const data = {
  pages: [
    { results: [optimistic, older], next: "/messages/?cursor=older", previous: null },
    { results: [oldest], next: null, previous: "/messages/?cursor=newer" },
  ],
  pageParams: [null, "/messages/?cursor=older"],
};

const confirmed = makeMessage("server-1", "2026-07-13T10:00:00Z", {
  client_temp_id: "client-1",
  delivery_status: "sent",
});
const confirmedData = upsertMessagePages(data, confirmed);
assert.equal(confirmedData.pages.length, 2, "Realtime updates must preserve page boundaries.");
assert.equal(confirmedData.pages[0].results.length, 2, "Optimistic replacement must not add a duplicate.");
assert.equal(confirmedData.pages[0].results[0].id, "server-1");
assert.equal(confirmedData.pages[1].results[0].id, "oldest");
assert.equal(flattenMessagePages(confirmedData).filter((message) => message.client_temp_id === "client-1").length, 1);

const failedData = mapMessagePages(
  confirmedData,
  (message) => message.id === "server-1",
  (message) => ({ ...message, delivery_status: "failed", failed_reason: "Network unavailable" }),
);
assert.equal(failedData.pages[0].results[0].delivery_status, "failed");
assert.equal(failedData.pages[0].results[1].delivery_status, undefined);

const deletedData = markMessageDeletedPages(
  upsertMessagePages(failedData, makeMessage("media", "2026-07-13T09:00:00Z", {
    text: "private",
    attachments: [{ id: "file-1", original_name: "private.png", mime_type: "image/png", size: 10 }],
    links: ["https://example.com"],
    is_encrypted: true,
    encryption: { algorithm: "AES-GCM", ciphertext: "secret", nonce: "nonce", sender_key_id: "key" },
    decryption_state: "ready",
    decryption_message: "opened",
  })),
  "media",
);
const deleted = flattenMessagePages(deletedData).find((message) => message.id === "media");
assert.equal(deleted?.is_deleted, true);
assert.equal(deleted?.text, "");
assert.deepEqual(deleted?.attachments, []);
assert.deepEqual(deleted?.links, []);
assert.equal(deleted?.is_encrypted, false);
assert.equal(deleted?.encryption, null);
assert.equal(deleted?.decryption_state, undefined);

const contextData = mergeMessageContextPages(deletedData, [
  makeMessage("context-before", "2026-07-01T10:00:00Z"),
  oldest,
  makeMessage("context-target", "2026-07-02T10:00:00Z"),
]);
const flattenedContext = flattenMessagePages(contextData);
assert.equal(flattenedContext.filter((message) => message.id === "oldest").length, 1, "Context merge must deduplicate already loaded messages.");
assert.ok(flattenedContext.some((message) => message.id === "context-target"));


const removedData = removeMessagePages(contextData, (message) => message.id === "context-target");
assert.ok(!flattenMessagePages(removedData).some((message) => message.id === "context-target"), "Local failed messages must be removable without refetching the timeline.");

const emptyData = upsertMessagePages(undefined, makeMessage("first", "2026-07-13T12:00:00Z"));
assert.equal(emptyData.pages[0].results[0].id, "first", "Optimistic sends must work before the first page settles.");

rmSync(new URL("../.timeline-test-build", import.meta.url), { recursive: true, force: true });
console.log("Message timeline core tests passed.");
