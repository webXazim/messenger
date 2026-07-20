import { REALTIME_WS_URL } from "./config";
import { detectPresenceDeviceType } from "./devicePresence";
import {
  requestRealtimeGrants,
  requestRealtimeTicket,
  realtimeAudienceKey,
  type RealtimeAudience,
} from "./realtimeCredentials";

export type SupportSocketEvent = {
  event: string;
  event_id?: string;
  occurred_at?: string;
  request_id?: string;
  data?: Record<string, unknown>;
};

export type SupportSocketStatus = "connecting" | "open" | "closed";
type EventListener = (event: SupportSocketEvent) => void;
type StatusListener = (status: SupportSocketStatus) => void;

export const SUPPORT_SOCKET_AUTH_FAILED_EVENT = "support-socket-auth-failed";

function requestId() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function websiteAudience(websiteId: string): RealtimeAudience {
  return { kind: "support_website", id: websiteId };
}

function socketUrl(ticket: string) {
  const url = new URL(REALTIME_WS_URL, window.location.origin);
  url.searchParams.set("ticket", ticket);
  return url.toString();
}

class SupportSocketClient {
  private socket: WebSocket | null = null;
  private token = "";
  private activeToken = "";
  private websiteIds = new Set<string>();
  private grants = new Map<string, string>();
  private reconnectTimer: number | null = null;
  private heartbeatTimer: number | null = null;
  private connectionTimer: number | null = null;
  private lastPongAt = 0;
  private reconnectAttempts = 0;
  private manualClose = false;
  private listeners = new Set<EventListener>();
  private statusListeners = new Set<StatusListener>();
  private status: SupportSocketStatus = "closed";
  private seenEvents = new Map<string, number>();
  private generation = 0;
  private connectedOnce = false;

