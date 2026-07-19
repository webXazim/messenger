import { REALTIME_WS_URL } from "./config";
import { detectPresenceDeviceType, PRESENCE_IDLE_AFTER_MS, type PresenceActivityStatus } from "./devicePresence";
import {
  requestRealtimeGrants,
  requestRealtimeTicket,
  realtimeAudienceKey,
  type RealtimeAudience,
} from "./realtimeCredentials";

export type SocketEvent = {
  event: string;
  event_id?: string;
  occurred_at?: string;
  request_id?: string;
  data?: Record<string, unknown>;
};

export type SocketStatus = "connecting" | "open" | "closed";
type Listener = (event: SocketEvent) => void;
type StatusListener = (status: SocketStatus) => void;

export const SOCKET_AUTH_FAILED_EVENT = "messenger-socket-auth-failed";

function buildSocketUrl(ticket: string) {
  const url = new URL(REALTIME_WS_URL, window.location.origin);
  url.searchParams.set("ticket", ticket);
  return url.toString();
}

function conversationAudience(conversationId: string): RealtimeAudience {
  return { kind: "conversation", id: conversationId };
}

function requestId() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function queueKey(payload: SocketEvent) {
  return [
    payload.event,
    String(payload.data?.conversation_id ?? ""),
    String(payload.data?.call_id ?? ""),
    String(payload.data?.signal_type ?? ""),
    String(payload.data?.signal_id ?? payload.data?.client_temp_id ?? ""),
    String(payload.data?.message_id ?? payload.data?.id ?? ""),
  ].join(":");
}

function incomingEventKey(payload: SocketEvent) {
  if (payload.event_id) return `id:${payload.event_id}`;
  const data = payload.data ?? {};
  return [
    "legacy",
    payload.event,
    String(data.conversation_id ?? data.conversation ?? ""),
    String(data.message_id ?? data.id ?? ""),
    String(data.call_id ?? ""),
    String(data.user_id ?? ""),
    String(data.signal_id ?? ""),
    String(data.updated_at ?? data.last_read_at ?? data.last_delivered_at ?? payload.occurred_at ?? ""),
  ].join(":");
}

export class ChatSocket {
  private socket: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private statusListeners = new Set<StatusListener>();
  private reconnectTimer: number | null = null;
  private reconnectAttempts = 0;
  private token: string | null = null;
  private deviceId: string | null = null;
  private activeToken: string | null = null;
  private activeDeviceId: string | null = null;
  private manualClose = false;
  private subscriptions = new Map<string, number>();
  private grants = new Map<string, string>();
  private pendingGrantRequests = new Map<string, Promise<string | null>>();
  private pendingQueue: SocketEvent[] = [];
  private heartbeatTimer: number | null = null;
  private idleTimer: number | null = null;
  private lastActivityAt = Date.now();
  private lastSentPresenceStatus: PresenceActivityStatus | null = null;
  private activityTracking = false;
  private seenEvents = new Map<string, number>();
  private generation = 0;

  private currentPresenceStatus(): PresenceActivityStatus {
    if (document.visibilityState === "hidden") return "idle";
    return Date.now() - this.lastActivityAt >= PRESENCE_IDLE_AFTER_MS ? "idle" : "active";
  }

  private presenceData() {
    return { device_type: detectPresenceDeviceType(), presence_status: this.currentPresenceStatus() };
  }

  private sendWire(payload: SocketEvent) {
    if (this.socket?.readyState !== WebSocket.OPEN) return false;
    this.socket.send(JSON.stringify({ v: 1, request_id: payload.request_id || requestId(), ...payload }));
    return true;
  }

  private sendPresencePing() {
    const data = this.presenceData();
    const sent = this.sendWire({ event: "presence.ping", data });
    if (sent) this.lastSentPresenceStatus = data.presence_status;
    return sent;
  }

  private scheduleIdleTransition() {
    if (this.idleTimer) window.clearTimeout(this.idleTimer);
    this.idleTimer = null;
    if (document.visibilityState === "hidden" || this.currentPresenceStatus() === "idle") return;
    const remaining = Math.max(250, PRESENCE_IDLE_AFTER_MS - (Date.now() - this.lastActivityAt));
    this.idleTimer = window.setTimeout(() => {
      this.idleTimer = null;
      this.syncPresenceTransition();
    }, remaining);
  }

  private syncPresenceTransition() {
    const nextStatus = this.currentPresenceStatus();
    if (nextStatus !== this.lastSentPresenceStatus) this.sendPresencePing();
    this.scheduleIdleTransition();
  }

  private handleUserActivity = () => {
    if (document.visibilityState === "hidden") return;
    this.lastActivityAt = Date.now();
    this.syncPresenceTransition();
  };

