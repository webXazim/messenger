import { useRef } from "react";
import { useModalAccessibility } from "../../hooks/useModalAccessibility";
import type { CallParticipant } from "../../types/chat";
import { participantName } from "./callPresentation";
import { UserAvatar } from "../UserAvatar";

function CloseIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>;
}

function MicrophoneIcon({ enabled }: { enabled: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="8" y="3" width="8" height="12" rx="4" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
      {!enabled ? <path d="m4 4 16 16" /> : null}
    </svg>
  );
}

function CameraIcon({ enabled }: { enabled: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="3" y="6" width="13" height="12" rx="3" />
      <path d="m16 10 5-3v10l-5-3" />
      {!enabled ? <path d="m4 4 16 16" /> : null}
    </svg>
  );
}

function participantStatus(participant: CallParticipant) {
  if (participant.state === "joined") return "In call";
  if (participant.state === "ringing" || participant.state === "invited") return "Ringing";
  if (participant.state === "declined") return "Declined";
  if (participant.state === "left") return "Left call";
  return participant.user.is_online ? "Online" : "Offline";
}

function ParticipantRow({ participant, isSelf = false }: { participant: CallParticipant; isSelf?: boolean }) {
  return (
    <div className="ms-call-participants__row">
      <UserAvatar person={participant.user} size="md" showPresence={!isSelf} className="ms-call-participants__avatar" decorative />
      <span className="ms-call-participants__copy">
        <strong>{isSelf ? "You" : participantName(participant)}</strong>
        <small>{participantStatus(participant)}</small>
      </span>
      <span className="ms-call-participants__media" aria-label={`Microphone ${participant.audio_enabled ? "on" : "off"}, camera ${participant.video_enabled ? "on" : "off"}`}>
        <span className={participant.audio_enabled ? "" : "is-off"}><MicrophoneIcon enabled={Boolean(participant.audio_enabled)} /></span>
        <span className={participant.video_enabled ? "" : "is-off"}><CameraIcon enabled={Boolean(participant.video_enabled)} /></span>
      </span>
    </div>
  );
}

export function CallParticipantsDrawer({
  open,
  selfParticipant,
  remoteParticipants,
  audioEnabled,
  videoEnabled,
  onClose,
}: {
  open: boolean;
  selfParticipant?: CallParticipant;
  remoteParticipants: CallParticipant[];
  audioEnabled: boolean;
  videoEnabled: boolean;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useModalAccessibility<HTMLElement>({
    open,
    onClose,
    initialFocusRef: closeRef,
  });

  if (!open) return null;

  const localParticipant: CallParticipant = selfParticipant ?? {
    id: "local-user",
    state: "joined",
    audio_enabled: audioEnabled,
    video_enabled: videoEnabled,
    user: { id: "local-user", username: "you", display_name: "You" },
  };

  return (
    <div className="ms-call-participants" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <aside ref={dialogRef} className="ms-call-participants__drawer" role="dialog" aria-modal="true" aria-label="Call participants" tabIndex={-1}>
        <header className="ms-call-participants__header">
          <span>
            <strong>Participants</strong>
            <small>{remoteParticipants.length + 1} in this call</small>
          </span>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="Close participants">
            <CloseIcon />
          </button>
        </header>
        <div className="ms-call-participants__list">
          <ParticipantRow participant={{ ...localParticipant, audio_enabled: audioEnabled, video_enabled: videoEnabled }} isSelf />
          {remoteParticipants.map((participant) => <ParticipantRow key={participant.id} participant={participant} />)}
        </div>
      </aside>
    </div>
  );
}
