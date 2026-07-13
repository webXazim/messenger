import { useMemo } from "react";
import { useConversationListPreferences } from "../../hooks/useConversationListPreferences";
import { ConversationListControls } from "./ConversationListControls";
import { OnlineFriendsStrip } from "./OnlineFriendsStrip";
import { ConversationRow } from "./ConversationRow";
import { conversationListEmptyCopy, filterConversationsForInbox } from "./conversationFiltering";
import type { ConversationListBaseProps } from "./types";

export function ConversationInboxList({ conversations, currentUserId, currentUser, onlineFriends, openingFriendId, onOpenFriend }: ConversationListBaseProps) {
  const { search, filter, setSearch, setFilter } = useConversationListPreferences();
  const filteredConversations = useMemo(
    () => filterConversationsForInbox({ conversations, currentUserId, currentUser, filter, search }),
    [conversations, currentUser, currentUserId, filter, search],
  );
  const emptyCopy = conversationListEmptyCopy(filter, search);

  return (
    <section className="ms-inbox-list" aria-label="Chat list">
      <OnlineFriendsStrip friends={onlineFriends} busyUserId={openingFriendId} onOpenFriend={onOpenFriend} />
      <ConversationListControls
        search={search}
        filter={filter}
        onSearchChange={setSearch}
        onFilterChange={setFilter}
      />

      <div className="ms-inbox-list__scroll ms-scroll-region">
        {filteredConversations.map((conversation) => (
          <ConversationRow
            key={conversation.id}
            conversation={conversation}
            currentUserId={currentUserId}
            currentUser={currentUser}
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
