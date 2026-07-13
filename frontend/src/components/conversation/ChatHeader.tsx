import { UserAvatar } from "../UserAvatar";
import type { UserLite } from "../../types/chat";
export type ChatHeaderNotice = {
  id: string;
  message: string;
  tone?: "neutral" | "warning" | "danger";
};

type ChatHeaderProps = {
  title: string;
  subtitle: string;
  avatarPerson?: Partial<UserLite> | null;
  isGroup?: boolean;
  notices: ChatHeaderNotice[];
  detailsOpen: boolean;
  startingCallType: "voice" | "video" | null;
  onBack: () => void;
  onToggleDetails: () => void;
  onStartVoiceCall: () => void;
  onStartVideoCall: () => void;
};

function BackIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6" /></svg>;
}

function PhoneIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7.7 4.5h2.6c.4 0 .8.3.9.7l.6 2.7a1 1 0 0 1-.3 1l-1.8 1.5a13.2 13.2 0 0 0 3.4 3.4l1.5-1.8a1 1 0 0 1 1-.3l2.7.6c.4.1.7.5.7.9v2.6c0 .6-.4 1-.9 1.1-.7.1-1.3.2-2 .2-7 0-12.6-5.6-12.6-12.6 0-.7.1-1.3.2-2 .1-.5.5-.9 1.1-.9Z" /></svg>;
}

function VideoIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="6.5" width="12" height="11" rx="2.5" /><path d="m15.5 10 5-2.5v9l-5-2.5" /></svg>;
}

function InfoIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" /><path d="M12 10.5v5" /><circle cx="12" cy="7.8" r="1" className="ms-chat-header__icon-fill" /></svg>;
}

export function ChatHeader({
  title,
  subtitle,
  avatarPerson,
  isGroup = false,
  notices,
  detailsOpen,
  startingCallType,
  onBack,
  onToggleDetails,
  onStartVoiceCall,
  onStartVideoCall,
}: ChatHeaderProps) {
  return (
    <header className="ms-chat-header">
      <div className="ms-chat-header__main">
        <button type="button" className="ms-icon-button ms-chat-header__back" onClick={onBack} aria-label="Back to conversations">
          <BackIcon />
        </button>

        <button
          type="button"
          className="ms-chat-header__profile"
          onClick={onToggleDetails}
          aria-label={detailsOpen ? `Close details for ${title}` : `Open details for ${title}`}
          aria-expanded={detailsOpen}
        >
          <UserAvatar
            person={avatarPerson ?? { display_name: title }}
            size="md"
            shape={isGroup ? "rounded" : "circle"}
            showPresence={!isGroup}
            className="ms-chat-header__avatar"
            decorative
          />
          <span className="ms-chat-header__identity">
            <h1>{title}</h1>
            <p>{subtitle}</p>
          </span>
        </button>

        <div className="ms-chat-header__actions" aria-label="Conversation actions">
          <button
            type="button"
            className="ms-icon-button"
            title="Start a voice call"
            disabled={Boolean(startingCallType)}
            onClick={onStartVoiceCall}
            aria-label="Start voice call"
          >
            {startingCallType === "voice" ? <span className="ms-chat-header__busy">…</span> : <PhoneIcon />}
          </button>
          <button
            type="button"
            className="ms-icon-button"
            title="Start a video call"
            disabled={Boolean(startingCallType)}
            onClick={onStartVideoCall}
            aria-label="Start video call"
          >
            {startingCallType === "video" ? <span className="ms-chat-header__busy">…</span> : <VideoIcon />}
          </button>
          <button
            type="button"
            className={`ms-icon-button ${detailsOpen ? "is-active" : ""}`}
            onClick={onToggleDetails}
            aria-label={detailsOpen ? "Close conversation details" : "Open conversation details"}
            aria-pressed={detailsOpen}
          >
            <InfoIcon />
          </button>
        </div>
      </div>
      {notices.length ? (
        <div className="ms-chat-header__notices" role="status" aria-live="polite">
          {notices.map((notice) => (
            <div key={notice.id} className={`ms-chat-notice ms-chat-notice--${notice.tone || "neutral"}`}>
              {notice.message}
            </div>
          ))}
        </div>
      ) : null}
    </header>
  );
}
