import type { InfiniteData, QueryClient } from "@tanstack/react-query";
import type { MessagePage } from "../api/chat";
import type { Call, Conversation, Message } from "../types/chat";

export type ChatSyncPayload = {
  conversations: Conversation[];
  messages: Message[];
  active_calls: Call[];
  has_more_conversations: boolean;
  has_more_messages: boolean;
  next_since?: string;
  server_time?: string;
};

function conversationTimestamp(conversation: Conversation) {
  const value = conversation.last_message_at || conversation.last_message?.created_at || "";
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

export function upsertConversationList(current: Conversation[] | undefined, incoming: Conversation) {
  if (!current) return [incoming];
  const existingIndex = current.findIndex((conversation) => conversation.id === incoming.id);
  const next = [...current];
  if (existingIndex >= 0) next[existingIndex] = mergeConversationPreservingPresence(next[existingIndex], incoming);
  else next.push(incoming);
  return next.sort((a, b) => conversationTimestamp(b) - conversationTimestamp(a));
}

export function patchConversationCaches(queryClient: QueryClient, incoming: Conversation) {
  queryClient.setQueryData<Conversation[]>(["conversations"], (current) => upsertConversationList(current, incoming));
  queryClient.setQueryData<Conversation>(["conversation", incoming.id], (current) =>
    current ? mergeConversationPreservingPresence(current, incoming) : incoming,
  );
  queryClient.setQueriesData<Conversation>({ queryKey: ["conversation-route"] }, (current) =>
    current?.id === incoming.id ? mergeConversationPreservingPresence(current, incoming) : current,
  );
}

export function patchMessageCache(queryClient: QueryClient, conversationId: string, incoming: Message) {
  queryClient.setQueryData<InfiniteData<MessagePage>>(["messages", conversationId], (current) => {
    if (!current?.pages?.length) return current;
    let found = false;
    const pages = current.pages.map((page) => ({
      ...page,
      results: page.results.map((message) => {
        const matches = message.id === incoming.id || (!!incoming.client_temp_id && message.client_temp_id === incoming.client_temp_id);
        if (!matches) return message;
        found = true;
        return { ...message, ...incoming };
      }),
    }));
    if (!found) {
      pages[0] = {
        ...pages[0],
        results: [...pages[0].results, incoming].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at)),
      };
    }
    return { ...current, pages };
  });
}

export function patchCallCaches(queryClient: QueryClient, incoming: Call) {
  queryClient.setQueryData<Call>(["call", incoming.id], (current) => ({ ...current, ...incoming }));
  queryClient.setQueryData<Call[]>(["recent-calls"], (current) => {
    if (!current) return current;
    const index = current.findIndex((call) => call.id === incoming.id);
    if (index < 0) return [incoming, ...current];
    const next = [...current];
    next[index] = { ...next[index], ...incoming };
    return next;
  });
}

export function mergeChatSync(queryClient: QueryClient, payload: ChatSyncPayload) {
  for (const conversation of payload.conversations) patchConversationCaches(queryClient, conversation);
  for (const message of payload.messages) {
    if (message.conversation_id) patchMessageCache(queryClient, message.conversation_id, message);
  }
  for (const call of payload.active_calls) patchCallCaches(queryClient, call);
}

export function getRealtimeSyncMarker(userId: string) {
  return window.localStorage.getItem(`messenger.realtime-since.${userId}`) || undefined;
}

export function setRealtimeSyncMarker(userId: string, marker?: string) {
  if (!marker) return;
  window.localStorage.setItem(`messenger.realtime-since.${userId}`, marker);
}

type PresencePayload = Record<string, unknown>;
type PresenceUser = {
  id: string | number;
  is_online?: boolean;
  active_devices?: number;
  last_seen_at?: string | null;
  presence_label?: string;
  presence_visibility?: "public" | "hidden";
};

type PresenceFriendRequest = {
  from_user: PresenceUser;
  to_user: PresenceUser;
};

function preservePresence<T extends PresenceUser>(current: T | undefined, incoming: T): T {
  if (!current) return incoming;
  return {
    ...incoming,
    ...(current.is_online !== undefined ? { is_online: current.is_online } : {}),
    ...(current.active_devices !== undefined ? { active_devices: current.active_devices } : {}),
    ...(current.last_seen_at !== undefined ? { last_seen_at: current.last_seen_at } : {}),
    ...(current.presence_label !== undefined ? { presence_label: current.presence_label } : {}),
    ...(current.presence_visibility !== undefined ? { presence_visibility: current.presence_visibility } : {}),
  };
}

