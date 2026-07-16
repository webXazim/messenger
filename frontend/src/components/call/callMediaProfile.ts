export type VideoSenderProfile = {
  active: boolean;
  maxBitrate?: number;
  maxFramerate?: number;
  scaleResolutionDownBy: number;
  reduced: boolean;
};

const PERSISTENT_MAX_BITRATE_BPS = 180_000;
const PERSISTENT_MAX_FRAMERATE = 10;
const PERSISTENT_SCALE_DOWN = 2.5;

export function resolveVideoSenderProfile({
  mode,
  videoActive,
  compact,
  remotePreferredVideoQuality,
  lowBandwidthMaxBitrate = 250_000,
  lowBandwidthMaxFramerate = 12,
}: {
  mode?: string;
  videoActive: boolean;
  compact: boolean;
  remotePreferredVideoQuality?: string;
  lowBandwidthMaxBitrate?: number;
  lowBandwidthMaxFramerate?: number;
}): VideoSenderProfile {
  const normalizedMode = String(mode || "standard");
  const audioOnly = normalizedMode === "audio_only";
  const networkReduced = normalizedMode === "low_bandwidth_video" || normalizedMode === "reconnect";
  const surfaceReduced = compact || remotePreferredVideoQuality === "low";

  let maxBitrate = audioOnly ? 40_000 : networkReduced ? lowBandwidthMaxBitrate : undefined;
  let maxFramerate = audioOnly ? 4 : networkReduced ? lowBandwidthMaxFramerate : undefined;
  let scaleResolutionDownBy = audioOnly ? 4 : networkReduced ? 2 : 1;

  if (surfaceReduced && !audioOnly) {
    maxBitrate = Math.min(maxBitrate ?? PERSISTENT_MAX_BITRATE_BPS, PERSISTENT_MAX_BITRATE_BPS);
    maxFramerate = Math.min(maxFramerate ?? PERSISTENT_MAX_FRAMERATE, PERSISTENT_MAX_FRAMERATE);
    scaleResolutionDownBy = Math.max(scaleResolutionDownBy, PERSISTENT_SCALE_DOWN);
  }

  return {
    active: videoActive,
    maxBitrate,
    maxFramerate,
    scaleResolutionDownBy,
    reduced: audioOnly || networkReduced || surfaceReduced,
  };
}
