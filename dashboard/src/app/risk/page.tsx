export default function RiskPage() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-mono font-semibold text-terminal-text">Risk Dashboard</h2>
        <p className="text-xs font-mono text-terminal-subtle mt-1">Exposure, drawdown, and risk metrics</p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {[
          { label: "Max Drawdown",        value: "—" },
          { label: "Total Exposure",      value: "—" },
          { label: "Risk Score",          value: "—" },
        ].map(({ label, value }) => (
          <div key={label} className="card">
            <p className="card-header">{label}</p>
            <p className="stat-value text-terminal-subtle">{value}</p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="card min-h-[280px] flex items-center justify-center">
          <p className="text-xs font-mono text-terminal-subtle">Exposure by Exchange · loading…</p>
        </div>
        <div className="card min-h-[280px] flex items-center justify-center">
          <p className="text-xs font-mono text-terminal-subtle">Drawdown Chart · loading…</p>
        </div>
      </div>
    </div>
  );
}
