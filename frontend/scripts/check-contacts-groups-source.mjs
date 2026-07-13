import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const friends = read("src/pages/FriendsPage.tsx");
const groups = read("src/pages/GroupsPage.tsx");
const modal = read("src/components/GroupChatModal.tsx");
const members = read("src/components/conversation/details/GroupMembersSection.tsx");
const profile = read("src/components/conversation/details/ConversationProfileSection.tsx");
const authApi = read("src/api/auth.ts");
const accountViews = read("../apps/accounts/api/views.py");
const accountSerializers = read("../apps/accounts/api/serializers.py");
const chatSerializers = read("../apps/chat/api/serializers.py");
const chatServices = read("../apps/chat/services.py");

for (const tab of ["Friends", "Requests", "Find people", "Nearby"]) {
  assert.ok(friends.includes(tab), `Contacts section is missing: ${tab}`);
}
assert.ok(friends.includes("requestNotes"), "Friend request notes are not isolated by person.");
assert.ok(!friends.includes('const [requestMessage'), "Contacts still use one shared request note.");
assert.ok(friends.includes("Stop sharing location"), "Nearby location cannot be removed from the account.");
assert.ok(friends.includes("shareNearby"), "Nearby visibility does not require explicit user choice.");
assert.ok(authApi.includes('replace("canceled", "cancelled")'), "Friend request status normalization is incomplete.");
assert.ok(modal.includes("validateGroupDraft"), "Group creation does not use centralized validation.");
assert.ok(modal.includes("submittingRef"), "Group creation can be double-submitted.");
assert.ok(groups.includes("ms-group-row"), "Groups are not using the simplified activity-first layout.");
for (const action of ["Transfer ownership", "Remove from group", "Prevent rejoining"]) {
  assert.ok(members.includes(action), `Group member action is missing: ${action}`);
}
assert.ok(members.includes("ConfirmDialog"), "Destructive group actions do not require confirmation.");
assert.ok(profile.includes("leaveDisabled"), "Owners can still attempt to leave before transferring ownership.");
assert.ok(accountViews.includes("NEARBY_LOCATION_TTL_HOURS"), "Stale nearby locations are still returned indefinitely.");
assert.ok(!accountViews.includes("profile.is_discoverable = True"), "Nearby opt-in still overrides account discovery privacy.");
assert.ok(accountSerializers.includes('profile_data["latitude"] = None'), "Disabling nearby discovery does not clear stored coordinates.");
assert.ok(chatSerializers.includes("Choose each participant only once."), "Duplicate group participants are not rejected by the API.");
assert.ok(chatServices.includes("Only the owner can prevent another admin from rejoining."), "Admin-to-admin moderation is not protected.");

console.log("Contacts and groups source regression checks passed.");
