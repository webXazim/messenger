import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const e2ee = read("src/lib/e2ee.ts");
const conversation = read("src/pages/ConversationPage.tsx");
const composer = read("src/components/MessageComposer.tsx");
const security = read("src/components/conversation/details/ConversationSecuritySection.tsx");
const authContext = read("src/contexts/AuthContext.tsx");
const settings = read("src/pages/SettingsPage.tsx");
const api = read("src/api/chat.ts");
const services = read("../apps/chat/services.py");
const serializers = read("../apps/chat/api/serializers.py");
const views = read("../apps/chat/api/views.py");
const consumers = read("../apps/chat/consumers.py");

for (const required of [
  "identitySyncPromises",
  "nestedApiErrors",
  "e2ee_device_key_revoked",
  "participant_device_missing",
  "current_device_missing",
  "decryptMessageTextResult",
  "rewrapAttachmentEncryptionForConversation",
  "Promise<MessageEncryptionEnvelope>",
]) {
  assert.ok(e2ee.includes(required), `Missing E2EE client invariant: ${required}`);
}

for (const required of [
  "disabledReason={composerDisabledReason}",
  "failedSendPayloadsRef",
  "decryptionCiphertextRef",
  "rewrapAttachmentEncryptionForConversation",
  "is_encrypted: true",
  "encryption: envelope",
]) {
  assert.ok(conversation.includes(required), `Missing conversation encryption behavior: ${required}`);
}

for (const required of [
  "e2ee_participant_device_missing",
  "e2ee_device_coverage_incomplete",
  "e2ee_sender_device_invalid",
  "wrapped_key_ids",
  "envelope_key_version != current_key_version",
]) {
  assert.ok(services.includes(required), `Missing server E2EE validation: ${required}`);
}

assert.ok(serializers.includes("Encryption envelope is required for an encrypted edit"), "Encrypted edits are not validated by the API serializer.");
assert.ok(consumers.includes('data.get("encryption")'), "WebSocket message operations do not preserve encryption envelopes.");
assert.ok(views.includes('output["security_changed"] = security_changed'), "Device registration does not report whether key material changed.");
assert.ok(authContext.includes("identity.registrationChanged"), "Login/focus reconciliation still refreshes every conversation when no device changed.");
assert.ok(settings.includes('["e2ee-identity", String(user?.id || "")]'), "Settings uses a duplicate E2EE identity cache key.");
assert.ok(composer.includes("Secure messaging unavailable"), "Composer does not explain blocked secure messaging.");
assert.ok(security.includes("Protected automatically"), "Security panel does not expose the automatic protection state.");
assert.ok(api.includes("security_changed: firstBoolean"), "E2EE device change state is discarded by API normalization.");

for (const forbidden of [
  "_use_e2ee",
  "if (!encrypted && encryptionAvailable)",
  "if (!envelope && encryptionAvailable)",
]) {
  assert.ok(!`${e2ee}\n${conversation}\n${api}`.includes(forbidden), `Legacy plaintext fallback remains: ${forbidden}`);
}

console.log("E2EE source regression checks passed.");
