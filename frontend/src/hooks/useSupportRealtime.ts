import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";
import { supportApi } from "../api/support";
import { AUTH_TOKEN_UPDATED_EVENT, getAccessToken } from "../lib/tokenStore";
import { supportSocket, SUPPORT_SOCKET_AUTH_FAILED_EVENT, type SupportSocketEvent, type SupportSocketStatus } from "../lib/supportSocket";
import { SOCKET_AUTH_FAILED_EVENT } from "../lib/chatSocket";

export type SupportToast = {
  id: string;
  conversationId: string;
  websiteName: string;
  visitorName: string;
  body: string;
};

function selectedSupportConversation(location: ReturnType<typeof useLocation>) {
  if (!location.pathname.startsWith("/support/inbox")) return "";
  return new URLSearchParams(location.search).get("conversation") || "";
}

function showSupportBrowserNotification(toast: SupportToast) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  const targetPath = `/support/inbox?conversation=${encodeURIComponent(toast.conversationId)}`;
  const notification = new Notification(`${toast.visitorName} · ${toast.websiteName}`, {
    body: toast.body,
    tag: `support:${toast.conversationId}:${toast.id}`,
    data: { target_path: targetPath, support_conversation_id: toast.conversationId },
  });
  notification.onclick = () => { window.focus(); window.location.assign(targetPath); };
}

