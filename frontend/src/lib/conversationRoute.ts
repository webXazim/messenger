import type { Conversation, UserLite } from "../types/chat";
import { isSameUserIdentity } from "./userIdentity";

export function conversationPath(conversation: Conversation, currentUser?: Partial<UserLite> | null) {
  if (conversation.type !== "direct") return `/chat/${conversation.id}`;
  const peer = conversation.participants.find((participant) => !isSameUserIdentity(participant.user, currentUser));
  const username = peer?.user.username?.trim();
  return username ? `/chat/@${encodeURIComponent(username.toLowerCase())}` : `/chat/${conversation.id}`;
}

export function isUsernameConversationRoute(value: string) {
  return value.startsWith("@") && value.length > 1;
}
