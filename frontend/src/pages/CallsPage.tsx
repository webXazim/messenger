import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { chatApi } from "../api/chat";
import { MessengerPageHeader, MessengerSectionHeader } from "../components/pages/MessengerPageHeader";
import { UserAvatar } from "../components/UserAvatar";
import { personPresenceText } from "../lib/personPresentation";
import { conversationDisplayName } from "../components/conversations/conversationPresentation";
import { useAuth } from "../contexts/AuthContext";
import { useActiveCall } from "../contexts/ActiveCallContext";
import {
  callDestination,
  callDirection,
  callPeerLabel,
  callPeerUsers,
  callStatusPresentation,
  findActiveCallForConversation,
  findActiveCallForUser,
  isActiveCallForUser,
  isMissedCallForUser,
} from "../lib/callLifecycle";
import { getCallMediaErrorMessage, preflightCallMedia } from "../lib/mediaPermissions";
import { patchCallCaches } from "../lib/realtimeCache";
import type { Call, Conversation } from "../types/chat";
import { useQueryClient } from "@tanstack/react-query";

function getErrorMessage(error: unknown) {
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
  return error instanceof Error ? error.message : "Could not start the call.";
}

function getExistingCallId(error: unknown) {
  if (!error || typeof error !== "object" || !("response" in error)) return "";
  const data = (error as { response?: { data?: unknown } }).response?.data;
  if (!data || typeof data !== "object") return "";
  return String((data as Record<string, unknown>).active_call_id || "");
}

function historyFilterMatches(
  call: Call,
  filter: "all" | "missed" | "incoming" | "outgoing",
  currentUser: Parameters<typeof callDirection>[1],
) {
  if (filter === "all") return true;
  if (filter === "missed") return isMissedCallForUser(call, currentUser);
  return callDirection(call, currentUser) === filter;
}

