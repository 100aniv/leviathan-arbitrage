"use client";

import { useState, useEffect, useRef } from "react";
import { getAlerts, acknowledgeAlert, resolveAlert } from "@/lib/api";
import { SkeletonCard, FriendlyError, EmptyState } from "@/components/ui";
import { Bell } from "lucide-react";
import type { Alert } from "@/types";

type Severity = Alert["severity"] | "ALL";
type AlertStatus = "open" | "acknowledged" | "resolved";

const SEVERITY_STYLES: Record<
  Alert["severity"],
  { bgClass: string; borderClass: string; colorClass: string; label: string }
> = {
  critical: { bgClass: "bg-danger/10",   borderClass: "border-danger/25",   colorClass: "text-danger", label: "CRITICAL" },
  warning:  { bgClass: "bg-warning/10",  borderClass: "border-warning/25",  colorClass: "text-warn",   label: "WARNING"  },
  info:     { bgClass: "bg-info/10",     borderClass: "border-info/25",     colorClass: "text-info",   label: "INFO"     },
};

const STATUS_STYLES: Record<AlertStatus, { colorClass: string; label: string }> = {
  open:         { colorClass: "text-loss",   label: "OPEN"   },
  acknowledged: { colorClass: "text-warn",   label: "ACK"    },
  resolved:     { colorClass: "text-profit", label: "CLOSED" },
};

