import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../contexts/AuthContext";
import { useChatSocket } from "../hooks/useChatSocket";
import { useSupportRealtime } from "../hooks/useSupportRealtime";
import { chatApi, normalizeCall, normalizeConversation, normalizeMessage } from "../api/chat";
import type { Call, Conversation } from "../types/chat";
import { IncomingCallBanner } from "./IncomingCallBanner";
import { IncomingCallOverlay } from "./IncomingCallOverlay";
import { UserAvatar } from "./UserAvatar";
import { ensureBrowserWebPushRegistration, getLastWebPushPromptAt, getStoredWebPushToken, getWebPushPermissionMessage, getWebPushStatus, rememberWebPushPrompt, showChatActivityNotification } from "../lib/pushNotifications";
import { isSameUserIdentity } from "../lib/userIdentity";
import { getCallMediaErrorMessage, preflightCallMedia } from "../lib/mediaPermissions";
import { decryptMessageTextResult } from "../lib/e2ee";
import { isConversationActivelyViewedAtLatest } from "../lib/activeConversationView";
import { claimCallAction, createCallActionChannel, createCallActionOwnerId, releaseCallAction, type CallCoordinationEvent } from "../lib/callCoordination";
import { DesktopNavigationRail, MobileBottomNavigation } from "./navigation/MessengerNavigation";
import { getRealtimeSyncMarker, markConversationReadInCaches, mergeChatSync, patchCallCaches, patchConversationCaches, patchConversationReceiptCaches, patchMessageCache, patchUserPresenceAcrossCaches, setRealtimeSyncMarker } from "../lib/realtimeCache";
import { CallRoomPage } from "../pages/CallRoomPage";
import { ActiveCallProvider } from "../contexts/ActiveCallContext";


function getCallActionError(error: unknown, fallback: string) {
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

type MessageToast = {
  id: string;
  conversationId: string;
  messageId: string;
  title: string;
  body: string;
  avatar?: string | null;
};

const ACTIVE_CALL_SESSION_KEY = "messenger.active-call-id";

function callIdFromPath(pathname: string) {
  const match = pathname.match(/^\/calls\/([^/]+)\/?$/);
  if (!match?.[1]) return "";
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return match[1];
  }
}

function isForegroundBrowserTab() {
  if (typeof document === "undefined") return false;
  return document.visibilityState === "visible";
}

function activeCallSessionKey(userId?: string | number) {
  return `${ACTIVE_CALL_SESSION_KEY}:${String(userId || "anonymous")}`;
}

function storedActiveCallId(userId?: string | number) {
  if (typeof window === "undefined") return "";
  try {
    return window.sessionStorage.getItem(activeCallSessionKey(userId)) || "";
  } catch {
    return "";
  }
}

function rememberActiveCallId(userId: string | number | undefined, callId: string) {
  try {
    window.sessionStorage.setItem(activeCallSessionKey(userId), callId);
  } catch {
    // The live in-memory call still persists when session storage is unavailable.
  }
}

function forgetActiveCallId(userId: string | number | undefined, callId: string) {
  try {
    const storageKey = activeCallSessionKey(userId);
    if (window.sessionStorage.getItem(storageKey) === callId) window.sessionStorage.removeItem(storageKey);
  } catch {
    // Nothing else is required when session storage is unavailable.
  }
}

function attachmentNotificationLabel(message: ReturnType<typeof normalizeMessage>) {
  const attachments = message.attachments ?? [];
  if (!attachments.length) return "New message";
  if (attachments.length > 1) return `Sent ${attachments.length} attachments`;
  const kind = String(attachments[0]?.media_kind || attachments[0]?.mime_type || "").toLowerCase();
  if (kind.includes("image")) return "Sent a photo";
  if (kind.includes("video")) return "Sent a video";
  if (kind.includes("audio")) return "Sent an audio message";
  return `Sent ${attachments[0]?.original_name || "a file"}`;
}

