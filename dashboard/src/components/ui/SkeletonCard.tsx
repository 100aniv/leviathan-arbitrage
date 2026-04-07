// ─── Skeleton primitives ──────────────────────────────────────────────────────

interface SkeletonLineProps {
  width?: string;
  height?: string;
  className?: string;
}

export function SkeletonLine({ width = 'w-full', height = 'h-3', className = '' }: SkeletonLineProps) {
  return <div className={`skeleton ${width} ${height} ${className}`} />;
}

// ─── SkeletonCard ─────────────────────────────────────────────────────────────

interface SkeletonCardProps {
  lines?: number;
  className?: string;
}

export function SkeletonCard({ lines = 3, className = '' }: SkeletonCardProps) {
  return (
    <div className={`bg-terminal-surface border border-terminal-border p-4 ${className}`}>
      {/* Header line */}
      <SkeletonLine width="w-20" height="h-2.5" className="mb-4" />
      {/* Value line */}
      <SkeletonLine width="w-28" height="h-7" className="mb-3" />
      {/* Extra lines */}
      {Array.from({ length: lines - 1 }).map((_, i) => (
        <SkeletonLine
          key={i}
          width={i % 2 === 0 ? 'w-full' : 'w-3/4'}
          height="h-2.5"
          className="mt-2"
        />
      ))}
    </div>
  );
}

// ─── SkeletonTable ────────────────────────────────────────────────────────────

interface SkeletonTableProps {
  rows?: number;
  cols?: number;
  className?: string;
}

export function SkeletonTable({ rows = 5, cols = 4, className = '' }: SkeletonTableProps) {
  return (
    <div className={`space-y-2 ${className}`}>
      {/* Header */}
      <div className={`grid gap-2`} style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
        {Array.from({ length: cols }).map((_, i) => (
          <SkeletonLine key={i} height="h-2" width="w-full" />
        ))}
      </div>
      {/* Rows */}
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className={`grid gap-2`} style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
          {Array.from({ length: cols }).map((_, c) => (
            <SkeletonLine
              key={c}
              height="h-3"
              width={c === 0 ? 'w-full' : 'w-3/4'}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