  private handleVisibilityChange = () => {
    if (document.visibilityState === "visible") this.lastActivityAt = Date.now();
    this.syncPresenceTransition();
  };

  private startActivityTracking() {
    if (this.activityTracking) return;
    this.activityTracking = true;
    window.addEventListener("focus", this.handleUserActivity);
    window.addEventListener("pointerdown", this.handleUserActivity, { passive: true });
    window.addEventListener("keydown", this.handleUserActivity);
    window.addEventListener("touchstart", this.handleUserActivity, { passive: true });
    window.addEventListener("wheel", this.handleUserActivity, { passive: true });
    document.addEventListener("visibilitychange", this.handleVisibilityChange);
    this.scheduleIdleTransition();
  }

  private stopActivityTracking() {
    if (!this.activityTracking) return;
    this.activityTracking = false;
    window.removeEventListener("focus", this.handleUserActivity);
    window.removeEventListener("pointerdown", this.handleUserActivity);
    window.removeEventListener("keydown", this.handleUserActivity);
    window.removeEventListener("touchstart", this.handleUserActivity);
    window.removeEventListener("wheel", this.handleUserActivity);
    document.removeEventListener("visibilitychange", this.handleVisibilityChange);
    if (this.idleTimer) window.clearTimeout(this.idleTimer);
    this.idleTimer = null;
  }