export function CallsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const { activateCall, expectOutgoingCall, clearOutgoingCallExpectation } = useActiveCall();
  const [startingCallKey, setStartingCallKey] = useState<string | null>(null);
  const [startingPhase, setStartingPhase] = useState<"permission" | "starting" | null>(null);
  const [callError, setCallError] = useState<{ message: string; activeCallId?: string } | null>(null);
  const [statusFilter, setStatusFilter] = useState<"all" | "missed" | "incoming" | "outgoing">("all");
  const callsQuery = useQuery({
    queryKey: ["recent-calls"],
    queryFn: ({ signal }) => chatApi.listCalls(undefined, signal),
    staleTime: 15_000,
    retry: 1,
    refetchOnWindowFocus: false,
  });
  const conversationsQuery = useQuery({
    queryKey: ["conversations"],
    queryFn: ({ signal }) => chatApi.listConversations(signal),
    staleTime: 15_000,
    retry: 1,
    refetchOnWindowFocus: false,
  });

  const currentIdentity = useMemo(() => ({
    id: user?.id,
    username: user?.username,
    email: user?.email,
    display_name: user?.profile?.display_name || user?.display_name,
  }), [user?.display_name, user?.email, user?.id, user?.profile?.display_name, user?.username]);

  const conversations = conversationsQuery.data ?? [];
  const conversationById = useMemo(
    () => new Map(conversations.map((conversation) => [String(conversation.id), conversation])),
    [conversations],
  );
  const allCalls = callsQuery.data ?? [];
  const activeCall = findActiveCallForUser(allCalls, currentIdentity);

  const startCall = async (conversationId: string, callType: "voice" | "video") => {
    const key = `${conversationId}:${callType}`;
    if (startingCallKey) return;

    const conversationCall = findActiveCallForConversation(allCalls, conversationId, currentIdentity);
    if (conversationCall) {
      activateCall(conversationCall.id);
      navigate(`/calls/${conversationCall.id}`);
      return;
    }
    if (activeCall) {
      setCallError({ message: "You already have a call in progress. Return to that call before starting another one.", activeCallId: activeCall.id });
      return;
    }

    try {
      setCallError(null);
      setStartingCallKey(key);
      setStartingPhase("permission");
      await preflightCallMedia(callType);
      setStartingPhase("starting");
      expectOutgoingCall(conversationId);
      const call = await chatApi.startCall(conversationId, { call_type: callType, metadata: { source: "web" } });
      if (!call.id) throw new Error("The call server did not return a call ID.");
      activateCall(call.id);
      patchCallCaches(queryClient, call);
      navigate(`/calls/${call.id}`);
    } catch (error) {
      const activeCallId = getExistingCallId(error);
      if (activeCallId) {
        setCallError({ message: "You already have a call in progress.", activeCallId });
      } else if (error instanceof DOMException || (error instanceof Error && /camera|microphone|media|https|permission/i.test(error.message))) {
        setCallError({ message: await getCallMediaErrorMessage(error, callType) });
      } else {
        setCallError({ message: getErrorMessage(error) });
      }
    } finally {
      clearOutgoingCallExpectation(conversationId);
      setStartingCallKey(null);
      setStartingPhase(null);
    }
  };

  const calls = useMemo(
    () => allCalls.filter((call) => historyFilterMatches(call, statusFilter, currentIdentity)),
    [allCalls, currentIdentity, statusFilter],
  );

  return (
    <div className="ms-workspace-page ms-calls-page">
      <MessengerPageHeader
        eyebrow="Calls"
        title="Calls"
        description="Start a voice or video call and return to recent conversations after a call ends."
      />

      {callError ? (
        <div className="ms-page-error ms-calls-page__error" role="alert">
          <span><strong>Call unavailable.</strong> {callError.message}</span>
          {callError.activeCallId ? (
            <button type="button" className="ms-button ms-button--compact" onClick={() => navigate(`/calls/${callError.activeCallId}`)}>
              Return to call
            </button>
          ) : null}
        </div>
      ) : null}

      <div className="ms-calls-page__layout">
        <section className="ms-page-surface ms-page-surface--padded ms-calls-page__directory">
          <MessengerSectionHeader
            eyebrow="Start a call"
            title="Conversations"
            description={activeCall ? "A call is already active. Return to it before starting another." : "Choose a conversation and call when you are ready."}
          />

          {conversationsQuery.isLoading ? <div className="ms-page-empty">Loading conversations…</div> : null}
          {conversationsQuery.isError ? (
            <div className="ms-page-error">
              <span>Unable to load conversations.</span>
              <button type="button" className="ms-button ms-button--compact" onClick={() => void conversationsQuery.refetch()}>Retry</button>
            </div>
          ) : null}

          <div className="ms-calls-page__conversation-list">
            {conversations.map((conversation: Conversation) => {
              const label = conversationDisplayName(conversation, String(user?.id || ""), currentIdentity);
              const peer = conversation.type === "direct"
                ? conversation.participants.find((participant) => String(participant.user.id) !== String(user?.id || ""))?.user ?? null
                : null;
              const conversationCall = findActiveCallForConversation(allCalls, conversation.id, currentIdentity);
              const blockedByOtherCall = Boolean(activeCall && !conversationCall);
              return (
                <article key={conversation.id} className="ms-calls-page__conversation">
                  <UserAvatar
                    person={peer ?? { display_name: label }}
                    size="lg"
                    shape={conversation.type === "group" ? "rounded" : "circle"}
                    showPresence={conversation.type === "direct"}
                    className="ms-calls-page__avatar"
                    decorative
                  />
                  <div className="ms-page-row__copy">
                    <strong>{label}</strong>
                    <span>{conversationCall ? "Call in progress" : conversation.type === "group" ? `${conversation.participants.length} participants` : personPresenceText(peer)}</span>
                  </div>
                  <div className="ms-page-actions">
                    {conversationCall ? (
                      <button type="button" className="ms-button ms-button--primary ms-button--compact" onClick={() => navigate(`/calls/${conversationCall.id}`)}>
                        Return to call
                      </button>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="ms-button ms-button--compact"
                          disabled={Boolean(startingCallKey) || blockedByOtherCall}
                          title={blockedByOtherCall ? "Finish your active call first" : "Start voice call"}
                          onClick={() => void startCall(conversation.id, "voice")}
                        >
                          {startingCallKey === `${conversation.id}:voice` ? (startingPhase === "permission" ? "Checking…" : "Starting…") : "Voice"}
                        </button>
                        <button
                          type="button"
                          className="ms-button ms-button--primary ms-button--compact"
                          disabled={Boolean(startingCallKey) || blockedByOtherCall}
                          title={blockedByOtherCall ? "Finish your active call first" : "Start video call"}
                          onClick={() => void startCall(conversation.id, "video")}
                        >
                          {startingCallKey === `${conversation.id}:video` ? (startingPhase === "permission" ? "Checking…" : "Starting…") : "Video"}
                        </button>
                      </>
                    )}
                  </div>
                </article>
              );
            })}
            {!conversationsQuery.isLoading && !conversations.length ? <div className="ms-page-empty">No conversations are available yet.</div> : null}
          </div>
        </section>

        <aside className="ms-page-surface ms-page-surface--padded ms-calls-page__history">
          <MessengerSectionHeader eyebrow="History" title="Recent calls" />
          <div className="ms-calls-page__filters" aria-label="Filter recent calls">
            {(["all", "missed", "incoming", "outgoing"] as const).map((status) => (
              <button
                key={status}
                type="button"
                className={`ms-calls-page__filter ${statusFilter === status ? "is-active" : ""}`}
                onClick={() => setStatusFilter(status)}
              >
                {status}
              </button>
            ))}
          </div>

          {callsQuery.isLoading ? <div className="ms-page-empty">Loading calls…</div> : null}
          {callsQuery.isError ? (
            <div className="ms-page-error">
              <span>Unable to load recent calls.</span>
              <button type="button" className="ms-button ms-button--compact" onClick={() => void callsQuery.refetch()}>Retry</button>
            </div>
          ) : null}

          <div className="ms-page-list ms-calls-page__history-list">
            {calls.map((call) => {
              const conversation = call.conversation ? conversationById.get(String(call.conversation)) : undefined;
              const label = callPeerLabel(call, currentIdentity, conversation);
              const peers = callPeerUsers(call, currentIdentity);
              const peer = peers.length === 1 ? peers[0] : null;
              const direction = callDirection(call, currentIdentity);
              const status = callStatusPresentation(call, currentIdentity);
              const active = isActiveCallForUser(call, currentIdentity);
              return (
                <Link
                  key={call.id}
                  to={callDestination(call, currentIdentity)}
                  className={`ms-calls-page__history-row is-${status.tone}`}
                  aria-label={`${label}, ${direction} ${call.call_type} call, ${status.label}`}
                >
                  <UserAvatar
                    person={peer ?? { display_name: label }}
                    size="lg"
                    shape={conversation?.type === "group" || peers.length > 1 ? "rounded" : "circle"}
                    showPresence={Boolean(peer)}
                    className="ms-calls-page__history-avatar"
                    decorative
                  />
                  <div className="ms-page-row__copy">
                    <strong>{label}</strong>
                    <span className="ms-calls-page__history-detail">
                      <b aria-hidden="true">{direction === "incoming" ? "↙" : "↗"}</b>
                      {call.call_type === "video" ? "Video" : "Voice"} · {status.label}
                    </span>
                    <time dateTime={call.started_at}>{new Date(call.started_at).toLocaleString()}</time>
                  </div>
                  <span className={`ms-page-badge ${active ? "ms-page-badge--strong" : status.tone === "danger" ? "ms-page-badge--danger" : ""}`}>
                    {active ? "Open" : "Chat"}
                  </span>
                </Link>
              );
            })}
            {!callsQuery.isLoading && !calls.length ? <div className="ms-page-empty">No calls match this filter.</div> : null}
          </div>
        </aside>
      </div>
    </div>
  );
}
