import { UserAvatar } from "../../UserAvatar";
import { personPresenceText } from "../../../lib/personPresentation";
import type { Participant } from "../../../types/chat";

function PhoneIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7.7 4.5h2.6c.4 0 .8.3.9.7l.6 2.7a1 1 0 0 1-.3 1l-1.8 1.5a13.2 13.2 0 0 0 3.4 3.4l1.5-1.8a1 1 0 0 1 1-.3l2.7.6c.4.1.7.5.7.9v2.6c0 .6-.4 1-.9 1.1-.7.1-1.3.2-2 .2-7 0-12.6-5.6-12.6-12.6 0-.7.1-1.3.2-2 .1-.5.5-.9 1.1-.9Z" /></svg>;
}

function VideoIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="6.5" width="12" height="11" rx="2.5" /><path d="m15.5 10 5-2.5v9l-5-2.5" /></svg>;
}

export function ConversationProfileSection({
  title,
  isGroup,
  participants,
  directParticipant,
  viewerState,
  pendingState,
  onStartVoiceCall,
  onStartVideoCall,
  onToggleState,
  onLeave,
  onDeleteConversation,
  leaveDisabled,
  leaveHint,
  onBlock,
}: {
  title: string;
  isGroup: boolean;
  participants: Participant[];
  directParticipant?: Participant;
  viewerState?: { isPinned: boolean; isMuted: boolean; isArchived: boolean };
  pendingState?: "pin" | "mute" | "archive" | null;
  onStartVoiceCall: () => void;
  onStartVideoCall: () => void;
  onToggleState: (state: "mute" | "archive" | "pin") => void;
  onLeave: () => void;
  onDeleteConversation?: () => void;
  leaveDisabled?: boolean;
  leaveHint?: string;
  onBlock?: () => void;
}) {
  const subtitle = isGroup
    ? `${participants.length} member${participants.length === 1 ? "" : "s"}`
    : directParticipant ? personPresenceText(directParticipant.user) : "Direct conversation";

  return (
    <section className="ms-details-profile">
      <UserAvatar
        person={isGroup ? { display_name: title } : directParticipant?.user ?? { display_name: title }}
        size="xl"
        shape={isGroup ? "rounded" : "circle"}
        showPresence={!isGroup}
        className={`ms-details-profile__avatar ${isGroup ? "is-group" : ""}`}
        decorative
      />
      <div className="ms-details-profile__identity">
        <h2>{title}</h2>
        <p>{subtitle}</p>
      </div>

      <div className="ms-details-profile__call-actions" aria-label="Call actions">
        <button type="button" onClick={onStartVoiceCall}>
          <PhoneIcon />
          <strong>Audio</strong>
        </button>
        <button type="button" onClick={onStartVideoCall}>
          <VideoIcon />
          <strong>Video</strong>
        </button>
      </div>

      <div className="ms-details-profile__actions" aria-label="Conversation management">
        <button type="button" disabled={Boolean(pendingState)} onClick={() => onToggleState("pin")} aria-pressed={viewerState?.isPinned}>
          <span aria-hidden="true">⌖</span>
          <strong>{pendingState === "pin" ? "Updating…" : viewerState?.isPinned ? "Unpin" : "Pin"}</strong>
        </button>
        <button type="button" disabled={Boolean(pendingState)} onClick={() => onToggleState("mute")} aria-pressed={viewerState?.isMuted}>
          <span aria-hidden="true">◌</span>
          <strong>{pendingState === "mute" ? "Updating…" : viewerState?.isMuted ? "Unmute" : "Mute"}</strong>
        </button>
        <button type="button" disabled={Boolean(pendingState)} onClick={() => onToggleState("archive")} aria-pressed={viewerState?.isArchived}>
          <span aria-hidden="true">□</span>
          <strong>{pendingState === "archive" ? "Updating…" : viewerState?.isArchived ? "Unarchive" : "Archive"}</strong>
        </button>
      </div>

      <div className="ms-details-profile__destructive">
        {isGroup ? (
          <>
            <button type="button" disabled={leaveDisabled} onClick={onLeave}>Leave group</button>
            {leaveHint ? <small>{leaveHint}</small> : null}
          </>
        ) : onBlock ? (
          <button type="button" onClick={onBlock}>Block contact</button>
        ) : null}
        {onDeleteConversation ? <button type="button" onClick={onDeleteConversation}>Delete chat</button> : null}
      </div>
    </section>
  );
}
