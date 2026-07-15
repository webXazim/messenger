import type { Conversation, UserLite } from "../types/chat";
import { isSameUserIdentity } from "./userIdentity";

export function conversationPath(conversation: Conversation, currentUser?: Partial<UserLite> | null) {
  if (conversation.type === "group") {
    const slug = conversation.slug?.trim();
    return slug ? `/chat/${encodeURIComponent(slug.toLowerCase())}` : `/chat/${conversation.id}`;
  }
  const peer = conversation.participants.find((participant) => !isSameUserIdentity(participant.user, currentUser));
  const username = peer?.user.username?.trim();
  return username ? `/chat/${encodeURIComponent(username.toLowerCase())}` : `/chat/${conversation.id}`;
}

export function isNamedConversationRoute(value: string) {
  const normalized = value.replace(/^@/, "").trim();
  return Boolean(normalized) && !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(normalized);
}
