import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { authApi } from "../api/auth";
import { chatApi } from "../api/chat";
import { ConversationList } from "../components/ConversationList";
import { GroupChatModal } from "../components/GroupChatModal";
import { NewConversationModal } from "../components/NewConversationModal";
import { useAuth } from "../contexts/AuthContext";
import { parseApiError } from "../lib/apiErrors";
import { conversationPath } from "../lib/conversationRoute";
import type { UserSearchResult } from "../types/auth";
import type { Conversation } from "../types/chat";

function NewMessageIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

function NewGroupIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M8.5 11.5a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.5-1a2.5 2.5 0 1 0 0-5" />
      <path d="M3.5 19v-.5c0-2.4 2.2-4.3 5-4.3s5 1.9 5 4.3v.5h-10Zm10-1.8c.7-1.5 2.1-2.5 4-2.5 2.3 0 4.2 1.5 4.2 3.5v.8h-5.2" />
      <path d="M18.5 4v4M16.5 6h4" />
    </svg>
  );
}

function EmptyConversationIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 5.5h14a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3H10l-5 3v-3a3 3 0 0 1-3-3v-6a3 3 0 0 1 3-3Z" />
      <path d="M8 10h8M8 13.5h5" />
    </svg>
  );
}

function findDirectConversation(conversations: Conversation[], personId: string) {
  return conversations.find(
    (conversation) => conversation.type === "direct" && conversation.participants.some((participant) => String(participant.user.id) === String(personId)),
  );
}

export function ConversationsPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showGroupModal, setShowGroupModal] = useState(false);
  const [showNewConversationModal, setShowNewConversationModal] = useState(false);
  const [groupError, setGroupError] = useState<string | null>(null);
  const [directChatError, setDirectChatError] = useState<string | null>(null);

  const conversationsQuery = useQuery({
    queryKey: ["conversations"],
    queryFn: ({ signal }) => chatApi.listConversations(signal),
  });
  const friendsQuery = useQuery({
    queryKey: ["friend-requests", "friends"],
    queryFn: ({ signal }) => authApi.listFriendRequests("friends", signal),
  });

  const conversations = useMemo(() => conversationsQuery.data ?? [], [conversationsQuery.data]);
  const currentUserIdentity = useMemo(
    () => ({
      id: user?.id,
      username: user?.username,
      email: user?.email,
      display_name: user?.profile?.display_name || user?.display_name,
    }),
    [user],
  );
  const friends = useMemo<UserSearchResult[]>(() => {
    const currentUserId = String(user?.id || "");
    const seen = new Set<string>();
    return (friendsQuery.data ?? [])
      .map((request) => String(request.from_user.id) === currentUserId ? request.to_user : request.from_user)
      .filter((friend) => {
        const id = String(friend.id || "");
        if (!id || id === currentUserId || seen.has(id)) return false;
        seen.add(id);
        return true;
      });
  }, [friendsQuery.data, user?.id]);

  const createGroupMutation = useMutation({
    mutationFn: ({ title, uniqueName, participantIds }: { title: string; uniqueName: string; participantIds: string[] }) =>
      chatApi.createGroupConversation(title, uniqueName, participantIds),
    onMutate: () => setGroupError(null),
    onSuccess: async (conversation) => {
      setShowGroupModal(false);
      queryClient.setQueryData(["conversation", conversation.id], conversation);
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      navigate(conversationPath(conversation, currentUserIdentity));
    },
    onError: (mutationError) => {
      setGroupError(parseApiError(mutationError, "Could not create this group.").message);
    },
  });

  const directChatMutation = useMutation({
    mutationFn: async (person: UserSearchResult) => {
      const existing = findDirectConversation(conversations, person.id);
      return existing ?? chatApi.createDirectConversation(person.id);
    },
    onMutate: () => setDirectChatError(null),
    onSuccess: async (conversation) => {
      setShowNewConversationModal(false);
      queryClient.setQueryData(["conversation", conversation.id], conversation);
      queryClient.setQueryData<typeof conversations>(["conversations"], (current = []) => {
        const next = current.filter((item) => item.id !== conversation.id);
        return [conversation, ...next];
      });
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      navigate(conversationPath(conversation, currentUserIdentity));
    },
    onError: (mutationError) => {
      setDirectChatError(parseApiError(mutationError, "Unable to open this conversation.").message);
    },
  });

  const openNewConversation = () => {
    setDirectChatError(null);
    setShowNewConversationModal(true);
  };

  return (
    <div className="ms-conversations-page" aria-label="Chats">
      <aside className="ms-conversations-page__inbox">
        <header className="ms-conversations-page__header">
          <div>
            <span className="ms-conversations-page__eyebrow">Messenger</span>
            <h1>Chats</h1>
          </div>
          <div className="ms-conversations-page__actions" aria-label="Chat actions">
            <button
              type="button"
              className="ms-conversations-page__action"
              onClick={() => setShowGroupModal(true)}
              aria-label="Create group"
              title="Create group"
            >
              <NewGroupIcon />
            </button>
            <button
              type="button"
              className="ms-conversations-page__action ms-conversations-page__action--primary"
              onClick={openNewConversation}
              aria-label="Start new conversation"
              title="Start new conversation"
            >
              <NewMessageIcon />
            </button>
          </div>
        </header>

        {conversationsQuery.isLoading ? (
          <div className="ms-conversations-state" role="status" aria-live="polite">
            <span className="ms-conversations-state__spinner" aria-hidden="true" />
            <strong>Loading chats</strong>
            <span>Your conversations will appear here.</span>
          </div>
        ) : conversationsQuery.isError ? (
          <div className="ms-conversations-state ms-conversations-state--error" role="alert">
            <strong>Chats could not be loaded</strong>
            <span>{parseApiError(conversationsQuery.error, "Check your connection and try again.").message}</span>
            <button type="button" onClick={() => void conversationsQuery.refetch()}>Retry</button>
          </div>
        ) : (
          <ConversationList
            conversations={conversations}
            currentUserId={String(user?.id || "")}
            currentUser={currentUserIdentity}
            variant="inbox"
            onlineFriends={friends}
            openingFriendId={directChatMutation.isPending ? String(directChatMutation.variables?.id || "") : null}
            onOpenFriend={(friend) => directChatMutation.mutate(friend)}
          />
        )}
      </aside>

      <section className="ms-conversations-page__empty" aria-label="No conversation selected">
        <div className="ms-conversations-page__empty-icon"><EmptyConversationIcon /></div>
        <h2>Select a chat</h2>
        <p>Choose a conversation from the list or start a private chat.</p>
        <button type="button" className="ms-button ms-button--primary" onClick={openNewConversation}>New conversation</button>
      </section>

      {showNewConversationModal ? (
        <NewConversationModal
          contacts={friends}
          conversations={conversations}
          currentUserId={String(user?.id || "")}
          busyUserId={directChatMutation.isPending ? String(directChatMutation.variables?.id || "") : null}
          error={directChatError}
          onClose={() => {
            if (directChatMutation.isPending) return;
            setShowNewConversationModal(false);
          }}
          onSelect={(person) => directChatMutation.mutate(person)}
        />
      ) : null}

      {showGroupModal ? (
        <GroupChatModal
          friends={friends}
          busy={createGroupMutation.isPending}
          error={groupError}
          onClose={() => setShowGroupModal(false)}
          onCreate={(title, uniqueName, participantIds) => createGroupMutation.mutate({ title, uniqueName, participantIds })}
        />
      ) : null}
    </div>
  );
}
