'use client';

import { ResponsiveContainer, LineChart, Line, Tooltip } from 'recharts';

// ─── Types ────────────────────────────────────────────────────────────────────

interface SparkLineProps {
  data: number[];
  color?: string;
  /** Height in pixels */
  height?: number;
  showTooltip?: boolean;
}

// ─── Component ────────────────────────────────────────────────────────────────

export function SparkLine({
  data,
  color = '#00B8FF',
  height = 40,
  showTooltip = false,
}: SparkLineProps) {
  if (!data || data.length < 2) {
    return (
      <div
        className="w-full flex items-center justify-center text-[10px] font-mono text-terminal-subtle"
        style={{ height }}
      >
        데이터 없음
      </div>
    );
  }

  const chartData = data.map((v, i) => ({ i, v }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={chartData} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
        <Line
          type="monotone"
          dataKey="v"
          stroke={color}
          strokeWidth={1.5}
          dot={false}
          animationDuration={800}
          animationEasing="ease-out"
        />
        {showTooltip && (
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const val = payload[0]?.value as number | undefined;
              return (
                <div className="bg-terminal-surface border border-terminal-border px-2 py-1 text-[10px] font-mono text-terminal-text">
                  {val !== undefined ? (val >= 0 ? '+' : '') + val.toFixed(2) : ''}
                </div>
              );
            }}
          />
        )}
      </LineChart>
    </ResponsiveContainer>
  );
}
