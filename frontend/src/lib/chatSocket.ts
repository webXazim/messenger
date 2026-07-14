import { WS_BASE_URL } from "./config";

export type SocketEvent = {
  event: string;
  event_id?: string;
  occurred_at?: string;
  data?: Record<string, unknown>;
};

export type SocketStatus = "connecting" | "open" | "closed";

type Listener = (event: SocketEvent) => void;
type StatusListener = (status: SocketStatus) => void;

export const SOCKET_AUTH_FAILED_EVENT = "messenger-socket-auth-failed";

function buildSocketUrl(baseUrl: string, token: string, deviceId: string) {
  const normalizedBase = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
  const url = new URL(normalizedBase);
  url.searchParams.set("token", token);
  url.searchParams.set("device_id", deviceId);
  return url.toString();
}

function queueKey(payload: SocketEvent) {
  return [
    payload.event,
    String(payload.data?.conversation_id ?? ""),
    String(payload.data?.call_id ?? ""),
    String(payload.data?.signal_type ?? ""),
    String(payload.data?.signal_id ?? payload.data?.client_temp_id ?? ""),
    String(payload.data?.message_id ?? payload.data?.id ?? ""),
    String(payload.data?.client_temp_id ?? ""),
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
  private pendingQueue: SocketEvent[] = [];
  private heartbeatTimer: number | null = null;
  private seenEvents = new Map<string, number>();

  connect(token: string, deviceId: string) {
    const credentialsChanged = token !== this.token || deviceId !== this.deviceId;
    this.token = token;
    this.deviceId = deviceId;
    this.manualClose = false;

    const activeCredentialsMatch = token === this.activeToken && deviceId === this.activeDeviceId;
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      if (activeCredentialsMatch && !credentialsChanged) return;
      this.replaceConnection();
      return;
    }

    this.clearReconnectTimer();
    this.openConnection();
  }

  reconnect() {
    if (!this.token || !this.deviceId) return;
    this.manualClose = false;
    this.replaceConnection();
  }

  private replaceConnection() {
    this.clearReconnectTimer();
    this.stopHeartbeat();
    const previous = this.socket;
    this.socket = null;
    this.activeToken = null;
    this.activeDeviceId = null;
    try {
      previous?.close(1000, "credentials-updated");
    } catch {
      // The next connection still proceeds when the old socket is already gone.
    }
    this.openConnection();
  }

  private openConnection() {
    if (!this.token || !this.deviceId || this.manualClose) return;
    this.emitStatus("connecting");

    const token = this.token;
    const deviceId = this.deviceId;
    let socket: WebSocket;
    try {
      socket = new WebSocket(buildSocketUrl(WS_BASE_URL, token, deviceId));
    } catch {
      this.emitStatus("closed");
      this.scheduleReconnect();
      return;
    }

    this.socket = socket;
    this.activeToken = token;
    this.activeDeviceId = deviceId;

    socket.onopen = () => {
      if (this.socket !== socket) return;
      this.reconnectAttempts = 0;
      this.emitStatus("open");
      this.startHeartbeat();
      this.subscriptions.forEach((_count, conversationId) => {
        this.send({ event: "conversation.subscribe", data: { conversation_id: conversationId } });
      });
      const queued = [...this.pendingQueue];
      this.pendingQueue = [];
      queued.forEach((payload) => this.send(payload));
    };

    socket.onerror = () => {
      if (this.socket === socket) this.emitStatus("closed");
    };

    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      this.stopHeartbeat();
      this.emitStatus("closed");
      this.socket = null;
      this.activeToken = null;
      this.activeDeviceId = null;
      if (event.code === 4401 || event.code === 4403) {
        window.dispatchEvent(new CustomEvent(SOCKET_AUTH_FAILED_EVENT, { detail: { code: event.code } }));
      }
      if (!this.manualClose) this.scheduleReconnect();
    };

    socket.onmessage = (event) => {
      if (this.socket !== socket) return;
      try {
        const parsed = JSON.parse(event.data) as SocketEvent;
        if (!parsed?.event || this.isDuplicateIncomingEvent(parsed)) return;
        this.listeners.forEach((listener) => listener(parsed));
      } catch {
        // Ignore malformed payloads without breaking the live connection.
      }
    };
  }

  private isDuplicateIncomingEvent(payload: SocketEvent) {
    const now = Date.now();
    const key = incomingEventKey(payload);
    const previous = this.seenEvents.get(key);
    this.seenEvents.set(key, now);
    if (this.seenEvents.size > 500) {
      for (const [eventKey, seenAt] of this.seenEvents) {
        if (now - seenAt > 120000) this.seenEvents.delete(eventKey);
      }
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
    this.send({ event: "presence.ping", data: {} });
    this.heartbeatTimer = window.setInterval(() => {
      if (this.socket?.readyState === WebSocket.OPEN) {
        this.socket.send(JSON.stringify({ event: "presence.ping", data: {} }));
      }
    }, 25000);
  }

  private stopHeartbeat() {
    if (this.heartbeatTimer) {
      window.clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private clearReconnectTimer() {
    if (this.reconnectTimer) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer || !this.token || !this.deviceId || this.manualClose) return;
    const delay = Math.min(15000, 750 * 2 ** Math.min(this.reconnectAttempts, 5));
    this.reconnectAttempts += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.openConnection();
    }, delay);
  }

  disconnect() {
    this.manualClose = true;
    this.stopHeartbeat();
    this.clearReconnectTimer();
    this.reconnectAttempts = 0;
    const previous = this.socket;
    this.socket = null;
    this.token = null;
    this.deviceId = null;
    this.activeToken = null;
    this.activeDeviceId = null;
    try {
      previous?.close(1000, "client-disconnect");
    } catch {
      // The local session is already cleared even if the transport is gone.
    }
    this.pendingQueue = [];
    this.emitStatus("closed");
  }

  isOpen() {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  send(payload: SocketEvent) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload));
      return true;
    }

    if (payload.event === "typing.start" || payload.event === "typing.stop") return false;

    const key = queueKey(payload);
    if (this.pendingQueue.some((entry) => queueKey(entry) === key)) return false;
    this.pendingQueue = [...this.pendingQueue.slice(-49), payload];
    return false;
  }

  subscribeToConversation(conversationId: string) {
    const currentCount = this.subscriptions.get(conversationId) ?? 0;
    this.subscriptions.set(conversationId, currentCount + 1);
    if (currentCount > 0) return;
    this.send({ event: "conversation.subscribe", data: { conversation_id: conversationId } });
  }

  unsubscribeFromConversation(conversationId: string) {
    const currentCount = this.subscriptions.get(conversationId) ?? 0;
    if (currentCount > 1) {
      this.subscriptions.set(conversationId, currentCount - 1);
      return;
    }
    this.subscriptions.delete(conversationId);
    this.send({ event: "conversation.unsubscribe", data: { conversation_id: conversationId } });
  }

  subscribe(listener: Listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  subscribeStatus(listener: StatusListener) {
    this.statusListeners.add(listener);
    listener(
      this.socket?.readyState === WebSocket.OPEN
        ? "open"
        : this.socket?.readyState === WebSocket.CONNECTING
          ? "connecting"
          : "closed",
    );
    return () => this.statusListeners.delete(listener);
  }

  private emitStatus(status: SocketStatus) {
    this.statusListeners.forEach((listener) => listener(status));
  }
}

export const chatSocket = new ChatSocket();
