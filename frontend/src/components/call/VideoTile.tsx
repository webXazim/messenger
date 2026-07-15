import type { KeyboardEvent, MutableRefObject, ReactNode } from "react";

export function VideoTile({
  label,
  className = "",
  videoRef,
  muted = false,
  fallback,
  showVideo = true,
  controls = false,
  onActivate,
  activateLabel,
}: {
  label: string;
  className?: string;
  videoRef?: MutableRefObject<HTMLVideoElement | null>;
  muted?: boolean;
  fallback?: ReactNode;
  showVideo?: boolean;
  controls?: boolean;
  onActivate?: () => void;
  activateLabel?: string;
}) {
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!onActivate || !["Enter", " "].includes(event.key)) return;
    event.preventDefault();
    onActivate();
  };

  return (
    <div
      className={`ms-video-tile ${showVideo && videoRef ? "is-showing-video" : "is-showing-fallback"} ${onActivate ? "is-interactive" : ""} ${className}`.trim()}
      role={onActivate ? "button" : undefined}
      tabIndex={onActivate ? 0 : undefined}
      aria-label={onActivate ? (activateLabel || `Switch ${label} video`) : undefined}
      onClick={onActivate}
      onKeyDown={handleKeyDown}
    >
      {videoRef ? (
        <video
          ref={(element) => { videoRef.current = element; }}
          autoPlay
          playsInline
          muted={muted}
          controls={controls}
          className={`ms-video-tile__video ${showVideo ? "is-visible" : ""}`}
        />
      ) : null}
      {!showVideo || !videoRef ? <div className="ms-video-tile__fallback">{fallback}</div> : null}
      <span className="ms-video-tile__label">{label}</span>
    </div>
  );
}
