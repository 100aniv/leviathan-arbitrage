"use client";

import { useState, useEffect, useRef } from "react";
import { getAlerts } from "@/lib/api";
import type { Alert } from "@/types";

const SEVERITY_STYLES: Record<
  Alert["severity"],
  { bg: string; border: string; color: string; label: string }
> = {
  critical: {
    bg: "rgba(255,77,77,0.1)",
    border: "rgba(255,77,77,0.25)",
    color: "#ff4d4d",
    label: "CRITICAL",
  },
  warning: {
    bg: "rgba(245,158,11,0.1)",
    border: "rgba(245,158,11,0.25)",
    color: "#f59e0b",
    label: "WARNING",
  },
  info: {
    bg: "rgba(59,130,246,0.1)",
    border: "rgba(59,130,246,0.25)",
    color: "#3b82f6",
    label: "INFO",
  },
};

function SeverityBadge({ severity }: { severity: Alert["severity"] }) {
  const s = SEVERITY_STYLES[severity];
  return (
    <span
      className="px-1.5 py-0.5 rounded text-[10px] font-mono"
      style={{ backgroundColor: s.bg, border: `1px solid ${s.border}`, color: s.color }}
    >
      {s.label}
    </span>
  );
}

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // Initial fetch + polling fallback
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

  // WebSocket for real-time alert pushes
  useEffect(() => {
    const engineUrl =
      process.env.NEXT_PUBLIC_ENGINE_URL ?? "http://localhost:8000";
    const wsBase = engineUrl.replace(/^http/, "ws") + "/ws/feed";
    const token = typeof localStorage !== "undefined" ? localStorage.getItem("leviathan_token") : null;
    const wsUrl = token ? `${wsBase}?token=${token}` : wsBase;

    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl);
    } catch {
      return;
    }
    wsRef.current = ws;

    ws.onmessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data as string) as {
          type: string;
          data?: Alert;
        };
        if (msg.type === "alert" && msg.data) {
          setAlerts((prev) => [msg.data as Alert, ...prev].slice(0, 200));
        }
      } catch {
        // ignore malformed frames
      }
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, []);

  const criticalCount = alerts.filter((a) => a.severity === "critical").length;
  const warningCount = alerts.filter((a) => a.severity === "warning").length;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-mono font-semibold text-terminal-text">Alert History</h2>
          <p className="text-xs font-mono text-terminal-subtle mt-0.5">
            System alerts · real-time WS + 10s polling
          </p>
        </div>
        {alerts.length > 0 && (
          <div className="flex items-center gap-3">
            {criticalCount > 0 && (
              <span className="text-xs font-mono" style={{ color: "#ff4d4d" }}>
                {criticalCount} critical
              </span>
            )}
            {warningCount > 0 && (
              <span className="text-xs font-mono" style={{ color: "#f59e0b" }}>
                {warningCount} warnings
              </span>
            )}
          </div>
        )}
      </div>

      {/* Summary badges */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {(["critical", "warning", "info"] as Alert["severity"][]).map((sev) => {
          const s = SEVERITY_STYLES[sev];
          const count = alerts.filter((a) => a.severity === sev).length;
          return (
            <div
              key={sev}
              className="bg-terminal-surface border border-terminal-border rounded-lg p-4"
            >
              <p className="text-terminal-subtle text-xs font-mono">{s.label}</p>
              <p
                className="text-2xl font-mono font-semibold tabular-nums mt-1"
                style={{ color: s.color }}
              >
                {count}
              </p>
            </div>
          );
        })}
      </div>

      {/* Table card */}
      <div className="bg-terminal-surface border border-terminal-border rounded-lg overflow-hidden">
        {loading && alerts.length === 0 ? (
          <div className="p-8 text-center text-terminal-subtle text-xs font-mono">
            Loading alerts...
          </div>
        ) : error ? (
          <div className="p-8 text-center font-mono text-xs" style={{ color: "#ff4d4d" }}>
            {error}
          </div>
        ) : alerts.length === 0 ? (
          <div className="p-8 text-center text-terminal-subtle text-xs font-mono">
            No alerts — system nominal
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="border-b border-terminal-border text-terminal-subtle">
                  <th className="text-left px-4 py-2">Timestamp</th>
                  <th className="text-left px-4 py-2">Severity</th>
                  <th className="text-left px-4 py-2">Type</th>
                  <th className="text-left px-4 py-2">Message</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map((alert, i) => (
                  <tr
                    key={alert.id}
                    className={`border-b border-terminal-border/50 hover:bg-terminal-muted/30 transition-colors ${
                      i % 2 === 0 ? "" : "bg-terminal-bg/30"
                    }`}
                  >
                    <td className="px-4 py-2 text-terminal-subtle tabular-nums whitespace-nowrap">
                      {new Date(alert.timestamp).toLocaleString()}
                    </td>
                    <td className="px-4 py-2">
                      <SeverityBadge severity={alert.severity} />
                    </td>
                    <td className="px-4 py-2 text-terminal-text">{alert.type}</td>
                    <td className="px-4 py-2 text-terminal-subtle max-w-md truncate">
                      {alert.message}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
