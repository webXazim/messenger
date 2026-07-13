import assert from "node:assert/strict";
import { rmSync } from "node:fs";
import { unwrapCursorPage } from "../.pagination-test-build/lib/apiResponse.js";
import { collectCursorPages, resolveApiCursorUrl } from "../.pagination-test-build/lib/pagination.js";

assert.deepEqual(unwrapCursorPage([{ id: "1" }]), {
  results: [{ id: "1" }],
  next: null,
  previous: null,
});
assert.deepEqual(unwrapCursorPage({ results: [{ id: "1" }], next: "/api/v1/items/?cursor=2", previous: null }), {
  results: [{ id: "1" }],
  next: "/api/v1/items/?cursor=2",
  previous: null,
});
assert.deepEqual(unwrapCursorPage({ data: { results: [{ id: "2" }], next: null } }), {
  results: [{ id: "2" }],
  next: null,
  previous: null,
});

assert.equal(
  resolveApiCursorUrl("/chat/conversations/", "https://messenger.example.com/api/v1"),
  "https://messenger.example.com/api/v1/chat/conversations/",
);
assert.equal(
  resolveApiCursorUrl("/api/v1/chat/conversations/?cursor=abc", "https://messenger.example.com/api/v1"),
  "https://messenger.example.com/api/v1/chat/conversations/?cursor=abc",
);
assert.equal(
  resolveApiCursorUrl("http://nginx/api/v1/chat/conversations/?cursor=abc", "https://messenger.example.com/api/v1"),
  "https://messenger.example.com/api/v1/chat/conversations/?cursor=abc",
);

const fetched = [];
const items = await collectCursorPages(
  "/items/",
  async (url) => {
    fetched.push(url);
    if (fetched.length === 1) {
      return { results: [{ id: "1", value: "first" }, { id: "2", value: "old" }], next: "/api/v1/items/?cursor=2", previous: null };
    }
    return { results: [{ id: "2", value: "new" }, { id: "3", value: "last" }], next: null, previous: null };
  },
  { baseUrl: "https://messenger.example.com/api/v1", getKey: (item) => item.id },
);
assert.deepEqual(items, [
  { id: "1", value: "first" },
  { id: "2", value: "new" },
  { id: "3", value: "last" },
]);
assert.equal(fetched.length, 2);

const relativeFetches = [];
await collectCursorPages(
  "/items/",
  async (url) => {
    relativeFetches.push(url);
    return relativeFetches.length === 1
      ? { results: [], next: "?cursor=relative", previous: null }
      : { results: [], next: null, previous: null };
  },
  { baseUrl: "https://messenger.example.com/api/v1" },
);
assert.equal(relativeFetches[1], "https://messenger.example.com/api/v1/items/?cursor=relative");

await assert.rejects(
  collectCursorPages(
    "/loop/",
    async () => ({ results: [], next: "/loop/", previous: null }),
    { baseUrl: "https://messenger.example.com/api/v1" },
  ),
  /repeated pagination cursor/i,
);

rmSync(new URL("../.pagination-test-build", import.meta.url), { recursive: true, force: true });
console.log("Pagination core tests passed.");
