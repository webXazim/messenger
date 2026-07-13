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

export function mergeTimelineMessage(existing: Message, incoming: Message): Message {
  return {
    ...existing,
    ...incoming,
    attachments: incoming.attachments ?? existing.attachments,
    reactions: incoming.reactions ?? existing.reactions,
    reaction_summary: incoming.reaction_summary ?? existing.reaction_summary,
    deliveries: incoming.deliveries ?? existing.deliveries,
    entities: incoming.entities ?? existing.entities,
    links: incoming.links ?? existing.links,
    metadata: incoming.metadata ?? existing.metadata,
  };
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
    pages.splice(0, pages.length, { ...firstPage, results: [incoming, ...firstPage.results] }, ...remainingPages);
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