async function resolveMessageNotificationBody(userId: string, message: ReturnType<typeof normalizeMessage>) {
  if (message.is_encrypted && message.encryption) {
    const decrypted = await decryptMessageTextResult(userId, message);
    if (decrypted.status === "ready" && decrypted.text.trim()) return decrypted.text.trim().slice(0, 180);
  } else if (message.text.trim()) {
    return message.text.trim().slice(0, 180);
  }
  return attachmentNotificationLabel(message);
}

export function AppShell() {
  const { user, logout } = useAuth();
  const { socket, socketStatus } = useChatSocket();
  const supportRealtime = useSupportRealtime();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const routeCallId = useMemo(() => callIdFromPath(location.pathname), [location.pathname]);
  const [activeCallId, setActiveCallId] = useState(() => routeCallId || storedActiveCallId(user?.id));
  const [expandedCallId, setExpandedCallId] = useState(() => routeCallId);
  const [incomingCall, setIncomingCall] = useState<Call | null>(null);
  const [messageToasts, setMessageToasts] = useState<MessageToast[]>([]);
  const [showCallOverlay, setShowCallOverlay] = useState(false);
  const [incomingCallAction, setIncomingCallAction] = useState<"accepting" | "declining" | null>(null);
  const [incomingCallError, setIncomingCallError] = useState<string | null>(null);
  const [webPushBanner, setWebPushBanner] = useState<{ tone: "warning" | "danger"; message: string; action?: "enable" | "settings" } | null>(null);
  const previousSocketStatusRef = useRef(socketStatus);
  const previousRouteCallIdRef = useRef(routeCallId);
  const realtimeSyncInFlightRef = useRef(false);
  const incomingActionInFlightRef = useRef(false);
  const outgoingCallConversationRef = useRef("");
  const callActionOwnerRef = useRef("");
  const callActionChannelRef = useRef<ReturnType<typeof createCallActionChannel> | null>(null);
  if (!callActionOwnerRef.current) callActionOwnerRef.current = createCallActionOwnerId();

  const activateCall = useCallback((callId: string) => {
    const normalizedCallId = String(callId || "");
    if (!normalizedCallId) return;
    setActiveCallId(normalizedCallId);
    setExpandedCallId(normalizedCallId);
    rememberActiveCallId(user?.id, normalizedCallId);
  }, [user?.id]);

  const expectOutgoingCall = useCallback((conversationId: string) => {
    outgoingCallConversationRef.current = String(conversationId || "");
  }, []);

  const clearOutgoingCallExpectation = useCallback((conversationId: string) => {
    if (outgoingCallConversationRef.current === String(conversationId || "")) {
      outgoingCallConversationRef.current = "";
    }
  }, []);

  useEffect(() => {
    if (!routeCallId) return;
    activateCall(routeCallId);
  }, [activateCall, routeCallId]);

  useEffect(() => {
    const previousRouteCallId = previousRouteCallIdRef.current;
    previousRouteCallIdRef.current = routeCallId;
    if (previousRouteCallId && !routeCallId) {
      setExpandedCallId((current) => current === previousRouteCallId ? "" : current);
    }
  }, [routeCallId]);

  useEffect(() => {
    if (routeCallId || activeCallId || !user?.id) return;
    const restoredCallId = storedActiveCallId(user.id);
    if (restoredCallId) setActiveCallId(restoredCallId);
  }, [activeCallId, routeCallId, user?.id]);

  const clearActiveCall = useCallback((finishedCallId: string) => {
    setActiveCallId((current) => current === finishedCallId ? "" : current);
    setExpandedCallId((current) => current === finishedCallId ? "" : current);
    forgetActiveCallId(user?.id, finishedCallId);
  }, [user?.id]);

  const minimizeActiveCall = useCallback((callId: string) => {
    setExpandedCallId((current) => current === callId ? "" : current);
  }, []);

  const presentIncomingCall = useCallback((call: Call) => {
    const callPath = `/calls/${call.id}`;
    setIncomingCall(call);
    setIncomingCallError(null);
    setIncomingCallAction(null);
    if (isForegroundBrowserTab()) {
      activateCall(call.id);
      setShowCallOverlay(false);
      if (location.pathname !== callPath) navigate(callPath);
      return;
    }
    setShowCallOverlay(!/^\/calls\/[^/]+\/?$/.test(location.pathname));
  }, [activateCall, location.pathname, navigate]);
  const conversationsQuery = useQuery({
    queryKey: ["conversations"],
    queryFn: ({ signal }) => chatApi.listConversations(signal),
    enabled: Boolean(user?.id),
  });
  const subscribedConversationIds = useMemo(
    () => (conversationsQuery.data ?? []).map((conversation) => conversation.id).sort(),
    [conversationsQuery.data],
  );
  const subscribedConversationKey = subscribedConversationIds.join("|");

  useEffect(() => {
    const channel = createCallActionChannel((event: CallCoordinationEvent) => {
      if (event.ownerId === callActionOwnerRef.current) return;
      setIncomingCall((current) => {
        if (!current || current.id !== event.callId) return current;
        if (["accepted", "declined", "cleared"].includes(event.action)) {
          setShowCallOverlay(false);
          setIncomingCallAction(null);
          setIncomingCallError(null);
          return null;
        }
        if (event.action === "released") {
          setIncomingCallAction(null);
          setIncomingCallError(null);
          return current;
        }
        setIncomingCallAction(event.action === "accepting" ? "accepting" : "declining");
        setIncomingCallError("This call is being handled in another browser tab.");
        return current;
      });
    });
    callActionChannelRef.current = channel;
    return () => {
      channel.close();
      callActionChannelRef.current = null;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function syncBrowserPush(interactive = false) {
      const status = await getWebPushStatus().catch(() => null);
      if (!status || cancelled || !status.supported || !status.configured) return;

      if (status.permission === "denied") {
        if (!cancelled) {
          setWebPushBanner({
            tone: "danger",
            message: getWebPushPermissionMessage("denied"),
            action: "settings",
          });
        }
        return;
      }

      const token = await ensureBrowserWebPushRegistration({ interactive }).catch((error) => {
        if (!cancelled && interactive) {
          const message = error instanceof Error ? error.message : "Unable to enable notifications in this browser.";
          setWebPushBanner({
            tone: message.toLowerCase().includes("blocked") || message.toLowerCase().includes("denied") ? "danger" : "warning",
            message,
            action: message.toLowerCase().includes("blocked") || message.toLowerCase().includes("denied") ? "settings" : "enable",
          });
        }
        return "";
      });

      const resolvedToken = token || getStoredWebPushToken();
      if (!resolvedToken) {
        if (!cancelled && status.permission === "default") {
          const lastPromptAt = getLastWebPushPromptAt();
          const recentlyPrompted = lastPromptAt && Date.now() - lastPromptAt < 12 * 60 * 60 * 1000;
          if (!recentlyPrompted) {
            setWebPushBanner({
              tone: "warning",
              message: "Turn on notifications to receive every new message and incoming call alert on the web.",
              action: "enable",
            });
          }
        }
        return;
      }

      await chatApi.registerDevice({ platform: "web", push_token: resolvedToken }).catch(() => undefined);
      if (!cancelled) {
        setWebPushBanner(null);
      }
    }

    if (!user?.id) return;
    void syncBrowserPush(false);

    const refreshOnFocus = () => {
      void syncBrowserPush(false);
    };

    window.addEventListener("focus", refreshOnFocus);
    document.addEventListener("visibilitychange", refreshOnFocus);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", refreshOnFocus);
      document.removeEventListener("visibilitychange", refreshOnFocus);
    };
  }, [user?.id]);

  useEffect(() => {
    const conversationIds = subscribedConversationKey ? subscribedConversationKey.split("|") : [];
    conversationIds.forEach((conversationId) => socket.subscribeToConversation(conversationId));
    return () => { conversationIds.forEach((conversationId) => socket.unsubscribeFromConversation(conversationId)); };
  }, [socket, subscribedConversationKey]);

  useEffect(() => {
    const userId = String(user?.id || "");
    if (!userId) return;
    let cancelled = false;

    const reconcileMissedRealtimeChanges = async () => {
      if (realtimeSyncInFlightRef.current) return;
      realtimeSyncInFlightRef.current = true;
      try {
        const payload = await chatApi.sync({ since: getRealtimeSyncMarker(userId), limit: 200 });
        if (cancelled) return;
        mergeChatSync(queryClient, payload);
        const pendingIncoming = payload.active_calls.find((call) => {
          if (!call || isSameUserIdentity(call.initiated_by, user)) return false;
          const participant = call.participants?.find((item) => isSameUserIdentity(item.user, user));
          return ["initiated", "ringing", "ongoing"].includes(call.status) && participant?.state === "ringing";
        });
        if (pendingIncoming && !location.pathname.startsWith(`/calls/${pendingIncoming.id}`)) {
          presentIncomingCall(pendingIncoming);
        }
        setRealtimeSyncMarker(userId, payload.next_since || payload.server_time);
        if (payload.has_more_conversations) {
          await queryClient.invalidateQueries({ queryKey: ["conversations"] });
        }
        if (payload.has_more_messages) {
          await queryClient.invalidateQueries({ queryKey: ["messages"] });
        }
        await queryClient.invalidateQueries({ queryKey: ["recent-calls"] });
      } catch {
        if (!cancelled) {
          await queryClient.invalidateQueries({ queryKey: ["conversations"] });
          await queryClient.invalidateQueries({ queryKey: ["recent-calls"] });
        }
      } finally {
        realtimeSyncInFlightRef.current = false;
      }
    };

    if (socketStatus === "open" && previousSocketStatusRef.current !== "open") {
      void reconcileMissedRealtimeChanges();
    }
    previousSocketStatusRef.current = socketStatus;
    return () => { cancelled = true; };
  }, [location.pathname, presentIncomingCall, queryClient, socketStatus, user]);

  const dismissMessageToast = (toastId: string) => {
    setMessageToasts((current) => current.filter((toast) => toast.id !== toastId));
  };

  const pushMessageToast = (toast: MessageToast) => {
    setMessageToasts((current) => [toast, ...current.filter((item) => item.id !== toast.id)].slice(0, 4));
    window.setTimeout(() => dismissMessageToast(toast.id), 6000);
  };

  useEffect(() => {
    const seenNotificationKeys = new Set<string>();
    const unsubscribe = socket.subscribe((payload) => {
      if (payload.event === "presence.updated") {
        const userId = String(payload.data?.user_id || "");
        if (!userId) return;
        const data = (payload.data || {}) as Record<string, unknown>;
        patchUserPresenceAcrossCaches(queryClient, userId, data);
        return;
      }
      if (["call.heartbeat", "call.media_state", "call.quality_report"].includes(payload.event)) {
        const activeUserId = String(payload.data?.user_id || "");
        if (activeUserId && activeUserId !== String(user?.id || "")) {
          patchUserPresenceAcrossCaches(queryClient, activeUserId, {
            is_online: true,
            active_devices: 1,
            presence_label: "online",
            presence_status: "active",
          });
        }
      }
      if (payload.event === "e2ee.keys.updated") {
        const conversationId = String(payload.data?.conversation_id || "");
        if (conversationId) {
          void queryClient.invalidateQueries({ queryKey: ["conversation-e2ee", conversationId] });
          queryClient.setQueryData<Conversation>(["conversation", conversationId], (current) => current ? {
            ...current,
            e2ee_key_version: Number(payload.data?.key_version || current.e2ee_key_version || 1),
            e2ee_rekey_required: Boolean(payload.data?.rekey_required),
          } : current);
        }
        void queryClient.invalidateQueries({ queryKey: ["e2ee-devices"] });
        if (String(payload.data?.user_id || "") === String(user?.id || "")) {
          void queryClient.invalidateQueries({ queryKey: ["e2ee-identity", String(user?.id || "")] });
        }
        return;
      }
      if (payload.event === "status.changed" || payload.event === "status.viewed") {
        void queryClient.invalidateQueries({ queryKey: ["user-statuses"] });
        return;
      }
      const eventKey = payload.event_id || `${payload.event}:${String(payload.data?.conversation_id || "")}:${String(payload.data?.message_id || payload.data?.id || payload.data?.call_id || "")}:${String(payload.occurred_at || "")}`;
      if (seenNotificationKeys.has(eventKey)) return;
      seenNotificationKeys.add(eventKey);
      if (seenNotificationKeys.size > 120) {
        const [first] = seenNotificationKeys;
        if (first) seenNotificationKeys.delete(first);
      }

      let realtimeCallPayload: Call | null = null;
      if (["call.started", "call.created", "call.accepted", "call.ended", "call.declined", "call.missed", "call.failed"].includes(payload.event)) {
        const callPayload = normalizeCall(payload.data || {});
        realtimeCallPayload = callPayload;
        if (callPayload.id) {
          patchCallCaches(queryClient, callPayload);
          if (payload.event === "call.accepted") {
            callPayload.participants
              ?.filter((participant) => participant.state === "joined" && !isSameUserIdentity(participant.user, user))
              .forEach((participant) => patchUserPresenceAcrossCaches(queryClient, String(participant.user.id), {
                is_online: true,
                active_devices: Math.max(1, Number(participant.user.active_devices || 0)),
                presence_label: "online",
                presence_status: "active",
              }));
            if (
              activeCallId === callPayload.id
              && isForegroundBrowserTab()
              && location.pathname !== `/calls/${callPayload.id}`
            ) {
              activateCall(callPayload.id);
              navigate(`/calls/${callPayload.id}`);
            }
          }
        }
      }
      if (["call.ended", "call.declined", "call.missed", "call.failed"].includes(payload.event)) {
        if (String(payload.data?.id || payload.data?.call_id || "") === incomingCall?.id) {
          clearIncoming();
        }
        return;
      }
      if (payload.event === "call.accepted" && String(payload.data?.id || payload.data?.call_id || "") === incomingCall?.id) {
        const answeredBy = payload.data?.answered_by && typeof payload.data.answered_by === "object"
          ? payload.data.answered_by as Record<string, unknown>
          : null;
        if (answeredBy && isSameUserIdentity(answeredBy, user)) clearIncoming();
        return;
      }
      if (payload.event === "conversation.updated") {
        const conversation = normalizeConversation(payload.data || {});
        if (conversation.id) patchConversationCaches(queryClient, conversation);
        return;
      }
      if (payload.event === "message.read" || payload.event === "message.delivered") {
        const conversationId = String(payload.data?.conversation_id || payload.data?.conversation || "");
        if (conversationId) {
          patchConversationReceiptCaches(queryClient, conversationId, payload.event, payload.data || {});
        }
        return;
      }
      if (payload.event === "conversation.deleted") {
        const conversationId = String(payload.data?.conversation_id || payload.data?.id || "");
        if (!conversationId) return;
        queryClient.removeQueries({ queryKey: ["conversation", conversationId] });
        queryClient.removeQueries({ queryKey: ["messages", conversationId] });
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
        if (location.pathname.startsWith(`/chat/${conversationId}`)) navigate("/chat", { replace: true });
        return;
      }
      if (payload.event === "message.created" || payload.event === "message.updated" || payload.event === "message.deleted") {
        const message = normalizeMessage(payload.data || {});
        const conversationId = String(message.conversation_id || payload.data?.conversation_id || payload.data?.conversation || "");
        if (conversationId && message.id) patchMessageCache(queryClient, conversationId, message);
      }
      if (payload.event === "message.created") {
        const message = normalizeMessage(payload.data || {});
        const sender = payload.data?.sender && typeof payload.data.sender === "object" ? (payload.data.sender as Record<string, unknown>) : null;
        const conversationId = String(message.conversation_id || payload.data?.conversation_id || payload.data?.conversation || "");
        const messageId = String(message.id || payload.data?.message_id || "");
        const chatIsOpenAtLatest = isConversationActivelyViewedAtLatest(conversationId);
        if (sender && !isSameUserIdentity(sender, user) && conversationId && messageId) {
          if (chatIsOpenAtLatest) {
            markConversationReadInCaches(queryClient, conversationId);
            void chatApi.markConversationRead(conversationId, { message_id: messageId }).then((receipt) => {
              patchConversationReceiptCaches(queryClient, conversationId, "message.read", receipt);
            }).catch(() => undefined);
          }
          void chatApi.markConversationDelivered(conversationId, { message_id: messageId }).then((receipt) => {
            patchConversationReceiptCaches(queryClient, conversationId, "message.delivered", receipt);
          }).catch(() => undefined);
        }

        const isCallTimelineMessage = message.type === "system" && message.metadata?.system_event === "call";
        if (isCallTimelineMessage) return;
        const senderName =
          sender
            ? String(sender.display_name || sender.username || "New message")
            : "New message";
        if (sender && !isSameUserIdentity(sender, user)) {
          if (chatIsOpenAtLatest) return;
          void resolveMessageNotificationBody(String(user?.id || ""), message).then((body) => {
            pushMessageToast({
              id: `message:${conversationId}:${messageId}`,
              conversationId,
              messageId,
              title: senderName,
              body,
              avatar: typeof sender.avatar === "string" ? sender.avatar : null,
            });
            return showChatActivityNotification({
              title: senderName,
              body,
              tag: `message:${conversationId}:${messageId}`,
              data: {
                conversation_id: conversationId,
                message_id: messageId,
              },
            });
          }).catch(() => undefined);
        }
      }
      if (payload.event !== "call.started" && payload.event !== "call.created") return;
      const call = realtimeCallPayload ?? normalizeCall(payload.data || {});
      if (!call.id || call.status !== "ringing") return;
      if (isSameUserIdentity(call.initiated_by, user)) {
        const conversationId = String(call.conversation || payload.data?.conversation_id || "");
        if (conversationId && outgoingCallConversationRef.current === conversationId) {
          outgoingCallConversationRef.current = "";
          activateCall(call.id);
          if (isForegroundBrowserTab() && location.pathname !== `/calls/${call.id}`) {
            navigate(`/calls/${call.id}`);
          }
        }
        return;
      }
      void showChatActivityNotification({
        title: `${call.initiated_by?.display_name || call.initiated_by?.username || "Someone"} is calling`,
        body: `${call.call_type === "video" ? "Video" : "Voice"} call incoming`,
        tag: `call:${call.id}`,
        data: { call_id: call.id, conversation_id: String(call.conversation || "") },
      }).catch(() => undefined);
      presentIncomingCall(call);
    });
    return () => {
      unsubscribe();
    };
  }, [activateCall, activeCallId, incomingCall?.id, location.pathname, navigate, presentIncomingCall, queryClient, socket, user?.id]);

  const clearIncoming = () => {
    setIncomingCall(null);
    setShowCallOverlay(false);
    setIncomingCallAction(null);
    setIncomingCallError(null);
  };

  const handleIncomingCallAction = async (action: "accept" | "decline") => {
    const call = incomingCall;
    if (!call || incomingActionInFlightRef.current) return;
    const ownerId = callActionOwnerRef.current;
    if (!claimCallAction(call.id, ownerId)) {
      setIncomingCallError("This call is already being handled in another browser tab.");
      return;
    }

    incomingActionInFlightRef.current = true;
    const pendingAction = action === "accept" ? "accepting" : "declining";
    setIncomingCallAction(pendingAction);
    setIncomingCallError(null);
    callActionChannelRef.current?.publish({ callId: call.id, action: pendingAction, ownerId, occurredAt: Date.now() });

    let mediaReady = action !== "accept";
    try {
      if (action === "accept") {
        await preflightCallMedia(call.call_type);
        mediaReady = true;
        activateCall(call.id);
        setShowCallOverlay(false);
        if (location.pathname !== `/calls/${call.id}`) navigate(`/calls/${call.id}`);
      }
      const updatedCall = action === "accept"
        ? await chatApi.acceptCall(call.id)
        : await chatApi.declineCall(call.id, "declined_from_incoming_call");
      patchCallCaches(queryClient, updatedCall);
      callActionChannelRef.current?.publish({
        callId: call.id,
        action: action === "accept" ? "accepted" : "declined",
        ownerId,
        occurredAt: Date.now(),
      });
      clearIncoming();
      if (action === "accept") {
        activateCall(call.id);
        navigate(`/calls/${call.id}`);
      }
    } catch (error) {
      const message = action === "accept" && !mediaReady
        ? await getCallMediaErrorMessage(error, call.call_type)
        : getCallActionError(error, action === "accept" ? "The call could not be answered." : "The call could not be declined.");
      setIncomingCallError(message);
      setShowCallOverlay(true);
      callActionChannelRef.current?.publish({ callId: call.id, action: "released", ownerId, occurredAt: Date.now() });
    } finally {
      releaseCallAction(call.id, ownerId);
      incomingActionInFlightRef.current = false;
      setIncomingCallAction(null);
    }
  };

  const enableWebPushFromBanner = async () => {
    rememberWebPushPrompt();
    try {
      const token = await ensureBrowserWebPushRegistration({ interactive: true });
      const resolvedToken = token || getStoredWebPushToken();
      if (!resolvedToken) throw new Error("This browser did not return a web push token.");
      await chatApi.registerDevice({ platform: "web", push_token: resolvedToken });
      setWebPushBanner(null);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unable to enable notifications in this browser.";
      setWebPushBanner({
        tone: message.toLowerCase().includes("blocked") || message.toLowerCase().includes("denied") ? "danger" : "warning",
        message,
        action: message.toLowerCase().includes("blocked") || message.toLowerCase().includes("denied") ? "settings" : "enable",
      });
    }
  };

  const userLabel = user?.profile?.display_name || user?.full_name || user?.username || "You";
  const isDirectChat = /^\/chat\/[^/]+\/?$/.test(location.pathname);
  const isSupportInbox = location.pathname.startsWith("/support/inbox");
  const isFocusedChat = isDirectChat || isSupportInbox;
  const productMode = location.pathname.startsWith("/support") ? "support" : "messenger";
  const activeCallIsExpanded = Boolean(activeCallId && (routeCallId === activeCallId || expandedCallId === activeCallId));
  const isCallRoom = Boolean(routeCallId || activeCallIsExpanded);
  const hideMobileNavigation =
    isDirectChat ||
    isCallRoom ||
    (isSupportInbox &&
      new URLSearchParams(location.search).has("conversation"));
  const activeCallContextValue = useMemo(() => ({
    activeCallId,
    activateCall,
    expectOutgoingCall,
    clearOutgoingCallExpectation,
  }), [activeCallId, activateCall, clearOutgoingCallExpectation, expectOutgoingCall]);
  return (
    <ActiveCallProvider value={activeCallContextValue}>
      <div
        className={[
          "ms-ui ms-app-shell",
          isFocusedChat ? "ms-app-shell--focused-chat" : "",
          isCallRoom ? "ms-app-shell--call-room" : "",
        ].filter(Boolean).join(" ")}
      >
      <a className="ms-skip-link" href="#main-content">Skip to main content</a>
      {!isCallRoom ? (
        <DesktopNavigationRail mode={productMode} userLabel={userLabel} userAvatar={user?.profile?.avatar} socketStatus={productMode === "support" ? supportRealtime.socketStatus : socketStatus} supportUnread={supportRealtime.unreadTotal + supportRealtime.alertUnread} onLogout={logout} />
      ) : null}
      <main id="main-content" className="ms-app-main" tabIndex={-1}>
        {webPushBanner ? (
          <div className={`ms-notice-banner ms-notice-banner--${webPushBanner.tone === "danger" ? "danger" : "warning"}`} role="status">
            <div className="ms-notice-banner__copy">
              <strong>{webPushBanner.tone === "danger" ? "Notifications blocked" : "Enable notifications"}</strong>
              <div className="ms-muted">{webPushBanner.message}</div>
            </div>
            <div className="ms-button-row">
              {webPushBanner.action === "enable" ? (
                <button type="button" className="ms-button ms-button--primary ms-button--compact" onClick={() => void enableWebPushFromBanner()}>
                  Turn on notifications
                </button>
              ) : null}
              <button type="button" className="ms-button ms-button--ghost ms-button--compact" onClick={() => navigate("/settings")}>
                Open settings
              </button>
              <button type="button" className="ms-button ms-button--ghost ms-button--compact" onClick={() => setWebPushBanner(null)}>
                Dismiss
              </button>
            </div>
          </div>
        ) : null}
        {incomingCall && !showCallOverlay && !(activeCallIsExpanded && activeCallId === incomingCall.id) ? (
          <IncomingCallBanner
            call={incomingCall}
            action={incomingCallAction}
            error={incomingCallError}
            onOpen={() => setShowCallOverlay(true)}
            onDecline={() => void handleIncomingCallAction("decline")}
            onAccept={() => void handleIncomingCallAction("accept")}
          />
        ) : null}
        {activeCallId ? (
          <CallRoomPage
            key={activeCallId}
            callIdOverride={activeCallId}
            displayMode={activeCallIsExpanded ? "full" : "compact"}
            onCallFinished={clearActiveCall}
            onCallMinimize={minimizeActiveCall}
          />
        ) : null}
        <div className={`ms-app-stage ${isCallRoom ? "ms-app-stage--call-route" : ""}`}>
          <div className="ms-app-stage__main">
            <Outlet />
          </div>
        </div>
      </main>
      {!hideMobileNavigation ? <MobileBottomNavigation mode={productMode} supportUnread={supportRealtime.unreadTotal + supportRealtime.alertUnread} /> : null}
      {incomingCall && showCallOverlay ? (
        <IncomingCallOverlay
          call={incomingCall}
          action={incomingCallAction}
          error={incomingCallError}
          onMinimize={() => setShowCallOverlay(false)}
          onDecline={() => void handleIncomingCallAction("decline")}
          onAccept={() => void handleIncomingCallAction("accept")}
        />
      ) : null}
      {messageToasts.length || supportRealtime.toasts.length ? (
        <div className="ms-toast-stack" role="status" aria-live="polite">
          {supportRealtime.toasts.map((toast) => (
            <article key={`support:${toast.id}`} className="ms-toast">
              <button
                type="button"
                className="ms-toast__open"
                onClick={() => {
                  supportRealtime.dismissToast(toast.id);
                  navigate(`/support/inbox?conversation=${encodeURIComponent(toast.conversationId)}`);
                }}
              >
                <UserAvatar person={{ display_name: toast.visitorName }} size="sm" className="ms-toast__avatar" decorative />
                <span className="ms-toast__copy">
                  <strong>{toast.visitorName}</strong>
                  <span>{toast.body}</span>
                  <small className="ms-support-toast__site">{toast.websiteName}</small>
                </span>
              </button>
              <button
                type="button"
                className="ms-toast__reply"
                onClick={() => {
                  supportRealtime.dismissToast(toast.id);
                  navigate(`/support/inbox?conversation=${encodeURIComponent(toast.conversationId)}`);
                }}
              >
                Open
              </button>
              <button type="button" className="ms-toast__close" aria-label="Dismiss Support Chat notification" onClick={() => supportRealtime.dismissToast(toast.id)}>×</button>
            </article>
          ))}
          {messageToasts.map((toast) => (
            <article key={toast.id} className="ms-toast">
              <button
                type="button"
                className="ms-toast__open"
                onClick={() => {
                  dismissMessageToast(toast.id);
                  if (toast.conversationId) navigate(`/chat/${toast.conversationId}`);
                }}
              >
                <UserAvatar person={{ display_name: toast.title, avatar: toast.avatar }} size="sm" className="ms-toast__avatar" decorative />
                <span className="ms-toast__copy">
                  <strong>{toast.title}</strong>
                  <span>{toast.body}</span>
                </span>
              </button>
              <button
                type="button"
                className="ms-toast__reply"
                onClick={() => {
                  dismissMessageToast(toast.id);
                  if (toast.conversationId) navigate(`/chat/${toast.conversationId}?reply=1`);
                }}
              >
                Reply
              </button>
              <button
                type="button"
                className="ms-toast__close"
                aria-label="Dismiss message notification"
                onClick={() => dismissMessageToast(toast.id)}
              >
                ×
              </button>
            </article>
          ))}
        </div>
      ) : null}
      </div>
    </ActiveCallProvider>
  );
}
