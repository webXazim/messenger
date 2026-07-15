import { useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { getMediaPermissionHint, requestCallMedia } from "../lib/mediaPermissions";
import { safeId } from "../lib/safeId";

function formatDuration(ms: number) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

type VoiceDraft = {
  file: File;
  previewUrl: string;
  durationMs: number;
  durationSeconds: number;
  mimeType: string;
  waveform: number[];
  waveformPromise?: Promise<number[] | null>;
  clientTempId: string;
};

export type VoiceNotePayload = {
  file: File;
  previewUrl: string;
  durationSeconds: number;
  mimeType: string;
  fileName: string;
  clientTempId: string;
  waveform: number[];
  waveformPromise?: Promise<number[] | null>;
};

const PREFERRED_RECORDING_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/ogg",
  "audio/mp4",
];

const LIVE_WAVEFORM_BAR_COUNT = 64;
const PREVIEW_WAVEFORM_BAR_COUNT = 72;
const WAVEFORM_SAMPLE_INTERVAL_MS = 72;

function getSupportedRecordingMimeType() {
  if (typeof MediaRecorder === "undefined" || typeof MediaRecorder.isTypeSupported !== "function") return "";
  return PREFERRED_RECORDING_MIME_TYPES.find((mimeType) => MediaRecorder.isTypeSupported(mimeType)) ?? "";
}

function fileExtensionForMimeType(mimeType: string) {
  const normalizedMimeType = mimeType.toLowerCase();
  if (normalizedMimeType.includes("ogg")) return "ogg";
  if (normalizedMimeType.includes("mp4") || normalizedMimeType.includes("m4a")) return "m4a";
  return "webm";
}

function clampLevel(level: number) {
  return Math.min(1, Math.max(0.07, level));
}

function padLiveWaveform(samples: number[]) {
  const visible = samples.slice(-LIVE_WAVEFORM_BAR_COUNT);
  return [...Array(Math.max(0, LIVE_WAVEFORM_BAR_COUNT - visible.length)).fill(0.07), ...visible];
}

function compressWaveform(samples: number[], outputCount: number) {
  if (!samples.length) return Array(outputCount).fill(0.07) as number[];
  if (samples.length <= outputCount) {
    const padded = [...samples];
    while (padded.length < outputCount) padded.push(0.07);
    return padded;
  }

  return Array.from({ length: outputCount }, (_, index) => {
    const start = Math.floor((index * samples.length) / outputCount);
    const end = Math.max(start + 1, Math.floor(((index + 1) * samples.length) / outputCount));
    const bucket = samples.slice(start, end);
    return clampLevel(bucket.reduce((highest, sample) => Math.max(highest, sample), 0.07));
  });
}

async function analyzeRecordedWaveform(blob: Blob, outputCount: number) {
  if (typeof window === "undefined") return null;
  const AudioContextCtor = window.AudioContext
    || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextCtor) return null;
  const context = new AudioContextCtor();
  try {
    const audioBuffer = await context.decodeAudioData((await blob.arrayBuffer()).slice(0));
    if (!audioBuffer.length || !audioBuffer.numberOfChannels) return null;
    const channels = Array.from({ length: audioBuffer.numberOfChannels }, (_, index) => audioBuffer.getChannelData(index));
    const levels = Array.from({ length: outputCount }, (_, index) => {
      const start = Math.floor((index * audioBuffer.length) / outputCount);
      const end = Math.max(start + 1, Math.floor(((index + 1) * audioBuffer.length) / outputCount));
      const stride = Math.max(1, Math.floor((end - start) / 1200));
      let squareTotal = 0;
      let sampleCount = 0;
      for (let cursor = start; cursor < end; cursor += stride) {
        let mixedSample = 0;
        for (const channel of channels) mixedSample += channel[cursor] ?? 0;
        mixedSample /= channels.length;
        squareTotal += mixedSample * mixedSample;
        sampleCount += 1;
      }
      return sampleCount ? Math.sqrt(squareTotal / sampleCount) : 0;
    });
    const sorted = [...levels].sort((left, right) => left - right);
    const noiseFloor = sorted[Math.floor(sorted.length * 0.1)] ?? 0;
    const voiceCeiling = sorted[Math.floor(sorted.length * 0.95)] ?? Math.max(...levels, 0.001);
    const range = Math.max(voiceCeiling - noiseFloor, 0.001);
    return levels.map((level) => clampLevel(Math.pow(Math.max(0, level - noiseFloor) / range, 0.72)));
  } catch {
    return null;
  } finally {
    void context.close().catch(() => undefined);
  }
}

function MicrophoneIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="8" y="3" width="8" height="12" rx="4" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
    </svg>
  );
}

function StopIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" /></svg>;
}

function DeleteIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5" />
    </svg>
  );
}

function SendIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 11.5 19.5 4l-4.2 16-3.6-5-4.6-3.5L4 11.5Z" /></svg>;
}

function PlayIcon({ playing }: { playing: boolean }) {
  if (playing) return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6v12M16 6v12" /></svg>;
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 9 6-9 6V6Z" /></svg>;
}

function LiveWaveform({ samples }: { samples: number[] }) {
  return (
    <div className="ms-voice-recorder__waveform ms-voice-recorder__waveform--live" aria-hidden="true">
      {padLiveWaveform(samples).map((level, index) => (
        <span key={index} style={{ height: `${Math.round(5 + level * 24)}px` }} />
      ))}
    </div>
  );
}

function PreviewWaveform({
  samples,
  progress,
  onSeek,
}: {
  samples: number[];
  progress: number;
  onSeek: (progress: number) => void;
}) {
  const playedBars = Math.round(samples.length * progress);

  const handleSeek = (event: MouseEvent<HTMLButtonElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const nextProgress = bounds.width ? (event.clientX - bounds.left) / bounds.width : 0;
    onSeek(Math.min(1, Math.max(0, nextProgress)));
  };

  return (
    <button
      type="button"
      className="ms-voice-recorder__waveform ms-voice-recorder__waveform--preview"
      onClick={handleSeek}
      aria-label="Seek voice-note preview"
    >
      {samples.map((level, index) => (
        <span className={index < playedBars ? "is-played" : ""} key={index} style={{ height: `${Math.round(5 + level * 24)}px` }} />
      ))}
    </button>
  );
}