  connect(token: string, deviceId: string) {
    const credentialsChanged = token !== this.token || deviceId !== this.deviceId;
    this.token = token;
    this.deviceId = deviceId;
    this.manualClose = false;
    const activeMatch = token === this.activeToken && deviceId === this.activeDeviceId;
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      if (activeMatch && !credentialsChanged) return;
      this.replaceConnection();
      return;
    }
    this.clearReconnectTimer();
    void this.openConnection();
  }

  reconnect() {
    if (!this.token || !this.deviceId) return;
    this.manualClose = false;
    this.replaceConnection();
  }

  private replaceConnection() {
    this.generation += 1;
    this.clearReconnectTimer();
    this.stopHeartbeat();
    const previous = this.socket;
    this.socket = null;
    this.activeToken = null;
    this.activeDeviceId = null;
    this.grants.clear();
    this.pendingGrantRequests.clear();
    try { previous?.close(1000, "credentials-updated"); } catch { /* no-op */ }
    void this.openConnection();
  }

  private async openConnection() {
    if (!this.token || !this.deviceId || this.manualClose) return;
    const generation = ++this.generation;
    const token = this.token;
    const deviceId = this.deviceId;
    this.emitStatus("connecting");
    try {
      const audiences = [...this.subscriptions.keys()].map(conversationAudience);
      const [ticket, grants] = await Promise.all([
        requestRealtimeTicket(token, deviceId, detectPresenceDeviceType()),
        requestRealtimeGrants(token, audiences),
      ]);
      if (generation !== this.generation || this.manualClose || token !== this.token) return;
      this.grants = grants;
      const socket = new WebSocket(buildSocketUrl(ticket.ticket));
      this.socket = socket;
      this.activeToken = token;
      this.activeDeviceId = deviceId;
      this.attachSocketHandlers(socket, generation);
    } catch (error) {
      if (generation !== this.generation || this.manualClose) return;
      this.emitStatus("closed");
      const status = Number((error as { response?: { status?: number } })?.response?.status || 0);
      if (status === 401 || status === 403) {
        window.dispatchEvent(new CustomEvent(SOCKET_AUTH_FAILED_EVENT, { detail: { code: status } }));
      }
      this.scheduleReconnect();
    }
  }

  private attachSocketHandlers(socket: WebSocket, generation: number) {
    socket.onopen = () => {
      if (this.socket !== socket || generation !== this.generation) return;
      this.reconnectAttempts = 0;
      this.emitStatus("open");
      this.startHeartbeat();
      this.subscriptions.forEach((_count, conversationId) => void this.subscribeAudience(conversationId));
      const queued = [...this.pendingQueue];
      this.pendingQueue = [];
      queued.forEach((payload) => this.send(payload));
    };
    socket.onerror = () => { if (this.socket === socket) this.emitStatus("closed"); };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.stopHeartbeat();
      this.socket = null;
      this.activeToken = null;
      this.activeDeviceId = null;
      this.grants.clear();
      this.emitStatus("closed");
      if (!this.manualClose) this.scheduleReconnect();
    };
    socket.onmessage = (event) => {
      if (this.socket !== socket) return;
      try {
        const parsed = JSON.parse(event.data) as SocketEvent;
        if (!parsed?.event || this.isDuplicateIncomingEvent(parsed)) return;
        this.listeners.forEach((listener) => listener(parsed));
      } catch { /* malformed frames are ignored */ }
    };
  }

  private async grantForConversation(conversationId: string) {
    const audience = conversationAudience(conversationId);
    const key = realtimeAudienceKey(audience);
    const cached = this.grants.get(key);
    if (cached) return cached;
    const existing = this.pendingGrantRequests.get(key);
    if (existing) return existing;
    const token = this.token;
    if (!token) return null;
    const request = requestRealtimeGrants(token, [audience])
      .then((grants) => {
        const grant = grants.get(key) || null;
        if (grant) this.grants.set(key, grant);
        return grant;
      })
      .catch(() => null)
      .finally(() => this.pendingGrantRequests.delete(key));
    this.pendingGrantRequests.set(key, request);
    return request;
  }

  private async subscribeAudience(conversationId: string) {
    if (!this.subscriptions.has(conversationId) || this.socket?.readyState !== WebSocket.OPEN) return;
    const grant = await this.grantForConversation(conversationId);
    if (!grant || !this.subscriptions.has(conversationId)) return;
    this.sendWire({
      event: "audience.subscribe",
      data: { audience: conversationAudience(conversationId), grant },
    });
  }

  private isDuplicateIncomingEvent(payload: SocketEvent) {
    const now = Date.now();
    const key = incomingEventKey(payload);
    const previous = this.seenEvents.get(key);
    this.seenEvents.set(key, now);
    if (this.seenEvents.size > 500) {
      for (const [eventKey, seenAt] of this.seenEvents) if (now - seenAt > 120000) this.seenEvents.delete(eventKey);
      while (this.seenEvents.size > 500) {
        const first = this.seenEvents.keys().next().value as string | undefined;
        if (!first) break;
        this.seenEvents.delete(first);
      }
    }
    return previous !== undefined && now - previous < 120000;
  }

  private startHeartbeat() {
    this.stopHeartbeat();
    this.startActivityTracking();
    this.sendPresencePing();
    this.heartbeatTimer = window.setInterval(() => this.sendPresencePing(), 25000);
  }

  private stopHeartbeat() {
    if (this.heartbeatTimer) window.clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
    this.stopActivityTracking();
  }

  private clearReconnectTimer() {
    if (this.reconnectTimer) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private scheduleReconnect() {
    if (this.reconnectTimer || !this.token || !this.deviceId || this.manualClose) return;
    const base = Math.min(30000, 750 * 2 ** Math.min(this.reconnectAttempts, 6));
    const delay = Math.round(base * (0.75 + Math.random() * 0.5));
    this.reconnectAttempts += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      void this.openConnection();
    }, delay);
  }

  disconnect() {
    this.manualClose = true;
    this.generation += 1;
    this.stopHeartbeat();
    this.clearReconnectTimer();
    this.reconnectAttempts = 0;
    const previous = this.socket;
    this.socket = null;
    this.token = null;
    this.deviceId = null;
    this.activeToken = null;
    this.activeDeviceId = null;
    this.grants.clear();
    this.pendingGrantRequests.clear();
    try { previous?.close(1000, "client-disconnect"); } catch { /* no-op */ }
    this.pendingQueue = [];
    this.emitStatus("closed");
  }

  isOpen() { return this.socket?.readyState === WebSocket.OPEN; }
  reportActivity() { this.handleUserActivity(); }

  send(payload: SocketEvent) {
    if (this.sendWire(payload)) return true;
    if (["typing.start", "typing.stop", "presence.ping", "call.signal"].includes(payload.event)) return false;
    const key = queueKey(payload);
    if (this.pendingQueue.some((entry) => queueKey(entry) === key)) return false;
    this.pendingQueue = [...this.pendingQueue.slice(-49), payload];
    return false;
  }

  subscribeToConversation(conversationId: string) {
    const currentCount = this.subscriptions.get(conversationId) ?? 0;
    this.subscriptions.set(conversationId, currentCount + 1);
    if (currentCount === 0) void this.subscribeAudience(conversationId);
  }

  unsubscribeFromConversation(conversationId: string) {
    const currentCount = this.subscriptions.get(conversationId) ?? 0;
    if (currentCount > 1) {
      this.subscriptions.set(conversationId, currentCount - 1);
      return;
    }
    this.subscriptions.delete(conversationId);
    this.grants.delete(realtimeAudienceKey(conversationAudience(conversationId)));
    this.sendWire({ event: "audience.unsubscribe", data: { audience: conversationAudience(conversationId) } });
  }

  subscribe(listener: Listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  subscribeStatus(listener: StatusListener) {
    this.statusListeners.add(listener);
    listener(this.socket?.readyState === WebSocket.OPEN ? "open" : this.socket?.readyState === WebSocket.CONNECTING ? "connecting" : "closed");
    return () => this.statusListeners.delete(listener);
  }

  private emitStatus(status: SocketStatus) {
    this.statusListeners.forEach((listener) => listener(status));
  }
}

export const chatSocket = new ChatSocket();
