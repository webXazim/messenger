import { useCallback, useEffect, useId, useRef, useState, type MutableRefObject } from "react";
import type { CallParticipant } from "../../types/chat";
import type { CallViewState } from "./callPresentation";
import { formatElapsed, participantName } from "./callPresentation";
import { UserAvatar } from "../UserAvatar";
import { CallParticipantsDrawer } from "./CallParticipantsDrawer";
import { VideoCallStage } from "./VideoCallStage";

const CONTROL_HIDE_DELAY_MS = 4200;

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

function CameraIcon({ off }: { off: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="3" y="6" width="13" height="12" rx="3" />
      <path d="m16 10 5-3v10l-5-3" />
      {off ? <path d="m4 4 16 16" /> : null}
    </svg>
  );
}

function SwitchCameraIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6h3l1.3-2h3.4L17 6h1a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V9a3 3 0 0 1 3-3h2Z" /><path d="M9 11a4 4 0 0 1 6.8-1.8M15 15a4 4 0 0 1-6.8-1.8M15.8 7.8v2.5h-2.5M8.2 16.2v-2.5h2.5" /></svg>;
}

function ParticipantsIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7 1a2.5 2.5 0 1 0 0-5M3.5 19c0-2.2 2.5-4 5.5-4s5.5 1.8 5.5 4v.5h-11V19Zm11-2.8c.6-.4 1.5-.7 2.5-.7 2.5 0 4.5 1.5 4.5 3.5v.5H17" /></svg>;
}

function MoreIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.4" /><circle cx="12" cy="12" r="1.4" /><circle cx="19" cy="12" r="1.4" /></svg>;
}

function SpeakerIcon({ enabled }: { enabled: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 10v4h4l5 4V6l-5 4H4Z" />
      {enabled ? <><path d="M16 9a4 4 0 0 1 0 6" /><path d="M18.5 6.5a8 8 0 0 1 0 11" /></> : <path d="m16 9 5 6M21 9l-5 6" />}
    </svg>
  );
}

function FullscreenIcon({ active }: { active: boolean }) {
  return active
    ? <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3v6H3M15 3v6h6M9 21v-6H3M15 21v-6h6" /></svg>
    : <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5" /></svg>;
}

function HangupIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 15.5c4.7-4 9.3-4 14 0l-2.5 3-3-2v-2.2a9 9 0 0 0-3 0v2.2l-3 2-2.5-3Z" /></svg>;
}

function AcceptIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3.8h3l1.2 4-2 1.7a14.4 14.4 0 0 0 5.3 5.3l1.7-2 4 1.2v3c0 1.1-.9 2-2 2C10.9 19 5 13.1 5 5.8c0-1.1.9-2 2-2Z" /></svg>;
}