function SeverityBadge({ severity }: { severity: Alert["severity"] }) {
  const s = SEVERITY_STYLES[severity];
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono whitespace-nowrap border ${s.bgClass} ${s.borderClass} ${s.colorClass}`}>
      {s.label}
    </span>
  );
}

function AlertStatusBadge({ status }: { status?: AlertStatus }) {
  const s = STATUS_STYLES[status ?? "open"];
  return (
    <span className={`text-[10px] font-mono tabular-nums ${s.colorClass}`}>
      {s.label}
    </span>
  );
}

export default function AlertsPage() {
  const [alerts, setAlerts]       = useState<Alert[]>([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState<string | null>(null);
  const [filter, setFilter]       = useState<Severity>("ALL");
  const [actioning, setActioning] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const fetchAlerts = async () => {
      try {
        const data = await getAlerts();
        setAlerts(data);
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to fetch alerts");
      } finally {
        setLoading(false);
      }
    };
    fetchAlerts();
    const interval = setInterval(fetchAlerts, 10_000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const engineUrl = process.env.NEXT_PUBLIC_ENGINE_URL ?? "http://localhost:8000";
    const wsBase    = engineUrl.replace(/^http/, "ws") + "/ws/feed";
    const wsUrl     = wsBase;

    let ws: WebSocket;
    try { ws = new WebSocket(wsUrl); } catch { return; }
    wsRef.current = ws;

    ws.onmessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data as string) as { type: string; data?: Alert };
        if (msg.type === "alert" && msg.data) {
          setAlerts((prev) => [msg.data as Alert, ...prev].slice(0, 200));
        }
      } catch { /* ignore malformed frames */ }
    };

    return () => { ws.close(); wsRef.current = null; };
  }, []);

  async function handleAcknowledge(id: string) {
    setActioning(id);
    try {
      await acknowledgeAlert(id);
      setAlerts((prev) =>
        prev.map((a) => (a.id === id ? { ...a, status: "acknowledged" as const } : a))
      );
    } catch { /* engine may be offline */ }
    finally { setActioning(null); }
  }

  async function handleResolve(id: string) {
    setActioning(id);
    try {
      await resolveAlert(id);
      setAlerts((prev) =>
        prev.map((a) => (a.id === id ? { ...a, status: "resolved" as const } : a))
      );
    } catch { /* engine may be offline */ }
    finally { setActioning(null); }
  }

  const filtered = filter === "ALL" ? alerts : alerts.filter((a) => a.severity === filter);
  const criticalCount = alerts.filter((a) => a.severity === "critical").length;
  const warningCount  = alerts.filter((a) => a.severity === "warning").length;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-mono font-semibold text-terminal-text">Alert History</h2>
          <p className="text-xs font-mono text-terminal-subtle mt-0.5">
            System alerts · real-time WS + 10s polling
          </p>
        </div>
        <div className="flex items-center gap-3">
          {criticalCount > 0 && (
            <span className="text-xs font-mono text-danger">{criticalCount} critical</span>
          )}
          {warningCount > 0 && (
            <span className="text-xs font-mono text-warn">{warningCount} warnings</span>
          )}
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value as Severity)}
            className="bg-terminal-muted border border-terminal-border text-xs font-mono text-terminal-text px-2 py-1 focus:outline-none focus:border-accent"
          >
            <option value="ALL">ALL</option>
            <option value="critical">CRITICAL</option>
            <option value="warning">WARNING</option>
            <option value="info">INFO</option>
          </select>
        </div>
      </div>

      {/* Summary badges */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {(["critical", "warning", "info"] as Alert["severity"][]).map((sev) => {
          const s = SEVERITY_STYLES[sev];
          const count = alerts.filter((a) => a.severity === sev).length;
          return (
            <div key={sev} className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
              <p className="text-terminal-subtle text-xs font-mono">{s.label}</p>
              <p className={`text-2xl font-mono font-semibold tabular-nums mt-1 ${s.colorClass}`}>
                {count}
              </p>
            </div>
          );
        })}
      </div>

      {/* Table card */}
      <div className="bg-terminal-surface border border-terminal-border rounded-lg overflow-hidden">
        {loading && alerts.length === 0 ? (
          <div className="p-6 space-y-3">
            <SkeletonCard /><SkeletonCard /><SkeletonCard />
          </div>
        ) : error ? (
          <FriendlyError error={error} />
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={Bell}
            title={alerts.length === 0 ? "알림 없음" : "필터 결과 없음"}
            description={alerts.length === 0 ? "No alerts — system nominal" : "No alerts match selected filter"}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="border-b border-terminal-border text-terminal-subtle">
                  <th className="text-left px-4 py-2 whitespace-nowrap">Timestamp</th>
                  <th className="text-left px-4 py-2">Severity</th>
                  <th className="text-left px-4 py-2">Type</th>
                  <th className="text-left px-4 py-2">Message</th>
                  <th className="text-left px-4 py-2">Status</th>
                  <th className="text-left px-4 py-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((alert, i) => {
                  const isActioning = actioning === alert.id;
                  const status = alert.status ?? "open";
                  return (
                    <tr
                      key={alert.id}
                      className={`border-b border-terminal-border/50 transition-colors ${
                        i % 2 === 0 ? "" : "bg-terminal-bg/30"
                      } ${status === "resolved" ? "opacity-50" : ""}`}
                    >
                      <td className="px-4 py-2 text-terminal-subtle tabular-nums whitespace-nowrap">
                        {new Date(alert.timestamp).toLocaleString()}
                      </td>
                      <td className="px-4 py-2">
                        <SeverityBadge severity={alert.severity} />
                      </td>
                      <td className="px-4 py-2 text-terminal-text">{alert.type}</td>
                      <td className="px-4 py-2 text-terminal-subtle max-w-xs truncate">
                        {alert.message}
                      </td>
                      <td className="px-4 py-2">
                        <AlertStatusBadge status={status} />
                      </td>
                      <td className="px-4 py-2">
                        <div className="flex items-center gap-2">
                          {status === "open" && (
                            <button
                              onClick={() => handleAcknowledge(alert.id)}
                              disabled={isActioning}
                              className="px-2 py-0.5 text-[10px] font-mono border border-warn/40 text-warn hover:bg-warn/10 disabled:opacity-40 transition-colors"
                            >
                              {isActioning ? "…" : "ACK"}
                            </button>
                          )}
                          {status !== "resolved" && (
                            <button
                              onClick={() => handleResolve(alert.id)}
                              disabled={isActioning}
                              className="px-2 py-0.5 text-[10px] font-mono border border-profit/30 text-profit hover:bg-profit/10 disabled:opacity-40 transition-colors"
                            >
                              {isActioning ? "…" : "RESOLVE"}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