export function useSupportRealtime() {
  const queryClient = useQueryClient();
  const location = useLocation();
  const [socketStatus, setSocketStatus] = useState<SupportSocketStatus>("closed");
  const [toasts, setToasts] = useState<SupportToast[]>([]);
  const bootstrapQuery = useQuery({
    queryKey: ["support-bootstrap"],
    queryFn: ({ signal }) => supportApi.bootstrap(signal),
    staleTime: 30_000,
  });
  const active = bootstrapQuery.data?.access === "active";
  const websiteIds = useMemo(
    () => (bootstrapQuery.data?.websites || []).map((website) => website.id).filter(Boolean),
    [bootstrapQuery.data?.websites],
  );
  const websiteKey = websiteIds.join(",");
  const unreadQuery = useQuery({
    queryKey: ["support-unread-summary"],
    queryFn: ({ signal }) => supportApi.unreadSummary(signal),
    enabled: active,
    staleTime: 3000,
    refetchInterval: socketStatus === "open" ? false : 10000,
  });
  const selectedConversationId = useMemo(() => selectedSupportConversation(location), [location]);

  useEffect(() => {
    const unsubscribe = supportSocket.subscribeStatus(setSocketStatus);
    return unsubscribe;
  }, []);

  useEffect(() => {
    if (!active) {
      supportSocket.disconnect();
      return;
    }
    const connect = () => {
      const token = getAccessToken();
      if (token) supportSocket.connect(token, websiteIds);
    };
    const reconnect = () => { if (document.visibilityState === "visible") connect(); };
    const tokenUpdated = () => connect();
    const authFailed = (event: Event) => {
      const code = Number((event as CustomEvent<{ code?: number }>).detail?.code || 0);
      if (code === 4403) {
        supportSocket.disconnect();
        void queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] });
        void queryClient.invalidateQueries({ queryKey: ["support-unread-summary"] });
        return;
      }
      window.dispatchEvent(new CustomEvent(SOCKET_AUTH_FAILED_EVENT, { detail: { source: "support" } }));
      window.setTimeout(connect, 1200);
    };
    connect();
    window.addEventListener(AUTH_TOKEN_UPDATED_EVENT, tokenUpdated);
    window.addEventListener(SUPPORT_SOCKET_AUTH_FAILED_EVENT, authFailed);
    window.addEventListener("online", reconnect);
    window.addEventListener("focus", reconnect);
    document.addEventListener("visibilitychange", reconnect);
    return () => {
      window.removeEventListener(AUTH_TOKEN_UPDATED_EVENT, tokenUpdated);
      window.removeEventListener(SUPPORT_SOCKET_AUTH_FAILED_EVENT, authFailed);
      window.removeEventListener("online", reconnect);
      window.removeEventListener("focus", reconnect);
      document.removeEventListener("visibilitychange", reconnect);
      supportSocket.disconnect();
    };
  }, [active, queryClient, websiteKey]);

  useEffect(() => {
    if (!active) return;
    const refreshSupport = (payload: SupportSocketEvent) => {
      if (payload.event === "support.ready") {
        void queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] });
        void queryClient.invalidateQueries({ queryKey: ["support-unread-summary"] });
        void queryClient.invalidateQueries({ queryKey: ["support-conversations"] });
        void queryClient.invalidateQueries({ queryKey: ["support-conversation-messages"] });
        void queryClient.invalidateQueries({ queryKey: ["support-conversation-activity"] });
        void queryClient.invalidateQueries({ queryKey: ["support-active-call"] });
        void queryClient.invalidateQueries({ queryKey: ["support-service-alerts"] });
        return;
      }
      if (!payload.event.startsWith("support.")) return;
      if (
        payload.event === "support.pong" ||
        payload.event === "support.visitor.presence" ||
        payload.event === "support.typing.started" ||
        payload.event === "support.typing.stopped" ||
        payload.event === "support.call.signal"
      ) {
        return;
      }
      const conversationId = String(payload.data?.conversation_id || "");
      void queryClient.invalidateQueries({ queryKey: ["support-unread-summary"] });
      void queryClient.invalidateQueries({ queryKey: ["support-service-alerts"] });
      void queryClient.invalidateQueries({ queryKey: ["support-conversations"] });
      if (payload.event === "support.csat.updated") {
        void queryClient.invalidateQueries({ queryKey: ["support-analytics"] });
      }
      if (conversationId) {
        void queryClient.invalidateQueries({ queryKey: ["support-conversation-messages", conversationId] });
        void queryClient.invalidateQueries({ queryKey: ["support-conversation-activity", conversationId] });
      }

      if (payload.event === "support.service.alert") {
        const toast: SupportToast = {
          id: String(payload.data?.alert_id || payload.event_id || Date.now()),
          conversationId,
          websiteName: String(payload.data?.website_name || "Support website"),
          visitorName: "Service alert",
          body: String(payload.data?.summary || "A Support service action needs attention.").slice(0, 180),
        };
        setToasts((current) => [toast, ...current.filter((item) => item.id !== toast.id)].slice(0, 4));
        window.setTimeout(() => setToasts((current) => current.filter((item) => item.id !== toast.id)), 9000);
        showSupportBrowserNotification(toast);
        return;
      }

      if (payload.event !== "support.message.created") return;
      const sender = payload.data?.sender && typeof payload.data.sender === "object" ? payload.data.sender as Record<string, unknown> : {};
      if (String(sender.kind || "") !== "visitor" || !conversationId || conversationId === selectedConversationId) return;
      const toast: SupportToast = {
        id: String(payload.data?.message_id || payload.event_id || Date.now()),
        conversationId,
        websiteName: String(payload.data?.website_name || "Support website"),
        visitorName: String(sender.display_name || "Website visitor"),
        body: String(payload.data?.preview || payload.data?.text || "New support message").slice(0, 180),
      };
      setToasts((current) => [toast, ...current.filter((item) => item.id !== toast.id)].slice(0, 4));
      window.setTimeout(() => setToasts((current) => current.filter((item) => item.id !== toast.id)), 7000);
      if (document.visibilityState !== "visible" || !location.pathname.startsWith("/support")) showSupportBrowserNotification(toast);
    };
    return supportSocket.subscribe(refreshSupport);
  }, [active, location.pathname, queryClient, selectedConversationId]);

  return {
    unreadTotal: unreadQuery.data?.unread_total || 0,
    alertUnread: unreadQuery.data?.alert_unread || 0,
    websiteUnread: unreadQuery.data?.website_unread || {},
    socketStatus,
    toasts,
    dismissToast: (id: string) => setToasts((current) => current.filter((item) => item.id !== id)),
  };
}

export function useSupportSocketStatus() {
  const [status, setStatus] = useState<SupportSocketStatus>("closed");
  useEffect(() => supportSocket.subscribeStatus(setStatus), []);
  return status;
}
