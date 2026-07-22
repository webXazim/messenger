function trimTrailingSlash(value: string) {
  return value.replace(/\/+$/, "");
}

function ensureLeadingSlash(value: string) {
  return value.startsWith("/") ? value : `/${value}`;
}

function normalizeRealtimeWsUrl(value: string) {
  const trimmed = trimTrailingSlash(value.trim());
  if (!trimmed) return "";
  if (trimmed.endsWith("/ws")) return trimmed;
  return `${trimmed}/ws`;
}

function deriveRealtimeWsUrl(apiBaseUrl: string) {
  const normalizedApi = trimTrailingSlash(apiBaseUrl);
  try {
    const url = new URL(normalizedApi, window.location.origin);
    const protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const pathname = url.pathname.replace(/\/api\/v\d+\/?$/, "").replace(/\/+$/, "");
    return `${protocol}//${url.host}${pathname}/ws`;
  } catch {
    return "ws://127.0.0.1:9000/ws";
  }
}

const envApiBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim();
const envAuthBaseUrl = import.meta.env.VITE_AUTH_BASE_URL?.trim();
const envWsBaseUrl = import.meta.env.VITE_WS_BASE_URL?.trim();
const envSupportWsUrl = import.meta.env.VITE_SUPPORT_WS_URL?.trim();
const envSocialBaseUrl = import.meta.env.VITE_SOCIAL_BASE_URL?.trim();
const envSupportPlansUrl = import.meta.env.VITE_SUPPORT_PLANS_URL?.trim();
const envChatCommandBackend = import.meta.env.VITE_CHAT_COMMAND_BACKEND?.trim().toLowerCase();
const envChatReadBackend = import.meta.env.VITE_CHAT_READ_BACKEND?.trim().toLowerCase();
const envChatInteractionBackend = import.meta.env.VITE_CHAT_INTERACTION_BACKEND?.trim().toLowerCase();
const envChatMessageMutationBackend = import.meta.env.VITE_CHAT_MESSAGE_MUTATION_BACKEND?.trim().toLowerCase();
const envChatCallRuntimeBackend = import.meta.env.VITE_CHAT_CALL_RUNTIME_BACKEND?.trim().toLowerCase();
const envChatAttachmentBackend = import.meta.env.VITE_CHAT_ATTACHMENT_BACKEND?.trim().toLowerCase();
const envChatConversationCommandBackend = import.meta.env.VITE_CHAT_CONVERSATION_COMMAND_BACKEND?.trim().toLowerCase();
const envSupportDataBackend = import.meta.env.VITE_SUPPORT_DATA_BACKEND?.trim().toLowerCase();

export const API_BASE_URL = trimTrailingSlash(envApiBaseUrl || "/api/v1");
export const CHAT_COMMAND_BACKEND = envChatCommandBackend === "axum" ? "axum" : "django";
export const CHAT_READ_BACKEND = envChatReadBackend === "sqlx" ? "sqlx" : "django";
export const CHAT_INTERACTION_BACKEND = envChatInteractionBackend === "axum"
  ? "axum"
  : envChatInteractionBackend === "django"
    ? "django"
    : CHAT_COMMAND_BACKEND;
export const CHAT_MESSAGE_MUTATION_BACKEND = envChatMessageMutationBackend === "axum" ? "axum" : "django";
export const CHAT_CALL_RUNTIME_BACKEND = envChatCallRuntimeBackend === "axum" ? "axum" : "django";
export const CHAT_ATTACHMENT_BACKEND = envChatAttachmentBackend === "axum" ? "axum" : "django";
export const CHAT_CONVERSATION_COMMAND_BACKEND = envChatConversationCommandBackend === "axum" ? "axum" : "django";
export const SUPPORT_DATA_BACKEND = envSupportDataBackend === "axum" ? "axum" : "django";
export const SUPPORT_DATA_URL = "/support-fast";
export const CHAT_COMMAND_URL = "/chat-fast";
export const CHAT_READ_URL = "/chat-fast";
export const AUTH_API_BASE_URL = trimTrailingSlash(envAuthBaseUrl || "https://accounts.crescentsphere.com/api/v1");

const configuredRealtimeWsUrl = envWsBaseUrl || envSupportWsUrl || "";
export const REALTIME_WS_URL = configuredRealtimeWsUrl
  ? normalizeRealtimeWsUrl(configuredRealtimeWsUrl)
  : deriveRealtimeWsUrl(API_BASE_URL);

// Compatibility aliases while feature code is migrated to the single Axum endpoint.
export const WS_BASE_URL = REALTIME_WS_URL;
export const SUPPORT_WS_URL = REALTIME_WS_URL;

export const APP_NAME = import.meta.env.VITE_APP_NAME ?? "Crescentsphere";
export const SOCIAL_BASE_URL = trimTrailingSlash(envSocialBaseUrl || "https://crescentsphere.com");
export const SUPPORT_PLANS_URL = envSupportPlansUrl || "/support/plans";
