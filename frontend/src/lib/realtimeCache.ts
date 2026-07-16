import type { InfiniteData, QueryClient } from "@tanstack/react-query";
import type { MessagePage } from "../api/chat";
import type { Call, Conversation, Message } from "../types/chat";
import { mergeConversationReceipts, mergeParticipantReceipts } from "./messageReceipts";
import { applyActiveConversationReadState } from "./activeConversationView";

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
  const reconciledIncoming = applyActiveConversationReadState(reconcileConversationPresence(queryClient, incoming));
  queryClient.setQueryData<Conversation[]>(["conversations"], (current) => upsertConversationList(current, reconciledIncoming));
  queryClient.setQueryData<Conversation>(["conversation", incoming.id], (current) =>
    current ? mergeConversationPreservingPresence(current, reconciledIncoming) : reconciledIncoming,
  );
  queryClient.setQueriesData<Conversation>({ queryKey: ["conversation-route"] }, (current) =>
    current?.id === incoming.id ? mergeConversationPreservingPresence(current, reconciledIncoming) : current,
  );
}

export function patchConversationReceiptCaches(
  queryClient: QueryClient,
  conversationId: string,
  eventName: "message.read" | "message.delivered",
  data: Record<string, unknown>,
) {
  const receiptUserId = String(data.user_id || "");
  if (!conversationId || !receiptUserId) return;

  const patchConversation = (conversation: Conversation | undefined) => {
    if (!conversation || String(conversation.id) !== conversationId) return conversation;
    return {
      ...conversation,
      participants: conversation.participants.map((participant) => {
        if (String(participant.user.id) !== receiptUserId) return participant;
        return mergeParticipantReceipts(participant, {
          last_delivered_message: String(data.last_delivered_message_id || "") || undefined,
          last_delivered_at: String(data.last_delivered_at || "") || undefined,
          last_read_message: eventName === "message.read" ? String(data.last_read_message_id || "") || undefined : undefined,
          last_read_at: eventName === "message.read" ? String(data.last_read_at || "") || undefined : undefined,
        });
      }),
    };
  };

  queryClient.setQueryData<Conversation>(["conversation", conversationId], patchConversation);
  queryClient.setQueryData<Conversation[]>(["conversations"], (current) => current?.map((conversation) => patchConversation(conversation) ?? conversation));
  queryClient.setQueriesData<Conversation>({ queryKey: ["conversation-route"] }, (current) => patchConversation(current));
}

export function markConversationReadInCaches(queryClient: QueryClient, conversationId: string) {
  if (!conversationId) return;
  const clearUnread = (conversation: Conversation | undefined) => {
    if (!conversation || String(conversation.id) !== conversationId || conversation.unread_count === 0) return conversation;
    return { ...conversation, unread_count: 0 };
  };

  queryClient.setQueryData<Conversation[]>(["conversations"], (current) =>
    current?.map((conversation) => clearUnread(conversation) ?? conversation),
  );
  queryClient.setQueryData<Conversation>(["conversation", conversationId], clearUnread);
  queryClient.setQueriesData<Conversation>({ queryKey: ["conversation-route"] }, clearUnread);
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
  presence_status?: "active" | "idle" | "offline";
  device_type?: "desktop" | "mobile" | "tablet" | null;
  device_types?: Array<"desktop" | "mobile" | "tablet">;
  presence_visibility?: "public" | "hidden";
};

type PresenceFriendRequest = {
  from_user: PresenceUser;
  to_user: PresenceUser;
};

