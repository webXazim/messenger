import type { MutableRefObject } from "react";
import type { CallParticipant } from "../../types/chat";
import type { CallViewState } from "./callPresentation";
import { formatElapsed, participantName } from "./callPresentation";
import { UserAvatar } from "../UserAvatar";

function BackIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6" /></svg>;
}

function MicrophoneIcon({ muted }: { muted: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="8" y="3" width="8" height="12" rx="4" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
      {muted ? <path d="m4 4 16 16" /> : null}
    </svg>
  );
}

function SpeakerIcon({ muted }: { muted: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 10v4h4l5 4V6l-5 4H4Z" />
      {muted ? <path d="m16 9 5 6M21 9l-5 6" /> : <path d="M16 9.5a4 4 0 0 1 0 5M18.5 7a7.5 7.5 0 0 1 0 10" />}
    </svg>
  );
}

function HangupIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 15.5c4.7-4 9.3-4 14 0l-2.5 3-3-2v-2.2a9 9 0 0 0-3 0v2.2l-3 2-2.5-3Z" /></svg>;
}

function AcceptIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3.8h3l1.2 4-2 1.7a14.4 14.4 0 0 0 5.3 5.3l1.7-2 4 1.2v3c0 1.1-.9 2-2 2C10.9 19 5 13.1 5 5.8c0-1.1.9-2 2-2Z" /></svg>;
}

export function AudioCallScreen({
  remoteParticipant,
  remoteParticipants,
  isGroupCall,
  viewState,
  elapsedSeconds,
  ringRemainingSeconds,
  audioEnabled,
  speakerEnabled,
  busy,
  canAccept,
  accepting,
  mediaError,
  connectionNeedsHelp,
  remotePlaybackBlocked,
  qualityMessage,
  remoteAudioRef,
  onLeave,
  onAccept,
  onToggleAudio,
  onToggleSpeaker,
  onHangup,
  onRetryMedia,
  onRestartConnection,
  onEnableSound,
}: {
  remoteParticipant?: CallParticipant;
  remoteParticipants: CallParticipant[];
  isGroupCall: boolean;
  viewState: CallViewState;
  elapsedSeconds: number;
  ringRemainingSeconds: number;
  audioEnabled: boolean;
  speakerEnabled: boolean;
  busy: boolean;
  canAccept: boolean;
  accepting: boolean;
  mediaError: string | null;
  connectionNeedsHelp: boolean;
  remotePlaybackBlocked: boolean;
  qualityMessage?: string;
  remoteAudioRef: MutableRefObject<HTMLAudioElement | null>;
  onLeave: () => void;
  onAccept: () => void;
  onToggleAudio: () => void;
  onToggleSpeaker: () => void;
  onHangup: () => void;
  onRetryMedia: () => void;
  onRestartConnection: () => void;
  onEnableSound: () => void;
}) {
  const displayName = isGroupCall
    ? `${Math.max(remoteParticipants.length, 1) + 1}-person call`
    : participantName(remoteParticipant);
  const showDuration = viewState.label === "Connected" || elapsedSeconds > 0;
  const showRingCountdown = !showDuration && ringRemainingSeconds > 0 && ["Calling…", "Ringing…", "Incoming call", "Waiting for connection…"].includes(viewState.label);

  return (
    <section className="ms-audio-call" aria-label="Voice call">
      <audio
        ref={(element) => { remoteAudioRef.current = element; }}
        autoPlay
        playsInline
        muted={!speakerEnabled}
        className="ms-call-remote-audio"
      />

      <header className="ms-audio-call__header">
        <button type="button" className="ms-audio-call__back" onClick={onLeave} aria-label="Minimize call">
          <BackIcon />
        </button>
        <span>Voice call</span>
        <span className="ms-audio-call__header-spacer" aria-hidden="true" />
      </header>

      <section className="ms-audio-call__content">
        <UserAvatar
          person={isGroupCall ? { display_name: displayName } : remoteParticipant?.user ?? { display_name: displayName }}
          size="xl"
          shape={isGroupCall ? "rounded" : "circle"}
          className="ms-audio-call__avatar"
          decorative
        />
        <h1>{displayName}</h1>
        <p className={`ms-audio-call__state is-${viewState.tone}`} aria-live="polite">{viewState.label}</p>
        <p className="ms-audio-call__detail">{viewState.detail}</p>
        {showDuration ? <time className="ms-audio-call__timer">{formatElapsed(elapsedSeconds)}</time> : null}
        {showRingCountdown ? <span className="ms-audio-call__countdown">Ringing · {ringRemainingSeconds}s</span> : null}

        {isGroupCall ? (
          <div className="ms-audio-call__people" aria-label="Call participants">
            {remoteParticipants.slice(0, 4).map((participant) => (
              <UserAvatar key={participant.id} person={participant.user} size="sm" decorative />
            ))}
            {remoteParticipants.length > 4 ? <span>+{remoteParticipants.length - 4}</span> : null}
          </div>
        ) : null}

        <div className="ms-audio-call__notices">
          {mediaError ? (
            <div className="ms-audio-call__notice is-danger">
              <span>Microphone access is required.</span>
              <button type="button" onClick={onRetryMedia}>Try again</button>
            </div>
          ) : null}
          {connectionNeedsHelp ? (
            <div className="ms-audio-call__notice">
              <span>Connection interrupted. Reconnecting…</span>
              <button type="button" onClick={onRestartConnection}>Reconnect</button>
            </div>
          ) : null}
          {remotePlaybackBlocked ? (
            <div className="ms-audio-call__notice">
              <span>Tap to hear the other person.</span>
              <button type="button" onClick={onEnableSound}>Enable sound</button>
            </div>
          ) : null}
          {qualityMessage ? <div className="ms-audio-call__notice"><span>{qualityMessage}</span></div> : null}
        </div>
      </section>

      <footer className="ms-audio-call__controls" aria-label="Call controls">
        {canAccept ? (
          <>
            <button type="button" className="ms-audio-call__control is-decline" onClick={onHangup} disabled={busy}>
              <HangupIcon />
              <span>Decline</span>
            </button>
            <button type="button" className="ms-audio-call__control is-accept" onClick={onAccept} disabled={busy || accepting}>
              <AcceptIcon />
              <span>{accepting ? "Answering…" : "Answer"}</span>
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className={`ms-audio-call__control ${audioEnabled ? "" : "is-active"}`}
              onClick={onToggleAudio}
              disabled={busy}
              aria-pressed={!audioEnabled}
            >
              <MicrophoneIcon muted={!audioEnabled} />
              <span>{audioEnabled ? "Mute" : "Unmute"}</span>
            </button>
            <button
              type="button"
              className={`ms-audio-call__control ${speakerEnabled ? "is-active" : ""}`}
              onClick={onToggleSpeaker}
              disabled={busy}
              aria-pressed={speakerEnabled}
            >
              <SpeakerIcon muted={!speakerEnabled} />
              <span>Speaker</span>
            </button>
            <button type="button" className="ms-audio-call__control is-end" onClick={onHangup} disabled={busy}>
              <HangupIcon />
              <span>End</span>
            </button>
          </>
        )}
      </footer>
    </section>
  );
}
