import { isSameUserIdentity } from "../../lib/userIdentity";
import type { Conversation, UserLite } from "../../types/chat";

export function applyKnownOnlinePresence(
  conversations: Conversation[],
  knownPeople: Array<Partial<UserLite> & { id: string | number }> = [],
) {
  const onlineById = new Map(
    knownPeople
      .filter((person) => person.is_online && person.presence_visibility !== "hidden")
      .map((person) => [String(person.id), person]),
  );
  if (!onlineById.size) return conversations;

  return conversations.map((conversation) => {
    let changed = false;
    const participants = conversation.participants.map((participant) => {
      const onlinePerson = onlineById.get(String(participant.user.id));
      if (!onlinePerson || participant.user.is_online) return participant;
      changed = true;
      return {
        ...participant,
        user: {
          ...participant.user,
          is_online: true,
          active_devices: Math.max(1, Number(onlinePerson.active_devices || 0)),
          last_seen_at: onlinePerson.last_seen_at ?? participant.user.last_seen_at,
          presence_label: onlinePerson.presence_label || "online",
          presence_status: onlinePerson.presence_status || "active",
          device_type: onlinePerson.device_type ?? participant.user.device_type,
          device_types: onlinePerson.device_types ?? participant.user.device_types,
          presence_visibility: "public" as const,
        },
      };
    });
    return changed ? { ...conversation, participants } : conversation;
  });
}

export function userDisplayLabel(user?: Partial<UserLite> | null) {
  if (!user) return "";
  const displayName = String(user.display_name || "").trim();
  const username = String(user.username || "").trim();
  if (displayName && !displayName.includes("@")) return displayName;
  return username || displayName || "";
}

export function conversationPeer(
  conversation: Conversation,
  currentUserId?: string,
  currentUser?: Partial<UserLite> | null,
) {
  if (conversation.type === "group") return null;
  const currentIdentity = currentUser ?? { id: currentUserId };
  return conversation.participants.find((participant) => !isSameUserIdentity(participant.user, currentIdentity))?.user
    ?? conversation.participants[0]?.user
    ?? null;
}

function attachmentSnippet(conversation: Conversation) {
  const attachments = conversation.last_message?.attachments ?? [];
  if (!attachments.length) return "";
  if (attachments.length > 1) return `${attachments.length} attachments`;

  const attachment = attachments[0];
  const kind = String(attachment.media_kind || attachment.mime_type || "").toLowerCase();
  if (kind.includes("image")) return "Photo";
  if (kind.includes("video")) return "Video";
  if (kind.includes("audio")) return "Audio";
  return attachment.original_name || "Attachment";
}

export function conversationSnippet(
  conversation: Conversation,
  currentUserId?: string,
  currentUser?: Partial<UserLite> | null,
) {
  const message = conversation.last_message;
  if (conversation.e2ee_rekey_required) return "Security update required";
  if (!message) return "Start a conversation";
  if (message.is_deleted) return "Message deleted";
  if (message.is_encrypted) return "Encrypted message";

  const currentIdentity = currentUser ?? { id: currentUserId };
  const callOwnedByViewer = message.call_event?.initiated_by_id
    ? String(message.call_event.initiated_by_id) === String(currentIdentity.id || "")
    : isSameUserIdentity(message.sender, currentIdentity);
  const callOutcome = String(message.call_event?.call_outcome || message.call_event?.call_status || "").toLowerCase();
  let content = "";
  if (message.call_event) {
    if (["ringing", "initiated"].includes(callOutcome)) content = callOwnedByViewer ? "Outgoing call" : "Incoming call";
    else if (callOutcome === "missed") content = callOwnedByViewer ? "No answer" : "Missed call";
    else content = message.call_event.summary_text || "Call update";
  }
  if (!content) content = message.text?.trim() || "";
  if (!content && message.voice_note?.is_voice_note) content = "Voice message";
  if (!content) content = attachmentSnippet(conversation);
  if (!content) content = "New message";

  if (message.call_event ? callOwnedByViewer : isSameUserIdentity(message.sender, currentIdentity)) return `You: ${content}`;
  if (conversation.type === "group") {
    const sender = userDisplayLabel(message.sender);
    return sender ? `${sender}: ${content}` : content;
  }
  return content;
}

export function conversationTime(conversation: Conversation) {
  const source = conversation.last_message?.created_at || conversation.last_message_at;
  if (!source) return "";
  const date = new Date(source);
  if (Number.isNaN(date.getTime())) return "";

  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfMessageDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const dayDifference = Math.round((startOfToday.getTime() - startOfMessageDay.getTime()) / 86_400_000);

  if (dayDifference === 0) return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (dayDifference === 1) return "Yesterday";
  if (dayDifference > 1 && dayDifference < 7) return date.toLocaleDateString([], { weekday: "short" });
  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}

export function conversationDisplayName(
  conversation: Conversation,
  currentUserId?: string,
  currentUser?: Partial<UserLite> | null,
) {
  if (conversation.type === "group") return conversation.title || "Group chat";
  const participant = conversationPeer(conversation, currentUserId, currentUser);
  return userDisplayLabel(participant) || conversation.title || "Conversation";
}

export function conversationInitials(
  conversation: Conversation,
  currentUserId?: string,
  currentUser?: Partial<UserLite> | null,
) {
  const name = conversationDisplayName(conversation, currentUserId, currentUser).trim();
  const parts = name.split(/\s+/).filter(Boolean).slice(0, 2);
  return (parts.map((part) => part[0]).join("") || name.slice(0, 2) || "C").toUpperCase();
}

export function conversationViewerParticipant(
  conversation: Conversation,
  currentUserId?: string,
  currentUser?: Partial<UserLite> | null,
) {
  const currentIdentity = currentUser ?? { id: currentUserId };
  return conversation.participants.find((participant) => isSameUserIdentity(participant.user, currentIdentity)) ?? null;
}

export function conversationActivityTimestamp(conversation: Conversation) {
  const source = conversation.last_message?.created_at || conversation.last_message_at || "";
  const timestamp = new Date(source).getTime();
  return Number.isFinite(timestamp) ? timestamp : 0;
}

export function conversationMatchesQuery(
  conversation: Conversation,
  query: string,
  currentUserId?: string,
  currentUser?: Partial<UserLite> | null,
) {
  const normalizedQuery = query.trim().toLowerCase();
  if (!normalizedQuery) return true;
  const people = conversation.participants.map((item) => userDisplayLabel(item.user)).join(" ");
  return [
    conversationDisplayName(conversation, currentUserId, currentUser),
    people,
    conversationSnippet(conversation, currentUserId, currentUser),
  ].join(" ").toLowerCase().includes(normalizedQuery);
}

export function sortConversationsForInbox(
  conversations: Conversation[],
  currentUserId?: string,
  currentUser?: Partial<UserLite> | null,
) {
  return [...conversations].sort((a, b) => {
    const aPinned = Boolean(conversationViewerParticipant(a, currentUserId, currentUser)?.is_pinned);
    const bPinned = Boolean(conversationViewerParticipant(b, currentUserId, currentUser)?.is_pinned);
    if (aPinned !== bPinned) return aPinned ? -1 : 1;
    return conversationActivityTimestamp(b) - conversationActivityTimestamp(a);
  });
}
