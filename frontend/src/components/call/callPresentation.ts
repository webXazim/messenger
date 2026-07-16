import type { Call, CallParticipant } from "../../types/chat";
import { personPresenceText } from "../../lib/personPresentation";

export type CallViewState = {
  label: string;
  detail: string;
  tone: "warn" | "ok" | "danger" | "stable";
};

export function qualityTone(alert?: string) {
  if (!alert) return "stable";
  if (["critical", "poor"].includes(alert.toLowerCase())) return "danger";
  return "warning";
}

export function formatElapsed(seconds: number) {
  const safeSeconds = Math.max(0, Math.floor(seconds));
  const mins = Math.floor(safeSeconds / 60);
  const secs = safeSeconds % 60;
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

export function getCallViewState(call: Call, options: {
  isInitiator: boolean;
  remoteParticipants: CallParticipant[];
  peerState: string;
  ringingSeconds: number;
}): CallViewState {
  const state = String(call.call_state || call.status || "").toLowerCase();
  const remoteCount = options.remoteParticipants.length;
  const offlineCount = options.remoteParticipants.filter((participant) => !participant.user.is_online).length;

  if (state === "incoming") return { label: "Incoming call", detail: "Answer when you are ready.", tone: "warn" };
  if (state === "calling_offline") return { label: "Waiting for connection…", detail: "The other person is currently offline.", tone: "warn" };
  if (state === "ringing") {
    return options.isInitiator
      ? { label: "Ringing…", detail: remoteCount > 1 ? "Waiting for people to answer." : "Waiting for an answer.", tone: "warn" }
      : { label: "Incoming call", detail: "Answer or decline the call.", tone: "warn" };
  }
  if (state === "calling" || state === "initiated") return { label: "Calling…", detail: "Sending the call.", tone: "warn" };
  if (state === "connecting") return { label: "Connecting…", detail: "Setting up the secure media connection.", tone: "warn" };
  if (state === "ongoing") {
    return {
      label: "Connected",
      detail: offlineCount > 0 && remoteCount > 1
        ? `${offlineCount} participant${offlineCount === 1 ? " is" : "s are"} offline.`
        : "Call is live.",
      tone: "ok",
    };
  }
  if (state === "missed") return { label: "Missed call", detail: "The call was not answered.", tone: "danger" };
  if (state === "declined") return { label: "Call declined", detail: "The call was declined.", tone: "danger" };
  if (state === "cancelled" || state === "canceled") return { label: "Call cancelled", detail: "The call ended before it was answered.", tone: "danger" };
  if (state === "ended" || state === "completed") return { label: "Call ended", detail: "The call has finished.", tone: "stable" };
  if (state === "failed") return { label: "Call failed", detail: "The connection could not be completed.", tone: "danger" };
  return { label: "Connecting…", detail: "Preparing the call.", tone: "stable" };
}

export function participantName(participant?: CallParticipant | null) {
  const user = participant?.user;
  return user?.display_name?.trim() || user?.username?.trim() || user?.email?.trim() || "Participant";
}

export function participantInitials(participant?: CallParticipant | null) {
  const name = participantName(participant).trim();
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "SN";
}

export function participantMediaLine(participant: CallParticipant, peerState: string) {
  const linkState = participant.connection_state || (participant.state === "joined" ? peerState : "waiting");
  return `${participant.state} · audio ${participant.audio_enabled ? "on" : "off"} · video ${participant.video_enabled ? "on" : "off"} · ${linkState}`;
}

export function participantPresenceLine(participant: CallParticipant) {
  if (participant.user.is_online) return personPresenceText(participant.user);
  return participant.user.presence_label === "unknown" ? "Presence unknown" : "Offline";
}
