// ─── Engine API Types ─────────────────────────────────────────────────────────

export interface HealthResponse {
  status: string;
  engine_running: boolean;
  kill_switch_active: boolean;
}

export interface StatusResponse {
  running: boolean;
  kill_switch_active: boolean;
  environment: string;
  execution_mode: string;
  strategy_count: number;
  position_count: number;
  connection_count: number;
}

export interface KillResponse {
  status: "halted";
  reason: string;
}

export interface Strategy {
  id: string;
  type: string;
  enabled: boolean;
  metrics?: Record<string, number>;
  [key: string]: unknown;
}

export interface ToggleResponse {
  id: string;
  enabled: boolean;
}

export interface Position {
  strategy_id: string;
  exchange_id: string;
  symbol: string;
  side: string;
  quantity: number;
  entry_price: number;
  mark_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
}

export interface PnlResponse {
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
}

export interface RiskMetrics {
  kill_switch_active: boolean;
  circuit_breaker_state: string;
  max_drawdown_pct: number;
  daily_loss_pct: number;
  position_count: number;
  correlation_alert: boolean;
  [key: string]: unknown;
}

export interface ModeResponse {
  mode: string;
  data_mode: string;
  shadow_active: boolean;
  live_gate_eligible: boolean;
}

// ─── WebSocket Message Types ──────────────────────────────────────────────────

export type WsMessageType =
  | "heartbeat"
  | "state_update"
  | "market_data"
  | "position_update"
  | "pnl_update"
  | "ack"
  | "error";

export interface WsMessage<T = unknown> {
  type: WsMessageType;
  ts?: number;
  data?: T;
}

export interface StateUpdateData {
  running: boolean;
  kill_switch: boolean;
  mode: string;
  strategy_count: number;
  strategies: { id: string; enabled: boolean; type: string }[];
  pnl: { realized: number; unrealized: number; total: number };
  positions: { strategy_id: string; exchange_id: string; symbol: string; side: string; pnl: number }[];
  position_count: number;
}

export interface HeartbeatMessage extends WsMessage {
  type: "heartbeat";
}

export interface StateUpdateMessage extends WsMessage<StateUpdateData> {
  type: "state_update";
}

// ─── Trade & Alert Types ──────────────────────────────────────────────────────

export interface Trade {
  id: string;
  strategy_id: string;
  symbol: string;
  buy_exchange: string;
  sell_exchange: string;
  side: string;
  size: number;
  entry_price: number;
  exit_price: number;
  pnl: number;
  timestamp: string;
  status: string;
}

export interface Alert {
  id: string;
  type: string;
  severity: "critical" | "warning" | "info";
  message: string;
  timestamp: string;
  metadata?: Record<string, unknown>;
}

// ─── Settings Types ───────────────────────────────────────────────────────────

export interface SettingsResponse {
  min_edge_bps: number;
  active_strategies: { id: string; type: string; enabled: boolean }[];
  active_exchanges: string[];
}

// ─── UI State Types ───────────────────────────────────────────────────────────

export type ConnectionState = "connecting" | "connected" | "disconnected" | "error";
