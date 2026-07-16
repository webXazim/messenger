export const CHAT_INBOX_MIN_WIDTH = 280;
export const CHAT_INBOX_MAX_WIDTH = 440;
export const CHAT_INBOX_DEFAULT_WIDTH = 340;

export function clampChatInboxWidth(width: number) {
  return Math.min(CHAT_INBOX_MAX_WIDTH, Math.max(CHAT_INBOX_MIN_WIDTH, Math.round(width)));
}

export function readStoredChatInboxWidth() {
  if (typeof window === "undefined") return CHAT_INBOX_DEFAULT_WIDTH;
  const stored = Number(window.localStorage.getItem("chat-inbox-width"));
  return Number.isFinite(stored) ? clampChatInboxWidth(stored) : CHAT_INBOX_DEFAULT_WIDTH;
}
