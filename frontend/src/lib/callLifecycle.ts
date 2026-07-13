import type { Call, Conversation, UserLite } from "../types/chat";
import { isSameUserIdentity } from "./userIdentity";

export type CallDirection = "incoming" | "outgoing";
export type CallHistoryTone = "default" | "active" | "danger" | "muted";

const ACTIVE_CALL_STATUSES = new Set(["initiated", "ringing", "ongoing"]);
const TERMINAL_CALL_STATUSES = new Set(["declined", "missed", "ended", "failed"]);

type IdentityLike = {
  id?: string | number | null;
  username?: string | null;
  email?: string | null;
  display_name?: string | null;
};

export function isActiveCall(call?: Pick<Call, "status"> | null) {
  return Boolean(call && ACTIVE_CALL_STATUSES.has(String(call.status || "").toLowerCase()));
}

export function isTerminalCall(call?: Pick<Call, "status"> | null) {
  return Boolean(call && TERMINAL_CALL_STATUSES.has(String(call.status || "").toLowerCase()));
}

export function isActiveCallForUser(call: Call | null | undefined, currentUser?: IdentityLike | null) {
  if (!call || !isActiveCall(call)) return false;
  if (!currentUser || !call.participants?.length) return true;
  const participant = call.participants.find((item) => isSameUserIdentity(item.user, currentUser));
  return !participant || ["invited", "ringing", "joined"].includes(participant.state);
}

export function callDirection(call: Call, currentUser?: IdentityLike | null): CallDirection {
  return isSameUserIdentity(call.initiated_by, currentUser) ? "outgoing" : "incoming";
}

export function callPeerUsers(call: Call, currentUser?: IdentityLike | null) {
  const peers = (call.participants ?? [])
    .map((participant) => participant.user)
    .filter((participant): participant is UserLite => Boolean(participant?.id) && !isSameUserIdentity(participant, currentUser));
  if (peers.length) return peers;
  if (call.initiated_by && !isSameUserIdentity(call.initiated_by, currentUser)) return [call.initiated_by];
  if (call.answered_by && !isSameUserIdentity(call.answered_by, currentUser)) return [call.answered_by];
  return [];
}

export function callPeerLabel(call: Call, currentUser?: IdentityLike | null, conversation?: Conversation | null) {
  if (conversation?.type === "group") return conversation.title?.trim() || "Group call";
  const peers = callPeerUsers(call, currentUser);
  const names = peers
    .map((participant) => participant.display_name?.trim() || participant.username?.trim())
    .filter(Boolean) as string[];
  if (!names.length) return conversation?.title?.trim() || "Conversation";
  if (names.length === 1) return names[0];
  if (names.length === 2) return names.join(" and ");
  return `${names[0]} and ${names.length - 1} others`;
}

export function callPeerInitials(call: Call, currentUser?: IdentityLike | null, conversation?: Conversation | null) {
  const label = callPeerLabel(call, currentUser, conversation);
  const parts = label.split(/\s+/).filter(Boolean).slice(0, 2);
  return (parts.map((part) => part[0]).join("") || "C").toUpperCase();
}

export function formatCallDuration(totalSeconds?: number | null) {
  const safeSeconds = Math.max(0, Math.floor(Number(totalSeconds || 0)));
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const seconds = safeSeconds % 60;
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function callStatusPresentation(call: Call, currentUser?: IdentityLike | null) {
  const direction = callDirection(call, currentUser);
  const status = String(call.status || "").toLowerCase();
  const reason = String(call.ended_reason || "").toLowerCase();

  if (status === "ongoing") return { label: "In progress", tone: "active" as CallHistoryTone };
  if (status === "ringing" || status === "initiated") {
    return {
      label: direction === "incoming" ? "Incoming call" : status === "ringing" ? "Ringing" : "Calling",
      tone: "active" as CallHistoryTone,
    };
  }
  if (status === "missed") {
    return {
      label: direction === "incoming" ? "Missed call" : "No answer",
      tone: "danger" as CallHistoryTone,
    };
  }
  if (status === "declined") {
    return {
      label: direction === "incoming" ? "Declined call" : "Call declined",
      tone: "danger" as CallHistoryTone,
    };
  }
  if (status === "failed") return { label: "Call failed", tone: "danger" as CallHistoryTone };
  if (status === "ended") {
    if (["cancelled", "caller_cancelled", "user_cancelled"].includes(reason)) {
      return { label: direction === "outgoing" ? "Cancelled call" : "Call cancelled", tone: "muted" as CallHistoryTone };
    }
    if (Number(call.duration_seconds || 0) > 0) {
      return { label: `Completed · ${formatCallDuration(call.duration_seconds)}`, tone: "default" as CallHistoryTone };
    }
    return { label: "Call ended", tone: "muted" as CallHistoryTone };
  }
  return { label: "Call update", tone: "muted" as CallHistoryTone };
}

export function callDestination(call: Call, currentUser?: IdentityLike | null) {
  if (isActiveCallForUser(call, currentUser)) return `/calls/${call.id}`;
  if (call.conversation) return `/chat/${call.conversation}`;
  return "/calls";
}

export function findActiveCallForConversation(calls: Call[] | undefined, conversationId: string, currentUser?: IdentityLike | null) {
  return (calls ?? []).find((call) => isActiveCallForUser(call, currentUser) && String(call.conversation || "") === String(conversationId));
}

export function findActiveCallForUser(calls: Call[] | undefined, currentUser?: IdentityLike | null) {
  return (calls ?? []).find((call) => isActiveCallForUser(call, currentUser));
}

export function isMissedCallForUser(call: Call, currentUser?: IdentityLike | null) {
  return call.status === "missed" && callDirection(call, currentUser) === "incoming";
}
