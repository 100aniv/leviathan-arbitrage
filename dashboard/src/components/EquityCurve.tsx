'use client';

interface DataPoint {
  date: string;
  equity: number;
  btc_benchmark: number | null;
}

interface EquityMetrics {
  sharpe_ratio:     number | null;
  max_drawdown_pct: number;
  calmar_ratio:     number | null;
}

interface EquityCurveProps {
  data:     DataPoint[];
  metrics?: EquityMetrics;
}

export function EquityCurve({ data, metrics }: EquityCurveProps) {
  if (data.length === 0) {
    return (
      <div className="bg-terminal-surface border border-terminal-border p-4 h-48 flex items-center justify-center">
        <span className="text-xs font-mono text-terminal-subtle">No equity data yet</span>
      </div>
    );
  }

  const w = 600, h = 200, pad = 30;

  if (data.length === 1) {
    return (
      <div className="bg-terminal-surface border border-terminal-border p-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">Equity Curve</span>
        </div>
        <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-48">
          <circle cx={w / 2} cy={h / 2} r="5" className="fill-profit" />
          <text x={w / 2} y={h / 2 - 15} textAnchor="middle" className="fill-terminal-text text-xs font-mono">
            ${data[0].equity.toLocaleString()}
          </text>
          <text x={w / 2} y={h / 2 + 20} textAnchor="middle" className="fill-terminal-subtle text-[10px] font-mono">
            {data[0].date}
          </text>
        </svg>
      </div>
    );
  }

  const hasBtc = data.some(d => d.btc_benchmark != null);
  const maxEquity = Math.max(...data.map(d => Math.max(d.equity, d.btc_benchmark ?? d.equity)));
  const minEquity = Math.min(...data.map(d => Math.min(d.equity, d.btc_benchmark ?? d.equity)));
  const range = maxEquity - minEquity || 1;

  const toX = (i: number) => pad + (i / Math.max(data.length - 1, 1)) * (w - 2 * pad);
  const toY = (v: number) => h - pad - ((v - minEquity) / range) * (h - 2 * pad);

  const equityPath = data.map((d, i) => `${i === 0 ? 'M' : 'L'}${toX(i)},${toY(d.equity)}`).join(' ');
  const btcPath    = hasBtc ? data.map((d, i) => `${i === 0 ? 'M' : 'L'}${toX(i)},${toY(d.btc_benchmark ?? d.equity)}`).join(' ') : '';

  return (
    <div className="bg-terminal-surface border border-terminal-border p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-mono uppercase tracking-[0.2em] text-terminal-subtle">Equity Curve</span>
        <div className="flex items-center gap-3">
          <span className="text-[10px] font-mono text-profit">━ Portfolio</span>
          <span className="text-[10px] font-mono text-terminal-subtle">╌ BTC Hold</span>
        </div>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-48">
        {hasBtc && <path d={btcPath} fill="none" stroke="currentColor" strokeWidth="1" strokeDasharray="4 2" className="text-terminal-subtle" />}
        <path d={equityPath} fill="none" stroke="currentColor" strokeWidth="2" className="text-profit" />
        {data.map((d, i) => (
          <circle
            key={i}
            cx={toX(i)}
            cy={toY(d.equity)}
            r="3"
            className="fill-profit"
            aria-label={`${d.date}: $${d.equity.toLocaleString()}`}
          />
        ))}
      </svg>
      {metrics && (
        <div className="flex items-center gap-4 mt-2 pt-2 border-t border-terminal-border/40">
          {[
            { label: 'Sharpe', value: metrics.sharpe_ratio?.toFixed(2)     ?? '—' },
            { label: 'MDD',    value: `${metrics.max_drawdown_pct.toFixed(2)}%` },
            { label: 'Calmar', value: metrics.calmar_ratio?.toFixed(2)      ?? '—' },
          ].map(({ label, value }) => (
            <div key={label} className="flex items-center gap-1.5">
              <span className="text-[9px] font-mono text-terminal-subtle uppercase tracking-wider">{label}</span>
              <span className="text-[10px] font-mono text-terminal-text tabular-nums">{value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
