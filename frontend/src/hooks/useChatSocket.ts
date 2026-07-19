import { useEffect, useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import { chatSocket, SOCKET_AUTH_FAILED_EVENT, type SocketStatus } from "../lib/chatSocket";
import { jwtExpiresSoon, refreshAccessTokenOnce } from "../lib/authRefresh";
import { getOrCreateDeviceId } from "../lib/deviceIdentity";
import {
  AUTH_TOKEN_UPDATED_EVENT,
  getAccessToken,
} from "../lib/tokenStore";

const REFRESH_INTERVAL_MS = 30000;

let activeHookCount = 0;
let refreshTimer: number | null = null;
let sharedListenersInstalled = false;

async function ensureSocketConnection(forceRefresh = false) {
  const token = await refreshAccessTokenOnce(forceRefresh);
  if (activeHookCount <= 0) return;
  if (!token) {
    chatSocket.disconnect();
    return;
  }
  chatSocket.connect(token, getOrCreateDeviceId());
}

function handleTokenUpdated(event: Event) {
  const token = (event as CustomEvent<{ token?: string | null }>).detail?.token ?? getAccessToken();
  if (activeHookCount <= 0) return;
  if (!token) {
    chatSocket.disconnect();
    return;
  }
  chatSocket.connect(token, getOrCreateDeviceId());
}

function handleSocketAuthFailure() {
  if (activeHookCount > 0) void ensureSocketConnection(true);
}

function handleConnectivityReturn() {
  if (activeHookCount <= 0) return;
  if (document.visibilityState === "hidden") return;
  chatSocket.reportActivity();
  void ensureSocketConnection(false);
}

function installSharedListeners() {
  if (sharedListenersInstalled) return;
  sharedListenersInstalled = true;
  window.addEventListener(AUTH_TOKEN_UPDATED_EVENT, handleTokenUpdated);
  window.addEventListener(SOCKET_AUTH_FAILED_EVENT, handleSocketAuthFailure);
  window.addEventListener("online", handleConnectivityReturn);
  window.addEventListener("focus", handleConnectivityReturn);
  document.addEventListener("visibilitychange", handleConnectivityReturn);
}

function removeSharedListeners() {
  if (!sharedListenersInstalled) return;
  sharedListenersInstalled = false;
  window.removeEventListener(AUTH_TOKEN_UPDATED_EVENT, handleTokenUpdated);
  window.removeEventListener(SOCKET_AUTH_FAILED_EVENT, handleSocketAuthFailure);
  window.removeEventListener("online", handleConnectivityReturn);
  window.removeEventListener("focus", handleConnectivityReturn);
  document.removeEventListener("visibilitychange", handleConnectivityReturn);
}

function startSharedConnectionLoop() {
  activeHookCount += 1;
  installSharedListeners();
  void ensureSocketConnection(false);
  if (!refreshTimer) {
    refreshTimer = window.setInterval(() => {
      const current = getAccessToken();
      if (!current || jwtExpiresSoon(current) || !chatSocket.isOpen()) {
        void ensureSocketConnection(false);
      }
    }, REFRESH_INTERVAL_MS);
  }
}

function stopSharedConnectionLoop() {
  activeHookCount = Math.max(0, activeHookCount - 1);
  if (activeHookCount > 0) return;
  if (refreshTimer) {
    window.clearInterval(refreshTimer);
    refreshTimer = null;
  }
  removeSharedListeners();
  if (!getAccessToken()) chatSocket.disconnect();
}

export function useChatSocket() {
  const [status, setStatus] = useState<SocketStatus>("closed");
  const { user } = useAuth();

  useEffect(() => {
    const unsubscribeStatus = chatSocket.subscribeStatus(setStatus);
    startSharedConnectionLoop();
    return () => {
      unsubscribeStatus();
      stopSharedConnectionLoop();
    };
  }, [user?.id]);

  return { socket: chatSocket, socketStatus: status };
}
