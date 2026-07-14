import { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import { conversationDisplayName } from "./ConversationList";
import { ConversationProfileSection } from "./conversation/details/ConversationProfileSection";
import { SharedContentSection } from "./conversation/details/SharedContentSection";
import { ConversationNotificationsSection } from "./conversation/details/ConversationNotificationsSection";
import { ConversationSecuritySection } from "./conversation/details/ConversationSecuritySection";
import { GroupMembersSection } from "./conversation/details/GroupMembersSection";
import type { Conversation } from "../types/chat";
import type { ConversationMediaItem, ConversationNotificationSettings } from "../api/chat";
import type { ConversationE2EEKeyMaterial } from "../types/chat";
import type { ConversationEncryptionReadiness } from "../lib/e2ee";
import { useModalAccessibility } from "../hooks/useModalAccessibility";

type MediaKind = "all" | "image" | "video" | "audio" | "file";

export function ConversationDetailsPanel({
  open,
  conversation,
  notifications,
  media,
  allMedia,
  mediaKind,
  securityMaterial,
  securityReadiness,
  onChangeMediaKind,
  onClose,
  onStartVoiceCall,
  onStartVideoCall,
  onToggleNotification,
  onSetMuteHours,
  onToggleConversationState,
  conversationStatePending,
  onLeaveConversation,
  onDeleteConversation,
  onBlockContact,
}: {
  open: boolean;
  conversation?: Conversation;
  notifications?: ConversationNotificationSettings;
  media: ConversationMediaItem[];
  allMedia: ConversationMediaItem[];
  mediaKind: MediaKind;
  securityMaterial?: ConversationE2EEKeyMaterial;
  securityReadiness?: ConversationEncryptionReadiness;
  onChangeMediaKind: (kind: MediaKind) => void;
  onClose: () => void;
  onStartVoiceCall: () => void;
  onStartVideoCall: () => void;
  onToggleNotification: (patch: Partial<ConversationNotificationSettings>) => void;
  onSetMuteHours: (hours: number | null) => void;
  onToggleConversationState: (state: "mute" | "archive" | "pin") => void;
  conversationStatePending?: "mute" | "archive" | "pin" | null;
  onLeaveConversation: () => void;
  onDeleteConversation: () => void;
  onBlockContact?: (participantUserId: string) => void;
}) {
  const { user } = useAuth();
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const [modalMode, setModalMode] = useState(() => typeof window !== "undefined" && window.matchMedia("(max-width: 1180px)").matches);
  const panelRef = useModalAccessibility<HTMLElement>({
    open: open && modalMode,
    onClose,
    initialFocusRef: closeRef,
  });
  const currentUserId = String(user?.id || "");
  const currentIdentity = useMemo(() => ({
    id: user?.id,
    username: user?.username,
    email: user?.email,
    display_name: user?.profile?.display_name || user?.display_name,
  }), [user]);
  const displayTitle = conversation
    ? conversationDisplayName(conversation, currentUserId, currentIdentity)
    : "Conversation";
  const activeParticipants = useMemo(
    () => (conversation?.participants ?? []).filter((participant) => !participant.left_at && !participant.banned_at),
    [conversation?.participants],
  );
  const isGroup = conversation?.type === "group";
  const otherParticipant = activeParticipants.find((participant) => String(participant.user.id) !== currentUserId);
  const directParticipant = otherParticipant ?? activeParticipants[0];
  const viewerParticipant = activeParticipants.find((participant) => String(participant.user.id) === currentUserId);
  const viewerState = viewerParticipant ? {
    isPinned: Boolean(viewerParticipant.is_pinned),
    isMuted: Boolean(viewerParticipant.is_muted),
    isArchived: Boolean(viewerParticipant.is_archived),
  } : undefined;

  useEffect(() => {
    const media = window.matchMedia("(max-width: 1180px)");
    const sync = () => setModalMode(media.matches);
    sync();
    media.addEventListener?.("change", sync);
    return () => media.removeEventListener?.("change", sync);
  }, []);

  useEffect(() => {
    if (!open || modalMode) return;
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [modalMode, onClose, open]);

  if (!open) return null;

  return (
    <aside
      ref={panelRef}
      className="ms-conversation-details"
      role="dialog"
      aria-modal={modalMode || undefined}
      aria-labelledby="conversation-details-title"
      tabIndex={-1}
    >
      <header className="ms-conversation-details__header">
        <div>
          <span>{isGroup ? "Group" : "Conversation"}</span>
          <h2 id="conversation-details-title">Details</h2>
        </div>
        <button ref={closeRef} type="button" className="ms-conversation-details__close" onClick={onClose} aria-label="Close conversation details">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17" /></svg>
        </button>
      </header>

      <div className="ms-conversation-details__scroll">
        <ConversationProfileSection
          title={displayTitle}
          isGroup={Boolean(isGroup)}
          participants={activeParticipants}
          directParticipant={directParticipant}
          viewerState={viewerState}
          pendingState={conversationStatePending}
          onStartVoiceCall={onStartVoiceCall}
          onStartVideoCall={onStartVideoCall}
          onToggleState={onToggleConversationState}
          onLeave={onLeaveConversation}
          onDeleteConversation={!isGroup || viewerParticipant?.role === "owner" ? onDeleteConversation : undefined}
          leaveDisabled={Boolean(isGroup && viewerParticipant?.role === "owner" && activeParticipants.length > 1)}
          leaveHint={isGroup && viewerParticipant?.role === "owner" && activeParticipants.length > 1 ? "Transfer ownership before leaving." : undefined}
          onBlock={otherParticipant && onBlockContact ? () => onBlockContact(String(otherParticipant.user.id)) : undefined}
        />

        <SharedContentSection
          allMedia={allMedia}
          visibleMedia={media}
          mediaKind={mediaKind}
          currentUserId={currentUserId}
          onChangeMediaKind={onChangeMediaKind}
        />

        {isGroup && conversation ? (
          <GroupMembersSection conversation={conversation} participants={activeParticipants} />
        ) : null}

        <ConversationNotificationsSection
          notifications={notifications}
          onToggleNotification={onToggleNotification}
          onSetMuteHours={onSetMuteHours}
        />

        <ConversationSecuritySection
          conversation={conversation}
          participants={activeParticipants}
          currentUserId={currentUserId}
          material={securityMaterial}
          readiness={securityReadiness}
        />
      </div>
    </aside>
  );
}
