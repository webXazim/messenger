import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { fetchAttachmentBlobForUser } from "./AuthenticatedMedia";
import type { MessageAttachment } from "../types/chat";

type AudioMessagePlayerProps = {
  src: string;
  label?: string;
  compact?: boolean;
  attachment?: MessageAttachment;
  currentUserId?: string;
  waveformData?: number[];
};

const SPEEDS = [1, 1.25, 1.5, 2];
const WAVEFORM_BAR_COUNT = 48;
const DEFAULT_WAVEFORM = [
  .22, .31, .46, .37, .58, .42, .66, .53, .35, .48, .72, .61,
  .44, .29, .54, .78, .62, .39, .51, .69, .47, .33, .57, .81,
  .65, .43, .28, .49, .73, .55, .38, .62, .76, .52, .34, .46,
  .68, .59, .41, .27, .5, .71, .56, .36, .63, .45, .32, .23,
];

function formatTime(seconds: number) {
  const safe = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  const minutes = Math.floor(safe / 60);
  const remainder = safe % 60;
  return `${minutes}:${remainder.toString().padStart(2, "0")}`;
}

async function decodeWaveform(blob: Blob, sampleCount = DEFAULT_WAVEFORM.length) {
  if (typeof window === "undefined") return DEFAULT_WAVEFORM;
  const AudioContextCtor = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextCtor) return DEFAULT_WAVEFORM;
  const audioContext = new AudioContextCtor();
  try {
    const arrayBuffer = await blob.arrayBuffer();
    const buffer = await audioContext.decodeAudioData(arrayBuffer.slice(0));
    const channelData = buffer.getChannelData(0);
    if (!channelData.length) return DEFAULT_WAVEFORM;

    const bucketSize = Math.max(1, Math.floor(channelData.length / sampleCount));
    const values = Array.from({ length: sampleCount }, (_, index) => {
      const start = index * bucketSize;
      const end = Math.min(channelData.length, start + bucketSize);
      let peak = 0;
      for (let cursor = start; cursor < end; cursor += 1) {
        peak = Math.max(peak, Math.abs(channelData[cursor] ?? 0));
      }
      return peak;
    });

    const max = Math.max(...values, 0.001);
    return values.map((value) => Math.max(0.18, Math.min(1, value / max)));
  } catch {
    return DEFAULT_WAVEFORM;
  } finally {
    void audioContext.close();
  }
}

function PlayIcon({ playing }: { playing: boolean }) {
  return playing
    ? <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6v12M16 6v12" /></svg>
    : <svg viewBox="0 0 24 24" aria-hidden="true"><path className="fill" d="m9 6 10 6-10 6V6Z" /></svg>;
}

function normalizeStoredWaveform(values?: number[]) {
  if (!values?.length) return DEFAULT_WAVEFORM;
  const normalized = values
    .map(Number)
    .filter(Number.isFinite)
    .map((value) => Math.max(0.08, Math.min(1, value > 1 ? value / 100 : value)));
  if (!normalized.length) return DEFAULT_WAVEFORM;
  return Array.from({ length: WAVEFORM_BAR_COUNT }, (_, index) => {
    const start = Math.floor((index * normalized.length) / WAVEFORM_BAR_COUNT);
    const end = Math.max(start + 1, Math.floor(((index + 1) * normalized.length) / WAVEFORM_BAR_COUNT));
    return normalized.slice(start, end).reduce((peak, value) => Math.max(peak, value), 0.08);
  });
}

