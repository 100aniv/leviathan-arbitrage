export default function SystemPage() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-mono font-semibold text-terminal-text">System Health</h2>
        <p className="text-xs font-mono text-terminal-subtle mt-1">Engine status, connectivity, and diagnostics</p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {[
          { label: "Engine Status",   value: "—" },
          { label: "Uptime",          value: "—" },
          { label: "Kill Switch",     value: "—" },
        ].map(({ label, value }) => (
          <div key={label} className="card">
            <p className="card-header">{label}</p>
            <p className="stat-value text-terminal-subtle">{value}</p>
          </div>
        ))}
      </div>

      <div className="card">
        <p className="card-header">System Log</p>
        <div className="flex items-center justify-center py-16">
          <p className="text-xs font-mono text-terminal-subtle">System log panel · loading…</p>
        </div>
      </div>
    </div>
  );
}
