import type { Conversation, Participant } from "../types/chat";

type ReceiptFields = Pick<Participant,
  "last_delivered_message" | "last_delivered_at" | "last_read_message" | "last_read_at"
>;

function validTimestamp(value: string | null | undefined) {
  const timestamp = Date.parse(String(value || ""));
  return Number.isFinite(timestamp) ? timestamp : null;
}

function mergePointer(
  currentId: string | null | undefined,
  currentAt: string | null | undefined,
  incomingId: string | null | undefined,
  incomingAt: string | null | undefined,
) {
  const currentTime = validTimestamp(currentAt);
  const incomingTime = validTimestamp(incomingAt);

  if (!incomingId) return { id: currentId ?? null, at: currentAt ?? null };
  if (!currentId) return { id: incomingId, at: incomingAt ?? null };
  if (currentTime !== null && (incomingTime === null || incomingTime <= currentTime)) {
    return { id: currentId, at: currentAt ?? null };
  }
  return { id: incomingId, at: incomingAt ?? currentAt ?? null };
}

export function mergeParticipantReceipts(
  current: Participant,
  incoming: Partial<ReceiptFields>,
): Participant {
  const delivered = mergePointer(
    current.last_delivered_message,
    current.last_delivered_at,
    incoming.last_delivered_message,
    incoming.last_delivered_at,
  );
  const read = mergePointer(
    current.last_read_message,
    current.last_read_at,
    incoming.last_read_message,
    incoming.last_read_at,
  );
  return {
    ...current,
    last_delivered_message: delivered.id,
    last_delivered_at: delivered.at,
    last_read_message: read.id,
    last_read_at: read.at,
  };
}

export function mergeConversationReceipts(
  current: Conversation | undefined,
  incoming: Conversation,
): Conversation {
  if (!current || String(current.id) !== String(incoming.id)) return incoming;
  const currentByUserId = new Map(
    current.participants.map((participant) => [String(participant.user.id), participant]),
  );
  return {
    ...incoming,
    participants: incoming.participants.map((participant) => {
      const existing = currentByUserId.get(String(participant.user.id));
      if (!existing) return participant;
      const receipts = mergeParticipantReceipts(existing, participant);
      return {
        ...participant,
        last_delivered_message: receipts.last_delivered_message,
        last_delivered_at: receipts.last_delivered_at,
        last_read_message: receipts.last_read_message,
        last_read_at: receipts.last_read_at,
      };
    }),
  };
}
