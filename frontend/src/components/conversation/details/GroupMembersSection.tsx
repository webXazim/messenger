import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authApi } from "../../../api/auth";
import { chatApi } from "../../../api/chat";
import { ConfirmDialog } from "../../ConfirmDialog";
import { useAuth } from "../../../contexts/AuthContext";
import { parseApiError } from "../../../lib/apiErrors";
import type { Conversation, Participant } from "../../../types/chat";
import type { UserSearchResult } from "../../../types/auth";
import { DetailsSection } from "./DetailsSection";
import { UserAvatar } from "../../UserAvatar";
import { personPresenceText } from "../../../lib/personPresentation";

function participantLabel(participant: Participant) {
  return participant.user.display_name || participant.user.username;
}

function roleLabel(role: string) {
  if (role === "owner") return "Owner";
  if (role === "admin") return "Admin";
  return "Member";
}

type MemberAction =
  | { kind: "remove"; participant: Participant }
  | { kind: "role"; participant: Participant; nextRole: "member" | "admin" }
  | { kind: "transfer"; participant: Participant }
  | { kind: "ban"; participant: Participant };

export function GroupMembersSection({ conversation, participants }: { conversation: Conversation; participants: Participant[] }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<MemberAction | null>(null);
  const [confirmationError, setConfirmationError] = useState<string | null>(null);
  const currentParticipant = participants.find((participant) => String(participant.user.id) === String(user?.id || ""));
  const currentRole = String(currentParticipant?.role || "member").toLowerCase();
  const canManage = currentRole === "owner" || currentRole === "admin";
  const isOwner = currentRole === "owner";
  const existingIds = useMemo(() => new Set(participants.map((participant) => String(participant.user.id))), [participants]);

  const friendsQuery = useQuery({
    queryKey: ["friend-requests", "friends"],
    queryFn: ({ signal }) => authApi.listFriendRequests("friends", signal),
    enabled: canManage,
  });
  const inviteLinksQuery = useQuery({
    queryKey: ["conversation-invite-links", conversation.id],
    queryFn: ({ signal }) => chatApi.listInviteLinks(conversation.id, signal),
    enabled: canManage,
  });

  const friends = useMemo<UserSearchResult[]>(() => {
    const currentUserId = String(user?.id || "");
    const seen = new Set<string>();
    return (friendsQuery.data ?? [])
      .map((request) => String(request.from_user.id) === currentUserId ? request.to_user : request.from_user)
      .filter((friend) => {
        const id = String(friend.id);
        if (!id || existingIds.has(id) || seen.has(id)) return false;
        seen.add(id);
        return true;
      });
  }, [existingIds, friendsQuery.data, user?.id]);

  const filteredFriends = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return friends;
    return friends.filter((friend) => `${friend.display_name || friend.full_name || friend.username} ${friend.username}`.toLowerCase().includes(needle));
  }, [friends, query]);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["conversation", conversation.id] }),
      queryClient.invalidateQueries({ queryKey: ["conversations"] }),
      queryClient.invalidateQueries({ queryKey: ["conversation-invite-links", conversation.id] }),
    ]);
  };

  const addMembers = useMutation({
    mutationFn: (ids: string[]) => chatApi.addGroupParticipants(conversation.id, Array.from(new Set(ids))),
    onMutate: () => {
      setError(null);
      setNotice(null);
    },
    onSuccess: async (_conversation, ids) => {
      setSelectedIds([]);
      setQuery("");
      setNotice(`${ids.length} member${ids.length === 1 ? "" : "s"} added.`);
      await refresh();
    },
    onError: (mutationError) => setError(parseApiError(mutationError, "Could not add members.").message),
  });

  const manageMember = useMutation({
    mutationFn: async (action: MemberAction) => {
      const userId = String(action.participant.user.id);
      if (action.kind === "remove") return chatApi.removeGroupParticipant(conversation.id, userId);
      if (action.kind === "role") return chatApi.updateGroupParticipantRole(conversation.id, userId, action.nextRole);
      if (action.kind === "transfer") return chatApi.transferGroupOwnership(conversation.id, userId);
      return chatApi.banGroupParticipant(conversation.id, userId);
    },
    onMutate: () => {
      setConfirmationError(null);
      setError(null);
      setNotice(null);
    },
    onSuccess: async (_result, action) => {
      const name = participantLabel(action.participant);
      if (action.kind === "remove") setNotice(`${name} was removed from the group.`);
      if (action.kind === "role") setNotice(`${name} is now ${action.nextRole === "admin" ? "an admin" : "a member"}.`);
      if (action.kind === "transfer") setNotice(`Ownership transferred to ${name}.`);
      if (action.kind === "ban") setNotice(`${name} was removed and cannot rejoin with the current invite link.`);
      setConfirmation(null);
      await refresh();
    },
    onError: (mutationError) => setConfirmationError(parseApiError(mutationError, "This group action could not be completed.").message),
  });

  const createInvite = useMutation({
    mutationFn: () => chatApi.createInviteLink(conversation.id, { expires_in_hours: 24 * 7, max_uses: 100 }),
    onMutate: () => {
      setError(null);
      setNotice(null);
    },
    onSuccess: async () => {
      setNotice("Invite link created. It expires in seven days.");
      await refresh();
    },
    onError: (mutationError) => setError(parseApiError(mutationError, "Could not create an invite link.").message),
  });

  const revokeInvite = useMutation({
    mutationFn: (inviteId: string) => chatApi.revokeInviteLink(conversation.id, inviteId),
    onMutate: () => {
      setError(null);
      setNotice(null);
    },
    onSuccess: async () => {
      setNotice("Invite link revoked.");
      await refresh();
    },
    onError: (mutationError) => setError(parseApiError(mutationError, "Could not revoke this invite link.").message),
  });

  const activeInvite = (inviteLinksQuery.data ?? []).find((invite) => invite.is_active !== false && !invite.revoked_at);

  const confirmationCopy = useMemo(() => {
    if (!confirmation) return null;
    const name = participantLabel(confirmation.participant);
    if (confirmation.kind === "remove") {
      return {
        title: `Remove ${name}?`,
        description: "They will stop receiving new messages and will need to be added again to return.",
        confirmLabel: "Remove member",
        tone: "danger" as const,
      };
    }
    if (confirmation.kind === "ban") {
      return {
        title: `Prevent ${name} from rejoining?`,
        description: "They will be removed immediately and cannot use the current invite link to return.",
        confirmLabel: "Remove and prevent rejoining",
        tone: "danger" as const,
      };
    }
    if (confirmation.kind === "transfer") {
      return {
        title: `Make ${name} the owner?`,
        description: "They will gain full control of the group. You will remain as an admin.",
        confirmLabel: "Transfer ownership",
        tone: "default" as const,
      };
    }
    return {
      title: confirmation.nextRole === "admin" ? `Make ${name} an admin?` : `Remove admin access from ${name}?`,
      description: confirmation.nextRole === "admin"
        ? "Admins can add and remove regular members and manage invite links."
        : "They will remain in the group as a regular member.",
      confirmLabel: confirmation.nextRole === "admin" ? "Make admin" : "Make member",
      tone: "default" as const,
    };
  }, [confirmation]);

  return (
    <DetailsSection title="Members" eyebrow="Group" note={`${participants.length} total`} collapsible defaultOpen>
      {notice ? <div className="ms-details-success" role="status">{notice}</div> : null}
      {error ? <div className="ms-details-error" role="alert">{error}</div> : null}

      <div className="ms-details-member-list">
        {participants.map((participant) => {
          const name = participantLabel(participant);
          const participantRole = String(participant.role || "member").toLowerCase();
          const isSelf = String(participant.user.id) === String(user?.id || "");
          const targetIsOwner = participantRole === "owner";
          const targetIsAdmin = participantRole === "admin";
          const canModerateTarget = canManage && !isSelf && !targetIsOwner && (isOwner || !targetIsAdmin);
          return (
            <div key={participant.id} className="ms-details-member-row">
              <UserAvatar person={participant.user} size="sm" showPresence className="ms-details-member-row__avatar" decorative />
              <div className="ms-details-member-row__copy">
                <strong>{name}{isSelf ? " (You)" : ""}</strong>
                <span>{roleLabel(participantRole)} · {personPresenceText(participant.user)}</span>
              </div>
              {!isSelf && (canModerateTarget || isOwner) ? (
                <details className="ms-details-member-menu">
                  <summary aria-label={`Manage ${name}`}>Manage</summary>
                  <div>
                    {isOwner && !targetIsOwner ? (
                      <button type="button" onClick={() => setConfirmation({ kind: "role", participant, nextRole: targetIsAdmin ? "member" : "admin" })}>
                        {targetIsAdmin ? "Make member" : "Make admin"}
                      </button>
                    ) : null}
                    {isOwner && !targetIsOwner ? (
                      <button type="button" onClick={() => setConfirmation({ kind: "transfer", participant })}>Transfer ownership</button>
                    ) : null}
                    {canModerateTarget ? (
                      <button type="button" onClick={() => setConfirmation({ kind: "remove", participant })}>Remove from group</button>
                    ) : null}
                    {canModerateTarget ? (
                      <button type="button" className="is-danger" onClick={() => setConfirmation({ kind: "ban", participant })}>Prevent rejoining</button>
                    ) : null}
                  </div>
                </details>
              ) : null}
            </div>
          );
        })}
      </div>

      {isOwner && participants.length > 1 ? (
        <div className="ms-details-owner-note">Transfer ownership before leaving this group.</div>
      ) : null}

      {canManage ? (
        <details className="ms-details-group-admin">
          <summary>Add members and manage invite link</summary>
          <div className="ms-details-group-admin__body">
            <label className="ms-details-search-field">
              <span>Search friends</span>
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Name or username" />
            </label>

            <div className="ms-details-member-picker">
              {filteredFriends.slice(0, 12).map((friend) => {
                const friendId = String(friend.id);
                const selected = selectedIds.includes(friendId);
                const name = friend.display_name || friend.full_name || friend.username;
                return (
                  <button
                    key={friend.id}
                    type="button"
                    className={selected ? "is-selected" : ""}
                    aria-pressed={selected}
                    disabled={addMembers.isPending}
                    onClick={() => setSelectedIds((current) => current.includes(friendId) ? current.filter((id) => id !== friendId) : [...current, friendId])}
                  >
                    <UserAvatar person={friend} size="sm" showPresence decorative />
                    <span><strong>{name}</strong><small>@{friend.username} · {personPresenceText(friend)}</small></span>
                    <b>{selected ? "Selected" : "Add"}</b>
                  </button>
                );
              })}
              {friendsQuery.isLoading ? <div className="ms-details-empty">Loading friends…</div> : null}
              {friendsQuery.isError ? <div className="ms-details-error">Friends could not be loaded.</div> : null}
              {!friendsQuery.isLoading && !filteredFriends.length ? <div className="ms-details-empty">No friends are available to add.</div> : null}
            </div>

            <button
              type="button"
              className="ms-details-primary-button"
              disabled={!selectedIds.length || addMembers.isPending}
              onClick={() => addMembers.mutate(selectedIds)}
            >
              {addMembers.isPending ? "Adding…" : selectedIds.length ? `Add ${selectedIds.length} member${selectedIds.length === 1 ? "" : "s"}` : "Choose members"}
            </button>

            <div className="ms-details-invite-actions">
              {inviteLinksQuery.isLoading ? <span className="ms-muted">Loading invite link…</span> : null}
              {!activeInvite ? (
                <button type="button" disabled={createInvite.isPending || inviteLinksQuery.isLoading} onClick={() => createInvite.mutate()}>
                  {createInvite.isPending ? "Creating…" : "Create invite link"}
                </button>
              ) : (
                <>
                  <button type="button" onClick={async () => {
                    const value = activeInvite.join_url || `${window.location.origin}/api/v1/chat/invite-links/join/?token=${activeInvite.token}`;
                    try {
                      await navigator.clipboard.writeText(value);
                      setNotice("Invite link copied.");
                      setError(null);
                    } catch {
                      setError("The invite link could not be copied. Try again from a secure browser window.");
                    }
                  }}>Copy invite link</button>
                  <button type="button" className="is-danger" disabled={revokeInvite.isPending} onClick={() => revokeInvite.mutate(activeInvite.id)}>
                    {revokeInvite.isPending ? "Revoking…" : "Revoke link"}
                  </button>
                </>
              )}
            </div>
          </div>
        </details>
      ) : null}

      <ConfirmDialog
        open={Boolean(confirmation && confirmationCopy)}
        title={confirmationCopy?.title || "Confirm group action"}
        description={confirmationCopy?.description || "Confirm this group action."}
        confirmLabel={confirmationCopy?.confirmLabel || "Confirm"}
        tone={confirmationCopy?.tone || "default"}
        pending={manageMember.isPending}
        error={confirmationError}
        onClose={() => {
          if (!manageMember.isPending) {
            setConfirmation(null);
            setConfirmationError(null);
          }
        }}
        onConfirm={() => {
          if (confirmation) manageMember.mutate(confirmation);
        }}
      />
    </DetailsSection>
  );
}