export function VoiceNoteRecorder({
  onSendVoiceNote,
  variant = "inline",
  disabled = false,
  onActiveChange,
}: {
  onSendVoiceNote: (payload: VoiceNotePayload) => Promise<void>;
  variant?: "dock" | "inline";
  disabled?: boolean;
  onActiveChange?: (active: boolean) => void;
}) {
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const startedAtRef = useRef<number | null>(null);
  const discardOnStopRef = useRef(false);
  const sendOnStopRef = useRef(false);
  const animationFrameRef = useRef<number | null>(null);
  const lastWaveformSampleAtRef = useRef(0);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const draftRef = useRef<VoiceDraft | null>(null);
  const waveformHistoryRef = useRef<number[]>([]);

  const [recording, setRecording] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [durationMs, setDurationMs] = useState(0);
  const [liveWaveform, setLiveWaveform] = useState<number[]>([]);
  const [draft, setDraft] = useState<VoiceDraft | null>(null);
  const [playing, setPlaying] = useState(false);
  const [playbackMs, setPlaybackMs] = useState(0);

  const active = recording || Boolean(draft);
  const previewProgress = draft?.durationMs ? Math.min(1, playbackMs / draft.durationMs) : 0;
  const previewWaveform = useMemo(
    () => draft?.waveform ?? Array(PREVIEW_WAVEFORM_BAR_COUNT).fill(0.07),
    [draft],
  );

  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  useLayoutEffect(() => {
    onActiveChange?.(active);
  }, [active, onActiveChange]);

  const stopAnimation = () => {
    if (animationFrameRef.current !== null) {
      window.cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
  };

  const stopAudioAnalysis = () => {
    stopAnimation();
    analyserRef.current?.disconnect();
    analyserRef.current = null;
    const context = audioContextRef.current;
    audioContextRef.current = null;
    if (context && context.state !== "closed") void context.close().catch(() => undefined);
  };

  const releaseStream = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  };

  const clearDraft = () => {
    audioRef.current?.pause();
    setPlaying(false);
    setPlaybackMs(0);
    setDraft((current) => {
      if (current?.previewUrl) URL.revokeObjectURL(current.previewUrl);
      return null;
    });
  };

  const resetRecordingState = () => {
    stopAudioAnalysis();
    releaseStream();
    recorderRef.current = null;
    startedAtRef.current = null;
    chunksRef.current = [];
    setRecording(false);
    setDurationMs(0);
    setLiveWaveform([]);
  };

  useEffect(() => () => {
    discardOnStopRef.current = true;
    sendOnStopRef.current = false;
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    stopAudioAnalysis();
    releaseStream();
    if (draftRef.current?.previewUrl) URL.revokeObjectURL(draftRef.current.previewUrl);
  }, []);

  const sendVoiceDraft = async (voiceDraft: VoiceDraft) => {
    try {
      setSending(true);
      setError(null);
      audioRef.current?.pause();
      setDraft((current) => current?.clientTempId === voiceDraft.clientTempId ? null : current);
      setPlaybackMs(0);
      setPlaying(false);
      await onSendVoiceNote({
        file: voiceDraft.file,
        previewUrl: voiceDraft.previewUrl,
        durationSeconds: voiceDraft.durationSeconds,
        mimeType: voiceDraft.mimeType,
        fileName: voiceDraft.file.name,
        clientTempId: voiceDraft.clientTempId,
        waveform: voiceDraft.waveform,
        waveformPromise: voiceDraft.waveformPromise,
      });
      if (voiceDraft.previewUrl) URL.revokeObjectURL(voiceDraft.previewUrl);
    } catch (sendError) {
      setDraft((current) => current ?? voiceDraft);
      setError(sendError instanceof Error ? sendError.message : "Voice note could not be sent.");
    } finally {
      setSending(false);
    }
  };

  const createAudioAnalysis = async (stream: MediaStream) => {
    if (typeof AudioContext === "undefined") return;

    const context = new AudioContext();
    audioContextRef.current = context;
    if (context.state === "suspended") await context.resume();

    const analyser = context.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.72;
    context.createMediaStreamSource(stream).connect(analyser);
    analyserRef.current = analyser;

    const waveformData = new Uint8Array(analyser.fftSize);
    lastWaveformSampleAtRef.current = 0;

    const updateWaveform = (timestamp: number) => {
      const startedAt = startedAtRef.current;
      if (!startedAt || !analyserRef.current) return;

      if (timestamp - lastWaveformSampleAtRef.current >= WAVEFORM_SAMPLE_INTERVAL_MS) {
        analyserRef.current.getByteTimeDomainData(waveformData);
        let squareTotal = 0;
        for (const sample of waveformData) {
          const normalized = (sample - 128) / 128;
          squareTotal += normalized * normalized;
        }
        const rms = Math.sqrt(squareTotal / waveformData.length);
        let peak = 0;
        for (const sample of waveformData) peak = Math.max(peak, Math.abs((sample - 128) / 128));
        // A lightly compressed peak/RMS blend keeps quiet speech visible while
        // leaving silence near the baseline and loud speech near full height.
        const level = clampLevel(Math.pow(Math.min(1, Math.max(rms * 10, peak * 2.8)), 0.72));
        waveformHistoryRef.current.push(level);
        setLiveWaveform(waveformHistoryRef.current.slice(-LIVE_WAVEFORM_BAR_COUNT));
        setDurationMs(Date.now() - startedAt);
        lastWaveformSampleAtRef.current = timestamp;
      }

      animationFrameRef.current = window.requestAnimationFrame(updateWaveform);
    };

    animationFrameRef.current = window.requestAnimationFrame(updateWaveform);
  };

  const startRecording = async () => {
    if (disabled || draft || recording) return;
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("Voice notes are not supported in this browser.");
      return;
    }

    try {
      setError(null);
      clearDraft();
      discardOnStopRef.current = false;
      sendOnStopRef.current = false;
      waveformHistoryRef.current = [];
      setLiveWaveform([]);

      const stream = await requestCallMedia("voice");
      streamRef.current = stream;
      const recordingMimeType = getSupportedRecordingMimeType();
      const recorder = recordingMimeType ? new MediaRecorder(stream, { mimeType: recordingMimeType }) : new MediaRecorder(stream);
      chunksRef.current = [];

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };

      recorder.onstop = () => {
        const finalDuration = startedAtRef.current ? Date.now() - startedAtRef.current : durationMs;
        const shouldDiscard = discardOnStopRef.current;
        const shouldSend = sendOnStopRef.current;
        const chunks = [...chunksRef.current];
        const liveWaveformFallback = compressWaveform(waveformHistoryRef.current, PREVIEW_WAVEFORM_BAR_COUNT);
        const mimeType = recorder.mimeType || recordingMimeType || "audio/webm";

        discardOnStopRef.current = false;
        sendOnStopRef.current = false;
        resetRecordingState();

        if (shouldDiscard || chunks.length === 0) {
          setSending(false);
          return;
        }

        const blob = new Blob(chunks, { type: mimeType });
        if (blob.size === 0) {
          setSending(false);
          setError("Voice note recording was empty.");
          return;
        }

        void (async () => {
          const waveformPromise = analyzeRecordedWaveform(blob, PREVIEW_WAVEFORM_BAR_COUNT);
          const waveform = shouldSend ? liveWaveformFallback : await waveformPromise ?? liveWaveformFallback;
          const fileName = `voice-note-${Date.now()}.${fileExtensionForMimeType(mimeType)}`;
          const voiceDraft: VoiceDraft = {
            file: new File([blob], fileName, { type: mimeType }),
            previewUrl: URL.createObjectURL(blob),
            durationMs: finalDuration,
            durationSeconds: Math.max(1, Math.ceil(finalDuration / 1000)),
            mimeType,
            waveform,
            waveformPromise: shouldSend ? waveformPromise : undefined,
            clientTempId: safeId("voice-note"),
          };

          if (shouldSend) await sendVoiceDraft(voiceDraft);
          else setDraft(voiceDraft);
        })().catch(() => {
          setSending(false);
          setError("Voice note processing failed. Please record it again.");
        });
      };

      recorderRef.current = recorder;
      startedAtRef.current = Date.now();
      setDurationMs(0);
      setRecording(true);
      recorder.start(250);
      await createAudioAnalysis(stream).catch(() => undefined);
    } catch (recordingError) {
      resetRecordingState();
      const detail = recordingError instanceof Error
        ? `${recordingError.name}: ${recordingError.message}`
        : "Unable to start the microphone.";
      setError(`${detail} ${await getMediaPermissionHint("voice")}`);
    }
  };

  const stopRecording = () => {
    sendOnStopRef.current = false;
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
  };

  const sendRecording = () => {
    if (!recording) return;
    sendOnStopRef.current = true;
    setSending(true);
    setRecording(false);
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.stop();
    } else {
      sendOnStopRef.current = false;
      setSending(false);
    }
  };

  const discardRecording = () => {
    if (recording) {
      discardOnStopRef.current = true;
      sendOnStopRef.current = false;
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") recorder.stop();
      else resetRecordingState();
    }
    clearDraft();
    setError(null);
  };

  const togglePreviewPlayback = async () => {
    const audio = audioRef.current;
    if (!audio || !draft) return;
    if (audio.paused) {
      await audio.play();
    } else {
      audio.pause();
    }
  };

  const seekPreview = (progress: number) => {
    const audio = audioRef.current;
    if (!audio || !draft) return;
    const durationSeconds = Number.isFinite(audio.duration) && audio.duration > 0
      ? audio.duration
      : draft.durationMs / 1000;
    audio.currentTime = progress * durationSeconds;
    setPlaybackMs(progress * draft.durationMs);
  };

  return (
    <div className={`ms-voice-recorder ms-voice-recorder--${variant} ${active ? "is-active" : ""}`}>
      {!active ? (
        <button
          type="button"
          className="ms-composer-icon-button ms-voice-recorder__trigger"
          onClick={() => void startRecording()}
          disabled={disabled}
          aria-label="Record voice note"
          title={disabled ? "Clear the message or attachments before recording" : "Record voice note"}
        >
          <MicrophoneIcon />
        </button>
      ) : null}

      {recording ? (
        <div className="ms-voice-recorder__inline-state ms-voice-recorder__inline-state--recording" aria-live="polite">
          <button type="button" className="ms-voice-recorder__control ms-voice-recorder__control--discard" onClick={discardRecording} aria-label="Discard recording" title="Discard recording">
            <DeleteIcon />
          </button>
          <div className="ms-voice-recorder__timer">
            <span className="ms-voice-recorder__live-dot" aria-hidden="true" />
            <time>{formatDuration(durationMs)}</time>
          </div>
          <LiveWaveform samples={liveWaveform} />
          <button type="button" className="ms-voice-recorder__control" onClick={stopRecording} aria-label="Stop and preview recording" title="Stop and preview">
            <StopIcon />
          </button>
          <button type="button" className="ms-voice-recorder__control ms-voice-recorder__control--primary" onClick={sendRecording} aria-label="Send voice note" title="Send voice note">
            <SendIcon />
          </button>
        </div>
      ) : null}

      {draft ? (
        <div className="ms-voice-recorder__inline-state ms-voice-recorder__inline-state--preview">
          <button type="button" className="ms-voice-recorder__control ms-voice-recorder__control--discard" onClick={discardRecording} aria-label="Discard voice note" title="Discard voice note">
            <DeleteIcon />
          </button>
          <button type="button" className="ms-voice-recorder__control ms-voice-recorder__control--play" onClick={() => void togglePreviewPlayback()} aria-label={playing ? "Pause voice-note preview" : "Play voice-note preview"} title={playing ? "Pause" : "Play"}>
            <PlayIcon playing={playing} />
          </button>
          <PreviewWaveform samples={previewWaveform} progress={previewProgress} onSeek={seekPreview} />
          <time className="ms-voice-recorder__preview-duration">{formatDuration(playbackMs || draft.durationMs)}</time>
          <button type="button" className="ms-voice-recorder__control ms-voice-recorder__control--primary" onClick={() => void sendVoiceDraft(draft)} aria-label="Send voice note" title="Send voice note">
            <SendIcon />
          </button>
          <audio
            ref={audioRef}
            className="ms-voice-recorder__audio"
            src={draft.previewUrl}
            preload="metadata"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => { setPlaying(false); setPlaybackMs(0); }}
            onTimeUpdate={(event) => setPlaybackMs(event.currentTarget.currentTime * 1000)}
          />
        </div>
      ) : null}

      {error ? <div className="ms-voice-recorder__error" role="alert">{error}</div> : null}
    </div>
  );
}
