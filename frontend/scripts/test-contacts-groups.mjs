import assert from "node:assert/strict";
import { rmSync } from "node:fs";
import {
  dedupeUsers,
  isMeaningfulGroupTitle,
  normalizeGroupTitle,
  validateGroupDraft,
} from "../.contacts-groups-test-build/lib/groupUsability.js";

assert.equal(normalizeGroupTitle("  Product   team  "), "Product team");
assert.equal(isMeaningfulGroupTitle("--"), false);
assert.equal(isMeaningfulGroupTitle("A"), false);
assert.equal(isMeaningfulGroupTitle("Ops 2"), true);

const invalid = validateGroupDraft("--", []);
assert.equal(invalid.valid, false);
assert.ok(invalid.errors.title);
assert.ok(invalid.errors.participants);

const valid = validateGroupDraft("  Design   Team ", ["2", "2", "3"]);
assert.equal(valid.valid, true);
assert.equal(valid.title, "Design Team");
assert.deepEqual(valid.participantIds, ["2", "3"]);

const users = [
  { id: "1", username: "me" },
  { id: "2", username: "amina" },
  { id: "2", username: "duplicate" },
  { id: "3", username: "ben" },
];
assert.deepEqual(dedupeUsers(users, "1").map((user) => user.id), ["2", "3"]);

rmSync(new URL("../.contacts-groups-test-build", import.meta.url), { recursive: true, force: true });
console.log("Contacts and groups core tests passed.");
