import type { ConnectionState, WsMessage } from "@/types";

export type MessageHandler = (msg: WsMessage) => void;
export type StateHandler   = (state: ConnectionState) => void;

const HEARTBEAT_TIMEOUT_MS = 30_000;
const INITIAL_BACKOFF_MS   = 500;
const MAX_BACKOFF_MS       = 30_000;

export class WebSocketManager {
  private url: string;
  private ws: WebSocket | null = null;
  private messageHandlers: Set<MessageHandler> = new Set();
  private stateHandlers:   Set<StateHandler>   = new Set();
  private state: ConnectionState = "disconnected";
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setTimeout> | null = null;
  private backoffMs = INITIAL_BACKOFF_MS;
  private destroyed = false;

  constructor(url: string) {
    this.url = url;
  }

  connect(): void {
    if (this.destroyed) return;
    this.clearReconnect();
    this.setState("connecting");

    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.backoffMs = INITIAL_BACKOFF_MS;
      this.setState("connected");
      this.resetHeartbeat();
    };

    this.ws.onmessage = (event: MessageEvent) => {
      this.resetHeartbeat();
      try {
        const msg = JSON.parse(event.data as string) as WsMessage;
        this.messageHandlers.forEach((h) => h(msg));
      } catch {
        // ignore malformed frames
      }
    };

    this.ws.onerror = () => {
      this.setState("error");
    };

    this.ws.onclose = () => {
      this.clearHeartbeat();
      if (!this.destroyed) {
        this.setState("disconnected");
        this.scheduleReconnect();
      }
    };
  }

  send(data: unknown): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  disconnect(): void {
    this.destroyed = true;
    this.clearReconnect();
    this.clearHeartbeat();
    this.ws?.close();
    this.ws = null;
  }

  onMessage(handler: MessageHandler): () => void {
    this.messageHandlers.add(handler);
    return () => this.messageHandlers.delete(handler);
  }

  onStateChange(handler: StateHandler): () => void {
    this.stateHandlers.add(handler);
    return () => this.stateHandlers.delete(handler);
  }

  getState(): ConnectionState {
    return this.state;
  }

  // ─── Private ────────────────────────────────────────────────────────────────

  private setState(next: ConnectionState): void {
    if (this.state === next) return;
    this.state = next;
    this.stateHandlers.forEach((h) => h(next));
  }

  private scheduleReconnect(): void {
    this.reconnectTimer = setTimeout(() => {
      this.backoffMs = Math.min(this.backoffMs * 2, MAX_BACKOFF_MS);
      this.connect();
    }, this.backoffMs);
  }

  private clearReconnect(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private resetHeartbeat(): void {
    this.clearHeartbeat();
    this.heartbeatTimer = setTimeout(() => {
      // No heartbeat received — connection is stale
      this.ws?.close();
    }, HEARTBEAT_TIMEOUT_MS);
  }

  private clearHeartbeat(): void {
    if (this.heartbeatTimer !== null) {
      clearTimeout(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }
}

// Singleton instances for the two WS endpoints
let feedManager:    WebSocketManager | null = null;
let controlManager: WebSocketManager | null = null;

export function getFeedManager(): WebSocketManager {
  if (!feedManager) {
    const url = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";
    feedManager = new WebSocketManager(`${url}/ws/feed`);
  }
  return feedManager;
}

export function getControlManager(): WebSocketManager {
  if (!controlManager) {
    const url = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";
    controlManager = new WebSocketManager(`${url}/ws`);
  }
  return controlManager;
}
