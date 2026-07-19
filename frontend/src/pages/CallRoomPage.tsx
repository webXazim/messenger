import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { chatApi } from "../api/chat";
import { AudioCallScreen } from "../components/call/AudioCallScreen";
import { cameraFacingFromTrack, findPreferredCameraDevice, supportsMobileCameraSwitch, type CameraFacingMode } from "../components/call/callCamera";
import { VideoCallScreen } from "../components/call/VideoCallScreen";
import { formatElapsed, getCallViewState, participantInitials, participantName } from "../components/call/callPresentation";
import { resolveVideoSenderProfile } from "../components/call/callMediaProfile";
import { useAuth } from "../contexts/AuthContext";
import { useChatSocket } from "../hooks/useChatSocket";
import { useCallWakeLock } from "../hooks/useCallWakeLock";
import { buildCallMediaConstraints, getCallMediaErrorMessage, preflightCallMedia, requestCallMedia } from "../lib/mediaPermissions";
import { patchCallCaches } from "../lib/realtimeCache";
import { clearRealtimeCallGrant, requestRealtimeCallGrant } from "../lib/realtimeCredentials";
import { safeId } from "../lib/safeId";
import { isSameUserIdentity } from "../lib/userIdentity";
import { isTerminalCall } from "../lib/callLifecycle";
import { claimCallAction, createCallActionChannel, createCallActionOwnerId, releaseCallAction } from "../lib/callCoordination";
import type { Call, CallParticipant } from "../types/chat";

function buildMediaStatePatch(payload: Record<string, unknown>) {
  const connectionState = payload.connection_state === "connecting" ? "checking" : payload.connection_state;
  return {
    ...payload,
    ...(connectionState ? { connection_state: connectionState } : {}),
    microphone_enabled: payload.microphone_enabled ?? payload.audio_enabled,
    camera_enabled: payload.camera_enabled ?? payload.video_enabled,
    screen_sharing: payload.screen_sharing ?? payload.screen_share_enabled,
  };
}


function getCallMutationError(error: unknown, fallback: string) {
  if (error && typeof error === "object" && "response" in error) {
    const data = (error as { response?: { data?: unknown } }).response?.data;
    if (data && typeof data === "object") {
      const detail = (data as Record<string, unknown>).detail;
      const call = (data as Record<string, unknown>).call;
      if (typeof detail === "string") return detail;
      if (typeof call === "string") return call;
      if (Array.isArray(call) && call.length) return String(call[0]);
    }
  }
  return error instanceof Error ? error.message : fallback;
}

function getCallHttpStatus(error: unknown) {
  if (!error || typeof error !== "object" || !("response" in error)) return 0;
  return Number((error as { response?: { status?: number } }).response?.status || 0);
}

function isLiveCallStatus(status?: string) {
  return status === "initiated" || status === "ringing" || status === "ongoing";
}

function iceServerKey(server: RTCIceServer) {
  const credentialType = (server as RTCIceServer & { credentialType?: string }).credentialType ?? "password";
  return JSON.stringify([server.urls, server.username ?? "", server.credential ?? "", credentialType]);
}

function mergeIceServers(...serverLists: Array<RTCIceServer[] | undefined>) {
  const seen = new Set<string>();
  const merged: RTCIceServer[] = [];
  for (const servers of serverLists) {
    for (const server of servers ?? []) {
      const key = iceServerKey(server);
      if (seen.has(key)) continue;
      seen.add(key);
      merged.push(server);
    }
  }
  return merged;
}

const SIGNAL_DEDUPE_WINDOW_MS = 45_000;

function toSignalRecord(value: unknown) {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function buildSignalFingerprint(signalType: string, payload: Record<string, unknown>) {
  return [
    signalType,
    String(payload.type ?? ""),
    String(payload.sdp ?? ""),
    String(payload.candidate ?? ""),
    String(payload.sdpMid ?? ""),
    String(payload.sdpMLineIndex ?? ""),
    String(payload.reason ?? ""),
    String(payload.from_user_id ?? ""),
    String(payload.to_user_id ?? ""),
  ].join("|");
}

function extractPendingSignals(orchestration: Record<string, unknown>) {
  const keys = ["pending_signals", "signals", "queued_signals", "call_signals"];
  for (const key of keys) {
    const value = orchestration[key];
    if (Array.isArray(value)) {
      return value.map((item) => toSignalRecord(item)).filter((item) => Object.keys(item).length > 0);
    }
  }
  return [] as Array<Record<string, unknown>>;
}

function mergeCallParticipantPatch(call: Call | null, userId: string, patch: Partial<CallParticipant>) {
  if (!call?.participants?.length) return call;
  return {
    ...call,
    participants: call.participants.map((participant) =>
      String(participant.user.id) === userId ? { ...participant, ...patch } : participant,
    ),
  };
}

function markCallParticipantOnline(call: Call | null, userId: string) {
  if (!call?.participants?.length || !userId) return call;
  return {
    ...call,
    participants: call.participants.map((participant) =>
      String(participant.user.id) === userId
        ? {
            ...participant,
            user: {
              ...participant.user,
              is_online: true,
              active_devices: Math.max(participant.user.active_devices || 0, 1),
              presence_label: "online",
            },
          }
        : participant,
    ),
  };
}

function mergeCallPayload(call: Call | null, payload: Partial<Call> & { id?: string }) {
  if (!call) return payload as Call;
  if (payload.id && String(payload.id) !== String(call.id)) return call;
  const nextParticipants = Array.isArray(payload.participants) ? payload.participants : call.participants;
  return {
    ...call,
    ...payload,
    participants: nextParticipants,
  } as Call;
}

function shouldLocalUserCreateOffer(call: Call | null | undefined, user: { id?: string | number | null } | null | undefined) {
  if (!call?.participants?.length || !user?.id) return false;
  if (call.participants.length <= 2) return isSameUserIdentity(call.initiated_by, user);
  const joinedIds = call.participants
    .filter((participant) => participant.state === "joined")
    .map((participant) => String(participant.user.id || ""))
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  if (joinedIds.length >= 2) return joinedIds[0] === String(user.id);
  return isSameUserIdentity(call.initiated_by, user);
}

type PeerQualityMetrics = {
  bitrateKbps?: number;
  packetLossPct?: number;
  roundTripTimeMs?: number;
  jitterMs?: number;
  frameRate?: number;
  networkQuality: "excellent" | "good" | "fair" | "poor" | "offline";
  preferredVideoQuality: "high" | "medium" | "low" | "off";
};

function toFiniteNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function getSenderForKind(peer: RTCPeerConnection, kind: "audio" | "video") {
  return peer.getSenders().find((sender) => sender.track?.kind === kind)
    ?? peer.getTransceivers().find((transceiver) => transceiver.sender.track?.kind === kind || transceiver.receiver.track?.kind === kind)?.sender
    ?? null;
}

function getTransceiverForKind(peer: RTCPeerConnection, kind: "audio" | "video") {
  return peer.getTransceivers().find((transceiver) => transceiver.sender.track?.kind === kind || transceiver.receiver.track?.kind === kind) ?? null;
}

function ensureCallTransceivers(peer: RTCPeerConnection, callType: "voice" | "video") {
  const audioTransceiver = getTransceiverForKind(peer, "audio") ?? peer.addTransceiver("audio", { direction: "sendrecv" });
  const videoTransceiver = callType === "video"
    ? (getTransceiverForKind(peer, "video") ?? peer.addTransceiver("video", { direction: "sendrecv" }))
    : null;
  return { audioTransceiver, videoTransceiver };
}

function preferVp8ForTransceiver(transceiver: RTCRtpTransceiver | null) {
  const capabilities = RTCRtpSender.getCapabilities?.("video");
  if (!transceiver || !capabilities?.codecs?.length || !transceiver.setCodecPreferences) return false;
  const vp8 = capabilities.codecs.filter((codec) => codec.mimeType.toLowerCase() === "video/vp8");
  const rtx = capabilities.codecs.filter((codec) => codec.mimeType.toLowerCase() === "video/rtx");
  const rest = capabilities.codecs.filter((codec) => !["video/vp8", "video/rtx"].includes(codec.mimeType.toLowerCase()));
  if (!vp8.length) return false;
  transceiver.setCodecPreferences([...vp8, ...rtx, ...rest]);
  return true;
}

function normalizeLocalSdpDirections(description: RTCSessionDescriptionInit, callType: "voice" | "video") {
  if (!description.sdp) return description;
  let currentMedia = "";
  let changed = false;
  const lines = description.sdp.split(/\r?\n/).map((line) => {
    if (line.startsWith("m=")) {
      currentMedia = line.slice(2).split(" ")[0]?.toLowerCase() ?? "";
      return line;
    }
    const shouldSend = currentMedia === "audio" || (callType === "video" && currentMedia === "video");
    if (shouldSend && ["a=sendonly", "a=recvonly", "a=inactive"].includes(line)) {
      changed = true;
      return "a=sendrecv";
    }
    return line;
  });
  return changed ? { ...description, sdp: lines.join("\r\n") } : description;
}

function summarizeSdpDirections(sdp: string) {
  const sections: string[] = [];
  let currentMedia = "";
  for (const line of sdp.split(/\r?\n/)) {
    if (line.startsWith("m=")) {
      currentMedia = line.slice(2).split(" ")[0] || "media";
    } else if (currentMedia && ["a=sendrecv", "a=sendonly", "a=recvonly", "a=inactive"].includes(line)) {
      sections.push(`${currentMedia}:${line.slice(2)}`);
      currentMedia = "";
    }
  }
  return sections.join(" ");
}

function summarizeLocalSenders(peer: RTCPeerConnection) {
  return peer.getSenders()
    .map((sender) => `${sender.track?.kind ?? "none"}:${sender.track?.readyState ?? "missing"}:${sender.track?.enabled ? "on" : "off"}`)
    .join(" ");
}

function syncLocalPreview(video: HTMLVideoElement | null, stream: MediaStream | null) {
  if (!video) return;
  video.muted = true;
  video.playsInline = true;
  if (video.srcObject !== stream) {
    video.srcObject = stream;
  }
  if (stream) {
    void tryPlayMediaElement(video);
  }
}

async function tryPlayMediaElement(element: HTMLMediaElement | null) {
  if (!element) return true;
  try {
    await element.play();
    return true;
  } catch {
    return false;
  }
}

function syncRemoteMedia(
  video: HTMLVideoElement | null,
  audio: HTMLAudioElement | null,
  videoStream: MediaStream | null,
  audioStream: MediaStream | null,
) {
  if (video) {
    video.muted = true;
    video.playsInline = true;
    if (video.srcObject !== videoStream) {
      video.srcObject = videoStream;
    }
    video.onloadedmetadata = () => {
      void tryPlayMediaElement(video);
    };
    if (videoStream?.getVideoTracks().length) {
      void tryPlayMediaElement(video);
    }
  }
  if (audio) {
    if (audio.srcObject !== audioStream) {
      audio.srcObject = audioStream;
    }
    audio.onloadedmetadata = () => {
      void tryPlayMediaElement(audio);
    };
  }
}

function mapMetricsToNetworkQuality(metrics: Partial<PeerQualityMetrics>, online: boolean, peerState: string) {
  if (!online || ["failed", "disconnected", "closed"].includes(peerState)) return "offline" as const;
  const packetLoss = metrics.packetLossPct ?? 0;
  const rtt = metrics.roundTripTimeMs ?? 0;
  const bitrate = metrics.bitrateKbps ?? 0;
  if (packetLoss >= 12 || rtt >= 1600 || bitrate < 40) return "poor" as const;
  if (packetLoss >= 6 || rtt >= 800 || bitrate < 140) return "fair" as const;
  if (packetLoss >= 2 || rtt >= 350 || bitrate < 450) return "good" as const;
  return "excellent" as const;
}

async function collectPeerQualityMetrics(
  peer: RTCPeerConnection,
  previousSampleRef: MutableRefObject<{ bytesSent: number; collectedAt: number } | null>,
  networkOnline: boolean,
  peerState: string,
) {
  const stats = await peer.getStats();
  let totalBytesSent = 0;
  let frameRate: number | undefined;
  let packetLossPct: number | undefined;
  let roundTripTimeMs: number | undefined;
  let jitterMs: number | undefined;

  stats.forEach((report) => {
    if (report.type === "outbound-rtp" && !report.isRemote) {
      totalBytesSent += toFiniteNumber(report.bytesSent) ?? 0;
      if (report.kind === "video") {
        frameRate = Math.max(frameRate ?? 0, toFiniteNumber(report.framesPerSecond) ?? 0) || frameRate;
      }
    }
    if (report.type === "remote-inbound-rtp") {
      const remoteInbound = report as RTCStats & { fractionLost?: number; jitter?: number; roundTripTime?: number };
      const fractionLost = toFiniteNumber(remoteInbound.fractionLost);
      if (fractionLost !== undefined) packetLossPct = Math.max(packetLossPct ?? 0, Math.min(100, fractionLost * 100));
      const jitter = toFiniteNumber(remoteInbound.jitter);
      if (jitter !== undefined) jitterMs = Math.max(jitterMs ?? 0, jitter * 1000);
      const roundTrip = toFiniteNumber(remoteInbound.roundTripTime);
      if (roundTrip !== undefined) roundTripTimeMs = Math.max(roundTripTimeMs ?? 0, roundTrip * 1000);
    }
    if (report.type === "candidate-pair" && (report as RTCIceCandidatePairStats).nominated) {
      const pair = report as RTCIceCandidatePairStats;
      const roundTrip = toFiniteNumber(pair.currentRoundTripTime);
      if (roundTrip !== undefined) roundTripTimeMs = Math.max(roundTripTimeMs ?? 0, roundTrip * 1000);
    }
  });

  const now = Date.now();
  const previous = previousSampleRef.current;
  const bitrateKbps = previous && now > previous.collectedAt
    ? Math.max(0, ((totalBytesSent - previous.bytesSent) * 8) / ((now - previous.collectedAt) / 1000) / 1000)
    : undefined;
  previousSampleRef.current = { bytesSent: totalBytesSent, collectedAt: now };

  const networkQuality = mapMetricsToNetworkQuality({ bitrateKbps, packetLossPct, roundTripTimeMs }, networkOnline, peerState);
  const preferredVideoQuality =
    networkQuality === "poor" || networkQuality === "offline"
      ? "off"
      : networkQuality === "fair"
        ? "low"
        : networkQuality === "good"
          ? "medium"
          : "high";

  return {
    bitrateKbps,
    packetLossPct,
    roundTripTimeMs,
    jitterMs,
    frameRate,
    networkQuality,
    preferredVideoQuality,
  } satisfies PeerQualityMetrics;
}

type CallRoomPageProps = {
  callIdOverride?: string;
  displayMode?: "full" | "compact";
  onCallFinished?: (callId: string) => void;
  onCallMinimize?: (callId: string) => void;
};

function CompactMicrophoneIcon({ muted }: { muted: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="8" y="3" width="8" height="12" rx="4" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
      {muted ? <path d="m4 4 16 16" /> : null}
    </svg>
  );
}

function CompactVideoIcon({ disabled }: { disabled: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="3" y="6" width="13" height="12" rx="2" />
      <path d="m16 10 5-3v10l-5-3" />
      {disabled ? <path d="m4 4 16 16" /> : null}
    </svg>
  );
}

function CompactExpandIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M21 16v5h-5" /></svg>;
}

function CompactHangupIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 15.5c4.7-4 9.3-4 14 0l-2.5 3-3-2v-2.2a9 9 0 0 0-3 0v2.2l-3 2-2.5-3Z" /></svg>;
}

function CompactAcceptIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3.8h3l1.2 4-2 1.7a14.4 14.4 0 0 0 5.3 5.3l1.7-2 4 1.2v3c0 1.1-.9 2-2 2C10.9 19 5 13.1 5 5.8c0-1.1.9-2 2-2Z" /></svg>;
}

export function CallRoomPage({ callIdOverride, displayMode = "full", onCallFinished, onCallMinimize }: CallRoomPageProps = {}) {
  const { callId: routeCallId = "" } = useParams();
  const callId = callIdOverride || routeCallId;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const { socket, socketStatus } = useChatSocket();
  const [audioEnabled, setAudioEnabled] = useState(true);
  const [videoEnabled, setVideoEnabled] = useState(true);
  const [speakerEnabled, setSpeakerEnabled] = useState(true);
  const [videoInputCount, setVideoInputCount] = useState(0);
  const [localVideoMirrored, setLocalVideoMirrored] = useState(true);
  const [signalLog, setSignalLog] = useState<string[]>([]);
  const [networkOnline, setNetworkOnline] = useState(typeof navigator !== "undefined" ? navigator.onLine : true);
  const [pageVisible, setPageVisible] = useState(typeof document !== "undefined" ? document.visibilityState === "visible" : true);
  const [peerState, setPeerState] = useState("connecting");
  const [peerReady, setPeerReady] = useState(false);
  const [mediaError, setMediaError] = useState<string | null>(null);
  const [mediaAttempt, setMediaAttempt] = useState(0);
  const [mediaAction, setMediaAction] = useState<string | null>(null);
  const [remoteTrackCount, setRemoteTrackCount] = useState(0);
  const [remotePlaybackBlocked, setRemotePlaybackBlocked] = useState(false);
  const [liveCall, setLiveCall] = useState<Call | null>(null);
  const [liveOrchestration, setLiveOrchestration] = useState<Record<string, unknown> | null>(null);
  const localVideoRef = useRef<HTMLVideoElement | null>(null);
  const remoteVideoRef = useRef<HTMLVideoElement | null>(null);
  const remoteAudioRef = useRef<HTMLAudioElement | null>(null);
  const peerRef = useRef<RTCPeerConnection | null>(null);
  const callRef = useRef<Call | null>(null);
  const localStreamRef = useRef<MediaStream | null>(null);
  const remoteVideoStreamRef = useRef<MediaStream | null>(null);
  const remoteAudioStreamRef = useRef<MediaStream | null>(null);
  const pendingSignalsRef = useRef<Array<{ signalType: string; signalPayload: Record<string, unknown> }>>([]);
  const pendingIceCandidatesRef = useRef<RTCIceCandidateInit[]>([]);
  const processedSignalsRef = useRef<Map<string, number>>(new Map());
  const observedOrchestrationSignalIdsRef = useRef<Set<string>>(new Set());
  const lastSurfaceVideoProfileRef = useRef("");
  const offerSentRef = useRef(false);
  const iceRestartTimerRef = useRef<number | null>(null);
  const disconnectGraceTimerRef = useRef<number | null>(null);
  const lastIceRestartAtRef = useRef(0);
  const lastOfferSentAtRef = useRef(0);
  const lastLocalMediaMutationAtRef = useRef(0);
  const statsSampleRef = useRef<{ bytesSent: number; collectedAt: number } | null>(null);
  const renegotiationRequestedAtRef = useRef(0);
  const readyRenegotiateSentRef = useRef(false);
  const callActionOwnerRef = useRef("");
  const callActionChannelRef = useRef<ReturnType<typeof createCallActionChannel> | null>(null);
  const observedLiveCallRef = useRef(false);
  const mobileFacingModeSwitchAvailable = useMemo(() => {
    if (typeof navigator === "undefined") return false;
    return supportsMobileCameraSwitch({
      facingModeSupported: navigator.mediaDevices?.getSupportedConstraints?.().facingMode === true,
      maxTouchPoints: navigator.maxTouchPoints,
      userAgent: navigator.userAgent,
    });
  }, []);
  if (!callActionOwnerRef.current) callActionOwnerRef.current = createCallActionOwnerId();

  const callQuery = useQuery({
    queryKey: ["call", callId],
    queryFn: () => chatApi.getCall(callId),
    enabled: !!callId,
    refetchInterval: (query) => {
      const currentCall = query.state.data as Call | undefined;
      if (!currentCall) return 2000;
      if (currentCall.status === "ringing" || currentCall.status === "initiated") return 1500;
      if (currentCall.status === "ongoing" && peerState !== "connected") return 2500;
      return socketStatus === "open" ? false : 10000;
    },
  });
  const configQuery = useQuery({ queryKey: ["calling-config"], queryFn: () => chatApi.getCallingConfig(), enabled: !!callId });
  const turnQuery = useQuery({ queryKey: ["turn-credentials"], queryFn: () => chatApi.getTurnCredentials(), enabled: !!callId, staleTime: 5 * 60 * 1000 });
  const orchestrationQuery = useQuery({ queryKey: ["call-orchestration", callId], queryFn: () => chatApi.getCallOrchestration(callId), enabled: !!callId, refetchInterval: false });
  const queryCall = callQuery.data as Call | undefined;
  const call = liveCall ?? queryCall;
  useCallWakeLock(Boolean(call && isLiveCallStatus(call.status)));
  const [clockTick, setClockTick] = useState(() => Date.now());
  const orchestration = liveOrchestration ?? orchestrationQuery.data as Record<string, unknown> | undefined;
  const peerConfig = useMemo<RTCConfiguration>(() => ({
    iceServers: mergeIceServers(configQuery.data?.ice_servers, turnQuery.data?.ice_servers),
    iceTransportPolicy: configQuery.data?.ice_transport_policy ?? "all",
    iceCandidatePoolSize: configQuery.data?.ice_candidate_pool_size ?? 4,
  }), [configQuery.data?.ice_candidate_pool_size, configQuery.data?.ice_servers, configQuery.data?.ice_transport_policy, turnQuery.data?.ice_servers]);
  const peerConfigKey = useMemo(() => JSON.stringify(peerConfig), [peerConfig]);
  const localParticipant = useMemo(
    () => call?.participants?.find((participant) => isSameUserIdentity(participant.user, user)) ?? null,
    [call?.participants, user],
  );
  const localParticipantUser = localParticipant?.user;
  const localCallUser = useMemo(
    () => ({
      id: localParticipantUser?.id ?? user?.id,
      username: localParticipantUser?.username ?? user?.username,
      email: localParticipantUser?.email ?? user?.email,
      display_name: localParticipantUser?.display_name ?? user?.profile?.display_name ?? user?.display_name,
    }),
    [
      localParticipantUser?.display_name,
      localParticipantUser?.email,
      localParticipantUser?.id,
      localParticipantUser?.username,
      user?.display_name,
      user?.email,
      user?.id,
      user?.profile?.display_name,
      user?.username,
    ],
  );
  const localCallUserId = String(localCallUser?.id || user?.id || "");
  const isInitiator = isSameUserIdentity(call?.initiated_by, localCallUser);
  const isDesignatedOfferer = shouldLocalUserCreateOffer(call, localCallUser);
  const remoteJoined = useMemo(
    () => Boolean(call?.participants?.some((participant) => !isSameUserIdentity(participant.user, localCallUser) && participant.state === "joined")),
    [call?.participants, localCallUser],
  );
  const remotePreferredVideoQuality = useMemo(
    () => {
      const preferences = (call?.participants ?? [])
        .filter((participant) => !isSameUserIdentity(participant.user, localCallUser) && participant.state === "joined")
        .map((participant) => participant.preferred_video_quality)
        .filter(Boolean);
      return preferences.includes("low") ? "low" : preferences[0];
    },
    [call?.participants, localCallUser],
  );

  useEffect(() => {
    if (!callId || !callQuery.isError || ![403, 404, 410].includes(getCallHttpStatus(callQuery.error))) return;
    onCallFinished?.(callId);
    if (displayMode === "full") navigate("/calls", { replace: true });
  }, [callId, callQuery.error, callQuery.isError, displayMode, navigate, onCallFinished]);

  const canAcceptCall = Boolean(call && !isInitiator && isLiveCallStatus(call.status) && localParticipant?.state === "ringing");
  const canInitializeLocalMedia = Boolean(call && (isInitiator || localParticipant?.state === "joined"));

  useEffect(() => {
    const channel = createCallActionChannel((event) => {
      if (event.ownerId === callActionOwnerRef.current || event.callId !== callId) return;
      if (event.action === "accepted") {
        void queryClient.invalidateQueries({ queryKey: ["call", callId] });
      } else if (["declined", "cleared"].includes(event.action) && canAcceptCall) {
        navigate(call?.conversation ? `/chat/${call.conversation}` : "/calls", { replace: true });
      }
    });
    callActionChannelRef.current = channel;
    return () => {
      channel.close();
      callActionChannelRef.current = null;
    };
  }, [call?.conversation, callId, canAcceptCall, navigate, queryClient]);

  const acceptMutation = useMutation({
    mutationFn: async () => {
      if (!call) throw new Error("This call is not available.");
      const ownerId = callActionOwnerRef.current;
      if (!claimCallAction(call.id, ownerId)) throw new Error("This call is already being answered in another browser tab.");
      callActionChannelRef.current?.publish({ callId: call.id, action: "accepting", ownerId, occurredAt: Date.now() });
      try {
        try {
          await preflightCallMedia(call.call_type);
        } catch (error) {
          throw new Error(await getCallMediaErrorMessage(error, call.call_type));
        }
        const updated = await chatApi.acceptCall(call.id);
        callActionChannelRef.current?.publish({ callId: call.id, action: "accepted", ownerId, occurredAt: Date.now() });
        return updated;
      } finally {
        releaseCallAction(call.id, ownerId);
      }
    },
    onSuccess: async (updated) => {
      setLiveCall(updated);
      setMediaError(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["call", callId] }),
        queryClient.invalidateQueries({ queryKey: ["recent-calls"] }),
      ]);
    },
    onError: (error) => {
      setMediaError(getCallMutationError(error, "The call could not be answered."));
      if (call) callActionChannelRef.current?.publish({ callId: call.id, action: "released", ownerId: callActionOwnerRef.current, occurredAt: Date.now() });
    },
  });

  const declineMutation = useMutation({
    mutationFn: async () => {
      if (!call) throw new Error("This call is not available.");
      const ownerId = callActionOwnerRef.current;
      if (!claimCallAction(call.id, ownerId)) throw new Error("This call is already being handled in another browser tab.");
      callActionChannelRef.current?.publish({ callId: call.id, action: "declining", ownerId, occurredAt: Date.now() });
      try {
        const updated = await chatApi.declineCall(call.id, "declined_from_call_room");
        callActionChannelRef.current?.publish({ callId: call.id, action: "declined", ownerId, occurredAt: Date.now() });
        return updated;
      } finally {
        releaseCallAction(call.id, ownerId);
      }
    },
    onSuccess: async (updated) => {
      setLiveCall(updated);
      patchCallCaches(queryClient, updated);
      await queryClient.invalidateQueries({ queryKey: ["recent-calls"] });
      navigate(updated.conversation ? `/chat/${updated.conversation}` : "/calls", { replace: true });
    },
    onError: (error) => {
      setMediaError(getCallMutationError(error, "The call could not be declined."));
      if (call) callActionChannelRef.current?.publish({ callId: call.id, action: "released", ownerId: callActionOwnerRef.current, occurredAt: Date.now() });
    },
  });

  const endMutation = useMutation({
    mutationFn: () => chatApi.endCall(callId, "user_left"),
    onSuccess: async (updated) => {
      setLiveCall(updated);
      await queryClient.invalidateQueries({ queryKey: ["recent-calls"] });
      navigate(updated.conversation ? `/chat/${updated.conversation}` : "/calls", { replace: true });
    },
    onError: (error) => setMediaError(getCallMutationError(error, "The call could not be ended.")),
  });

  useEffect(() => {
    if (!call) return;
    if (isLiveCallStatus(call.status)) {
      observedLiveCallRef.current = true;
      return;
    }
    if (call.status === "missed") {
      navigate(call.conversation ? `/chat/${call.conversation}` : "/calls", { replace: true });
      return;
    }
    if (isTerminalCall(call) && !observedLiveCallRef.current) {
      navigate(call.conversation ? `/chat/${call.conversation}` : "/calls", { replace: true });
    }
  }, [call, navigate]);

  useEffect(() => {
    if (!call || !isTerminalCall(call)) return;
    onCallFinished?.(call.id);
  }, [call?.id, call?.status, onCallFinished]);

  useEffect(() => {
    callRef.current = call ?? null;
  }, [call]);

  const sendPresenceHeartbeat = useCallback(async () => {
    if (!callId || !isLiveCallStatus(callRef.current?.status) || !pageVisible) return;
    const payload = await chatApi.sendCallHeartbeat(callId, {
      network_quality: mapMetricsToNetworkQuality({}, networkOnline, peerState),
      metrics: { browser: navigator.userAgent, peer_state: peerState, network_online: networkOnline, page_visible: pageVisible },
    });
    const userId = String(payload?.user_id || localCallUserId || "");
    setLiveCall((current) => markCallParticipantOnline(current, userId));
    await queryClient.invalidateQueries({ queryKey: ["call", callId] });
  }, [callId, localCallUserId, networkOnline, pageVisible, peerState, queryClient]);

  useEffect(() => {
    if (!call || !isLiveCallStatus(call.status)) return;
    const timer = window.setInterval(() => setClockTick(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [call?.id, call?.status]);

  useEffect(() => {
    syncLocalPreview(localVideoRef.current, localStreamRef.current);
    syncRemoteMedia(
      remoteVideoRef.current,
      remoteAudioRef.current,
      remoteVideoStreamRef.current,
      remoteAudioStreamRef.current,
    );
  });

  useEffect(() => {
    if (!queryCall) return;
    setLiveCall((current) => mergeCallPayload(current, queryCall));
  }, [queryCall]);

  useEffect(() => {
    setLiveOrchestration((orchestrationQuery.data as Record<string, unknown> | undefined) ?? null);
  }, [orchestrationQuery.data]);

  const appendSignalLog = useCallback((label: string) => {
    setSignalLog((current) => [`${new Date().toLocaleTimeString()} ${label}`, ...current].slice(0, 12));
  }, []);

  const flushPendingIceCandidates = useCallback(async (peer: RTCPeerConnection) => {
    if (!peer.remoteDescription) return;
    const candidates = pendingIceCandidatesRef.current.splice(0);
    for (const candidate of candidates) {
      try {
        await peer.addIceCandidate(candidate);
      } catch {
        appendSignalLog("ignored bad queued ICE candidate");
      }
    }
  }, [appendSignalLog]);

  const logOutboundMediaStats = useCallback((peer: RTCPeerConnection, label: string) => {
    window.setTimeout(() => {
      void peer.getStats().then((stats) => {
        const parts: string[] = [];
        stats.forEach((report) => {
          if (report.type !== "outbound-rtp" || report.isRemote) return;
          const kind = String(report.kind ?? report.mediaType ?? "media");
          const bytesSent = toFiniteNumber(report.bytesSent) ?? 0;
          const packetsSent = toFiniteNumber(report.packetsSent) ?? 0;
          const framesEncoded = toFiniteNumber(report.framesEncoded);
          parts.push(`${kind}:bytes=${Math.round(bytesSent)} packets=${Math.round(packetsSent)}${framesEncoded !== undefined ? ` frames=${Math.round(framesEncoded)}` : ""}`);
        });
        appendSignalLog(`${label} outbound ${parts.join(" ") || "no outbound stats"}`);
      }).catch(() => appendSignalLog(`${label} outbound stats failed`));
    }, 2500);
  }, [appendSignalLog]);

  const remoteUserId = useMemo(() => {
    const participants = call?.participants ?? [];
    return String(participants.find((participant) => !isSameUserIdentity(participant.user, localCallUser))?.user.id || "");
  }, [call?.participants, localCallUser]);

  const markSignalProcessed = useCallback((signalType: string, payload: Record<string, unknown>) => {
    const now = Date.now();
    const fingerprint = buildSignalFingerprint(signalType, payload);
    const registry = processedSignalsRef.current;
    registry.set(fingerprint, now);
    for (const [key, seenAt] of registry.entries()) {
      if (now - seenAt > SIGNAL_DEDUPE_WINDOW_MS) registry.delete(key);
    }
    return fingerprint;
  }, []);

  const hasSeenSignal = useCallback((signalType: string, payload: Record<string, unknown>) => {
    const fingerprint = buildSignalFingerprint(signalType, payload);
    const seenAt = processedSignalsRef.current.get(fingerprint);
    return typeof seenAt === "number" && Date.now() - seenAt <= SIGNAL_DEDUPE_WINDOW_MS;
  }, []);

  const sendSignal = useCallback(async (signalType: string, payload: Record<string, unknown>, options?: { forceHttp?: boolean }) => {
    const signalId = typeof payload.signal_id === "string" && payload.signal_id ? payload.signal_id : safeId(`call-${signalType}`);
    const envelopePayload: Record<string, unknown> = {
      ...payload,
      call_id: callId,
      conversation_id: String(callRef.current?.conversation || ""),
      call_type: callRef.current?.call_type,
      signal_id: signalId,
      sent_at: payload.sent_at ?? new Date().toISOString(),
      ...(remoteUserId ? { to_user_id: remoteUserId } : {}),
      ...(localCallUserId ? { from_user_id: localCallUserId } : {}),
    };

    let wsDelivered = false;
    if (!options?.forceHttp && socket.isOpen()) {
      try {
        const callGrant = await requestRealtimeCallGrant(callId);
        wsDelivered = socket.send({
          event: "call.signal",
          data: {
            call_id: callId,
            call_grant: callGrant.grant,
            conversation_id: String(callRef.current?.conversation || ""),
            signal_id: signalId,
            ...(remoteUserId ? { to_user_id: remoteUserId } : {}),
            ...(localCallUserId ? { from_user_id: localCallUserId } : {}),
            signal_type: signalType,
            payload: envelopePayload,
          },
        });
      } catch {
        clearRealtimeCallGrant(callId);
        wsDelivered = false;
      }
    }

    const shouldPersistSdp = signalType === "offer" || signalType === "answer";
    if (wsDelivered && !shouldPersistSdp) {
      appendSignalLog(`ws ${signalType}`);
      return { transport: "ws" as const, signalId };
    }

    await chatApi.sendCallSignal(callId, signalType, envelopePayload);
    appendSignalLog(wsDelivered ? `ws+http ${signalType}` : `http ${signalType}`);
    return { transport: wsDelivered ? "ws" as const : "http" as const, signalId };
  }, [appendSignalLog, callId, localCallUserId, remoteUserId, socket]);

  const syncPeerLocalTracks = useCallback(async (peer: RTCPeerConnection, callType: "voice" | "video") => {
    const { audioTransceiver, videoTransceiver } = ensureCallTransceivers(peer, callType);
    const localStream = localStreamRef.current;
    const audioTrack = localStream?.getAudioTracks()[0] ?? null;
    const videoTrack = callType === "video" ? (localStream?.getVideoTracks()[0] ?? null) : null;

    if (audioTrack) audioTrack.enabled = true;
    if (videoTrack) videoTrack.enabled = true;

    await audioTransceiver.sender.replaceTrack(audioTrack);
    audioTransceiver.direction = "sendrecv";

    if (videoTransceiver) {
      await videoTransceiver.sender.replaceTrack(videoTrack);
      videoTransceiver.direction = "sendrecv";
      preferVp8ForTransceiver(videoTransceiver);
    }

    peer.getTransceivers().forEach((transceiver) => {
      const senderKind = transceiver.sender.track?.kind;
      const receiverKind = transceiver.receiver.track?.kind;
      if (senderKind === "audio" || receiverKind === "audio" || senderKind === "video" || receiverKind === "video") {
        transceiver.direction = "sendrecv";
      }
    });
  }, []);

  const sendOffer = useCallback(async (peer: RTCPeerConnection, options?: RTCOfferOptions) => {
    const now = Date.now();
    if (!options?.iceRestart && now - lastOfferSentAtRef.current < 2500) {
      appendSignalLog("offer throttled");
      return false;
    }
    const activeCallType = callRef.current?.call_type ?? "voice";
    await syncPeerLocalTracks(peer, activeCallType);
    appendSignalLog(`offer senders ${summarizeLocalSenders(peer)}`);
    const offer = normalizeLocalSdpDirections(await peer.createOffer(options), activeCallType);
    await peer.setLocalDescription(offer);
    await sendSignal("offer", {
      sdp: offer.sdp,
      type: offer.type,
      sdp_type: offer.type,
      description: { type: offer.type, sdp: offer.sdp },
    });
    lastOfferSentAtRef.current = Date.now();
    appendSignalLog(`offer ${summarizeSdpDirections(offer.sdp ?? "")}`);
    appendSignalLog(options?.iceRestart ? "sent ICE restart offer" : "sent offer");
    logOutboundMediaStats(peer, "offer");
    return true;
  }, [appendSignalLog, logOutboundMediaStats, sendSignal, syncPeerLocalTracks]);

  const markLocalMediaMutation = useCallback(() => {
    lastLocalMediaMutationAtRef.current = Date.now();
  }, []);

  const isWithinLocalMediaMutationWindow = useCallback((windowMs = 8000) => {
    return Date.now() - lastLocalMediaMutationAtRef.current < windowMs;
  }, []);

  const requestRenegotiation = useCallback(async (reason: string) => {
    const peer = peerRef.current;
    const activeCall = callRef.current;
    if (!peer || !activeCall || !isLiveCallStatus(activeCall.status)) return;
    const now = Date.now();
    if (now - renegotiationRequestedAtRef.current < 1200) return;
    if (isWithinLocalMediaMutationWindow()) return;
    renegotiationRequestedAtRef.current = now;
    const localUserShouldOffer = shouldLocalUserCreateOffer(activeCall, localCallUser);
    if (localUserShouldOffer && peer.signalingState === "stable") {
      await sendOffer(peer);
      return;
    }
    await sendSignal("renegotiate", { reason }, { forceHttp: true });
  }, [isWithinLocalMediaMutationWindow, localCallUser, sendOffer, sendSignal]);

  const applyNetworkRecommendation = useCallback(async (
    recommendation: Record<string, unknown> | null | undefined,
    videoActive = videoEnabled,
  ) => {
    const peer = peerRef.current;
    if (!peer || callRef.current?.call_type !== "video") return;
    const sender = getSenderForKind(peer, "video");
    if (!sender) return;
    const profile = (configQuery.data?.network_profiles ?? {}) as Record<string, unknown>;
    const lowBandwidth = (profile.low_bandwidth_video ?? {}) as Record<string, unknown>;
    const networkRecommendation = toSignalRecord(recommendation?.network_recommendation);
    const params = sender.getParameters();
    const encodings = params.encodings?.length ? [...params.encodings] : [{}];
    const senderProfile = resolveVideoSenderProfile({
      mode: String(recommendation?.mode || networkRecommendation.mode || "standard"),
      videoActive,
      compact: displayMode === "compact",
      remotePreferredVideoQuality,
      lowBandwidthMaxBitrate: Number(lowBandwidth.max_bitrate_bps ?? 250_000),
      lowBandwidthMaxFramerate: Number(lowBandwidth.max_framerate ?? 12),
    });
    for (const encoding of encodings) {
      encoding.active = senderProfile.active;
      if (senderProfile.maxBitrate === undefined) delete encoding.maxBitrate;
      else encoding.maxBitrate = senderProfile.maxBitrate;
      if (senderProfile.maxFramerate === undefined) delete encoding.maxFramerate;
      else encoding.maxFramerate = senderProfile.maxFramerate;
      encoding.scaleResolutionDownBy = senderProfile.scaleResolutionDownBy;
    }
    params.encodings = encodings;
    try {
      await sender.setParameters(params);
    } catch {
      appendSignalLog("video sender params skipped");
    }
  }, [appendSignalLog, configQuery.data?.network_profiles, displayMode, remotePreferredVideoQuality, videoEnabled]);

  const refreshVideoDeviceState = useCallback(async (stream: MediaStream | null = localStreamRef.current) => {
    const track = stream?.getVideoTracks()[0];
    if (track) {
      setLocalVideoMirrored(cameraFacingFromTrack(track) !== "environment");
    }
    if (!navigator.mediaDevices?.enumerateDevices) return;
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      setVideoInputCount(devices.filter((device) => device.kind === "videoinput").length);
    } catch {
      setVideoInputCount(track ? 1 : 0);
    }
  }, []);

  useEffect(() => {
    if (!navigator.mediaDevices?.addEventListener) return;
    const handleDeviceChange = () => { void refreshVideoDeviceState(); };
    navigator.mediaDevices.addEventListener("devicechange", handleDeviceChange);
    return () => navigator.mediaDevices.removeEventListener("devicechange", handleDeviceChange);
  }, [refreshVideoDeviceState]);

  const acquireTrack = useCallback(async (
    kind: "audio" | "video",
    options?: { videoDeviceId?: string; facingMode?: CameraFacingMode; relaxedFacingMode?: boolean },
  ) => {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("Media devices are not supported in this browser.");
    const baseVideoConstraints = buildCallMediaConstraints("video").video as MediaTrackConstraints;
    const videoConstraints: MediaTrackConstraints = options?.videoDeviceId
      ? { ...baseVideoConstraints, facingMode: undefined, deviceId: { exact: options.videoDeviceId } }
      : options?.facingMode
        ? {
            ...baseVideoConstraints,
            deviceId: undefined,
            facingMode: options.relaxedFacingMode
              ? { ideal: options.facingMode }
              : { exact: options.facingMode },
          }
        : baseVideoConstraints;
    const constraints = kind === "audio"
      ? { audio: buildCallMediaConstraints("voice").audio, video: false }
      : { audio: false, video: videoConstraints };
    const stream = await navigator.mediaDevices.getUserMedia(constraints);
    const track = kind === "audio" ? stream.getAudioTracks()[0] : stream.getVideoTracks()[0];
    if (!track) {
      stream.getTracks().forEach((item) => item.stop());
      throw new Error(`Unable to access the ${kind === "audio" ? "microphone" : "camera"} track.`);
    }
    return { stream, track };
  }, []);

  const ensureCompleteLocalMedia = useCallback(async (callType: "voice" | "video") => {
    const stream = await requestCallMedia(callType);
    const hasAudio = stream.getAudioTracks().length > 0;
    const hasVideo = stream.getVideoTracks().length > 0;

    if (!hasAudio) {
      const { track } = await acquireTrack("audio");
      stream.addTrack(track);
    }

    if (callType === "video" && !hasVideo) {
      const { track } = await acquireTrack("video");
      stream.addTrack(track);
    }

    if (!stream.getAudioTracks().length) {
      stream.getTracks().forEach((track) => track.stop());
      throw new Error("Microphone could not be started for this call.");
    }

    if (callType === "video" && !stream.getVideoTracks().length) {
      stream.getTracks().forEach((track) => track.stop());
      throw new Error("Camera could not be started for this video call.");
    }

    return stream;
  }, [acquireTrack]);

  const replaceLocalTrack = useCallback(async (
    kind: "audio" | "video",
    options?: {
      videoDeviceId?: string;
      facingMode?: CameraFacingMode;
      relaxedFacingMode?: boolean;
      releaseCurrentBeforeAcquire?: boolean;
    },
  ) => {
    const peer = peerRef.current;
    markLocalMediaMutation();
    const localStream = localStreamRef.current ?? new MediaStream();
    localStreamRef.current = localStream;
    const sender = peer ? getSenderForKind(peer, kind) : null;
    const transceiver = peer ? getTransceiverForKind(peer, kind) : null;
    const previousTracks = kind === "audio" ? localStream.getAudioTracks() : localStream.getVideoTracks();
    const senderHadTrack = Boolean(sender?.track);
    const transceiverWasRecvOnly = transceiver?.direction === "recvonly";
    if (kind === "video" && options?.releaseCurrentBeforeAcquire) {
      previousTracks.forEach((existing) => {
        localStream.removeTrack(existing);
        existing.stop();
      });
      syncLocalPreview(localVideoRef.current, localStream);
    }
    const { stream, track } = await acquireTrack(kind, options);
    try {
      if (sender) {
        await sender.replaceTrack(track);
      } else if (peer && transceiver) {
        await transceiver.sender.replaceTrack(track);
      } else if (peer) {
        peer.addTrack(track, localStream);
      }
      if (transceiver && transceiver.direction === "recvonly") {
        transceiver.direction = "sendrecv";
      }
      previousTracks.forEach((existing) => {
        localStream.removeTrack(existing);
        if (existing.readyState !== "ended") existing.stop();
      });
      localStream.addTrack(track);
      syncLocalPreview(localVideoRef.current, localStream);
      if (kind === "audio") {
        setAudioEnabled(true);
      } else {
        setVideoEnabled(true);
        await refreshVideoDeviceState(localStream);
      }
      track.onended = () => {
        if (kind === "audio") setAudioEnabled(false);
        else setVideoEnabled(false);
        void chatApi.updateCallMediaState(callId, buildMediaStatePatch({
          audio_enabled: kind === "audio" ? false : audioEnabled,
          video_enabled: kind === "video" ? false : videoEnabled,
        }));
      };
    } catch (error) {
      stream.getTracks().forEach((item) => item.stop());
      throw error;
    }
    if (peer) {
      await applyNetworkRecommendation(orchestration);
      if (!senderHadTrack || transceiverWasRecvOnly) {
        window.setTimeout(() => {
          void requestRenegotiation(`${kind}_track_attached`).catch(() => undefined);
        }, 900);
      }
    }
    return track;
  }, [acquireTrack, applyNetworkRecommendation, audioEnabled, callId, markLocalMediaMutation, orchestration, refreshVideoDeviceState, requestRenegotiation, videoEnabled]);

  const handleCallSignal = useCallback(async (signalType: string, signalPayload: Record<string, unknown>) => {
    if (hasSeenSignal(signalType, signalPayload)) {
      appendSignalLog(`deduped ${signalType}`);
      return;
    }
    const peer = peerRef.current;
    if (!peer) {
      pendingSignalsRef.current.push({ signalType, signalPayload });
      appendSignalLog(`queued ${signalType}`);
      return;
    }

    const normalizedSignalType = signalType.replace("-", "_");
    const description = toSignalRecord(signalPayload.description ?? signalPayload.session_description ?? signalPayload.sessionDescription);
    const sdp = typeof signalPayload.sdp === "string" ? signalPayload.sdp : typeof description.sdp === "string" ? description.sdp : "";

    if (normalizedSignalType === "offer" && sdp) {
      const activeCall = callRef.current;
      const activeUserIsInitiator = isSameUserIdentity(activeCall?.initiated_by, localCallUser);
      const selfParticipant = activeCall?.participants?.find((participant) => isSameUserIdentity(participant.user, localCallUser));
      const isAcceptedLocally = activeCall?.status === "ongoing" || selfParticipant?.state === "joined";
      if (!activeUserIsInitiator && !isAcceptedLocally) {
        pendingSignalsRef.current.push({ signalType, signalPayload });
        appendSignalLog("deferred offer until accept");
        return;
      }
      if (peer.signalingState !== "stable") {
        appendSignalLog("ignored offer during negotiation");
        return;
      }
      markSignalProcessed(signalType, signalPayload);
      await peer.setRemoteDescription({ type: "offer", sdp });
      await flushPendingIceCandidates(peer);
      await syncPeerLocalTracks(peer, callRef.current?.call_type ?? "voice");
      appendSignalLog(`answer senders ${summarizeLocalSenders(peer)}`);
      appendSignalLog(`remote offer ${summarizeSdpDirections(sdp)}`);
      const answer = normalizeLocalSdpDirections(await peer.createAnswer(), callRef.current?.call_type ?? "voice");
      await peer.setLocalDescription(answer);
      await sendSignal("answer", {
        sdp: answer.sdp,
        type: answer.type,
        sdp_type: answer.type,
        description: { type: answer.type, sdp: answer.sdp },
      });
      appendSignalLog(`answer ${summarizeSdpDirections(answer.sdp ?? "")}`);
      logOutboundMediaStats(peer, "answer");
      return;
    }

    if (normalizedSignalType === "answer" && sdp) {
      markSignalProcessed(signalType, signalPayload);
      if (peer.signalingState !== "have-local-offer") {
        appendSignalLog(`ignored answer in ${peer.signalingState}`);
        return;
      }
      appendSignalLog(`remote answer ${summarizeSdpDirections(sdp)}`);
      await peer.setRemoteDescription({ type: "answer", sdp });
      await flushPendingIceCandidates(peer);
      return;
    }

    if (["ice_candidate", "candidate"].includes(normalizedSignalType) && signalPayload.candidate) {
      const candidate = signalPayload as RTCIceCandidateInit;
      if (!peer.remoteDescription) {
        pendingIceCandidatesRef.current.push(candidate);
        appendSignalLog("queued ICE candidate");
        return;
      }
      markSignalProcessed(signalType, signalPayload);
      try {
        await peer.addIceCandidate(candidate);
      } catch {
        appendSignalLog("ignored bad ICE candidate");
      }
      return;
    }

    if (normalizedSignalType === "ice_restart") {
      markSignalProcessed(signalType, signalPayload);
      const activeCall = callRef.current;
      const localUserShouldOffer = shouldLocalUserCreateOffer(activeCall, localCallUser);
      if (localUserShouldOffer) await sendOffer(peer, { iceRestart: true });
      return;
    }

    if (normalizedSignalType === "renegotiate") {
      markSignalProcessed(signalType, signalPayload);
      const localUserShouldOffer = shouldLocalUserCreateOffer(callRef.current, localCallUser);
      if (localUserShouldOffer && peer.signalingState === "stable") {
        await sendOffer(peer);
      }
    }
  }, [appendSignalLog, flushPendingIceCandidates, hasSeenSignal, localCallUser, markSignalProcessed, sendOffer, sendSignal, syncPeerLocalTracks]);

  const processPendingSignals = useCallback(async () => {
    const signals = pendingSignalsRef.current.splice(0);
    for (const signal of signals) {
      await handleCallSignal(signal.signalType, signal.signalPayload);
    }
  }, [handleCallSignal]);

  const startLocalOffer = useCallback(async (reason: string) => {
    const peer = peerRef.current;
    const activeCall = callRef.current;
    if (!peer || !activeCall || offerSentRef.current) return;
    if (!shouldLocalUserCreateOffer(activeCall, localCallUser)) return;
    if (!isLiveCallStatus(activeCall.status)) return;
    if (peer.signalingState !== "stable") return;
    offerSentRef.current = true;
    appendSignalLog(`start offer: ${reason}`);
    try {
      const sent = await sendOffer(peer);
      if (!sent) offerSentRef.current = false;
    } catch (error) {
      offerSentRef.current = false;
      setMediaError(error instanceof Error ? error.message : "Unable to start the media handshake.");
    }
  }, [appendSignalLog, localCallUser, sendOffer]);

  const unlockRemotePlayback = useCallback(async () => {
    try {
      await remoteAudioRef.current?.play();
      await remoteVideoRef.current?.play();
      setRemotePlaybackBlocked(false);
      appendSignalLog("remote playback enabled");
    } catch {
      setRemotePlaybackBlocked(true);
    }
  }, [appendSignalLog]);

  useEffect(() => {
    if (!callId || !isLiveCallStatus(call?.status)) return;
    const tryUnlock = () => {
      void unlockRemotePlayback();
    };
    window.addEventListener("pointerdown", tryUnlock, { passive: true });
    window.addEventListener("touchstart", tryUnlock, { passive: true });
    window.addEventListener("keydown", tryUnlock);
    return () => {
      window.removeEventListener("pointerdown", tryUnlock);
      window.removeEventListener("touchstart", tryUnlock);
      window.removeEventListener("keydown", tryUnlock);
    };
  }, [call?.status, callId, unlockRemotePlayback]);

  useEffect(() => {
    const handleOnline = () => setNetworkOnline(true);
    const handleOffline = () => setNetworkOnline(false);
    const handleVisibility = () => setPageVisible(document.visibilityState === "visible");
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, []);

  useEffect(() => {
    if (!callId || !call || !isLiveCallStatus(call.status)) return;
    let cancelled = false;
    const shouldKeepRecovering = () => {
      const peer = peerRef.current;
      if (!peer) return true;
      if (peerState !== "connected") return true;
      if (!peer.remoteDescription) return true;
      if (call.status === "ringing") return true;
      return false;
    };
    const pollSignals = async () => {
      if (!shouldKeepRecovering()) return;
      try {
        const payload = await chatApi.getCallOrchestration(callId);
        if (cancelled) return;
        setLiveOrchestration(payload as Record<string, unknown>);
        const pendingSignals = extractPendingSignals(payload);
        for (const item of pendingSignals) {
          const signalType = String(item.signal_type ?? item.type ?? "");
          if (!signalType) continue;
          const signalPayload = toSignalRecord(item.payload ?? item.data ?? item);
          await handleCallSignal(signalType, signalPayload);
        }
      } catch {
        appendSignalLog("http orchestration retry failed");
      }
    };
    void pollSignals();
    const timer = window.setInterval(() => {
      void pollSignals();
    }, socketStatus === "open" ? 1200 : 1800);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [appendSignalLog, call, callId, handleCallSignal, peerState, socketStatus]);

  useEffect(() => {
    if (!callId || !call?.conversation) return;
    socket.subscribeToConversation(String(call.conversation));
    const unsubscribe = socket.subscribe(async (payload) => {
      if (payload.event === "call.accepted" && String(payload.data?.id || "") === callId) {
        setLiveCall((current) => mergeCallPayload(current, payload.data as Partial<Call> & { id?: string }));
        await queryClient.invalidateQueries({ queryKey: ["call", callId] });
        window.setTimeout(() => {
          void startLocalOffer("accepted").catch(() => undefined);
        }, 100);
        return;
      }
      if ((payload.event === "call.ended" || payload.event === "call.declined" || payload.event === "call.missed" || payload.event === "call.failed") && String(payload.data?.id || "") === callId) {
        setLiveCall((current) => mergeCallPayload(current, payload.data as Partial<Call> & { id?: string }));
        await queryClient.invalidateQueries({ queryKey: ["call", callId] });
        const conversationId = String(payload.data?.conversation || callRef.current?.conversation || "");
        navigate(conversationId ? `/chat/${conversationId}` : "/calls", { replace: true });
        return;
      }
      if (payload.event === "call.media_state" && String(payload.data?.call_id || "") === callId) {
        const userId = String(payload.data?.user_id || "");
        setLiveCall((current) => mergeCallParticipantPatch(markCallParticipantOnline(current, userId), userId, {
          audio_enabled: typeof payload.data?.audio_enabled === "boolean" ? Boolean(payload.data.audio_enabled) : undefined,
          video_enabled: typeof payload.data?.video_enabled === "boolean" ? Boolean(payload.data.video_enabled) : undefined,
          is_on_hold: typeof payload.data?.is_on_hold === "boolean" ? Boolean(payload.data.is_on_hold) : undefined,
          reconnecting: typeof payload.data?.reconnecting === "boolean" ? Boolean(payload.data.reconnecting) : undefined,
          connection_state: typeof payload.data?.connection_state === "string" ? String(payload.data.connection_state) : undefined,
          audio_route: typeof payload.data?.audio_route === "string" ? String(payload.data.audio_route) : undefined,
          screen_share_enabled: typeof payload.data?.screen_share_enabled === "boolean" ? Boolean(payload.data.screen_share_enabled) : undefined,
          preferred_video_quality: typeof payload.data?.preferred_video_quality === "string" ? String(payload.data.preferred_video_quality) : undefined,
        }));
        return;
      }
      if (payload.event === "call.heartbeat" || payload.event === "call.quality_report") {
        if (String(payload.data?.call_id || "") !== callId) return;
        const userId = String(payload.data?.user_id || "");
        if (payload.event === "call.heartbeat") {
          setLiveCall((current) => mergeCallParticipantPatch(markCallParticipantOnline(current, userId), userId, {
            network_quality: typeof payload.data?.network_quality === "string" ? String(payload.data.network_quality) : undefined,
            reconnecting: false,
          }));
        } else {
          setLiveCall((current) => mergeCallParticipantPatch(markCallParticipantOnline(current, userId), userId, {
            network_quality: typeof payload.data?.network_quality === "string" ? String(payload.data.network_quality) : undefined,
            preferred_video_quality: typeof payload.data?.preferred_video_quality === "string" ? String(payload.data.preferred_video_quality) : undefined,
            quality_score: typeof payload.data?.quality_score === "number" ? payload.data.quality_score as number : undefined,
            quality_alert: typeof payload.data?.quality_alert === "string" ? String(payload.data.quality_alert) : undefined,
            audio_enabled: typeof payload.data?.audio_enabled === "boolean" ? Boolean(payload.data.audio_enabled) : undefined,
            video_enabled: typeof payload.data?.video_enabled === "boolean" ? Boolean(payload.data.video_enabled) : undefined,
          }));
        }
        return;
      }
      if (payload.event === "call.orchestration" && String(payload.data?.call_id || "") === callId) {
        setLiveOrchestration(toSignalRecord(payload.data));
        return;
      }
      if (payload.event !== "call.signal") return;
      if (String(payload.data?.call_id || "") !== callId) return;
      if (isSameUserIdentity({ id: String(payload.data?.from_user_id || "") }, localCallUser)) return;
      const signalType = String(payload.data?.signal_type || "");
      const signalPayload = {
        ...toSignalRecord(payload.data?.payload),
        signal_id: String(payload.data?.signal_id || toSignalRecord(payload.data?.payload).signal_id || ""),
      } as Record<string, unknown>;
      const targetUserId = String(payload.data?.to_user_id || signalPayload.to_user_id || "");
      if (targetUserId && !isSameUserIdentity({ id: targetUserId }, localCallUser)) return;
      appendSignalLog(signalType);
      await handleCallSignal(signalType, signalPayload);
    });
    return () => {
      socket.unsubscribeFromConversation(String(call.conversation));
      unsubscribe();
    };
  }, [appendSignalLog, call?.conversation, callId, handleCallSignal, localCallUser, navigate, queryClient, socket, startLocalOffer]);

  useEffect(() => {
    const callType = call?.call_type;
    if (!callId || !callType || !canInitializeLocalMedia) return;
    let cancelled = false;
    const createPeer = () => {
      const peer = new RTCPeerConnection(JSON.parse(peerConfigKey) as RTCConfiguration);
      peerRef.current = peer;
      remoteVideoStreamRef.current = new MediaStream();
      remoteAudioStreamRef.current = new MediaStream();
      syncRemoteMedia(
        remoteVideoRef.current,
        remoteAudioRef.current,
        remoteVideoStreamRef.current,
        remoteAudioStreamRef.current,
      );
      peer.ontrack = (event) => {
        const routeTrack = (track: MediaStreamTrack) => {
          const targetStream = track.kind === "video" ? remoteVideoStreamRef.current : remoteAudioStreamRef.current;
          if (!targetStream) return;
          if (!targetStream.getTracks().some((existing) => existing.id === track.id)) {
            targetStream.addTrack(track);
          }
          track.onunmute = () => {
            syncRemoteMedia(
              remoteVideoRef.current,
              remoteAudioRef.current,
              remoteVideoStreamRef.current,
              remoteAudioStreamRef.current,
            );
            const playTarget = track.kind === "video" ? remoteVideoRef.current : remoteAudioRef.current;
            void tryPlayMediaElement(playTarget);
          };
          track.onended = () => {
            targetStream.removeTrack(track);
            setRemoteTrackCount(
              (remoteVideoStreamRef.current?.getTracks().length ?? 0)
              + (remoteAudioStreamRef.current?.getTracks().length ?? 0),
            );
          };
        };

        const incomingStream = event.streams[0];
        if (incomingStream) {
          incomingStream.getTracks().forEach(routeTrack);
        } else {
          routeTrack(event.track);
        }
        setRemoteTrackCount(
          (remoteVideoStreamRef.current?.getTracks().length ?? 0)
          + (remoteAudioStreamRef.current?.getTracks().length ?? 0),
        );
        syncRemoteMedia(
          remoteVideoRef.current,
          remoteAudioRef.current,
          remoteVideoStreamRef.current,
          remoteAudioStreamRef.current,
        );
        const audioPlay = tryPlayMediaElement(remoteAudioRef.current);
        const videoPlay = tryPlayMediaElement(remoteVideoRef.current);
        void Promise.all([audioPlay, videoPlay]).then((results) => {
          setRemotePlaybackBlocked(results.some((result) => result === false));
        });
      };
      peer.onicecandidate = (event) => {
        if (event.candidate) {
          void sendSignal("ice_candidate", event.candidate.toJSON() as Record<string, unknown>).catch(() => undefined);
        }
      };
      peer.onnegotiationneeded = () => {
        if (isWithinLocalMediaMutationWindow()) return;
        if (!peer.localDescription && !peer.remoteDescription) return;
        void requestRenegotiation("negotiation_needed").catch(() => undefined);
      };
      const syncPeerState = () => {
        const state = peer.connectionState || "connecting";
        setPeerState(state);
        const reconnecting = ["disconnected", "failed", "closed"].includes(state);
        void chatApi.updateCallMediaState(callId, buildMediaStatePatch({
          connection_state: state,
          reconnecting,
        }));
      };
      peer.onconnectionstatechange = syncPeerState;
      peer.oniceconnectionstatechange = () => {
        appendSignalLog(`ICE ${peer.iceConnectionState}`);
        if (isWithinLocalMediaMutationWindow(10000)) return;
        if (peer.iceConnectionState === "connected" || peer.iceConnectionState === "completed") {
          if (disconnectGraceTimerRef.current) {
            window.clearTimeout(disconnectGraceTimerRef.current);
            disconnectGraceTimerRef.current = null;
          }
          if (iceRestartTimerRef.current) {
            window.clearTimeout(iceRestartTimerRef.current);
            iceRestartTimerRef.current = null;
          }
          return;
        }
        if (peer.iceConnectionState !== "disconnected" && peer.iceConnectionState !== "failed") return;
        if (disconnectGraceTimerRef.current || iceRestartTimerRef.current) return;
        disconnectGraceTimerRef.current = window.setTimeout(() => {
          disconnectGraceTimerRef.current = null;
          if (!peerRef.current) return;
          if (isWithinLocalMediaMutationWindow(10000)) return;
          if (peerRef.current.iceConnectionState === "disconnected") return;
          if (peerRef.current.iceConnectionState !== "failed") return;
          iceRestartTimerRef.current = window.setTimeout(() => {
            iceRestartTimerRef.current = null;
            const activePeer = peerRef.current;
            const activeCall = callRef.current;
            if (!activePeer || !activeCall || !isLiveCallStatus(activeCall.status)) return;
            if (isWithinLocalMediaMutationWindow(10000)) return;
            if (activePeer.iceConnectionState !== "failed") return;
            const now = Date.now();
            if (now - lastIceRestartAtRef.current < 20000) return;
            lastIceRestartAtRef.current = now;
            appendSignalLog("auto ICE restart");
            void chatApi.updateCallMediaState(callId, buildMediaStatePatch({
              connection_state: activePeer.connectionState || "disconnected",
              reconnecting: true,
            })).catch(() => undefined);
            const activeUserShouldOffer = shouldLocalUserCreateOffer(activeCall, localCallUser);
            const restart = activeUserShouldOffer
              ? sendOffer(activePeer, { iceRestart: true })
              : sendSignal("ice_restart", { reason: "auto_recovery" });
            void restart.catch((error) => {
              setMediaError(error instanceof Error ? error.message : "Unable to restart the media path.");
            });
          }, 1500);
        }, 10000);
      };
      setPeerReady(true);
      return peer;
    };
    const init = async () => {
      try {
        const stream = await ensureCompleteLocalMedia(callType);
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        setMediaError(null);
        setAudioEnabled(stream.getAudioTracks().some((track) => track.enabled));
        setVideoEnabled(stream.getVideoTracks().some((track) => track.enabled));
        localStreamRef.current = stream;
        syncLocalPreview(localVideoRef.current, stream);
        void refreshVideoDeviceState(stream);

        const peer = createPeer();
        const activeUserIsInitiator = isSameUserIdentity(callRef.current?.initiated_by, localCallUser);
        if (activeUserIsInitiator) {
          await syncPeerLocalTracks(peer, callType);
        }
        void chatApi.updateCallMediaState(callId, buildMediaStatePatch({
          audio_enabled: stream.getAudioTracks().some((track) => track.enabled),
          video_enabled: stream.getVideoTracks().some((track) => track.enabled),
          connection_state: peer.connectionState || "connecting",
          reconnecting: false,
        })).catch(() => undefined);
        void processPendingSignals().catch((error) => {
          setMediaError(error instanceof Error ? error.message : "Unable to process call signaling.");
        });
      } catch (error) {
        if (cancelled) return;
        createPeer();
        setAudioEnabled(false);
        setVideoEnabled(false);
        setMediaError(await getCallMediaErrorMessage(error, callType));
        void chatApi.updateCallMediaState(callId, buildMediaStatePatch({ audio_enabled: false, video_enabled: false }));
        void processPendingSignals().catch((signalError) => {
          setMediaError(signalError instanceof Error ? signalError.message : "Unable to process call signaling.");
        });
      }
    };
    void init();
    return () => {
      cancelled = true;
      peerRef.current?.close();
      peerRef.current = null;
      setPeerReady(false);
      localStreamRef.current?.getTracks().forEach((track) => track.stop());
      remoteVideoStreamRef.current?.getTracks().forEach((track) => track.stop());
      remoteAudioStreamRef.current?.getTracks().forEach((track) => track.stop());
      localStreamRef.current = null;
      remoteVideoStreamRef.current = null;
      remoteAudioStreamRef.current = null;
      statsSampleRef.current = null;
      offerSentRef.current = false;
      lastOfferSentAtRef.current = 0;
      readyRenegotiateSentRef.current = false;
      pendingSignalsRef.current = [];
      pendingIceCandidatesRef.current = [];
      processedSignalsRef.current.clear();
      observedOrchestrationSignalIdsRef.current.clear();
      setRemoteTrackCount(0);
      setRemotePlaybackBlocked(false);
      if (disconnectGraceTimerRef.current) {
        window.clearTimeout(disconnectGraceTimerRef.current);
        disconnectGraceTimerRef.current = null;
      }
      if (iceRestartTimerRef.current) {
        window.clearTimeout(iceRestartTimerRef.current);
        iceRestartTimerRef.current = null;
      }
    };
  }, [appendSignalLog, call?.call_type, callId, canInitializeLocalMedia, ensureCompleteLocalMedia, isWithinLocalMediaMutationWindow, localCallUser, mediaAttempt, peerConfigKey, refreshVideoDeviceState, requestRenegotiation, sendOffer, sendSignal, syncPeerLocalTracks]);

  useEffect(() => {
    if (!callId || !isDesignatedOfferer || !peerReady) return;
    if (call?.status !== "ongoing" && !remoteJoined) return;
    void startLocalOffer(remoteJoined ? "remote joined" : "ongoing").catch(() => undefined);
  }, [call?.status, callId, isDesignatedOfferer, peerReady, remoteJoined, startLocalOffer]);

  useEffect(() => {
    if (!callId || !peerReady || isDesignatedOfferer || readyRenegotiateSentRef.current) return;
    if (call?.status !== "ongoing" && !remoteJoined) return;
    readyRenegotiateSentRef.current = true;
    void sendSignal("renegotiate", { reason: "participant_ready" }, { forceHttp: true }).catch(() => {
      readyRenegotiateSentRef.current = false;
    });
  }, [call?.status, callId, isDesignatedOfferer, peerReady, remoteJoined, sendSignal]);

  useEffect(() => {
    if (call?.status !== "ongoing") return;
    if (!pendingSignalsRef.current.length) return;
    void processPendingSignals().catch((error) => {
      setMediaError(error instanceof Error ? error.message : "Unable to resume deferred call signaling.");
    });
  }, [call?.status, processPendingSignals]);

  useEffect(() => {
    if (!peerReady) return;
    void applyNetworkRecommendation(orchestration);
  }, [applyNetworkRecommendation, orchestration, peerReady]);

  useEffect(() => {
    if (!callId || call?.call_type !== "video" || !isLiveCallStatus(call.status)) return;
    const preferredVideoQuality = videoEnabled
      ? String(orchestration?.recommended_video_quality || "high")
      : "off";
    const profileKey = `${callId}:${displayMode}:${preferredVideoQuality}`;
    if (lastSurfaceVideoProfileRef.current === profileKey) return;
    lastSurfaceVideoProfileRef.current = profileKey;
    void chatApi.updateCallMediaState(callId, buildMediaStatePatch({
      preferred_video_quality: preferredVideoQuality,
      diagnostics: {
        surface_mode: displayMode,
        persistent_video_profile: displayMode === "compact",
      },
    })).catch(() => {
      if (lastSurfaceVideoProfileRef.current === profileKey) lastSurfaceVideoProfileRef.current = "";
    });
  }, [call?.call_type, call?.status, callId, displayMode, orchestration?.recommended_video_quality, videoEnabled]);

  useEffect(() => {
    if (!orchestration) return;
    const pendingSignals = extractPendingSignals(orchestration);
    if (!pendingSignals.length) return;
    const processSignals = async () => {
      for (const item of pendingSignals) {
        const signalType = String(item.signal_type ?? item.type ?? "");
        if (!signalType) continue;
        const signalPayload = toSignalRecord(item.payload ?? item.data ?? item);
        const signalId = String(item.signal_id ?? signalPayload.signal_id ?? "");
        if (signalId) {
          if (observedOrchestrationSignalIdsRef.current.has(signalId)) continue;
          observedOrchestrationSignalIdsRef.current.add(signalId);
        }
        await handleCallSignal(signalType, signalPayload);
      }
    };
    void processSignals().catch((error) => {
      setMediaError(error instanceof Error ? error.message : "Unable to process call signaling.");
    });
  }, [handleCallSignal, orchestration]);

  useEffect(() => {
    if (!callId || !isLiveCallStatus(call?.status) || !pageVisible) return;
    const networkProfiles = (configQuery.data?.network_profiles ?? {}) as Record<string, unknown>;
    const reconnectProfile = (networkProfiles.reconnect_profile ?? {}) as Record<string, unknown>;
    const heartbeatIntervalSeconds = Math.max(
      Number(configQuery.data?.heartbeat_interval_seconds ?? reconnectProfile.heartbeat_interval_seconds ?? 10),
      6,
    );
    void sendPresenceHeartbeat().catch(() => undefined);
    const timer = window.setInterval(() => {
      void sendPresenceHeartbeat().catch(() => undefined);
    }, heartbeatIntervalSeconds * 1000);
    return () => window.clearInterval(timer);
  }, [call?.status, callId, configQuery.data?.heartbeat_interval_seconds, configQuery.data?.network_profiles, pageVisible, sendPresenceHeartbeat]);

  useEffect(() => {
    if (!callId || !isLiveCallStatus(call?.status) || !pageVisible) return;
    const timer = window.setInterval(() => {
      const peer = peerRef.current;
      const submit = async () => {
        const metrics = peer
          ? await collectPeerQualityMetrics(peer, statsSampleRef, networkOnline, peerState)
          : {
              networkQuality: mapMetricsToNetworkQuality({}, networkOnline, peerState),
              preferredVideoQuality: videoEnabled ? "high" : "off",
            } as PeerQualityMetrics;
        await chatApi.sendCallQualityReport(callId, {
          audio_enabled: audioEnabled,
          video_enabled: videoEnabled,
          peer_state: peerState,
          network_quality: metrics.networkQuality,
          preferred_video_quality: metrics.preferredVideoQuality,
          packet_loss_pct: metrics.packetLossPct,
          round_trip_time_ms: metrics.roundTripTimeMs,
          jitter_ms: metrics.jitterMs,
          bitrate_kbps: metrics.bitrateKbps ? Math.round(metrics.bitrateKbps) : undefined,
          frame_rate: metrics.frameRate ? Math.round(metrics.frameRate) : undefined,
          diagnostics: {
            browser: navigator.userAgent,
            peer_state: peerState,
            network_online: networkOnline,
            surface_mode: displayMode,
          },
        });
      };
      void submit().catch(() => undefined);
    }, Math.max(configQuery.data?.quality_report_interval_seconds ?? 12, displayMode === "compact" ? 24 : 12) * 1000);
    return () => window.clearInterval(timer);
  }, [audioEnabled, call?.status, callId, configQuery.data?.quality_report_interval_seconds, displayMode, networkOnline, pageVisible, peerState, videoEnabled]);

  const toggleAudio = async () => {
    const next = !audioEnabled;
    try {
      setMediaAction(next ? "Turning microphone on..." : "Muting microphone...");
      markLocalMediaMutation();
      if (next && !localStreamRef.current?.getAudioTracks().length) {
        await replaceLocalTrack("audio");
      } else {
        localStreamRef.current?.getAudioTracks().forEach((track) => {
          track.enabled = next;
        });
        setAudioEnabled(next);
      }
      await chatApi.updateCallMediaState(callId, buildMediaStatePatch({
        audio_enabled: next,
        preferred_video_quality: String(orchestration?.recommended_video_quality || (videoEnabled ? "high" : "off")),
      }));
    } catch (error) {
      setAudioEnabled(!next);
      setMediaError(next ? await getCallMediaErrorMessage(error, call?.call_type === "video" ? "video" : "voice") : error instanceof Error ? error.message : "Unable to update microphone state.");
    } finally {
      setMediaAction(null);
    }
  };

  const toggleVideo = async () => {
    const next = !videoEnabled;
    try {
      setMediaAction(next ? "Turning camera on..." : "Turning camera off...");
      markLocalMediaMutation();
      if (next && !localStreamRef.current?.getVideoTracks().length) {
        await replaceLocalTrack("video");
      } else {
        localStreamRef.current?.getVideoTracks().forEach((track) => {
          track.enabled = next;
        });
        setVideoEnabled(next);
      }
      await applyNetworkRecommendation({
        ...orchestration,
        mode: next ? orchestration?.mode ?? "standard" : "audio_only",
      }, next);
      await chatApi.updateCallMediaState(callId, buildMediaStatePatch({
        video_enabled: next,
        preferred_video_quality: next ? String(orchestration?.recommended_video_quality || "high") : "off",
      }));
    } catch (error) {
      setVideoEnabled(!next);
      setMediaError(next ? await getCallMediaErrorMessage(error, "video") : error instanceof Error ? error.message : "Unable to update camera state.");
    } finally {
      setMediaAction(null);
    }
  };

  const switchCamera = async () => {
    if (!navigator.mediaDevices?.getUserMedia) return;
    const previousTrack = localStreamRef.current?.getVideoTracks()[0];
    const previousFacing = cameraFacingFromTrack(previousTrack);
    const cameraWasEnabled = videoEnabled;
    try {
      setMediaAction("Switching camera...");
      setMediaError(null);
      const devices = navigator.mediaDevices.enumerateDevices
        ? (await navigator.mediaDevices.enumerateDevices()).filter((device) => device.kind === "videoinput")
        : [];
      setVideoInputCount(devices.length);
      const currentTrack = localStreamRef.current?.getVideoTracks()[0];
      const currentDeviceId = currentTrack?.getSettings().deviceId || "";
      const targetFacing: CameraFacingMode = cameraFacingFromTrack(currentTrack) === "environment" ? "user" : "environment";
      const nextDevice = findPreferredCameraDevice(devices, targetFacing, currentDeviceId);

      let switched = false;
      if (nextDevice?.deviceId) {
        try {
          await replaceLocalTrack("video", { videoDeviceId: nextDevice.deviceId });
          switched = true;
        } catch {
          // Some mobile browsers enumerate both cameras but only allow switching by facing mode.
        }
      }
      if (!switched) {
        try {
          await replaceLocalTrack("video", {
            facingMode: targetFacing,
            releaseCurrentBeforeAcquire: true,
          });
          switched = true;
        } catch {
          await replaceLocalTrack("video", {
            facingMode: targetFacing,
            relaxedFacingMode: true,
            releaseCurrentBeforeAcquire: true,
          });
          switched = true;
        }
      }
      if (!switched) throw new Error("No alternate camera is available.");
      const activeTrack = localStreamRef.current?.getVideoTracks()[0];
      if (activeTrack) activeTrack.enabled = true;
      setVideoEnabled(true);
      syncLocalPreview(localVideoRef.current, localStreamRef.current);
      await applyNetworkRecommendation({
        ...orchestration,
        mode: orchestration?.mode === "audio_only" ? "standard" : orchestration?.mode ?? "standard",
      }, true);
      await chatApi.updateCallMediaState(callId, buildMediaStatePatch({
        video_enabled: true,
        preferred_video_quality: String(orchestration?.recommended_video_quality || "high"),
      }));
    } catch (error) {
      let usableVideoTrack = localStreamRef.current?.getVideoTracks().some((track) => track.readyState === "live") ?? false;
      if (!usableVideoTrack && previousTrack) {
        try {
          const restoredTrack = await replaceLocalTrack("video", {
            facingMode: previousFacing,
            relaxedFacingMode: true,
            releaseCurrentBeforeAcquire: true,
          });
          restoredTrack.enabled = cameraWasEnabled;
          setLocalVideoMirrored(previousFacing !== "environment");
          usableVideoTrack = true;
        } catch {
          // Keep the original switch error when the browser cannot restore the previous camera.
        }
      }
      setVideoEnabled(usableVideoTrack && cameraWasEnabled);
      setMediaError(error instanceof Error ? error.message : "Unable to switch camera.");
    } finally {
      setMediaAction(null);
    }
  };

  const requestIceRestart = async () => {
    try {
      setMediaAction("Restarting media path...");
      setSignalLog((current) => [`${new Date().toLocaleTimeString()} manual ICE restart`, ...current].slice(0, 12));
      const peer = peerRef.current;
      if (peer && isDesignatedOfferer) {
        await sendOffer(peer, { iceRestart: true });
        return;
      }
      await sendSignal("ice_restart", { reason: "manual_recovery" }, { forceHttp: true });
    } catch (error) {
      setMediaError(error instanceof Error ? error.message : "Unable to restart the media path.");
    } finally {
      setMediaAction(null);
    }
  };

  const retryMediaFromUserTap = async () => {
    if (!call) return;
    try {
      setMediaAction("Checking media permission...");
      await preflightCallMedia(call.call_type);
      setMediaError(null);
      setMediaAttempt((current) => current + 1);
    } catch (error) {
      setMediaError(await getCallMediaErrorMessage(error, call.call_type));
    } finally {
      setMediaAction(null);
    }
  };

  if (!call && displayMode === "compact") {
    return (
      <section className="ms-persistent-call ms-persistent-call--compact" aria-label="Restoring active call">
        <aside className="ms-active-call-bar">
          <div className="ms-active-call-bar__identity" role="status">
            <span className="ms-active-call-bar__avatar" aria-hidden="true">CS</span>
            <span className="ms-active-call-bar__copy">
              <strong>Restoring active call</strong>
              <span><i className="is-warn" aria-hidden="true" />Reconnecting secure media…</span>
            </span>
          </div>
          <div className="ms-active-call-bar__controls">
            <button
              type="button"
              className="ms-active-call-bar__control"
              onClick={() => navigate(`/calls/${callId}`)}
              aria-label="Open active call"
              title="Open active call"
            >
              <CompactExpandIcon />
            </button>
          </div>
        </aside>
      </section>
    );
  }

  if (!call) {
    return (
      <div className="ms-page-loading" role={callQuery.isError ? "alert" : "status"}>
        {callQuery.isError ? "This call is no longer available." : "Loading call…"}
      </div>
    );
  }

  const participants = call.participants ?? [];
  const selfParticipant = participants.find((participant) => isSameUserIdentity(participant.user, localCallUser));
  const remoteParticipants = participants.filter((participant) => !isSameUserIdentity(participant.user, localCallUser));
  const isGroupCall = participants.length > 2;
  const primaryRemoteParticipant = remoteParticipants.find((participant) => participant.state === "joined") ?? remoteParticipants[0];
  const ringingStartedAt = Date.parse(call.started_at || "");
  const ringingSeconds = call.status === "ringing" || call.status === "initiated"
    ? (Number.isFinite(ringingStartedAt) ? Math.max(0, Math.floor((clockTick - ringingStartedAt) / 1000)) : call.ringing_seconds ?? 0)
    : call.ringing_seconds ?? 0;
  const ringTimeoutSeconds = call.ring_timeout_seconds ?? 45;
  const ringRemainingSeconds = Math.max(ringTimeoutSeconds - ringingSeconds, 0);
  const callUxState = getCallViewState(call, { isInitiator, remoteParticipants, peerState, ringingSeconds });
  const qualityAlerts = (call.participants ?? []).filter((participant) => participant.quality_alert).map((participant) => ({
    id: participant.id,
    name: participant.user.display_name || participant.user.username,
    alert: String(participant.quality_alert),
    score: participant.quality_score,
  }));
  const connectionNeedsHelp = !networkOnline || socketStatus !== "open" || ["disconnected", "failed"].includes(peerState);
  const callActionBusy = Boolean(mediaAction) || acceptMutation.isPending || declineMutation.isPending || endMutation.isPending;
  const answeredStartedAt = Date.parse(call.answered_at || call.started_at || "");
  const connectedSeconds = call.status === "ongoing" && Number.isFinite(answeredStartedAt)
    ? Math.max(0, Math.floor((clockTick - answeredStartedAt) / 1000))
    : Math.max(0, Number(call.duration_seconds || 0));
  const primaryQualityAlert = qualityAlerts[0];
  const minimizeCall = () => {
    onCallMinimize?.(call.id);
    navigate(call.conversation ? `/chat/${call.conversation}` : "/chat");
  };
  const compactDisplayName = isGroupCall
    ? `${Math.max(remoteParticipants.length, 1) + 1}-person call`
    : participantName(primaryRemoteParticipant);
  const compactStatus = callUxState.label === "Connected"
    ? `${callUxState.label} · ${formatElapsed(connectedSeconds)}`
    : callUxState.label;

  const callScreen = call.call_type === "voice"
    ? (
      <AudioCallScreen
        remoteParticipant={primaryRemoteParticipant}
        remoteParticipants={remoteParticipants}
        isGroupCall={isGroupCall}
        viewState={callUxState}
        elapsedSeconds={connectedSeconds}
        ringRemainingSeconds={ringRemainingSeconds}
        audioEnabled={audioEnabled}
        speakerEnabled={speakerEnabled}
        busy={callActionBusy}
        canAccept={canAcceptCall}
        accepting={acceptMutation.isPending}
        mediaError={mediaError}
        connectionNeedsHelp={connectionNeedsHelp}
        remotePlaybackBlocked={remotePlaybackBlocked}
        qualityMessage={primaryQualityAlert ? "Call quality is reduced. The connection is adjusting automatically." : undefined}
        remoteAudioRef={remoteAudioRef}
        onLeave={minimizeCall}
        onAccept={() => acceptMutation.mutate()}
        onToggleAudio={toggleAudio}
        onToggleSpeaker={() => setSpeakerEnabled((current) => !current)}
        onHangup={() => canAcceptCall ? declineMutation.mutate() : endMutation.mutate()}
        onRetryMedia={() => void retryMediaFromUserTap()}
        onRestartConnection={() => void requestIceRestart()}
        onEnableSound={() => void unlockRemotePlayback()}
      />
    )
    : (
      <VideoCallScreen
      selfParticipant={selfParticipant}
      remoteParticipant={primaryRemoteParticipant}
      remoteParticipants={remoteParticipants}
      isGroupCall={isGroupCall}
      viewState={callUxState}
      elapsedSeconds={connectedSeconds}
      ringRemainingSeconds={ringRemainingSeconds}
      audioEnabled={audioEnabled}
      videoEnabled={videoEnabled}
      speakerEnabled={speakerEnabled}
      busy={callActionBusy}
      canAccept={canAcceptCall}
      accepting={acceptMutation.isPending}
      mediaError={mediaError}
      connectionNeedsHelp={connectionNeedsHelp}
      remotePlaybackBlocked={remotePlaybackBlocked}
      qualityMessage={primaryQualityAlert ? "Call quality is reduced. The connection is adjusting automatically." : undefined}
      remoteTrackCount={remoteTrackCount}
      canSwitchCamera={videoInputCount > 1 || mobileFacingModeSwitchAvailable}
      localVideoMirrored={localVideoMirrored}
      localVideoRef={localVideoRef}
      remoteVideoRef={remoteVideoRef}
      remoteAudioRef={remoteAudioRef}
      onLeave={minimizeCall}
      onAccept={() => acceptMutation.mutate()}
      onToggleAudio={toggleAudio}
      onToggleVideo={toggleVideo}
      onToggleSpeaker={() => setSpeakerEnabled((current) => !current)}
      onSwitchCamera={() => void switchCamera()}
      onHangup={() => canAcceptCall ? declineMutation.mutate() : endMutation.mutate()}
      onRetryMedia={() => void retryMediaFromUserTap()}
      onRestartConnection={() => void requestIceRestart()}
      onEnableSound={() => void unlockRemotePlayback()}
      onVideoLayoutChange={() => {
        syncLocalPreview(localVideoRef.current, localStreamRef.current);
        syncRemoteMedia(
          remoteVideoRef.current,
          remoteAudioRef.current,
          remoteVideoStreamRef.current,
          remoteAudioStreamRef.current,
        );
      }}
      />
    );

  return (
    <section className={`ms-persistent-call ms-persistent-call--${displayMode}`} aria-label="Active call">
      <div
        ref={(element) => {
          if (element) element.inert = displayMode === "compact";
        }}
        className="ms-persistent-call__stage"
        aria-hidden={displayMode === "compact"}
      >
        {callScreen}
      </div>
      {displayMode === "compact" ? (
        <aside className="ms-active-call-bar" aria-label={`Active call with ${compactDisplayName}`}>
          <button
            type="button"
            className="ms-active-call-bar__identity"
            onClick={() => navigate(`/calls/${callId}`)}
            aria-label={`Return to call with ${compactDisplayName}`}
          >
            <span className={`ms-active-call-bar__avatar is-${callUxState.tone}`} aria-hidden="true">
              {participantInitials(primaryRemoteParticipant)}
            </span>
            <span className="ms-active-call-bar__copy">
              <strong>{compactDisplayName}</strong>
              <span><i className={`is-${callUxState.tone}`} aria-hidden="true" />{compactStatus}</span>
            </span>
          </button>
          <div className="ms-active-call-bar__controls" aria-label="Call controls">
            {canAcceptCall ? (
              <button
                type="button"
                className="ms-active-call-bar__control is-accept"
                onClick={() => acceptMutation.mutate()}
                disabled={callActionBusy}
                aria-label="Answer call"
                title="Answer call"
              >
                <CompactAcceptIcon />
              </button>
            ) : (
              <>
                <button
                  type="button"
                  className={`ms-active-call-bar__control ${audioEnabled ? "" : "is-off"}`}
                  onClick={() => void toggleAudio()}
                  disabled={callActionBusy}
                  aria-label={audioEnabled ? "Mute microphone" : "Turn on microphone"}
                  title={audioEnabled ? "Mute microphone" : "Turn on microphone"}
                >
                  <CompactMicrophoneIcon muted={!audioEnabled} />
                </button>
                {call.call_type === "video" ? (
                  <button
                    type="button"
                    className={`ms-active-call-bar__control ${videoEnabled ? "" : "is-off"}`}
                    onClick={() => void toggleVideo()}
                    disabled={callActionBusy}
                    aria-label={videoEnabled ? "Turn off camera" : "Turn on camera"}
                    title={videoEnabled ? "Turn off camera" : "Turn on camera"}
                  >
                    <CompactVideoIcon disabled={!videoEnabled} />
                  </button>
                ) : null}
              </>
            )}
            <button
              type="button"
              className="ms-active-call-bar__control"
              onClick={() => navigate(`/calls/${callId}`)}
              aria-label="Return to full call"
              title="Return to full call"
            >
              <CompactExpandIcon />
            </button>
            <button
              type="button"
              className="ms-active-call-bar__control is-end"
              onClick={() => canAcceptCall ? declineMutation.mutate() : endMutation.mutate()}
              disabled={callActionBusy}
              aria-label={canAcceptCall ? "Decline call" : "End call"}
              title={canAcceptCall ? "Decline call" : "End call"}
            >
              <CompactHangupIcon />
            </button>
          </div>
        </aside>
      ) : null}
    </section>
  );
}
