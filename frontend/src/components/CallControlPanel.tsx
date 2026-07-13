import type { Call } from "../types/chat";

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

function HangupIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 15.5c4.7-4 9.3-4 14 0l-2.5 3-3-2v-2.2a9 9 0 0 0-3 0v2.2l-3 2-2.5-3Z" />
    </svg>
  );
}

export function CallControlPanel({
  call,
  onToggleAudio,
  onToggleVideo,
  onHangup,
  audioEnabled,
  videoEnabled,
  disabled = false,
  busyLabel,
}: {
  call: Call;
  audioEnabled: boolean;
  videoEnabled: boolean;
  onToggleAudio: () => void;
  onToggleVideo: () => void;
  onHangup: () => void;
  disabled?: boolean;
  busyLabel?: string | null;
}) {
  return (
    <div className="ms-call-controls" aria-label="Call controls">
      <div className="ms-call-controls__state">
        <strong>{call.call_type === "video" ? "Video call" : "Voice call"}</strong>
        <span>{busyLabel || call.status}</span>
      </div>
      <div className="ms-call-controls__buttons">
        <button
          type="button"
          className={`ms-call-control ${audioEnabled ? "" : "is-off"}`}
          disabled={disabled}
          onClick={onToggleAudio}
          aria-label={audioEnabled ? "Mute microphone" : "Unmute microphone"}
          aria-pressed={!audioEnabled}
        >
          <MicrophoneIcon muted={!audioEnabled} />
          <span>{audioEnabled ? "Mute" : "Unmute"}</span>
        </button>
        {call.call_type === "video" ? (
          <button
            type="button"
            className={`ms-call-control ${videoEnabled ? "" : "is-off"}`}
            disabled={disabled}
            onClick={onToggleVideo}
            aria-label={videoEnabled ? "Turn camera off" : "Turn camera on"}
            aria-pressed={!videoEnabled}
          >
            <CameraIcon off={!videoEnabled} />
            <span>{videoEnabled ? "Camera" : "Camera off"}</span>
          </button>
        ) : null}
        <button
          type="button"
          className="ms-call-control ms-call-control--end"
          disabled={disabled}
          onClick={onHangup}
          aria-label="End call"
        >
          <HangupIcon />
          <span>{busyLabel === "Ending call..." ? "Ending…" : "End"}</span>
        </button>
      </div>
    </div>
  );
}
