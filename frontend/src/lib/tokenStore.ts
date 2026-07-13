const ACCESS_TOKEN_KEY = "messenger.access";
const REFRESH_TOKEN_KEY = "messenger.refresh";
const SESSION_ID_KEY = "messenger.session-id";
export const AUTH_CLEARED_EVENT = "messenger-auth-cleared";
export const AUTH_TOKEN_UPDATED_EVENT = "messenger-auth-token-updated";

export const getAccessToken = () => localStorage.getItem(ACCESS_TOKEN_KEY);
export const getRefreshToken = () => localStorage.getItem(REFRESH_TOKEN_KEY);
export const setAccessToken = (token: string) => {
  localStorage.setItem(ACCESS_TOKEN_KEY, token);
  window.dispatchEvent(new CustomEvent(AUTH_TOKEN_UPDATED_EVENT, { detail: { token } }));
};
export const setRefreshToken = (token: string) => localStorage.setItem(REFRESH_TOKEN_KEY, token);
export const getSessionId = () => localStorage.getItem(SESSION_ID_KEY);
export const setSessionId = (sessionId: string) => localStorage.setItem(SESSION_ID_KEY, sessionId);
export const clearTokens = () => {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  localStorage.removeItem(SESSION_ID_KEY);
  window.dispatchEvent(new CustomEvent(AUTH_TOKEN_UPDATED_EVENT, { detail: { token: null } }));
  window.dispatchEvent(new Event(AUTH_CLEARED_EVENT));
};
