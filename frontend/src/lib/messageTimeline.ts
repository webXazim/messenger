import type { InfiniteData } from "@tanstack/react-query";
import type { Message } from "../types/chat";

export type TimelineMessagePage = {
  results: Message[];
  next?: string | null;
  previous?: string | null;
};

function matchesMessage(left: Message, right: Message) {
  if (left.id && right.id && left.id === right.id) return true;
  return Boolean(left.client_temp_id && right.client_temp_id && left.client_temp_id === right.client_temp_id);
}

const DELIVERY_STATUS_RANK: Record<string, number> = {
  pending: 0,
  sending: 0,
  sent: 1,
  delivered: 2,
  read: 3,
};

export function mergeDeliveryStatus(currentStatus?: string | null, incomingStatus?: string | null): string | undefined {
  const current = String(currentStatus || "").toLowerCase();
  const incoming = String(incomingStatus || "").toLowerCase();
  if (!incoming) return currentStatus || undefined;
  if (!current) return incomingStatus || undefined;
  if (incoming === "failed") return incomingStatus || undefined;
  if (current === "failed") return incomingStatus || undefined;
  const currentRank = DELIVERY_STATUS_RANK[current];
  const incomingRank = DELIVERY_STATUS_RANK[incoming];
  if (currentRank === undefined || incomingRank === undefined) return incomingStatus || undefined;
  return (incomingRank >= currentRank ? incomingStatus : currentStatus) || undefined;
}

export function mergeTimelineMessage(existing: Message, incoming: Message): Message {
  return {
    ...existing,
    ...incoming,
    // A message's creation position is immutable. In particular, keep the
    // optimistic timestamp when the server replaces the temporary id so the
    // bubble does not jump between neighbouring rapid sends.
    created_at: existing.created_at || incoming.created_at,
    delivery_status: mergeDeliveryStatus(existing.delivery_status, incoming.delivery_status),
    attachments: incoming.attachments ?? existing.attachments,
    reactions: incoming.reactions ?? existing.reactions,
    reaction_summary: incoming.reaction_summary ?? existing.reaction_summary,
    deliveries: incoming.deliveries ?? existing.deliveries,
    entities: incoming.entities ?? existing.entities,
    links: incoming.links ?? existing.links,
    metadata: incoming.metadata ?? existing.metadata,
  };
}

export function advanceMessageReceiptPages(
  current: InfiniteData<TimelineMessagePage> | undefined,
  pointerMessageId: string,
  status: "delivered" | "read",
  senderUserId: string,
) {
  const pointer = findMessageInPages(current, pointerMessageId);
  if (!pointer) return current;
  const pointerSequence = Number(pointer.sequence);
  const pointerTime = Date.parse(pointer.created_at);
  if (!Number.isFinite(pointerSequence) && !Number.isFinite(pointerTime)) return current;
  return mapMessagePages(
    current,
    (message) => String(message.sender.id) === String(senderUserId)
      && (Number.isFinite(pointerSequence) && Number.isFinite(Number(message.sequence))
        ? Number(message.sequence) <= pointerSequence
        : Date.parse(message.created_at) <= pointerTime)
      && String(message.delivery_status || "").toLowerCase() !== "failed",
    (message) => ({
      ...message,
      delivery_status: mergeDeliveryStatus(message.delivery_status, status),
    }),
  );
}

export function flattenMessagePages(data: InfiniteData<TimelineMessagePage> | undefined): Message[] {
  const byId = new Map<string, Message>();
  const tempIdToId = new Map<string, string>();

  for (const page of data?.pages ?? []) {
    for (const message of page.results) {
      const clientTempId = String(message.client_temp_id || "");
      const knownId = clientTempId ? tempIdToId.get(clientTempId) : undefined;
      const key = knownId || message.id || (clientTempId ? `temp:${clientTempId}` : "");
      if (!key) continue;

      const existing = byId.get(key);
      const merged = existing ? mergeTimelineMessage(existing, message) : message;
      byId.set(key, merged);
      if (clientTempId) tempIdToId.set(clientTempId, key);
    }
  }

  return Array.from(byId.values());
}

