export default function OverviewPage() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-mono font-semibold text-terminal-text">Overview</h2>
        <p className="text-xs font-mono text-terminal-subtle mt-1">Real-time arbitrage engine status</p>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        {[
          { label: "Total P&L",        value: "—",  unit: "USDT", color: "text-terminal-subtle" },
          { label: "Active Strategies", value: "—",  unit: "",     color: "text-terminal-subtle" },
          { label: "Open Positions",   value: "—",  unit: "",     color: "text-terminal-subtle" },
          { label: "Uptime",           value: "—",  unit: "",     color: "text-terminal-subtle" },
        ].map(({ label, value, unit, color }) => (
          <div key={label} className="card">
            <p className="card-header">{label}</p>
            <p className={`stat-value ${color}`}>
              {value}
              {unit && <span className="text-sm ml-1 text-terminal-subtle">{unit}</span>}
            </p>
          </div>
        ))}
      </div>

      {/* Main panels placeholder */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="card min-h-[280px] flex items-center justify-center">
          <p className="text-xs font-mono text-terminal-subtle">P&L Chart · loading components…</p>
        </div>
        <div className="card min-h-[280px] flex items-center justify-center">
          <p className="text-xs font-mono text-terminal-subtle">Positions Table · loading components…</p>
        </div>
      </div>
    </div>
  );
}
