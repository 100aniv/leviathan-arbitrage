type StatusVariant = 'active' | 'paused' | 'stopped' | 'error';
type BadgeSize = 'sm' | 'md' | 'lg';

interface StatusBadgeProps {
  status: StatusVariant;
  size?: BadgeSize;
  showLabel?: boolean;
  label?: string;
}

const VARIANT: Record<StatusVariant, { dot: string; text: string; bg: string; label: string; pulse: boolean }> = {
  active:  { dot: 'bg-profit',       text: 'text-profit',       bg: 'bg-profit/10 border-profit/20',   label: 'ACTIVE',  pulse: false },
  paused:  { dot: 'bg-warn',         text: 'text-warn',         bg: 'bg-warn/10 border-warn/20',       label: 'PAUSED',  pulse: false },
  stopped: { dot: 'bg-loss',         text: 'text-loss',         bg: 'bg-loss/10 border-loss/20',       label: 'STOPPED', pulse: false },
  error:   { dot: 'bg-loss animate-pulse', text: 'text-loss',   bg: 'bg-loss/10 border-loss/20',       label: 'ERROR',   pulse: false },
};

const SIZE: Record<BadgeSize, { dot: string; text: string; px: string; gap: string }> = {
  sm: { dot: 'w-1.5 h-1.5', text: 'text-[10px]', px: 'px-1.5 py-0.5', gap: 'gap-1'   },
  md: { dot: 'w-2 h-2',     text: 'text-xs',     px: 'px-2 py-1',     gap: 'gap-1.5' },
  lg: { dot: 'w-2.5 h-2.5', text: 'text-sm',     px: 'px-3 py-1.5',   gap: 'gap-2'   },
};

export function StatusBadge({ status, size = 'md', showLabel = true, label }: StatusBadgeProps) {
  const v = VARIANT[status];
  const s = SIZE[size];

  return (
    <span className={`inline-flex items-center ${s.gap} ${s.px} ${v.bg} border font-mono ${s.text} ${v.text} uppercase tracking-wider`}>
      <span className={`rounded-full flex-shrink-0 ${s.dot} ${v.dot}`} />
      {showLabel && (label ?? v.label)}
    </span>
  );
}
