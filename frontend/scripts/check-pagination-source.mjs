import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const chatApi = read("src/api/chat.ts");
const authApi = read("src/api/auth.ts");
const friends = read("src/pages/FriendsPage.tsx");
const conversation = read("src/pages/ConversationPage.tsx");
const accountsViews = read("../apps/accounts/api/views.py");

for (const required of [
  'collectChatPages("/chat/conversations/"',
  'collectChatPages("/chat/calls/recent/"',
  'collectChatPages(`/chat/conversations/${conversationId}/media/`',
  'collectChatPages("/chat/e2ee/devices/"',
  'collectChatPages("/chat/blocks/"',
  'collectChatPages("/chat/devices/"',
]) {
  assert.ok(chatApi.includes(required), `Missing paginated chat collection: ${required}`);
}

for (const required of [
  "collectAuthPages(centralPath(\"/accounts/sessions/\")",
  "paginated: 1",
  "collectCursorPages<FriendRequest>",
]) {
  assert.ok(authApi.includes(required), `Missing paginated account collection: ${required}`);
}

assert.ok(friends.includes("useDebouncedValue"), "Contact search is not debounced.");
assert.ok(friends.includes("placeholderData: (previous) => previous"), "Search results disappear while the next query loads.");
assert.ok(conversation.includes("pageParam, signal"), "Message pagination requests are not cancellable.");
assert.ok(accountsViews.includes("class UserSearchCursorPagination"), "User search has no cursor paginator.");
assert.ok(accountsViews.includes('request.query_params.get("paginated")'), "User-search pagination is not opt-in for compatibility.");
assert.ok(accountsViews.includes("users = list(queryset[:30])"), "Legacy user-search array response was not preserved.");

console.log("Pagination source regression checks passed.");
