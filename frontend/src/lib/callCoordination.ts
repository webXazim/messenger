export type CoordinatedCallAction = "accepting" | "declining" | "accepted" | "declined" | "released" | "cleared";

export type CallCoordinationEvent = {
  callId: string;
  action: CoordinatedCallAction;
  ownerId: string;
  occurredAt: number;
};

const CHANNEL_NAME = "messenger.call-actions.v1";
const STORAGE_EVENT_KEY = "messenger.call-action-event.v1";
const LEASE_PREFIX = "messenger.call-action-lease.v1.";
const DEFAULT_LEASE_MS = 20_000;

function storageAvailable() {
  return typeof window !== "undefined" && Boolean(window.localStorage);
}

function safeParse(value: string | null): CallCoordinationEvent | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as Partial<CallCoordinationEvent>;
    if (!parsed.callId || !parsed.action || !parsed.ownerId || !Number.isFinite(parsed.occurredAt)) return null;
    return parsed as CallCoordinationEvent;
  } catch {
    return null;
  }
}

export function createCallActionOwnerId() {
  const storageKey = "messenger.call-action-owner.v1";
  if (typeof window !== "undefined") {
    try {
      const existing = window.sessionStorage.getItem(storageKey);
      if (existing) return existing;
      const generated = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `call-action-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      window.sessionStorage.setItem(storageKey, generated);
      return generated;
    } catch {
      // Session storage can be unavailable in private or hardened browser modes.
    }
  }
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `call-action-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function claimCallAction(callId: string, ownerId: string, leaseMs = DEFAULT_LEASE_MS) {
  if (!storageAvailable()) return true;
  const key = `${LEASE_PREFIX}${callId}`;
  const now = Date.now();
  const existing = safeParse(window.localStorage.getItem(key));
  if (existing && existing.ownerId !== ownerId && now - existing.occurredAt < leaseMs) return false;
  const lease: CallCoordinationEvent = { callId, action: "accepting", ownerId, occurredAt: now };
  try {
    window.localStorage.setItem(key, JSON.stringify(lease));
    return safeParse(window.localStorage.getItem(key))?.ownerId === ownerId;
  } catch {
    return true;
  }
}

export function releaseCallAction(callId: string, ownerId: string) {
  if (!storageAvailable()) return;
  const key = `${LEASE_PREFIX}${callId}`;
  try {
    const existing = safeParse(window.localStorage.getItem(key));
    if (!existing || existing.ownerId === ownerId) window.localStorage.removeItem(key);
  } catch {
    // Browser storage is optional. Server-side call actions remain authoritative.
  }
}

export function createCallActionChannel(onEvent: (event: CallCoordinationEvent) => void) {
  const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel(CHANNEL_NAME) : null;
  if (channel) {
    channel.onmessage = (message) => {
      const event = message.data as CallCoordinationEvent;
      if (event?.callId && event?.action) onEvent(event);
    };
  }

  const handleStorage = (event: StorageEvent) => {
    if (event.key !== STORAGE_EVENT_KEY) return;
    const value = safeParse(event.newValue);
    if (value) onEvent(value);
  };
  if (typeof window !== "undefined") window.addEventListener("storage", handleStorage);

  return {
    publish(event: CallCoordinationEvent) {
      channel?.postMessage(event);
      if (!storageAvailable()) return;
      try {
        window.localStorage.setItem(STORAGE_EVENT_KEY, JSON.stringify(event));
        window.localStorage.removeItem(STORAGE_EVENT_KEY);
      } catch {
        // Cross-tab coordination is best-effort; backend actions remain idempotent.
      }
    },
    close() {
      channel?.close();
      if (typeof window !== "undefined") window.removeEventListener("storage", handleStorage);
    },
  };
}
