import { useMemo } from "react";
import { useConversationListPreferences } from "../../hooks/useConversationListPreferences";
import { ConversationListControls } from "./ConversationListControls";
import { StatusTray } from "./StatusTray";
import { ConversationRow } from "./ConversationRow";
import { conversationListEmptyCopy, filterConversationsForInbox } from "./conversationFiltering";
import { applyKnownOnlinePresence } from "./conversationPresentation";
import type { ConversationListBaseProps } from "./types";

export function ConversationSidebarList({
  conversations,
  currentUserId,
  currentUser,
  searchInputId,
  onlineFriends,
  openingFriendId,
  onOpenFriend,
  onPrefetchConversation,
}: ConversationListBaseProps) {
  const { search, filter, setSearch, setFilter } = useConversationListPreferences();
  const presenceAwareConversations = useMemo(
    () => applyKnownOnlinePresence(conversations, onlineFriends),
    [conversations, onlineFriends],
  );
  const filteredConversations = useMemo(
    () => filterConversationsForInbox({ conversations: presenceAwareConversations, currentUserId, currentUser, filter, search }),
    [presenceAwareConversations, currentUser, currentUserId, filter, search],
  );
  const unreadCount = useMemo(
    () => presenceAwareConversations.filter((conversation) => conversation.unread_count > 0).length,
    [presenceAwareConversations],
  );
  const emptyCopy = conversationListEmptyCopy(filter, search);

  return (
    <section className="ms-chat-inbox" aria-label="Chat list">
      <header className="ms-chat-inbox__header">
        <div>
          <span>Messenger</span>
          <h2>Chats</h2>
        </div>
        {unreadCount ? <strong aria-label={`${unreadCount} unread chats`}>{unreadCount > 99 ? "99+" : unreadCount}</strong> : null}
      </header>

      <ConversationListControls
        search={search}
        filter={filter}
        searchInputId={searchInputId}
        onSearchChange={setSearch}
        onFilterChange={setFilter}
        middleContent={<StatusTray currentUser={currentUser} friends={onlineFriends} busyUserId={openingFriendId} onOpenFriend={onOpenFriend} />}
      />

      <div className="ms-inbox-list__scroll ms-scroll-region">
        {filteredConversations.map((conversation) => (
          <ConversationRow
            key={conversation.id}
            conversation={conversation}
            currentUserId={currentUserId}
            currentUser={currentUser}
            onPrefetch={onPrefetchConversation}
          />
        ))}
        {!filteredConversations.length ? (
          <div className="ms-inbox-empty">
            <strong>{emptyCopy.title}</strong>
            <span>{emptyCopy.description}</span>
          </div>
        ) : null}
      </div>
    </section>
  );
}
