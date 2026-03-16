import type {
  HealthResponse,
  StatusResponse,
  KillResponse,
  Strategy,
  ToggleResponse,
  Position,
  PnlResponse,
  RiskMetrics,
  ModeResponse,
  Trade,
  Alert,
  SettingsResponse,
  StrategyMetric,
  FundingRate,
  ExchangeStatus,
  AttributionResponse,
  ShadowStats,
  PortfolioSummaryResponse,
  TCASummary,
  ContainerStatus,
  SystemResources,
} from "@/types";

const BASE_URL =
  process.env.NEXT_PUBLIC_ENGINE_URL ?? "http://localhost:8000";

function getAuthHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const token = localStorage.getItem("leviathan_token");
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

async function request<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
    },
    ...options,
  });
  if (res.status === 401) {
    // Clear stale token and redirect to login
    if (typeof window !== "undefined") {
      localStorage.removeItem("leviathan_token");
      document.cookie = "leviathan_token=; path=/; max-age=0";
      window.location.href = "/login";
    }
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    throw new Error(`Engine API error ${res.status}: ${path}`);
  }
  return res.json() as Promise<T>;
}

// ─── Health & Status ──────────────────────────────────────────────────────────

export const getHealth = () =>
  request<HealthResponse>("/health");

export const getStatus = () =>
  request<StatusResponse>("/api/v1/status");

// ─── Kill Switch ──────────────────────────────────────────────────────────────

export const killEngine = (reason: string) =>
  request<KillResponse>("/api/v1/kill-switch", {
    method: "POST",
    body: JSON.stringify({ reason }),
  });

// ─── Strategies ───────────────────────────────────────────────────────────────

export const getStrategies = () =>
  request<Strategy[]>("/api/v1/strategies");

export const toggleStrategy = (id: string) =>
  request<ToggleResponse>(`/api/v1/strategies/${id}/toggle`, { method: "POST" });

// ─── Trading ──────────────────────────────────────────────────────────────────

export const getPositions = () =>
  request<Position[]>("/api/v1/positions");

export const getPnl = () =>
  request<PnlResponse>("/api/v1/pnl");

// ─── Risk ─────────────────────────────────────────────────────────────────────

export const getRiskMetrics = () =>
  request<RiskMetrics>("/api/v1/risk/metrics");

// ─── Mode ─────────────────────────────────────────────────────────────────────

export const getMode = () =>
  request<ModeResponse>("/api/v1/mode");

// ─── Trades ───────────────────────────────────────────────────────────────────

export const getTrades = (params?: {
  strategy?: string;
  exchange?: string;
  symbol?: string;
  from?: string;
  to?: string;
  limit?: number;
}) => {
  const qs = new URLSearchParams();
  if (params?.strategy) qs.set("strategy", params.strategy);
  if (params?.exchange) qs.set("exchange", params.exchange);
  if (params?.symbol) qs.set("symbol", params.symbol);
  if (params?.from) qs.set("from", params.from);
  if (params?.to) qs.set("to", params.to);
  if (params?.limit) qs.set("limit", String(params.limit));
  return request<Trade[]>(`/api/v1/trades?${qs}`);
};

// ─── Alerts ───────────────────────────────────────────────────────────────────

export const getAlerts = () =>
  request<Alert[]>("/api/v1/alerts");

export const acknowledgeAlert = (id: string) =>
  request<{ id: string; status: string }>(`/api/v1/alerts/${id}/acknowledge`, { method: "POST" });

export const resolveAlert = (id: string) =>
  request<{ id: string; status: string }>(`/api/v1/alerts/${id}/resolve`, { method: "POST" });

// ─── Settings ─────────────────────────────────────────────────────────────────

export const getSettings = () =>
  request<SettingsResponse>("/api/v1/settings");

export const updateSettings = (settings: Partial<SettingsResponse>) =>
  request<SettingsResponse>("/api/v1/settings", {
    method: "PUT",
    body: JSON.stringify(settings),
  });

// ─── Analytics ────────────────────────────────────────────────────────────────

export const getStrategyMetrics = () =>
  request<{ strategies: Record<string, StrategyMetric> }>("/api/v1/strategy-metrics");

export const getFundingRates = () =>
  request<Record<string, Record<string, FundingRate>>>("/api/v1/funding-rates");

// ─── Exchanges ────────────────────────────────────────────────────────────────

export const getExchangeStatus = () =>
  request<Record<string, ExchangeStatus>>("/api/v1/exchanges");

// ─── Attribution ──────────────────────────────────────────────────────────────

export const getAttribution = () =>
  request<AttributionResponse>("/api/v1/attribution");

// ─── Shadow ───────────────────────────────────────────────────────────────────

export const getShadowStats = () =>
  request<ShadowStats>("/api/v1/shadow/stats");

// ─── Portfolio ───────────────────────────────────────────────────────────────

export const getPortfolioSummary = () =>
  request<PortfolioSummaryResponse>("/api/v1/portfolio-summary");

export const getEquityCurve = () =>
  request<{ curve: { date: string; equity: number; pnl: number; btc_benchmark: number | null }[] }>("/api/v1/portfolio/equity-curve");

export const getPortfolioMetrics = () =>
  request<{ sharpe_ratio: number | null; max_drawdown_pct: number; calmar_ratio: number | null; win_rate: number; total_trades: number; total_pnl: number }>("/api/v1/portfolio/metrics");

// ─── Raw fetch helper (returns Response, caller handles parsing) ──────────────

export async function fetchApi(path: string, options?: RequestInit): Promise<Response> {
  return fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      ...getAuthHeaders(),
      ...options?.headers,
    },
  });
}

export const logout = () => {
  localStorage.removeItem("leviathan_token");
  document.cookie = "leviathan_token=; path=/; max-age=0";
  window.location.href = "/login";
};

// ─── TCA ─────────────────────────────────────────────────────────────────────

export const getTCASummary = () =>
  request<TCASummary>("/api/v1/tca/summary");

// ─── System ──────────────────────────────────────────────────────────────────

export const getSystemContainers = () =>
  request<ContainerStatus[]>("/api/v1/system/containers");

export const getSystemResources = () =>
  request<SystemResources>("/api/v1/system/resources");

// ─── Market Data ─────────────────────────────────────────────────────────────

export const getSymbols = () =>
  request<{ symbols: string[]; count: number }>("/api/v1/symbols");

export interface SpreadItem {
  symbol: string;
  exchange_a: string;
  exchange_b: string;
  spread_bps: number;
  timestamp: string;
}

export const getSpreads = () =>
  request<SpreadItem[]>("/api/v1/spreads");

// ─── Portfolio Extended ───────────────────────────────────────────────────────

export const getDailyReturns = () =>
  request<{ returns: { date: string; pnl: number }[] }>("/api/v1/portfolio/daily-returns");