export function AudioMessagePlayer({ src, label = "Audio", compact = false, attachment, currentUserId, waveformData }: AudioMessagePlayerProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const pendingPlayRef = useRef(false);
  const retryCountRef = useRef(0);
  const progressFrameRef = useRef<number | null>(null);
  const [speedIndex, setSpeedIndex] = useState(0);
  const [resolvedSrc, setResolvedSrc] = useState("");
  const [loadRequested, setLoadRequested] = useState(false);
  const [failed, setFailed] = useState(false);
  const [retryTick, setRetryTick] = useState(0);
  const [waveform, setWaveform] = useState<number[]>(() => normalizeStoredWaveform(waveformData));
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(Number(attachment?.duration_seconds) || 0);
  const [playing, setPlaying] = useState(false);
  const speed = SPEEDS[speedIndex] ?? 1;
  const speedLabel = useMemo(() => `${speed}×`, [speed]);
  const isVoiceNote = compact || label.toLowerCase().includes("voice");

  useEffect(() => {
    retryCountRef.current = 0;
    setRetryTick(0);
    setCurrentTime(0);
    setResolvedSrc("");
    setLoadRequested(false);
    pendingPlayRef.current = false;
    setDuration(Number(attachment?.duration_seconds) || 0);
    setPlaying(false);
    setWaveform(normalizeStoredWaveform(waveformData));
  }, [attachment?.duration_seconds, attachment?.id, src, waveformData]);

  useEffect(() => {
    if (!loadRequested) return;
    let cancelled = false;
    let objectUrl = "";
    let retryTimer: number | null = null;
    if (retryTick === 0) retryCountRef.current = 0;

    async function load() {
      setFailed(false);
      if (!src || src === "#") {
        setResolvedSrc(src);
        setFailed(true);
        return;
      }
      try {
        const blob = await fetchAttachmentBlobForUser(src, attachment, currentUserId);
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        const decodedWaveform = waveformData?.length ? null : await decodeWaveform(blob);
        if (cancelled) return;
        setResolvedSrc(objectUrl);
        if (decodedWaveform) setWaveform(decodedWaveform);
        retryCountRef.current = 0;
      } catch {
        if (!cancelled) {
          setResolvedSrc(attachment?.id ? "" : src);
          setWaveform(DEFAULT_WAVEFORM);
          const nextRetryCount = retryCountRef.current + 1;
          const shouldRetry = nextRetryCount <= 4 && Boolean(attachment?.id);
          retryCountRef.current = nextRetryCount;
          setFailed(!shouldRetry);
          if (shouldRetry) retryTimer = window.setTimeout(() => setRetryTick((current) => current + 1), 700 * nextRetryCount);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
      if (retryTimer) window.clearTimeout(retryTimer);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [attachment?.id, attachment?.is_encrypted, currentUserId, loadRequested, retryTick, src, waveformData]);

  useEffect(() => {
    if (!resolvedSrc || !pendingPlayRef.current) return;
    pendingPlayRef.current = false;
    const audio = audioRef.current;
    if (!audio) return;
    void audio.play().catch(() => undefined);
  }, [resolvedSrc]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.defaultPlaybackRate = speed;
    audio.playbackRate = speed;
  }, [resolvedSrc, speed]);

  useEffect(() => {
    if (!playing) {
      if (progressFrameRef.current !== null) window.cancelAnimationFrame(progressFrameRef.current);
      progressFrameRef.current = null;
      return;
    }
    const updateProgress = () => {
      const audio = audioRef.current;
      if (!audio || audio.paused || audio.ended) return;
      setCurrentTime(audio.currentTime || 0);
      progressFrameRef.current = window.requestAnimationFrame(updateProgress);
    };
    progressFrameRef.current = window.requestAnimationFrame(updateProgress);
    return () => {
      if (progressFrameRef.current !== null) window.cancelAnimationFrame(progressFrameRef.current);
      progressFrameRef.current = null;
    };
  }, [playing]);

  const progress = duration > 0 ? Math.max(0, Math.min(1, currentTime / duration)) : 0;
  const activeBars = Math.round(progress * waveform.length);

  const togglePlayback = async () => {
    if (!loadRequested) {
      pendingPlayRef.current = true;
      setLoadRequested(true);
      return;
    }
    const audio = audioRef.current;
    if (!audio || failed || !resolvedSrc) return;
    if (audio.paused) {
      if (duration > 0 && audio.currentTime >= duration - 0.05) audio.currentTime = 0;
      try {
        await audio.play();
      } catch {
        setFailed(true);
      }
    } else {
      audio.pause();
    }
  };

  const cycleSpeed = () => {
    const nextIndex = (speedIndex + 1) % SPEEDS.length;
    setSpeedIndex(nextIndex);
  };

  const syncDuration = (audio: HTMLAudioElement) => {
    setDuration(Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : Number(attachment?.duration_seconds) || 0);
  };

  const seek = (event: ReactMouseEvent<HTMLButtonElement>) => {
    const audio = audioRef.current;
    if (!audio || !duration) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(rect.width, 1)));
    audio.currentTime = duration * ratio;
    setCurrentTime(audio.currentTime);
  };

  return (
    <div className={`ms-voice-message ${compact ? "ms-voice-message--compact" : ""} ${failed ? "is-failed" : ""}`}>
      <button type="button" className="ms-voice-message__play" onClick={() => void togglePlayback()} disabled={failed} aria-label={playing ? `Pause ${label}` : `Play ${label}`}>
        <PlayIcon playing={playing} />
      </button>
      <div className="ms-voice-message__content">
        {!isVoiceNote ? <strong className="ms-voice-message__label">{label}</strong> : null}
        <button type="button" className="ms-voice-message__waveform" onClick={seek} disabled={failed || !loadRequested || !duration} aria-label={`Seek ${label}`}>
          {waveform.map((value, index) => (
            <span key={`${attachment?.id || label}-${index}`} className={index < activeBars ? "is-active" : ""} style={{ height: `${Math.round(7 + value * 24)}px` }} />
          ))}
        </button>
        <div className="ms-voice-message__timing">
          <span>{formatTime(currentTime)}</span>
          <span>{failed ? "Unavailable" : formatTime(duration)}</span>
        </div>
      </div>
      <button type="button" className="ms-voice-message__speed" onClick={cycleSpeed} disabled={failed} aria-label={`Playback speed ${speedLabel}`}>
        {speedLabel}
      </button>
      {loadRequested ? (
        <audio
          ref={audioRef}
          className="ms-voice-message__audio"
          src={resolvedSrc}
          preload="metadata"
          playsInline
          onLoadedMetadata={(event) => syncDuration(event.currentTarget)}
          onDurationChange={(event) => syncDuration(event.currentTarget)}
          onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime || 0)}
          onPlaying={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={(event) => {
            setPlaying(false);
            setCurrentTime(event.currentTarget.duration || duration);
          }}
          onRateChange={(event) => {
            if (Math.abs(event.currentTarget.playbackRate - speed) > 0.001) event.currentTarget.playbackRate = speed;
          }}
        />
      ) : null}
    </div>
  );
}