export function compareTimelineMessages(left: Message, right: Message) {
  const leftTimestamp = Date.parse(left.created_at);
  const rightTimestamp = Date.parse(right.created_at);
  if (Number.isFinite(leftTimestamp) && Number.isFinite(rightTimestamp)) {
    const timestampDelta = leftTimestamp - rightTimestamp;
    if (timestampDelta) return timestampDelta;
  }
  // Modern JavaScript sorting is stable. Returning zero preserves arrival or
  // optimistic insertion order when the server timestamps are identical,
  // instead of letting unrelated UUID values shuffle rapid messages.
  return 0;
}

export function findMessageInPages(data: InfiniteData<TimelineMessagePage> | undefined, messageId: string) {
  for (const page of data?.pages ?? []) {
    const message = page.results.find((entry) => entry.id === messageId);
    if (message) return message;
  }
  return undefined;
}

export function mapMessagePages(
  current: InfiniteData<TimelineMessagePage> | undefined,
  predicate: (message: Message) => boolean,
  updater: (message: Message) => Message,
) {
  if (!current?.pages?.length) return current;
  let changed = false;
  const pages = current.pages.map((page) => {
    let pageChanged = false;
    const results = page.results.map((message) => {
      if (!predicate(message)) return message;
      pageChanged = true;
      changed = true;
      return updater(message);
    });
    return pageChanged ? { ...page, results } : page;
  });
  return changed ? { ...current, pages } : current;
}

export function upsertMessagePages(
  current: InfiniteData<TimelineMessagePage> | undefined,
  incoming: Message,
  options: { insertWhenMissing?: boolean } = {},
) {
  const insertWhenMissing = options.insertWhenMissing !== false;
  if (!current?.pages?.length) {
    if (!insertWhenMissing) return current;
    return {
      pages: [{ results: [incoming], next: null, previous: null }],
      pageParams: [null],
    } as InfiniteData<TimelineMessagePage>;
  }
  let found = false;
  let mergedMessage = incoming;

  const pages = current.pages.map((page) => {
    const results: Message[] = [];
    let pageChanged = false;
    for (const message of page.results) {
      if (!matchesMessage(message, incoming)) {
        results.push(message);
        continue;
      }
      if (!found) {
        mergedMessage = mergeTimelineMessage(message, incoming);
        results.push(mergedMessage);
        found = true;
      }
      pageChanged = true;
    }
    return pageChanged ? { ...page, results } : page;
  });

  if (!found && insertWhenMissing) {
    const [firstPage, ...remainingPages] = pages;
    pages.splice(0, pages.length, { ...firstPage, results: [...firstPage.results, incoming] }, ...remainingPages);
  }

  return { ...current, pages };
}

export function mergeMessageContextPages(
  current: InfiniteData<TimelineMessagePage> | undefined,
  contextMessages: Message[],
) {
  if (!contextMessages.length) return current;
  let next = current;
  for (const message of contextMessages) {
    next = upsertMessagePages(next, message);
  }
  return next;
}

export function removeMessagePages(
  current: InfiniteData<TimelineMessagePage> | undefined,
  predicate: (message: Message) => boolean,
) {
  if (!current?.pages?.length) return current;
  let changed = false;
  const pages = current.pages.map((page) => {
    const results = page.results.filter((message) => {
      if (!predicate(message)) return true;
      changed = true;
      return false;
    });
    return results.length === page.results.length ? page : { ...page, results };
  });
  return changed ? { ...current, pages } : current;
}

export function markMessageDeletedPages(
  current: InfiniteData<TimelineMessagePage> | undefined,
  messageId: string,
) {
  return mapMessagePages(current, (message) => message.id === messageId, (message) => ({
    ...message,
    text: "",
    is_deleted: true,
    attachments: [],
    links: [],
    entities: [],
    transcript: null,
    voice_note: null,
    encryption: null,
    is_encrypted: false,
    decryption_state: undefined,
    decryption_message: undefined,
    reactions: [],
    reaction_summary: {},
    failed_reason: null,
  }));
}
