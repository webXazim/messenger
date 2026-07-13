const DRAFT_PREFIX = "messenger:draft:v1:";
const LEGACY_DRAFT_PREFIX = "chat-draft:";

export function buildConversationDraftKey(userId: string, conversationId: string) {
  return `${DRAFT_PREFIX}${userId}:${conversationId}`;
}

export function buildLegacyConversationDraftKey(conversationId: string) {
  return `${LEGACY_DRAFT_PREFIX}${conversationId}`;
}

export function readConversationDraft(key: string, legacyKey?: string) {
  try {
    const current = window.localStorage.getItem(key);
    if (current !== null) return current;
    if (!legacyKey) return "";
    const legacy = window.localStorage.getItem(legacyKey);
    if (legacy === null) return "";
    window.localStorage.setItem(key, legacy);
    window.localStorage.removeItem(legacyKey);
    return legacy;
  } catch {
    return "";
  }
}

export function writeConversationDraft(key: string, text: string) {
  try {
    if (text.trim()) window.localStorage.setItem(key, text);
    else window.localStorage.removeItem(key);
  } catch {
    // Draft persistence is best-effort. A storage quota or restrictive browser
    // mode must never prevent the user from typing or sending a message.
  }
}

export function removeConversationDraft(key?: string) {
  if (!key) return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    // The in-memory composer can still be cleared safely.
  }
}

export function clearConversationDraftsForUser(userId: string) {
  try {
    const prefix = `${DRAFT_PREFIX}${userId}:`;
    const keys: string[] = [];
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index);
      if (key?.startsWith(prefix) || key?.startsWith(LEGACY_DRAFT_PREFIX)) keys.push(key);
    }
    keys.forEach((key) => window.localStorage.removeItem(key));
  } catch {
    // Logout continues even when browser storage is unavailable.
  }
}
