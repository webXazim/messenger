import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const app = read("src/App.tsx");
const navigation = read("src/components/navigation/MessengerNavigation.tsx");
const navigationCss = read("src/styles/components/navigation.css");
const styles = read("src/styles/index.css");
const conversations = read("src/pages/ConversationsPage.tsx");
const modal = read("src/components/NewConversationModal.tsx");
const row = read("src/components/conversations/ConversationRow.tsx");
const preferences = read("src/hooks/useConversationListPreferences.ts");
const chatApi = read("src/api/chat.ts");
const details = read("src/pages/ConversationPage.tsx");
const prefetch = read("src/lib/conversationPrefetch.ts");

assert.ok(!app.includes("SavedPage"), "Unfinished Saved page is still imported.");
assert.ok(app.includes('<Route path="saved" element={<Navigate to="/chat" replace />} />'), "Legacy Saved URLs are not redirected safely.");
assert.ok(!navigation.includes('/saved'), "Saved is still exposed in production navigation.");
assert.ok(navigationCss.includes("repeat(5, minmax(0, 1fr))"), "Mobile navigation is not balanced for five destinations.");
assert.ok(!styles.includes("saved.css"), "Removed Saved styles are still bundled.");
assert.equal(existsSync(new URL("../src/pages/SavedPage.tsx", import.meta.url)), false, "Saved page source still exists.");
assert.ok(conversations.includes("NewConversationModal"), "Chats does not use the focused private-chat picker.");
assert.ok(!conversations.includes('navigate("/friends")'), "New conversation still sends users to the full Contacts workspace.");
assert.ok(modal.includes("Search by name or username"), "Focused user search is missing.");
assert.ok(modal.includes("useModalAccessibility"), "New-conversation dialog does not use the shared focus trap and restoration hook.");
assert.ok(preferences.includes("sessionStorage"), "Chat search/filter state is not preserved during navigation.");
for (const state of ["is_pinned", "is_muted", "is_archived", "is_blocked"]) {
  assert.ok(chatApi.includes(state), `Participant state is not normalized: ${state}`);
}
for (const label of ["Pinned", "Muted", "Archived", "End-to-end encrypted"]) {
  assert.ok(row.includes(label), `Chat row does not expose state: ${label}`);
}
assert.ok(details.includes("conversationStatePending"), "Conversation state actions can still be double-submitted.");
assert.ok(details.includes("patchConversationViewerState"), "Pin/mute/archive changes do not update the inbox immediately.");
assert.ok(details.includes("readyConversationIds"), "Previously opened chats lose their ready state while switching conversations.");
assert.ok(row.includes("onPointerEnter") && row.includes("onFocus"), "Chat rows do not warm conversation data before navigation.");
assert.ok(prefetch.includes("prefetchInfiniteQuery") && prefetch.includes('["messages", conversationId]'), "Message history is not prefetched before opening a chat.");

console.log("Navigation source regression checks passed.");
