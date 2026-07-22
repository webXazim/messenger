import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { authApi } from "../api/auth";
import { chatApi } from "../api/chat";
import { GroupChatModal } from "../components/GroupChatModal";
import { MessengerPageHeader } from "../components/pages/MessengerPageHeader";
import { useAuth } from "../contexts/AuthContext";
import { parseApiError } from "../lib/apiErrors";
import { conversationPath } from "../lib/conversationRoute";
import { mergeConversationListsPreservingPresence } from "../lib/realtimeCache";
import type { Conversation } from "../types/chat";

function initials(value: string) {
  return value.trim().split(/\s+/).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "G";
}

function lastActivity(value?: string | null) {
  if (!value) return "No activity yet";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "No activity yet";
  const now = Date.now();
  const diffMinutes = Math.max(0, Math.floor((now - date.getTime()) / 60_000));
  if (diffMinutes < 1) return "Just now";
  if (diffMinutes < 60) return `${diffMinutes}m ago`;
  if (diffMinutes < 24 * 60) return `${Math.floor(diffMinutes / 60)}h ago`;
  if (diffMinutes < 7 * 24 * 60) return `${Math.floor(diffMinutes / (24 * 60))}d ago`;
  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}

function messagePreview(text?: string | null) {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  return normalized || "No messages yet.";
}

export function GroupsPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [showGroupModal, setShowGroupModal] = useState(false);
  const [groupError, setGroupError] = useState<string | null>(null);

  const conversationsQuery = useQuery({
    queryKey: ["conversations"],
    queryFn: ({ signal }) => chatApi.listConversations(signal),
    structuralSharing: (current, incoming) => mergeConversationListsPreservingPresence(current as Conversation[] | undefined, incoming as Conversation[]),
  });
  const friendsQuery = useQuery({
    queryKey: ["friend-requests", "friends"],
    queryFn: ({ signal }) => authApi.listFriendRequests("friends", signal),
  });

  const allGroups = useMemo(
    () => (conversationsQuery.data ?? [])
      .filter((conversation) => conversation.type === "group")
      .sort((a, b) => new Date(b.last_message_at || b.last_message?.created_at || 0).getTime() - new Date(a.last_message_at || a.last_message?.created_at || 0).getTime()),
    [conversationsQuery.data],
  );

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return allGroups;
    return allGroups.filter((conversation) => {
      const participants = conversation.participants.map((participant) => participant.user.display_name || participant.user.username).join(" ");
      return `${conversation.title || "Group chat"} ${participants} ${conversation.last_message?.text || ""}`.toLowerCase().includes(needle);
    });
  }, [allGroups, query]);

  const friends = useMemo(() => {
    const currentUserId = String(user?.id || "");
    return (friendsQuery.data ?? []).map((request) => String(request.from_user.id) === currentUserId ? request.to_user : request.from_user);
  }, [friendsQuery.data, user?.id]);

  const createGroupMutation = useMutation({
    mutationFn: ({ title, uniqueName, participantIds }: { title: string; uniqueName: string; participantIds: string[] }) => chatApi.createGroupConversation(title, uniqueName, participantIds),
    onMutate: () => setGroupError(null),
    onSuccess: async (conversation) => {
      queryClient.setQueryData(["conversations"], (current: unknown) => {
        if (!Array.isArray(current)) return [conversation];
        return [conversation, ...current.filter((item) => item && typeof item === "object" && "id" in item && item.id !== conversation.id)];
      });
      setShowGroupModal(false);
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      navigate(conversationPath(conversation, user));
    },
    onError: (error) => setGroupError(parseApiError(error, "Could not create this group.").message),
  });

  const unreadGroups = allGroups.filter((group) => group.unread_count > 0).length;

  return (
    <div className="ms-workspace-page ms-groups-page">
      <MessengerPageHeader
        eyebrow="Groups"
        title="Groups"
        description="Return to shared conversations and create a group with people you already know."
        stats={[
          { label: "groups", value: allGroups.length },
          { label: "unread", value: unreadGroups },
        ]}
        actions={(
          <button
            type="button"
            className="ms-button ms-button--primary"
            disabled={friendsQuery.isLoading}
            onClick={() => {
              setGroupError(null);
              setShowGroupModal(true);
            }}
          >
            Create group
          </button>
        )}
      />

      <section className="ms-page-surface ms-groups-page__toolbar">
        <label className="ms-groups-page__search">
          <span aria-hidden="true">⌕</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search groups or members" aria-label="Search groups" />
          {query ? <button type="button" onClick={() => setQuery("")} aria-label="Clear group search">×</button> : null}
        </label>
      </section>

      {conversationsQuery.isError ? (
        <div className="ms-page-error">
          Groups could not be loaded. <button type="button" onClick={() => void conversationsQuery.refetch()}>Retry</button>
        </div>
      ) : null}
      {friendsQuery.isError ? (
        <div className="ms-page-error">
          Friends could not be loaded for group creation. <button type="button" onClick={() => void friendsQuery.refetch()}>Retry</button>
        </div>
      ) : null}

      <section className="ms-groups-page__list" aria-live="polite">
        {conversationsQuery.isLoading ? <div className="ms-page-surface ms-page-empty">Loading groups…</div> : null}
        {groups.map((group) => {
          const title = group.title || "Group chat";
          const activeMembers = group.participants.filter((participant) => !participant.left_at && !participant.banned_at);
          const preview = messagePreview(group.last_message?.text);
          return (
            <Link key={group.id} to={conversationPath(group, user)} className={`ms-group-row ${group.unread_count > 0 ? "has-unread" : ""}`}>
              <span className="ms-group-row__avatar" aria-hidden="true">{initials(title)}</span>
              <span className="ms-group-row__main">
                <span className="ms-group-row__heading">
                  <strong>{title}</strong>
                  <time dateTime={group.last_message_at || group.last_message?.created_at || undefined}>{lastActivity(group.last_message_at || group.last_message?.created_at)}</time>
                </span>
                <span className="ms-group-row__preview">{preview}</span>
                <span className="ms-group-row__meta">{activeMembers.length} member{activeMembers.length === 1 ? "" : "s"}</span>
              </span>
              {group.unread_count > 0 ? <span className="ms-group-row__unread" aria-label={`${group.unread_count} unread messages`}>{group.unread_count > 99 ? "99+" : group.unread_count}</span> : <span className="ms-group-row__arrow" aria-hidden="true">›</span>}
            </Link>
          );
        })}
        {!conversationsQuery.isLoading && !groups.length ? (
          <div className="ms-page-surface ms-page-empty">
            {query.trim() ? "No groups match this search." : "No groups yet. Create one when you need a shared conversation."}
          </div>
        ) : null}
      </section>

      {showGroupModal ? (
        <GroupChatModal
          friends={friends}
          currentUserId={user?.id}
          busy={createGroupMutation.isPending}
          error={groupError}
          onClose={() => {
            if (!createGroupMutation.isPending) setShowGroupModal(false);
          }}
          onCreate={(title, uniqueName, participantIds) => createGroupMutation.mutate({ title, uniqueName, participantIds })}
        />
      ) : null}
    </div>
  );
}
