import type { UserSearchResult } from "../../types/auth";
import type { Conversation, UserLite } from "../../types/chat";

export type ConversationListBaseProps = {
  conversations: Conversation[];
  currentUserId?: string;
  currentUser?: Partial<UserLite> | null;
  searchInputId?: string;
  onlineFriends?: UserSearchResult[];
  openingFriendId?: string | null;
  onOpenFriend?: (friend: UserSearchResult) => void;
};

export type ConversationFilter = "all" | "unread" | "groups" | "archived";
