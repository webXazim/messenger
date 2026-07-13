import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const app = read("src/App.tsx");
const appShell = read("src/components/AppShell.tsx");
const routeAccessibility = read("src/components/RouteAccessibility.tsx");
const modalHook = read("src/hooks/useModalAccessibility.ts");
const accessibilityCss = read("src/styles/foundation/accessibility.css");
const styles = read("src/styles/index.css");
const html = read("index.html");
const contacts = read("src/pages/FriendsPage.tsx");
const conversation = read("src/pages/ConversationPage.tsx");
const composer = read("src/components/MessageComposer.tsx");
const messageActions = read("src/components/messages/MessageActions.tsx");
const confirmDialog = read("src/components/ConfirmDialog.tsx");
const newConversation = read("src/components/NewConversationModal.tsx");
const groupModal = read("src/components/GroupChatModal.tsx");
const mediaModal = read("src/components/MediaPreviewModal.tsx");
const callParticipants = read("src/components/call/CallParticipantsDrawer.tsx");

assert.ok(app.includes("RouteAccessibility"), "Route announcements are not mounted.");
assert.ok(appShell.includes('className="ms-skip-link"'), "Authenticated pages are missing a skip link.");
assert.ok(appShell.includes('id="main-content"'), "Authenticated pages are missing a main-content target.");
assert.ok(routeAccessibility.includes("document.title"), "Page titles are not updated on navigation.");
assert.ok(routeAccessibility.includes('aria-live="polite"'), "Route changes are not announced.");
assert.ok(modalHook.includes('event.key !== "Tab"'), "Shared modal focus trapping is missing.");
assert.ok(modalHook.includes('event.key === "Escape"'), "Shared modal Escape handling is missing.");
assert.ok(modalHook.includes("previousFocusRef"), "Shared modal focus restoration is missing.");
assert.ok(modalHook.includes("lockDocumentScroll"), "Open dialogs do not lock background scrolling.");
for (const [name, source] of [
  ["confirm dialog", confirmDialog],
  ["new conversation dialog", newConversation],
  ["group dialog", groupModal],
  ["media preview", mediaModal],
  ["call participant drawer", callParticipants],
]) {
  assert.ok(source.includes("useModalAccessibility"), `${name} does not use shared modal accessibility behavior.`);
}
assert.ok(styles.trimEnd().endsWith('@import "./foundation/accessibility.css";'), "Accessibility overrides must load last.");
assert.ok(accessibilityCss.includes("prefers-reduced-motion: reduce"), "Reduced-motion preferences are not respected.");
assert.ok(accessibilityCss.includes("forced-colors: active"), "Forced-colors support is missing.");
assert.ok(accessibilityCss.includes("pointer: coarse"), "Touch target adaptations are missing.");
assert.ok(accessibilityCss.includes(":focus-visible"), "Visible keyboard focus styling is missing.");
assert.ok(html.includes("viewport-fit=cover"), "Safe-area viewport support is missing.");
assert.ok(html.includes("interactive-widget=resizes-content"), "Virtual-keyboard viewport resizing is not configured.");
assert.ok(contacts.includes('role="tablist"'), "Contacts navigation is not exposed as tabs.");
assert.ok(contacts.includes('event.key === "ArrowRight"'), "Contacts tabs do not support arrow-key navigation.");
assert.ok(contacts.includes('role="tabpanel"'), "Contacts tab panels are missing semantics.");
assert.ok(conversation.includes('aria-keyshortcuts="ArrowLeft ArrowRight Home End"'), "Desktop panel resizers are not keyboard operable.");
assert.ok(composer.includes('aria-keyshortcuts="Enter"'), "Composer keyboard behavior is not announced.");
assert.ok(messageActions.includes('aria-haspopup="menu"'), "Message action menu semantics are missing.");
assert.ok(messageActions.includes('event.key === "ArrowDown"'), "Message action menu does not support arrow-key navigation.");

const nestedMainCandidates = [
  read("src/pages/ConversationPage.tsx"),
  read("src/components/call/AudioCallScreen.tsx"),
  read("src/components/call/VideoCallScreen.tsx"),
];
for (const source of nestedMainCandidates) {
  assert.equal(source.includes("<main"), false, "A routed screen contains a nested main landmark.");
}

console.log("Accessibility and responsive source regression checks passed.");
