import assert from "node:assert/strict";
import { rmSync } from "node:fs";
import {
  advanceMessageReceiptPages,
  compareTimelineMessages,
  flattenMessagePages,
  mapMessagePages,
  markMessageDeletedPages,
  mergeMessageContextPages,
  removeMessagePages,
  upsertMessagePages,
} from "../.timeline-test-build/lib/messageTimeline.js";
import { mergeConversationReceipts, mergeParticipantReceipts } from "../.timeline-test-build/lib/messageReceipts.js";
import { createSerializedTaskQueue } from "../.timeline-test-build/lib/serializedTaskQueue.js";
import {
  TYPING_MIN_VISIBLE_MS,
  typingRemovalDelay,
} from "../.timeline-test-build/lib/typingPresence.js";

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

const confirmed = makeMessage("server-1", "2026-07-13T10:00:05Z", {
  client_temp_id: "client-1",
  delivery_status: "sent",
});
const confirmedData = upsertMessagePages(data, confirmed);
assert.equal(confirmedData.pages.length, 2, "Realtime updates must preserve page boundaries.");
assert.equal(confirmedData.pages[0].results.length, 2, "Optimistic replacement must not add a duplicate.");
assert.equal(confirmedData.pages[0].results[0].id, "server-1");
assert.equal(confirmedData.pages[0].results[0].created_at, optimistic.created_at, "Server confirmation must preserve the optimistic timeline position.");
assert.equal(confirmedData.pages[1].results[0].id, "oldest");
assert.equal(flattenMessagePages(confirmedData).filter((message) => message.client_temp_id === "client-1").length, 1);

const readData = advanceMessageReceiptPages(confirmedData, "server-1", "read", "user-1");
assert.equal(readData.pages[0].results[0].delivery_status, "read", "A live read receipt must update the message immediately.");
const staleSentData = upsertMessagePages(readData, confirmed);
assert.equal(staleSentData.pages[0].results[0].delivery_status, "read", "A late sent payload must not downgrade a read receipt.");

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

const sameTimestamp = "2026-07-13T12:01:00.000Z";
const rapidFirst = makeMessage("z-random-id", sameTimestamp);
const rapidSecond = makeMessage("a-random-id", sameTimestamp);
assert.deepEqual(
  [rapidFirst, rapidSecond].sort(compareTimelineMessages).map((message) => message.id),
  ["z-random-id", "a-random-id"],
  "Equal server timestamps must preserve arrival order instead of sorting by random UUID.",
);
const rapidPages = upsertMessagePages(
  upsertMessagePages(undefined, rapidFirst),
  rapidSecond,
);
assert.deepEqual(
  rapidPages.pages[0].results.map((message) => message.id),
  ["z-random-id", "a-random-id"],
  "New rapid messages must append in arrival order.",
);

const serialQueue = createSerializedTaskQueue();
const serialOrder = [];
let releaseFirst;
const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
const firstTask = serialQueue.enqueue(async () => {
  serialOrder.push("first:start");
  await firstGate;
  serialOrder.push("first:end");
  return "first";
});
const secondTask = serialQueue.enqueue(async () => {
  serialOrder.push("second:start");
  return "second";
});
await new Promise((resolve) => setImmediate(resolve));
assert.deepEqual(serialOrder, ["first:start"], "A later send must not begin while the earlier send is preparing.");
releaseFirst();
assert.deepEqual(await Promise.all([firstTask, secondTask]), ["first", "second"]);
assert.deepEqual(serialOrder, ["first:start", "first:end", "second:start"]);
await assert.rejects(serialQueue.enqueue(async () => { throw new Error("expected"); }), /expected/);
assert.equal(
  await serialQueue.enqueue(async () => "after-failure"),
  "after-failure",
  "One failed send must not permanently block the queue.",
);

assert.equal(
  typingRemovalDelay(1_000, 100, 1_200),
  TYPING_MIN_VISIBLE_MS - 200,
  "Typing presence must stay visible for its minimum display window.",
);
assert.equal(
  typingRemovalDelay(1_000, 420, 2_000),
  420,
  "Typing stop events must retain their smoothing grace after the minimum window.",
);

const participant = {
  id: "participant-1",
  role: "member",
  user,
  last_delivered_message: "new-delivered",
  last_delivered_at: "2026-07-13T12:02:00Z",
  last_read_message: "new-read",
  last_read_at: "2026-07-13T12:03:00Z",
};
const staleReceipt = mergeParticipantReceipts(participant, {
  last_delivered_message: "old-delivered",
  last_delivered_at: "2026-07-13T12:00:00Z",
  last_read_message: "old-read",
  last_read_at: "2026-07-13T12:01:00Z",
});
assert.equal(staleReceipt.last_delivered_message, "new-delivered", "Delivered receipts must never move backwards.");
assert.equal(staleReceipt.last_read_message, "new-read", "Read receipts must never move backwards.");

const baseConversation = { id: "conversation-1", type: "direct", title: "", unread_count: 0, participants: [participant] };
const staleConversation = {
  ...baseConversation,
  participants: [{ ...participant, last_read_message: "old-read", last_read_at: "2026-07-13T12:01:00Z" }],
};
assert.equal(
  mergeConversationReceipts(baseConversation, staleConversation).participants[0].last_read_message,
  "new-read",
  "A stale conversation refresh must not downgrade a live receipt.",
);

rmSync(new URL("../.timeline-test-build", import.meta.url), { recursive: true, force: true });
console.log("Message timeline core tests passed.");