  connect(token: string, websiteIds: string[] = []) {
    const normalized = String(token || "");
    if (!normalized) return this.disconnect();
    const nextWebsiteIds = new Set(websiteIds.filter(Boolean));
    const websitesChanged = [...nextWebsiteIds].sort().join(",") !== [...this.websiteIds].sort().join(",");
    const tokenChanged = normalized !== this.token;
    this.token = normalized;
    this.websiteIds = nextWebsiteIds;
    this.manualClose = false;
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      if (!tokenChanged && !websitesChanged && this.activeToken === normalized) return;
    }
    this.replaceConnection();
  }

  reconnect() {
    if (!this.token) return;
    this.manualClose = false;
    this.replaceConnection();
  }

  disconnect() {
    this.manualClose = true;
    this.generation += 1;
    this.clearReconnect();
    this.stopHeartbeat();
    this.clearConnectionTimer();
    const current = this.socket;
    this.socket = null;
    this.activeToken = "";
    this.grants.clear();
    this.connectedOnce = false;
    try { current?.close(1000, "support-disconnect"); } catch { /* no-op */ }
    this.emitStatus("closed");
  }

  isOpen() { return this.socket?.readyState === WebSocket.OPEN; }

  send(payload: SupportSocketEvent) {
    if (this.socket?.readyState !== WebSocket.OPEN) return false;
    this.socket.send(JSON.stringify({ v: 1, request_id: payload.request_id || requestId(), ...payload }));
    return true;
  }

  subscribe(listener: EventListener) {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }

  subscribeStatus(listener: StatusListener) {
    this.statusListeners.add(listener);
    listener(this.status);
    return () => { this.statusListeners.delete(listener); };
  }

  private replaceConnection() {
    this.generation += 1;
    this.clearReconnect();
    this.stopHeartbeat();
    this.clearConnectionTimer();
    const previous = this.socket;
    this.socket = null;
    this.activeToken = "";
    this.grants.clear();
    try { previous?.close(1000, "support-credentials-updated"); } catch { /* no-op */ }
    void this.open();
  }

  private async open() {
    if (!this.token || this.manualClose) return;
    const generation = ++this.generation;
    const token = this.token;
    const audiences = [...this.websiteIds].map(websiteAudience);
    this.emitStatus("connecting");
    try {
      const [ticket, grants] = await Promise.all([
        requestRealtimeTicket(token, `support:${detectPresenceDeviceType()}`, detectPresenceDeviceType()),
        requestRealtimeGrants(token, audiences),
      ]);
      if (generation !== this.generation || this.manualClose || token !== this.token) return;
      this.grants = grants;
      const socket = new WebSocket(socketUrl(ticket.ticket));
      this.socket = socket;
      this.activeToken = token;
      this.attachSocketHandlers(socket, generation);
      this.connectionTimer = window.setTimeout(() => {
        if (this.socket !== socket || socket.readyState === WebSocket.OPEN) return;
        try { socket.close(); } catch { /* no-op */ }
        this.socket = null;
        this.activeToken = "";
        this.emitStatus("closed");
        this.scheduleReconnect();
      }, 10000);
    } catch (error) {
      if (generation !== this.generation || this.manualClose) return;
      this.emitStatus("closed");
      const status = Number((error as { response?: { status?: number } })?.response?.status || 0);
      if (status === 401 || status === 403) {
        window.dispatchEvent(new CustomEvent(SUPPORT_SOCKET_AUTH_FAILED_EVENT, { detail: { code: status } }));
      }
      this.scheduleReconnect();
    }
  }

  private attachSocketHandlers(socket: WebSocket, generation: number) {
    socket.onopen = () => {
      if (this.socket !== socket || generation !== this.generation) return;
      this.clearConnectionTimer();
      this.lastPongAt = Date.now();
      this.reconnectAttempts = 0;
      this.emitStatus("open");
      for (const websiteId of this.websiteIds) {
        const audience = websiteAudience(websiteId);
        const grant = this.grants.get(realtimeAudienceKey(audience));
        if (grant) this.send({ event: "audience.subscribe", data: { audience, grant } });
      }
      this.startHeartbeat();
    };
    socket.onerror = () => { if (this.socket === socket) this.emitStatus("closed"); };
    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      this.clearConnectionTimer();
      this.stopHeartbeat();
      this.socket = null;
      this.activeToken = "";
      this.grants.clear();
      this.emitStatus("closed");
      if (!this.manualClose && event.code === 4001) {
        window.dispatchEvent(new CustomEvent(SUPPORT_SOCKET_AUTH_FAILED_EVENT, {
          detail: { code: 401, closeCode: event.code, reason: event.reason },
        }));
      }
      if (!this.manualClose) this.scheduleReconnect();
    };
    socket.onmessage = (event) => {
      if (this.socket !== socket) return;
      try {
        const payload = JSON.parse(event.data) as SupportSocketEvent;
        if (!payload?.event || this.isDuplicate(payload)) return;
        this.lastPongAt = Date.now();
        if (payload.event === "connection.ready") {
          const ready: SupportSocketEvent = {
            event: "support.ready",
            event_id: `support-ready:${String(payload.data?.connection_id || Date.now())}`,
            occurred_at: payload.occurred_at || new Date().toISOString(),
            data: { reconnected: this.connectedOnce },
          };
          this.connectedOnce = true;
          this.listeners.forEach((listener) => listener(ready));
        }
        this.listeners.forEach((listener) => listener(payload));
      } catch { /* malformed events cannot break reconnect */ }
    };
  }

  private sendPing() {
    if (
      this.socket?.readyState === WebSocket.OPEN
      && this.lastPongAt
      && Date.now() - this.lastPongAt > 55000
    ) {
      try { this.socket.close(); } catch { /* no-op */ }
      return;
    }
    this.send({ event: "support.ping", data: {} });
  }

  private startHeartbeat() {
    this.stopHeartbeat();
    this.sendPing();
    this.heartbeatTimer = window.setInterval(() => this.sendPing(), 25000);
  }

  private stopHeartbeat() {
    if (this.heartbeatTimer) window.clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  private clearConnectionTimer() {
    if (this.connectionTimer) window.clearTimeout(this.connectionTimer);
    this.connectionTimer = null;
  }

  private scheduleReconnect() {
    if (this.reconnectTimer || !this.token || this.manualClose) return;
    const base = Math.min(30000, 750 * 2 ** Math.min(this.reconnectAttempts, 6));
    const delay = Math.round(base * (0.75 + Math.random() * 0.5));
    this.reconnectAttempts += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      void this.open();
    }, delay);
  }

  private clearReconnect() {
    if (this.reconnectTimer) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private emitStatus(status: SupportSocketStatus) {
    this.status = status;
    this.statusListeners.forEach((listener) => listener(status));
  }

  private isDuplicate(payload: SupportSocketEvent) {
    const key = payload.event_id || `${payload.event}:${String(payload.data?.conversation_id || "")}:${String(payload.data?.message_id || "")}:${String(payload.occurred_at || "")}`;
    const now = Date.now();
    const prior = this.seenEvents.get(key);
    this.seenEvents.set(key, now);
    if (this.seenEvents.size > 300) {
      for (const [eventKey, timestamp] of this.seenEvents) if (now - timestamp > 120000) this.seenEvents.delete(eventKey);
    }
    return prior !== undefined && now - prior < 120000;
  }
}

export const supportSocket = new SupportSocketClient();