export function VideoCallScreen({
  selfParticipant,
  remoteParticipant,
  remoteParticipants,
  isGroupCall,
  viewState,
  elapsedSeconds,
  ringRemainingSeconds,
  audioEnabled,
  videoEnabled,
  speakerEnabled,
  busy,
  canAccept,
  accepting,
  mediaError,
  connectionNeedsHelp,
  remotePlaybackBlocked,
  qualityMessage,
  remoteTrackCount,
  canSwitchCamera,
  localVideoMirrored,
  localVideoRef,
  remoteVideoRef,
  remoteAudioRef,
  onLeave,
  onAccept,
  onToggleAudio,
  onToggleVideo,
  onToggleSpeaker,
  onSwitchCamera,
  onHangup,
  onRetryMedia,
  onRestartConnection,
  onEnableSound,
  onVideoLayoutChange,
}: {
  selfParticipant?: CallParticipant;
  remoteParticipant?: CallParticipant;
  remoteParticipants: CallParticipant[];
  isGroupCall: boolean;
  viewState: CallViewState;
  elapsedSeconds: number;
  ringRemainingSeconds: number;
  audioEnabled: boolean;
  videoEnabled: boolean;
  speakerEnabled: boolean;
  busy: boolean;
  canAccept: boolean;
  accepting: boolean;
  mediaError: string | null;
  connectionNeedsHelp: boolean;
  remotePlaybackBlocked: boolean;
  qualityMessage?: string;
  remoteTrackCount: number;
  canSwitchCamera: boolean;
  localVideoMirrored: boolean;
  localVideoRef: MutableRefObject<HTMLVideoElement | null>;
  remoteVideoRef: MutableRefObject<HTMLVideoElement | null>;
  remoteAudioRef: MutableRefObject<HTMLAudioElement | null>;
  onLeave: () => void;
  onAccept: () => void;
  onToggleAudio: () => void;
  onToggleVideo: () => void;
  onToggleSpeaker: () => void;
  onSwitchCamera: () => void;
  onHangup: () => void;
  onRetryMedia: () => void;
  onRestartConnection: () => void;
  onEnableSound: () => void;
  onVideoLayoutChange?: () => void;
}) {
  const callScreenRef = useRef<HTMLElement | null>(null);
  const moreRootRef = useRef<HTMLDivElement | null>(null);
  const moreButtonRef = useRef<HTMLButtonElement | null>(null);
  const moreMenuRef = useRef<HTMLDivElement | null>(null);
  const moreMenuId = useId();
  const hideTimerRef = useRef<number | null>(null);
  const lastPointerMoveRef = useRef(0);
  const [fullscreenActive, setFullscreenActive] = useState(false);
  const [participantsOpen, setParticipantsOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [chromeVisible, setChromeVisible] = useState(true);

  const hasBlockingNotice = Boolean(mediaError || connectionNeedsHelp || remotePlaybackBlocked);
  const canAutoHide = viewState.label === "Connected" && !busy && !canAccept && !participantsOpen && !moreOpen && !hasBlockingNotice;

  const clearHideTimer = useCallback(() => {
    if (hideTimerRef.current !== null) {
      window.clearTimeout(hideTimerRef.current);
      hideTimerRef.current = null;
    }
  }, []);

  const scheduleHide = useCallback(() => {
    clearHideTimer();
    if (!canAutoHide) return;
    hideTimerRef.current = window.setTimeout(() => setChromeVisible(false), CONTROL_HIDE_DELAY_MS);
  }, [canAutoHide, clearHideTimer]);

  const revealChrome = useCallback(() => {
    setChromeVisible(true);
    scheduleHide();
  }, [scheduleHide]);

  useEffect(() => {
    if (!canAutoHide) {
      clearHideTimer();
      setChromeVisible(true);
      return;
    }
    scheduleHide();
    return clearHideTimer;
  }, [canAutoHide, clearHideTimer, scheduleHide]);

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (participantsOpen) {
        setParticipantsOpen(false);
        revealChrome();
        return;
      }
      if (moreOpen) {
        setMoreOpen(false);
        revealChrome();
      }
    };
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [moreOpen, participantsOpen, revealChrome]);

  useEffect(() => {
    if (!moreOpen) return;
    const frame = window.requestAnimationFrame(() => {
      moreMenuRef.current?.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus();
    });
    const handlePointerDown = (event: PointerEvent) => {
      if (!moreRootRef.current?.contains(event.target as Node)) setMoreOpen(false);
    };
    document.addEventListener("pointerdown", handlePointerDown, true);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("pointerdown", handlePointerDown, true);
    };
  }, [moreOpen]);

  useEffect(() => {
    const syncFullscreenState = () => {
      setFullscreenActive(document.fullscreenElement === callScreenRef.current);
      setChromeVisible(true);
      window.requestAnimationFrame(scheduleHide);
    };
    document.addEventListener("fullscreenchange", syncFullscreenState);
    return () => document.removeEventListener("fullscreenchange", syncFullscreenState);
  }, [scheduleHide]);

  const toggleFullscreen = async () => {
    const target = callScreenRef.current;
    if (!target || !document.fullscreenEnabled) return;
    setMoreOpen(false);
    if (document.fullscreenElement === target) {
      await document.exitFullscreen().catch(() => undefined);
    } else {
      await target.requestFullscreen().catch(() => undefined);
    }
  };

  const displayName = isGroupCall
    ? `${Math.max(remoteParticipants.length, 1) + 1}-person video call`
    : participantName(remoteParticipant);
  const showDuration = viewState.label === "Connected" || elapsedSeconds > 0;
  const showRingCountdown = !showDuration && ringRemainingSeconds > 0 && ["Calling…", "Ringing…", "Incoming call", "Waiting for connection…"].includes(viewState.label);
  const participantCount = remoteParticipants.length + 1;

  return (
    <section
      ref={callScreenRef}
      className={`ms-video-call ${fullscreenActive ? "is-fullscreen" : ""} ${chromeVisible ? "" : "is-chrome-hidden"}`}
      aria-label="Video call"
      onPointerDown={revealChrome}
      onPointerMove={(event) => {
        if (event.pointerType !== "mouse") return;
        const now = Date.now();
        if (now - lastPointerMoveRef.current < 350) return;
        lastPointerMoveRef.current = now;
        revealChrome();
      }}
      onKeyDown={revealChrome}
      onFocusCapture={revealChrome}
    >
      <audio
        ref={(element) => { remoteAudioRef.current = element; }}
        autoPlay
        playsInline
        muted={!speakerEnabled}
        className="ms-call-remote-audio"
      />

      <header className="ms-video-call__header">
        <button type="button" className="ms-video-call__back" onClick={onLeave} aria-label="Minimize call">
          <BackIcon />
        </button>
        <div className="ms-video-call__identity">
          <UserAvatar
            person={isGroupCall ? { display_name: displayName } : remoteParticipant?.user ?? { display_name: displayName }}
            size="sm"
            shape={isGroupCall ? "rounded" : "circle"}
            className="ms-video-call__identity-avatar"
            decorative
          />
          <span>
            <strong>{displayName}</strong>
            <small>{viewState.label}{showDuration ? ` · ${formatElapsed(elapsedSeconds)}` : ""}</small>
          </span>
        </div>
        <div ref={moreRootRef} className="ms-video-call__header-actions">
          <button
            ref={moreButtonRef}
            type="button"
            className="ms-video-call__header-button"
            onClick={() => { setMoreOpen((current) => !current); setParticipantsOpen(false); }}
            aria-label="More call options"
            aria-expanded={moreOpen}
            aria-haspopup="menu"
            aria-controls={moreOpen ? moreMenuId : undefined}
          >
            <MoreIcon />
          </button>
          {moreOpen ? (
            <div
              ref={moreMenuRef}
              id={moreMenuId}
              className="ms-video-call__more-menu"
              role="menu"
              aria-label="More call options"
              onKeyDown={(event) => {
                const items = Array.from(moreMenuRef.current?.querySelectorAll<HTMLButtonElement>('button:not([disabled])') ?? []);
                const currentIndex = items.indexOf(document.activeElement as HTMLButtonElement);
                if (event.key === "Escape") {
                  event.preventDefault();
                  event.stopPropagation();
                  setMoreOpen(false);
                  window.requestAnimationFrame(() => moreButtonRef.current?.focus());
                  return;
                }
                if (event.key === "Tab") {
                  setMoreOpen(false);
                  return;
                }
                let nextIndex = currentIndex;
                if (event.key === "ArrowDown") nextIndex = currentIndex < 0 ? 0 : (currentIndex + 1) % items.length;
                else if (event.key === "ArrowUp") nextIndex = currentIndex < 0 ? items.length - 1 : (currentIndex - 1 + items.length) % items.length;
                else if (event.key === "Home") nextIndex = 0;
                else if (event.key === "End") nextIndex = items.length - 1;
                else return;
                event.preventDefault();
                items[nextIndex]?.focus();
              }}
            >
              <button type="button" role="menuitem" onClick={() => { onToggleSpeaker(); setMoreOpen(false); }}>
                <SpeakerIcon enabled={speakerEnabled} />
                <span>{speakerEnabled ? "Turn sound off" : "Turn sound on"}</span>
              </button>
              <button type="button" role="menuitem" onClick={() => void toggleFullscreen()} disabled={!document.fullscreenEnabled}>
                <FullscreenIcon active={fullscreenActive} />
                <span>{fullscreenActive ? "Exit fullscreen" : "Enter fullscreen"}</span>
              </button>
            </div>
          ) : null}
        </div>
      </header>

      <VideoCallStage
        localVideoRef={localVideoRef}
        remoteVideoRef={remoteVideoRef}
        remoteParticipants={remoteParticipants}
        primaryRemoteParticipant={remoteParticipant}
        isGroupCall={isGroupCall}
        remoteTrackCount={remoteTrackCount}
        localVideoEnabled={videoEnabled}
        localVideoMirrored={localVideoMirrored}
        onUserActivity={revealChrome}
        onVideoLayoutChange={onVideoLayoutChange}
      />

      <div className="ms-video-call__status" aria-live="polite">
        {!showDuration ? <strong>{viewState.label}</strong> : null}
        {!showDuration ? <span>{viewState.detail}</span> : null}
        {showRingCountdown ? <small>{ringRemainingSeconds}s remaining</small> : null}
      </div>

      <div className="ms-video-call__notices">
        {mediaError ? (
          <div className="ms-video-call__notice is-danger">
            <span>Camera or microphone is unavailable.</span>
            <button type="button" onClick={onRetryMedia}>Try again</button>
          </div>
        ) : null}
        {connectionNeedsHelp ? (
          <div className="ms-video-call__notice">
            <span>Connection interrupted. Reconnecting…</span>
            <button type="button" onClick={onRestartConnection}>Reconnect</button>
          </div>
        ) : null}
        {remotePlaybackBlocked ? (
          <div className="ms-video-call__notice">
            <span>Tap to hear the other person.</span>
            <button type="button" onClick={onEnableSound}>Enable sound</button>
          </div>
        ) : null}
        {qualityMessage ? <div className="ms-video-call__notice"><span>{qualityMessage}</span></div> : null}
      </div>

      <footer className="ms-video-call__controls" aria-label="Call controls">
        {canAccept ? (
          <>
            <button type="button" className="ms-video-call__control is-decline" onClick={onHangup} disabled={busy} aria-label="Decline call">
              <HangupIcon />
              <span>Decline</span>
            </button>
            <button type="button" className="ms-video-call__control is-accept" onClick={onAccept} disabled={busy || accepting} aria-label="Answer call">
              <AcceptIcon />
              <span>{accepting ? "Answering…" : "Answer"}</span>
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className={`ms-video-call__control ${audioEnabled ? "" : "is-off"}`}
              onClick={onToggleAudio}
              disabled={busy}
              aria-label={audioEnabled ? "Mute microphone" : "Unmute microphone"}
              aria-pressed={!audioEnabled}
              title={audioEnabled ? "Mute" : "Unmute"}
            >
              <MicrophoneIcon muted={!audioEnabled} />
              <span>{audioEnabled ? "Mute" : "Unmute"}</span>
            </button>
            <button
              type="button"
              className={`ms-video-call__control ${videoEnabled ? "" : "is-off"}`}
              onClick={onToggleVideo}
              disabled={busy}
              aria-label={videoEnabled ? "Turn camera off" : "Turn camera on"}
              aria-pressed={!videoEnabled}
              title={videoEnabled ? "Camera off" : "Camera on"}
            >
              <CameraIcon off={!videoEnabled} />
              <span>{videoEnabled ? "Camera" : "Camera off"}</span>
            </button>
            {canSwitchCamera ? (
              <button
                type="button"
                className="ms-video-call__control"
                onClick={onSwitchCamera}
                disabled={busy}
                aria-label={videoEnabled ? "Switch camera" : "Turn camera on and switch"}
                title={videoEnabled ? "Switch camera" : "Turn camera on and switch"}
              >
                <SwitchCameraIcon />
                <span>Switch</span>
              </button>
            ) : null}
            <button
              type="button"
              className={`ms-video-call__control ${participantsOpen ? "is-off" : ""}`}
              onClick={() => { setParticipantsOpen((current) => !current); setMoreOpen(false); }}
              aria-label={`Show ${participantCount} call participants`}
              aria-expanded={participantsOpen}
              title="Participants"
            >
              <ParticipantsIcon />
              <span>People</span>
              <b className="ms-video-call__control-count" aria-hidden="true">{participantCount}</b>
            </button>
            <button type="button" className="ms-video-call__control is-end" onClick={onHangup} disabled={busy} aria-label="End call" title="End call">
              <HangupIcon />
              <span>End</span>
            </button>
          </>
        )}
      </footer>

      <CallParticipantsDrawer
        open={participantsOpen}
        selfParticipant={selfParticipant}
        remoteParticipants={remoteParticipants}
        audioEnabled={audioEnabled}
        videoEnabled={videoEnabled}
        onClose={() => { setParticipantsOpen(false); revealChrome(); }}
      />
    </section>
  );
}
