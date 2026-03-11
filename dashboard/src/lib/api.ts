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
  request<KillResponse>("/kill", {
    method: "POST",
    body: JSON.stringify({ reason }),
  });

// ─── Strategies ───────────────────────────────────────────────────────────────

export const getStrategies = () =>
  request<Strategy[]>("/strategies");

export const toggleStrategy = (id: string) =>
  request<ToggleResponse>(`/strategies/${id}/toggle`, { method: "POST" });

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

export const getTrades = (strategy?: string, limit?: number) => {
  const params = new URLSearchParams();
  if (strategy) params.set("strategy", strategy);
  if (limit) params.set("limit", String(limit));
  return request<Trade[]>(`/api/v1/trades?${params}`);
};

// ─── Alerts ───────────────────────────────────────────────────────────────────

export const getAlerts = () =>
  request<Alert[]>("/api/v1/alerts");

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

export const logout = () => {
  localStorage.removeItem("leviathan_token");
  document.cookie = "leviathan_token=; path=/; max-age=0";
  window.location.href = "/login";
};
