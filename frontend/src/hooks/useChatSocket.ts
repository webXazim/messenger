import { useEffect, useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import { chatSocket, SOCKET_AUTH_FAILED_EVENT, type SocketStatus } from "../lib/chatSocket";
import { unwrapData } from "../lib/apiResponse";
import { AUTH_API_BASE_URL } from "../lib/config";
import { getOrCreateDeviceId } from "../lib/deviceIdentity";
import {
  AUTH_TOKEN_UPDATED_EVENT,
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setAccessToken,
  setRefreshToken,
} from "../lib/tokenStore";

const REFRESH_INTERVAL_MS = 30000;

let activeHookCount = 0;
let refreshTimer: number | null = null;
let refreshInFlight: Promise<string | null> | null = null;
let sharedListenersInstalled = false;

function jwtExpiresSoon(token: string, skewSeconds = 60) {
  try {
    const segment = token.split(".")[1] ?? "";
    const normalized = segment.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), "=");
    const payload = JSON.parse(atob(padded)) as { exp?: number };
    return typeof payload.exp !== "number" || payload.exp * 1000 <= Date.now() + skewSeconds * 1000;
  } catch {
    return true;
  }
}

async function refreshAccessTokenIfNeeded(force = false) {
  const current = getAccessToken();
  if (!force && current && !jwtExpiresSoon(current)) return current;
  const refresh = getRefreshToken();
  if (!refresh) {
    if (force) {
      clearTokens();
      return null;
    }
    return current;
  }

  const response = await fetch(`${AUTH_API_BASE_URL}/auth/token/refresh/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh }),
  });
  if (!response.ok) {
    clearTokens();
    return null;
  }
  const data = unwrapData<{ access?: string; refresh?: string }>(await response.json());
  if (!data.access) {
    clearTokens();
    return null;
  }
  setAccessToken(data.access);
  if (data.refresh) setRefreshToken(data.refresh);
  return data.access;
}

function refreshAccessTokenOnce(force = false) {
  if (!refreshInFlight) {
    refreshInFlight = refreshAccessTokenIfNeeded(force).finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

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
  chatSocket.send({ event: "presence.ping", data: {} });
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