function preservePresence<T extends PresenceUser>(current: T | undefined, incoming: T): T {
  if (!current) return incoming;
  const source = current.is_online ? current : incoming.is_online ? incoming : current;
  return {
    ...incoming,
    ...(source.is_online !== undefined ? { is_online: source.is_online } : {}),
    ...(source.active_devices !== undefined ? { active_devices: source.active_devices } : {}),
    ...(source.last_seen_at !== undefined ? { last_seen_at: source.last_seen_at } : {}),
    ...(source.presence_label !== undefined ? { presence_label: source.presence_label } : {}),
    ...(source.presence_status !== undefined || incoming.presence_status !== undefined
      ? { presence_status: source.presence_status ?? incoming.presence_status }
      : {}),
    ...(source.device_type !== undefined || incoming.device_type !== undefined
      ? { device_type: source.device_type ?? incoming.device_type }
      : {}),
    ...(source.device_types !== undefined || incoming.device_types !== undefined
      ? { device_types: source.device_types ?? incoming.device_types }
      : {}),
    ...(source.presence_visibility !== undefined ? { presence_visibility: source.presence_visibility } : {}),
  };
}

function reconcileConversationPresence(queryClient: QueryClient, incoming: Conversation): Conversation {
  const knownOnlineUsers = new Map<string, PresenceUser>();
  const rememberOnline = (user: PresenceUser | undefined) => {
    if (user?.is_online && user.presence_visibility !== "hidden") knownOnlineUsers.set(String(user.id), user);
  };

  queryClient.getQueriesData<PresenceFriendRequest[]>({ queryKey: ["friend-requests"] }).forEach(([, requests]) => {
    requests?.forEach((request) => {
      rememberOnline(request.from_user);
      rememberOnline(request.to_user);
    });
  });
  queryClient.getQueryData<Conversation[]>(["conversations"])?.forEach((conversation) => {
    conversation.participants.forEach((participant) => rememberOnline(participant.user));
  });

  return {
    ...incoming,
    participants: incoming.participants.map((participant) => {
      const known = knownOnlineUsers.get(String(participant.user.id));
      return known ? {
        ...participant,
        user: {
          ...participant.user,
          is_online: true,
          active_devices: Math.max(1, Number(known.active_devices || 0)),
          last_seen_at: known.last_seen_at ?? participant.user.last_seen_at,
          presence_label: known.presence_label || "online",
          presence_status: known.presence_status || "active",
          device_type: known.device_type ?? participant.user.device_type,
          device_types: known.device_types ?? participant.user.device_types,
          presence_visibility: "public",
        },
      } : participant;
    }),
  };
}

function mergeConversationPreservingPresence(current: Conversation, incoming: Conversation): Conversation {
  const receiptSafeIncoming = mergeConversationReceipts(current, incoming);
  const currentUsers = new Map(
    current.participants.map((participant) => [String(participant.user.id), participant.user]),
  );
  return {
    ...current,
    ...receiptSafeIncoming,
    participants: receiptSafeIncoming.participants.map((participant) => ({
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
  const rawStatus = String(payload.presence_status ?? payload.presence_label ?? "").toLowerCase();
  const presenceStatus: "active" | "idle" | "offline" = online ? rawStatus === "idle" ? "idle" : "active" : "offline";
  const rawDeviceType = String(payload.device_type ?? "").toLowerCase();
  const deviceType = ["desktop", "mobile", "tablet"].includes(rawDeviceType)
    ? rawDeviceType as "desktop" | "mobile" | "tablet"
    : null;
  const deviceTypes = (Array.isArray(payload.device_types) ? payload.device_types : [])
    .map((entry) => String(entry).toLowerCase())
    .filter((entry): entry is "desktop" | "mobile" | "tablet" => ["desktop", "mobile", "tablet"].includes(entry));
  return {
    ...user,
    is_online: online,
    active_devices: hidden ? 0 : (Number.isFinite(activeDevices) ? activeDevices : user.active_devices),
    presence_label: presenceStatus === "idle" ? "idle" : online ? "online" : "offline",
    presence_status: presenceStatus,
    device_type: hidden || !online ? null : (deviceType ?? user.device_type ?? null),
    device_types: hidden || !online ? [] : (deviceTypes.length ? deviceTypes : user.device_types),
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
