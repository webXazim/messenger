import assert from "node:assert/strict";
import { rmSync } from "node:fs";
import {
  applyKnownOnlinePresence,
  conversationDisplayName,
  conversationMatchesQuery,
  conversationSnippet,
  conversationViewerParticipant,
  sortConversationsForInbox,
} from "../.navigation-test-build/components/conversations/conversationPresentation.js";
import {
  conversationListEmptyCopy,
  filterConversationsForInbox,
} from "../.navigation-test-build/components/conversations/conversationFiltering.js";

const me = { id: "me", username: "owner", display_name: "Current User" };
const amina = { id: "u-2", username: "amina", display_name: "Amina Noor" };
const ben = { id: "u-3", username: "ben", display_name: "Ben Ali" };

const makeConversation = ({
  id,
  peer,
  at,
  unread = 0,
  pinned = false,
  archived = false,
  muted = false,
  type = "direct",
  title = "",
}) => ({
  id,
  type,
  title,
  unread_count: unread,
  participants: [
    { id: `${id}-me`, role: "owner", user: me, is_pinned: pinned, is_archived: archived, is_muted: muted },
    { id: `${id}-peer`, role: "member", user: peer },
  ],
  last_message: {
    id: `${id}-message`,
    type: "text",
    text: `Message from ${peer.display_name}`,
    sender: peer,
    created_at: at,
    attachments: [],
  },
  last_message_at: at,
});

const recent = makeConversation({ id: "recent", peer: amina, at: "2026-07-13T12:00:00Z" });
const pinned = makeConversation({ id: "pinned", peer: ben, at: "2026-07-12T12:00:00Z", pinned: true, muted: true });
const archived = makeConversation({ id: "archived", peer: amina, at: "2026-07-14T12:00:00Z", archived: true, unread: 2 });
const group = makeConversation({ id: "group", peer: ben, at: "2026-07-11T12:00:00Z", type: "group", title: "Operations" });
const ringingConversation = {
  ...recent,
  last_message: {
    ...recent.last_message,
    text: "Outgoing call",
    sender: amina,
    call_event: { system_event: "call", call_outcome: "ringing", initiated_by_id: amina.id, summary_text: "Outgoing call" },
  },
};

assert.equal(conversationDisplayName(recent, me.id, me), "Amina Noor", "Direct chats must show the other participant.");
assert.equal(conversationSnippet(ringingConversation, me.id, me), "Incoming call");
assert.equal(conversationSnippet(ringingConversation, amina.id, amina), "You: Outgoing call");
const presenceAware = applyKnownOnlinePresence([recent], [{ ...amina, is_online: true, active_devices: 1 }]);
assert.equal(presenceAware[0].participants[1].user.is_online, true, "An online friend must not appear offline in the conversation row.");
assert.equal(recent.participants[1].user.is_online, undefined, "Presence reconciliation must not mutate cached conversations.");
const staleOnlineConversation = {
  ...recent,
  participants: recent.participants.map((participant) => participant.user.id === amina.id
    ? { ...participant, user: { ...participant.user, is_online: true, presence_status: "active" } }
    : participant),
};
const reconciledOffline = applyKnownOnlinePresence([staleOnlineConversation], [{ ...amina, is_online: false, presence_status: "offline", presence_visibility: "public" }]);
assert.equal(reconciledOffline[0].participants[1].user.is_online, false, "A stale conversation presence must follow the authoritative friend snapshot.");
const reconciledIdle = applyKnownOnlinePresence([recent], [{ ...amina, is_online: true, presence_status: "idle", presence_label: "idle", device_type: "mobile" }]);
assert.equal(reconciledIdle[0].participants[1].user.presence_status, "idle", "Idle state must stay consistent between the online strip and conversation surfaces.");
assert.equal(reconciledIdle[0].participants[1].user.device_type, "mobile", "Device presence must stay consistent between conversation surfaces.");
assert.equal(conversationViewerParticipant(pinned, me.id, me)?.is_muted, true);
assert.equal(conversationMatchesQuery(recent, "amina", me.id, me), true);
assert.equal(conversationMatchesQuery(recent, "unknown", me.id, me), false);
assert.deepEqual(sortConversationsForInbox([recent, pinned], me.id, me).map((item) => item.id), ["pinned", "recent"], "Pinned chats must stay above newer chats.");

const all = filterConversationsForInbox({ conversations: [archived, recent, pinned, group], filter: "all", search: "", currentUserId: me.id, currentUser: me });
assert.deepEqual(all.map((item) => item.id), ["pinned", "recent", "group"], "Archived chats must stay out of the normal inbox.");

const archivedOnly = filterConversationsForInbox({ conversations: [archived, recent], filter: "archived", search: "", currentUserId: me.id, currentUser: me });
assert.deepEqual(archivedOnly.map((item) => item.id), ["archived"]);

const groupsOnly = filterConversationsForInbox({ conversations: [recent, group], filter: "groups", search: "", currentUserId: me.id, currentUser: me });
assert.deepEqual(groupsOnly.map((item) => item.id), ["group"]);

assert.equal(conversationListEmptyCopy("unread", "").title, "You are all caught up");
assert.equal(conversationListEmptyCopy("all", "amina").title, "No matching chats");

rmSync(new URL("../.navigation-test-build", import.meta.url), { recursive: true, force: true });
console.log("Conversation navigation core tests passed.");
