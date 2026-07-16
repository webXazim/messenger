function trimTrailingSlash(value: string) {
  return value.replace(/\/+$/, "");
}

function ensureLeadingSlash(value: string) {
  return value.startsWith("/") ? value : `/${value}`;
}

function normalizeWsChatUrl(value: string) {
  const trimmed = trimTrailingSlash(value.trim());

  if (!trimmed) {
    return "";
  }

  // If user provides full websocket endpoint, keep it.
  if (trimmed.endsWith("/ws/chat")) {
    return `${trimmed}/`;
  }

  // If user provides domain/base URL only, append websocket endpoint.
  return `${trimmed}/ws/chat/`;
}

function deriveWsBaseUrl(apiBaseUrl: string) {
  const normalizedApi = trimTrailingSlash(apiBaseUrl);

  try {
    const url = new URL(normalizedApi, window.location.origin);
    const protocol = url.protocol === "https:" ? "wss:" : "ws:";

    const pathname = url.pathname
      .replace(/\/api\/v\d+\/?$/, "")
      .replace(/\/+$/, "");

    return `${protocol}//${url.host}${pathname}/ws/chat/`;
  } catch {
    return "ws://127.0.0.1:8000/ws/chat/";
  }
}

const envApiBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim();
const envAuthBaseUrl = import.meta.env.VITE_AUTH_BASE_URL?.trim();
const envWsBaseUrl = import.meta.env.VITE_WS_BASE_URL?.trim();
const envSocialBaseUrl = import.meta.env.VITE_SOCIAL_BASE_URL?.trim();

export const API_BASE_URL = trimTrailingSlash(envApiBaseUrl || "/api/v1");
export const AUTH_API_BASE_URL = trimTrailingSlash(envAuthBaseUrl || "https://accounts.crescentsphere.com/api/v1");

export const WS_BASE_URL = envWsBaseUrl
  ? normalizeWsChatUrl(envWsBaseUrl)
  : deriveWsBaseUrl(API_BASE_URL);

export const APP_NAME = import.meta.env.VITE_APP_NAME ?? "Crescentsphere";
export const SOCIAL_BASE_URL = trimTrailingSlash(envSocialBaseUrl || "https://crescentsphere.com");
