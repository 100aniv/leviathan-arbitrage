// ─── Engine API Types ─────────────────────────────────────────────────────────

export interface HealthResponse {
  status: "healthy" | "degraded" | "unhealthy";
  timestamp: string;
}

export interface StatusResponse {
  running: boolean;
  kill_switch_active: boolean;
  environment: string;
  strategy_count: number;
  uptime_seconds: number;
}

export interface KillResponse {
  status: "halted";
  reason: string;
}

export interface Strategy {
  id: string;
  name: string;
  enabled: boolean;
  exchange_a?: string;
  exchange_b?: string;
  symbol?: string;
  [key: string]: unknown;
}

export interface ToggleResponse {
  id: string;
  enabled: boolean;
}

export interface Position {
  strategy: string;
  exchange: string;
  symbol: string;
  size: number;
  entry_price: number;
  unrealized_pnl: number;
}

export interface PnlResponse {
  realized: number;
  unrealized: number;
  total: number;
}

export interface RiskMetrics {
  drawdown: number;
  exposure_by_exchange: Record<string, number>;
  [key: string]: unknown;
}

// ─── WebSocket Message Types ──────────────────────────────────────────────────

export type WsMessageType =
  | "heartbeat"
  | "market_data"
  | "position_update"
  | "pnl_update"
  | "ack"
  | "error";

export interface WsMessage<T = unknown> {
  type: WsMessageType;
  timestamp?: string;
  data?: T;
}

export interface HeartbeatMessage extends WsMessage {
  type: "heartbeat";
}

export interface PositionUpdateMessage extends WsMessage<Position[]> {
  type: "position_update";
}

export interface PnlUpdateMessage extends WsMessage<PnlResponse> {
  type: "pnl_update";
}

// ─── UI State Types ───────────────────────────────────────────────────────────

export type ConnectionState = "connecting" | "connected" | "disconnected" | "error";
