import { SUPPORT_WS_URL } from "./config";

export type SupportSocketEvent = {
  event: string;
  event_id?: string;
  occurred_at?: string;
  data?: Record<string, unknown>;
};

export type SupportSocketStatus = "connecting" | "open" | "closed";
type EventListener = (event: SupportSocketEvent) => void;
type StatusListener = (status: SupportSocketStatus) => void;

export const SUPPORT_SOCKET_AUTH_FAILED_EVENT = "support-socket-auth-failed";

function socketUrl(token: string) {
  const separator = SUPPORT_WS_URL.includes("?") ? "&" : "?";
  return `${SUPPORT_WS_URL}${separator}token=${encodeURIComponent(token)}`;
}

class SupportSocketClient {
  private socket: WebSocket | null = null;
  private token = "";
  private activeToken = "";
  private reconnectTimer: number | null = null;
  private heartbeatTimer: number | null = null;
  private reconnectAttempts = 0;
  private manualClose = false;
  private listeners = new Set<EventListener>();
  private statusListeners = new Set<StatusListener>();
  private status: SupportSocketStatus = "closed";
  private seenEvents = new Map<string, number>();

  connect(token: string) {
    const normalized = String(token || "");
    if (!normalized) return this.disconnect();
    this.token = normalized;
    this.manualClose = false;
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING) && this.activeToken === normalized) return;
    this.replaceConnection();
  }

  reconnect() {
    if (!this.token) return;
    this.manualClose = false;
    this.replaceConnection();
  }

  disconnect() {
    this.manualClose = true;
    this.clearReconnect();
    this.stopHeartbeat();
    const current = this.socket;
    this.socket = null;
    this.activeToken = "";
    try { current?.close(1000, "support-disconnect"); } catch { /* no-op */ }
    this.emitStatus("closed");
  }

  isOpen() {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  send(payload: SupportSocketEvent) {
    if (this.socket?.readyState !== WebSocket.OPEN) return false;
    this.socket.send(JSON.stringify(payload));
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
    this.clearReconnect();
    this.stopHeartbeat();
    const previous = this.socket;
    this.socket = null;
    this.activeToken = "";
    try { previous?.close(1000, "support-credentials-updated"); } catch { /* no-op */ }
    this.open();
  }

  private open() {
    if (!this.token || this.manualClose) return;
    this.emitStatus("connecting");
    let socket: WebSocket;
    try { socket = new WebSocket(socketUrl(this.token)); }
    catch { this.emitStatus("closed"); this.scheduleReconnect(); return; }
    this.socket = socket;
    this.activeToken = this.token;

    socket.onopen = () => {
      if (this.socket !== socket) return;
      this.reconnectAttempts = 0;
      this.emitStatus("open");
      this.startHeartbeat();
    };
    socket.onerror = () => { if (this.socket === socket) this.emitStatus("closed"); };
    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      this.stopHeartbeat();
      this.socket = null;
      this.activeToken = "";
      this.emitStatus("closed");
      if (event.code === 4401 || event.code === 4403) {
        window.dispatchEvent(new CustomEvent(SUPPORT_SOCKET_AUTH_FAILED_EVENT, { detail: { code: event.code } }));
      }
      if (!this.manualClose) this.scheduleReconnect();
    };
    socket.onmessage = (event) => {
      if (this.socket !== socket) return;
      try {
        const payload = JSON.parse(event.data) as SupportSocketEvent;
        if (!payload?.event || this.isDuplicate(payload)) return;
        this.listeners.forEach((listener) => listener(payload));
      } catch { /* malformed events cannot break reconnect */ }
    };
  }

  private sendPing() {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ event: "support.ping", data: {} }));
    }
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

  private scheduleReconnect() {
    if (this.reconnectTimer || !this.token || this.manualClose) return;
    const delay = Math.min(15000, 750 * 2 ** Math.min(this.reconnectAttempts, 5));
    this.reconnectAttempts += 1;
    this.reconnectTimer = window.setTimeout(() => { this.reconnectTimer = null; this.open(); }, delay);
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