function mergeConversationPreservingPresence(current: Conversation, incoming: Conversation): Conversation {
  const currentUsers = new Map(
    current.participants.map((participant) => [String(participant.user.id), participant.user]),
  );
  return {
    ...current,
    ...incoming,
    participants: incoming.participants.map((participant) => ({
      ...participant,
      user: preservePresence(currentUsers.get(String(participant.user.id)), participant.user),
    })),
  };
}

function patchPresenceUser<T extends PresenceUser>(user: T, userId: string, payload: PresencePayload): T {
  if (String(user.id) !== userId) return user;
  const hidden = String(payload.visibility ?? payload.presence_visibility ?? "") === "hidden";
  const activeDevices = Number(payload.active_devices);
  const online = !hidden && Boolean(payload.is_online);
  const lastSeenAt = typeof payload.last_seen_at === "string" && payload.last_seen_at ? payload.last_seen_at : null;
  return {
    ...user,
    is_online: online,
    active_devices: hidden ? 0 : (Number.isFinite(activeDevices) ? activeDevices : user.active_devices),
    presence_label: online ? "online" : "offline",
    presence_visibility: hidden ? "hidden" : "public",
    last_seen_at: hidden ? null : (lastSeenAt || user.last_seen_at || null),
  };
}

function patchConversationPresence(conversation: Conversation | undefined, userId: string, payload: PresencePayload) {
  if (!conversation) return conversation;
  let changed = false;
  const participants = conversation.participants.map((participant) => {
    const user = patchPresenceUser(participant.user, userId, payload);
    if (user !== participant.user) changed = true;
    return user === participant.user ? participant : { ...participant, user };
  });
  return changed ? { ...conversation, participants } : conversation;
}

function patchCallPresence(call: Call | undefined, userId: string, payload: PresencePayload) {
  if (!call) return call;
  const initiatedBy = call.initiated_by ? patchPresenceUser(call.initiated_by, userId, payload) : call.initiated_by;
  const answeredBy = call.answered_by ? patchPresenceUser(call.answered_by, userId, payload) : call.answered_by;
  let participantsChanged = false;
  const participants = call.participants?.map((participant) => {
    const user = patchPresenceUser(participant.user, userId, payload);
    if (user !== participant.user) participantsChanged = true;
    return user === participant.user ? participant : { ...participant, user };
  });
  if (initiatedBy === call.initiated_by && answeredBy === call.answered_by && !participantsChanged) return call;
  return { ...call, initiated_by: initiatedBy, answered_by: answeredBy, participants };
}

export function patchUserPresenceAcrossCaches(queryClient: QueryClient, userId: string, payload: PresencePayload) {
  if (!userId) return;

  queryClient.setQueryData<Conversation[]>(["conversations"], (current) =>
    current?.map((conversation) => patchConversationPresence(conversation, userId, payload) ?? conversation),
  );
  queryClient.setQueriesData<Conversation>({ queryKey: ["conversation"] }, (current) =>
    patchConversationPresence(current, userId, payload),
  );
  queryClient.setQueriesData<Conversation>({ queryKey: ["conversation-route"] }, (current) =>
    patchConversationPresence(current, userId, payload),
  );
  queryClient.setQueriesData<PresenceFriendRequest[]>({ queryKey: ["friend-requests"] }, (current) =>
    current?.map((request) => ({
      ...request,
      from_user: patchPresenceUser(request.from_user, userId, payload),
      to_user: patchPresenceUser(request.to_user, userId, payload),
    })),
  );
  queryClient.setQueriesData<PresenceUser[]>({ queryKey: ["user-search"] }, (current) =>
    current?.map((person) => patchPresenceUser(person, userId, payload)),
  );
  queryClient.setQueryData<PresenceUser[]>(["nearby-users"], (current) =>
    current?.map((person) => patchPresenceUser(person, userId, payload)),
  );
  queryClient.setQueryData<Call[]>(["recent-calls"], (current) =>
    current?.map((call) => patchCallPresence(call, userId, payload) ?? call),
  );
  queryClient.setQueriesData<Call>({ queryKey: ["call"] }, (current) =>
    patchCallPresence(current, userId, payload),
  );
}
