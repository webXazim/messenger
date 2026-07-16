export type VideoSenderProfile = {
  active: boolean;
  maxBitrate?: number;
  maxFramerate?: number;
  scaleResolutionDownBy: number;
  reduced: boolean;
};

export function resolveVideoSenderProfile({
  mode,
  videoActive,
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
  const bandwidthReduced = networkReduced || remotePreferredVideoQuality === "low";

  const maxBitrate = audioOnly ? 40_000 : bandwidthReduced ? lowBandwidthMaxBitrate : undefined;
  const maxFramerate = audioOnly ? 4 : bandwidthReduced ? lowBandwidthMaxFramerate : undefined;
  const scaleResolutionDownBy = audioOnly ? 4 : bandwidthReduced ? 2 : 1;

  return {
    active: videoActive,
    maxBitrate,
    maxFramerate,
    scaleResolutionDownBy,
    // The rendering surface must not decide transmission quality. A minimized
    // call stays clear; only measured network pressure or the peer can reduce it.
    reduced: audioOnly || bandwidthReduced,
  };
}
