import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const inbox = read("src/components/support/SupportInbox.tsx");
const supportPage = read("src/pages/SupportChatPage.tsx");
const api = read("src/api/support.ts");
const styles = read("src/styles/pages/support.css");

// Frozen Inbox frame contract.
for (const token of [
  "ms-support-inbox__list",
  "ms-support-conversation-view",
  "ms-support-details-content",
  "MessageComposer",
  "ChatHeader",
]) assert.ok(inbox.includes(token), `Support Inbox baseline is missing ${token}`);

// Support remains its own product surface and API client.
assert.ok(supportPage.includes("SupportInbox"));
assert.ok(api.includes("support"));
assert.ok(styles.includes("ms-support"));
assert.ok(!inbox.includes("MessengerNavigation"), "Support Inbox must not embed Messenger navigation");

console.log("Support frontend baseline source checks passed.");
