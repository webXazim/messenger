import { useEffect, useRef, useState, type MutableRefObject } from "react";
import type { CallParticipant } from "../../types/chat";
import { participantName } from "./callPresentation";
import { UserAvatar } from "../UserAvatar";
import { FloatingLocalVideo } from "./FloatingLocalVideo";
import { VideoTile } from "./VideoTile";

function ParticipantFallback({ participant, message }: { participant?: CallParticipant; message: string }) {
  return (
    <div className="ms-video-call__avatar-state">
      <UserAvatar person={participant?.user ?? { display_name: participantName(participant) }} size="xl" className="ms-video-call__avatar" decorative />
      <strong>{participantName(participant)}</strong>
      <small>{message}</small>
    </div>
  );
}

function LocalFallback() {
  return (
    <div className="ms-video-call__avatar-state">
      <span className="ms-video-call__avatar">YOU</span>
      <strong>You</strong>
      <small>Camera off</small>
    </div>
  );
}

type MainVideo = "remote" | "local";

export function VideoCallStage({
  localVideoRef,
  remoteVideoRef,
  remoteParticipants,
  primaryRemoteParticipant,
  isGroupCall,
  remoteTrackCount,
  localVideoEnabled = true,
  localVideoMirrored = true,
  onUserActivity,
  onVideoLayoutChange,
}: {
  localVideoRef: MutableRefObject<HTMLVideoElement | null>;
  remoteVideoRef: MutableRefObject<HTMLVideoElement | null>;
  remoteParticipants: CallParticipant[];
  primaryRemoteParticipant?: CallParticipant;
  isGroupCall: boolean;
  remoteTrackCount: number;
  localVideoEnabled?: boolean;
  localVideoMirrored?: boolean;
  peerState?: string;
  onUserActivity?: () => void;
  onVideoLayoutChange?: () => void;
}) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [mainVideo, setMainVideo] = useState<MainVideo>("remote");
  const primaryName = participantName(primaryRemoteParticipant) === "Participant"
    ? "Remote participant"
    : participantName(primaryRemoteParticipant);
  const remoteVideoAvailable = Boolean(primaryRemoteParticipant?.video_enabled && remoteTrackCount > 0);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => onVideoLayoutChange?.());
    return () => window.cancelAnimationFrame(frame);
  }, [mainVideo]);

  if (isGroupCall) {
    return (
      <div className="ms-video-call__group-grid">
        <VideoTile
          label="You"
          className={`ms-video-tile--group ms-video-tile--local ${localVideoMirrored ? "" : "is-unmirrored"}`.trim()}
          videoRef={localVideoRef}
          muted
          showVideo={localVideoEnabled}
          fallback={<LocalFallback />}
        />
        {remoteParticipants.map((participant) => {
          const isPrimary = participant.id === primaryRemoteParticipant?.id;
          const hasVideo = Boolean(isPrimary && participant.video_enabled && remoteTrackCount > 0);
          return (
            <VideoTile
              key={participant.id}
              label={participantName(participant)}
              className="ms-video-tile--group"
              videoRef={isPrimary ? remoteVideoRef : undefined}
              muted
              showVideo={hasVideo}
              fallback={<ParticipantFallback participant={participant} message={participant.state === "joined" ? "Camera off" : "Waiting to join"} />}
            />
          );
        })}
      </div>
    );
  }

  const remoteIsMain = mainVideo === "remote";
  const swapVideos = () => setMainVideo((current) => current === "remote" ? "local" : "remote");

  return (
    <div ref={stageRef} className={`ms-video-call__stage ${remoteIsMain ? "is-remote-main" : "is-local-main"}`}>
      <VideoTile
        label={remoteIsMain ? primaryName : "You"}
        className={remoteIsMain
          ? "ms-video-tile--remote"
          : `ms-video-tile--remote ms-video-tile--main-local ${localVideoMirrored ? "" : "is-unmirrored"}`.trim()}
        videoRef={remoteIsMain ? remoteVideoRef : localVideoRef}
        muted
        showVideo={remoteIsMain ? remoteVideoAvailable : localVideoEnabled}
        fallback={remoteIsMain ? (
          <ParticipantFallback
            participant={primaryRemoteParticipant}
            message={primaryRemoteParticipant?.state === "joined" ? "Camera off" : "Waiting for video"}
          />
        ) : <LocalFallback />}
      />
      <FloatingLocalVideo
        stageRef={stageRef}
        videoRef={remoteIsMain ? localVideoRef : remoteVideoRef}
        enabled={remoteIsMain ? localVideoEnabled : remoteVideoAvailable}
        label={remoteIsMain ? "You" : primaryName}
        fallback={remoteIsMain ? <LocalFallback /> : (
          <ParticipantFallback
            participant={primaryRemoteParticipant}
            message={primaryRemoteParticipant?.state === "joined" ? "Camera off" : "Waiting for video"}
          />
        )}
        mirrored={remoteIsMain ? localVideoMirrored : false}
        onActivate={() => { onUserActivity?.(); swapVideos(); }}
      />
    </div>
  );
}
