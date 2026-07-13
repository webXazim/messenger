import { formatFingerprint, type ConversationEncryptionReadiness } from "../../../lib/e2ee";
import type { Conversation, ConversationE2EEKeyMaterial, Participant } from "../../../types/chat";
import { DetailsSection } from "./DetailsSection";

function securityStatus(
  material: ConversationE2EEKeyMaterial | undefined,
  missingParticipantCount: number,
  readiness?: ConversationEncryptionReadiness,
) {
  if (readiness?.status === "blocked") return { note: "Setup needed", value: "Not ready" };
  if (readiness?.status === "preparing") return { note: "Setting up", value: "Preparing" };
  if (!material) return { note: "Setting up", value: "Preparing" };
  if (missingParticipantCount) return { note: "Setup needed", value: "Waiting for device setup" };
  if (material.rekey_required) return { note: "Updating securely", value: "Updating" };
  return { note: "Protected automatically", value: "Protected" };
}

export function ConversationSecuritySection({
  conversation,
  participants,
  currentUserId,
  material,
  readiness,
}: {
  conversation?: Conversation;
  participants: Participant[];
  currentUserId: string;
  material?: ConversationE2EEKeyMaterial;
  readiness?: ConversationEncryptionReadiness;
}) {
  const participantKeys = material?.participants ?? {};
  const keyVersion = material?.key_version ?? conversation?.e2ee_key_version ?? 1;
  const totalDevices = Object.values(participantKeys).reduce((count, devices) => count + devices.length, 0);
  const missingParticipants = participants.filter((participant) => !(participantKeys[String(participant.user.id)] ?? []).length);
  const status = securityStatus(material, missingParticipants.length, readiness);

  return (
    <DetailsSection
      title="End-to-end encryption"
      eyebrow="Privacy"
      note={status.note}
      collapsible
      defaultOpen={false}
    >
      <div className="ms-details-security-summary">
        <div><span>Status</span><strong>{status.value}</strong></div>
        <div><span>Secure devices</span><strong>{material ? totalDevices : "—"}</strong></div>
        {readiness && !readiness.canEncrypt && readiness.code !== "participant_device_missing" ? (
          <p>{readiness.message}</p>
        ) : missingParticipants.length ? (
          <p>
            {missingParticipants.map((participant) => participant.user.display_name || participant.user.username).join(", ")}
            {missingParticipants.length === 1 ? " needs" : " need"} to finish secure-device setup before new messages can be sent.
          </p>
        ) : (
          <p>
            Messages and supported attachments are encrypted automatically. There is no encryption switch to manage for each message.
          </p>
        )}
      </div>

      <details className="ms-details-security-codes">
        <summary>Advanced security information</summary>
        <p>Most people never need this. Security codes are useful only when participants want to compare codes through another trusted channel.</p>
        <div className="ms-details-device-groups">
          {participants.map((participant) => {
            const devices = participantKeys[String(participant.user.id)] ?? [];
            if (!devices.length) return null;
            const name = participant.user.display_name || participant.user.username;
            const isSelf = String(participant.user.id) === currentUserId;

            return (
              <section key={participant.id} className="ms-details-device-group">
                <header><strong>{isSelf ? "Your devices" : name}</strong><span>{devices.length} active</span></header>
                {devices.map((device) => {
                  const fingerprint = device.fingerprint || device.key_id || "";
                  return (
                    <div key={device.id || device.key_id} className="ms-details-device-row">
                      <div>
                        <strong>{device.label || (isSelf ? "Your linked device" : "Linked device")}</strong>
                        <code>{formatFingerprint(fingerprint)}</code>
                        <small>{device.last_seen_at ? `Last active ${new Date(device.last_seen_at).toLocaleString()}` : "Active recently"}</small>
                      </div>
                    </div>
                  );
                })}
              </section>
            );
          })}
        </div>
      </details>

      <small className="ms-details-security-version">Security session v{keyVersion}</small>
    </DetailsSection>
  );
}
