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
  return values.slice(0, 64).map((value) => Math.max(0.18, Math.min(1, Number(value) > 1 ? Number(value) / 100 : Number(value))));
}

export function AudioMessagePlayer({ src, label = "Audio", compact = false, attachment, currentUserId, waveformData }: AudioMessagePlayerProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const pendingPlayRef = useRef(false);
  const retryCountRef = useRef(0);
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
        const decodedWaveform = await decodeWaveform(blob);
        if (cancelled) return;
        setResolvedSrc(objectUrl);
        setWaveform(decodedWaveform);
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
  }, [attachment?.id, attachment?.is_encrypted, currentUserId, loadRequested, retryTick, src]);

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

    const syncTime = () => setCurrentTime(audio.currentTime || 0);
    const syncDuration = () => setDuration(Number.isFinite(audio.duration) ? audio.duration : Number(attachment?.duration_seconds) || 0);
    const handlePlay = () => setPlaying(true);
    const handlePause = () => setPlaying(false);
    const handleEnded = () => {
      setPlaying(false);
      setCurrentTime(audio.duration || duration);
    };

    audio.playbackRate = speed;
    audio.addEventListener("timeupdate", syncTime);
    audio.addEventListener("loadedmetadata", syncDuration);
    audio.addEventListener("durationchange", syncDuration);
    audio.addEventListener("play", handlePlay);
    audio.addEventListener("pause", handlePause);
    audio.addEventListener("ended", handleEnded);
    return () => {
      audio.removeEventListener("timeupdate", syncTime);
      audio.removeEventListener("loadedmetadata", syncDuration);
      audio.removeEventListener("durationchange", syncDuration);
      audio.removeEventListener("play", handlePlay);
      audio.removeEventListener("pause", handlePause);
      audio.removeEventListener("ended", handleEnded);
    };
  }, [attachment?.duration_seconds, duration, resolvedSrc, speed]);

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
    if (audioRef.current) audioRef.current.playbackRate = SPEEDS[nextIndex] ?? 1;
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
      {loadRequested ? <audio ref={audioRef} className="ms-voice-message__audio" src={resolvedSrc} preload="none" playsInline /> : null}
    </div>
  );
}
