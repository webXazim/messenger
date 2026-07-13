import type { Conversation, UserLite } from "../../types/chat";
import type { ConversationFilter } from "./types";
import {
  conversationMatchesQuery,
  conversationViewerParticipant,
  sortConversationsForInbox,
} from "./conversationPresentation";

export function filterConversationsForInbox({
  conversations,
  filter,
  search,
  currentUserId,
  currentUser,
}: {
  conversations: Conversation[];
  filter: ConversationFilter;
  search: string;
  currentUserId?: string;
  currentUser?: Partial<UserLite> | null;
}) {
  return sortConversationsForInbox(conversations, currentUserId, currentUser)
    .filter((conversation) => {
      const archived = Boolean(conversationViewerParticipant(conversation, currentUserId, currentUser)?.is_archived);
      if (filter === "archived") return archived;
      if (archived) return false;
      if (filter === "unread") return conversation.unread_count > 0;
      if (filter === "groups") return conversation.type === "group";
      return true;
    })
    .filter((conversation) => conversationMatchesQuery(conversation, search, currentUserId, currentUser));
}

export function conversationListEmptyCopy(filter: ConversationFilter, search: string) {
  if (search.trim()) return { title: "No matching chats", description: "Try another name or message." };
  if (filter === "unread") return { title: "You are all caught up", description: "Unread chats will appear here." };
  if (filter === "groups") return { title: "No group chats", description: "Create a group when you need a shared conversation." };
  if (filter === "archived") return { title: "No archived chats", description: "Chats you archive will stay available here." };
  return { title: "No chats yet", description: "Start a conversation to see it here." };
}
