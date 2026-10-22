import React, { useEffect } from 'react';
import { X, CheckCircle2, AlertTriangle, RotateCw, Clock } from 'lucide-react';
import type { DeviceStatus, OperStatus } from '../../api';

const STATUS: Record<string, { label: string; dot: string; text: string; bg: string; border: string }> = {
  IN_SYNC: {
    label: 'В синхроне',
    dot: 'bg-emerald-400',
    text: 'text-[var(--text-primary)]',
    bg: 'bg-[var(--bg-elevated)]',
    border: 'border-[var(--border-subtle)]',
  },
  DRIFT_DETECTED: {
    label: 'Дрейф',
    dot: 'bg-amber-400',
    text: 'text-amber-500 dark:text-amber-400',
    bg: 'bg-amber-500/[0.06]',
    border: 'border-amber-500/25',
  },
  UNREACHABLE: {
    label: 'Недоступно',
    dot: 'bg-rose-400',
    text: 'text-rose-500 dark:text-rose-400',
    bg: 'bg-rose-500/[0.06]',
    border: 'border-rose-500/25',
  },
  IN_PROGRESS: {
    label: 'В работе',
    dot: 'bg-zinc-300 animate-pulse',
    text: 'text-[var(--text-secondary)]',
    bg: 'bg-[var(--bg-elevated)]',
    border: 'border-[var(--border-subtle)]',
  },
  UNKNOWN: {
    label: 'Неизвестно',
    dot: 'bg-zinc-500',
    text: 'text-[var(--text-secondary)]',
    bg: 'bg-[var(--bg-subtle)]',
    border: 'border-[var(--border-subtle)]',
  },
};

export const StatusBadge: React.FC<{ status: DeviceStatus; className?: string }> = React.memo(({ status, className = '' }) => {
  const s = STATUS[status] ?? STATUS.UNKNOWN;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded border text-[11px] font-mono font-medium tabular-nums ${s.bg} ${s.text} ${s.border} ${className}`}>
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${s.dot}`} />
      <span>{s.label}</span>
    </span>
  );
});

export const OperStatusBadge: React.FC<{ status?: OperStatus; className?: string }> = React.memo(({ status = 'UP', className = '' }) => {
  const isUp = status === 'UP';
  const isDegraded = status === 'DEGRADED';
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded border text-[11px] font-mono font-medium tabular-nums ${
        isUp
          ? 'bg-[var(--bg-elevated)] text-[var(--text-primary)] border-[var(--border-subtle)]'
          : isDegraded
          ? 'bg-amber-500/[0.06] text-amber-500 dark:text-amber-400 border-amber-500/25'
          : 'bg-rose-500/[0.06] text-rose-500 dark:text-rose-400 border-rose-500/25'
      } ${className}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${isUp ? 'bg-emerald-400' : isDegraded ? 'bg-amber-400' : 'bg-rose-400'}`} />
      <span>{status}</span>
    </span>
  );
});

export const Sparkline: React.FC<{
  points?: number[];
  width?: number;
  height?: number;
  fullWidth?: boolean;
  showValue?: boolean;
  className?: string;
}> = React.memo(({ points = [], width = 76, height = 22, fullWidth = false, showValue = true, className = '' }) => {
  const normalized =
    points && points.length >= 2
      ? points
      : points && points.length === 1
      ? [points[0], points[0], points[0], points[0]]
      : [0, 0, 0, 0];

  const dataMin = Math.min(...normalized);
  const dataMax = Math.max(...normalized);
  const min = Math.max(0, dataMin - 6);
  const max = Math.min(100, Math.max(dataMax + 6, min + 14));
  const range = max - min || 1;
  const pad = 2;
  const w = width - pad * 2;
  const h = height - pad * 2;

  const xy = normalized.map((v, i) => {
    const x = pad + (i / (normalized.length - 1)) * w;
    const y = height - pad - (Math.min(Math.max(v, min), max) - min) / range * h;
    return { x, y };
  });

  const smoothLinePath = xy.reduce((acc, p, i, arr) => {
    if (i === 0) return `M${p.x.toFixed(1)},${p.y.toFixed(1)}`;
    const prev = arr[i - 1];
    const cx = ((prev.x + p.x) / 2).toFixed(1);
    return `${acc} C${cx},${prev.y.toFixed(1)} ${cx},${p.y.toFixed(1)} ${p.x.toFixed(1)},${p.y.toFixed(1)}`;
  }, '');

  const first = normalized[0];
  const last = normalized[normalized.length - 1];
  const diff = last - first;
  const strokeColor = last > 80 ? '#f43f5e' : last > 55 ? '#f59e0b' : '#71717a';
  const arrow = diff > 4 ? '↗' : diff < -4 ? '↘' : '→';

  return (
    <div className={`sparkline inline-flex items-center gap-1.5 tabular-nums ${fullWidth ? 'w-full' : ''} ${className}`}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width={fullWidth ? '100%' : width}
        height={height}
        preserveAspectRatio="none"
        className="sparkline shrink-0 overflow-visible"
      >
        <path
          fill="none"
          stroke={strokeColor}
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          d={smoothLinePath}
        />
        <circle
          cx={xy[xy.length - 1].x}
          cy={xy[xy.length - 1].y}
          r="2"
          fill={strokeColor}
        />
      </svg>
      {showValue && (
        <>
          <span className="font-mono text-[10px] text-[var(--text-muted)]">{arrow}</span>
          <span className="font-mono text-[10px] text-[var(--text-secondary)]">{Math.round(last)}%</span>
        </>
      )}
    </div>
  );
});

export const StateTimeline: React.FC<{
  states?: string[];
  className?: string;
}> = React.memo(({ states = [], className = '' }) => {
  if (!states || states.length === 0) {
    return <span className="text-[11px] font-mono text-[var(--text-muted)]">—</span>;
  }
  const getColor = (s: string) => {
    if (s === 'UP' || s === 'IN_SYNC' || s === 'HEALTHY') return 'bg-emerald-500/75 hover:bg-emerald-400';
    if (s === 'DRIFT' || s === 'DRIFT_DETECTED' || s === 'DEGRADED') return 'bg-amber-500/80 hover:bg-amber-400';
    if (s === 'DOWN' || s === 'UNREACHABLE' || s === 'CRITICAL') return 'bg-rose-500/80 hover:bg-rose-400';
    return 'bg-zinc-600/50 hover:bg-zinc-500';
  };

  return (
    <div className={`inline-flex items-center gap-[2px] ${className}`} title="24h State Timeline (последние 24 часа)">
      {states.map((st, i) => (
        <span
          key={i}
          className={`w-1.5 h-3.5 rounded-[1px] transition-colors cursor-help ${getColor(st)}`}
          title={`-${24 - i}ч: ${st}`}
        />
      ))}
    </div>
  );
});

export const ManagementModeBadge: React.FC<{ mode?: string; className?: string }> = React.memo(({ mode = 'MONITORING_ONLY', className = '' }) => {
  const isManaged = mode === 'MANAGED';
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded border text-[10px] font-mono font-medium ${
        isManaged
          ? 'bg-[var(--bg-elevated)] text-[var(--text-primary)] border-[var(--border-hover)]'
          : 'bg-[var(--bg-subtle)] text-[var(--text-muted)] border-[var(--border-subtle)]'
      } ${className}`}
      title={isManaged ? 'Управляемое: разрешены деплой и мутации' : 'Только наблюдение: безопасный режим чтения'}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${isManaged ? 'bg-emerald-400' : 'bg-zinc-500'}`} />
      <span>{isManaged ? 'УПРАВЛЯЕМОЕ' : 'НАБЛЮДЕНИЕ'}</span>
    </span>
  );
});

export const Panel: React.FC<{
  title: string;
  accent?: string;
  indicator?: string;
  right?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}> = React.memo(({
  title,
  accent,
  indicator,
  right,
  children,
  className = '',
}) => {
  const dotColor = indicator ?? (
    accent?.includes('emerald') || accent?.includes('teal') ? 'bg-emerald-400' :
    accent?.includes('amber') ? 'bg-amber-400' :
    accent?.includes('rose') || accent?.includes('red') ? 'bg-rose-400' :
    'bg-zinc-500'
  );

  return (
    <section className={`rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-card)] overflow-hidden ${className}`}>
      <div className="bg-[var(--bg-elevated)]/60 border-b border-[var(--border-subtle)] px-3.5 py-2 text-xs font-medium text-[var(--text-primary)] flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotColor}`} />
          <span className="tracking-tight font-medium truncate">{title}</span>
        </div>
        {right && <div className="text-[var(--text-secondary)] text-xs shrink-0 whitespace-nowrap">{right}</div>}
      </div>
      <div className="p-3.5">{children}</div>
    </section>
  );
});

export const BentoCard: React.FC<{
  title: string;
  icon?: React.ReactNode;
  indicator?: string;
  right?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}> = React.memo(({ title, icon, indicator = 'bg-zinc-500', right, children, className = '' }) => (
  <section className={`rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-card)] overflow-hidden flex flex-col transition-colors hover:border-[var(--border-hover)] ${className}`}>
    <div className="bg-[var(--bg-elevated)]/50 border-b border-[var(--border-subtle)] px-3.5 py-2.5 text-xs font-medium text-[var(--text-primary)] flex items-center justify-between gap-2 shrink-0">
      <div className="flex items-center gap-2 min-w-0">
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${indicator}`} />
        {icon}
        <span className="tracking-tight font-medium truncate">{title}</span>
      </div>
      {right && <div className="text-[var(--text-secondary)] text-xs shrink-0 whitespace-nowrap">{right}</div>}
    </div>
    <div className="p-3.5 flex-1 min-h-0 overflow-hidden">{children}</div>
  </section>
));

export const JobStatusBadge: React.FC<{ status: string; className?: string }> = React.memo(({
  status,
  className = '',
}) => {
  switch (status) {
    case 'SUCCESS':
      return (
        <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-mono font-medium bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 ${className}`}>
          <CheckCircle2 className="w-3 h-3" />
          <span>SUCCESS</span>
        </span>
      );
    case 'FAILED':
      return (
        <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-mono font-medium bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20 ${className}`}>
          <AlertTriangle className="w-3 h-3" />
          <span>FAILED</span>
        </span>
      );
    case 'RUNNING':
      return (
        <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-mono font-medium bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border border-cyan-500/20 ${className}`}>
          <RotateCw className="w-3 h-3 animate-spin" />
          <span>RUNNING</span>
        </span>
      );
    case 'PENDING':
      return (
        <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-mono font-medium bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20 ${className}`}>
          <Clock className="w-3 h-3" />
          <span>PENDING</span>
        </span>
      );
    default:
      return (
        <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-mono font-medium bg-[var(--bg-subtle)] text-[var(--text-secondary)] border border-[var(--border-subtle)] ${className}`}>
          <span>{status}</span>
        </span>
      );
  }
});

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  icon?: React.ReactNode;
  badge?: React.ReactNode;
  children: React.ReactNode;
  maxWidth?: string;
  footer?: React.ReactNode;
}

export const Modal: React.FC<ModalProps> = ({
  open,
  onClose,
  title,
  icon,
  badge,
  children,
  maxWidth = 'max-w-xl',
  footer,
}) => {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="fixed inset-0 bg-black/75 backdrop-blur-xs transition-opacity animate-fade-in"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        className={`relative w-full ${maxWidth} rounded-xl bg-[var(--bg-card)] border border-[var(--border-hover)] shadow-2xl overflow-hidden flex flex-col z-10 text-[var(--text-primary)] animate-win-fade`}
      >
        <div className="px-5 py-3.5 border-b border-[var(--border-subtle)] flex items-center justify-between bg-[var(--bg-elevated)]/70">
          <div className="flex items-center gap-2.5">
            {icon}
            <h3 className="text-sm font-semibold text-[var(--text-primary)] tracking-tight">{title}</h3>
            {badge}
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-md text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-subtle)] transition-colors cursor-pointer"
            title="Закрыть (Esc)"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-5 overflow-y-auto max-h-[calc(90vh-120px)]">{children}</div>
        {footer && (
          <div className="px-5 py-3 border-t border-[var(--border-subtle)] bg-[var(--bg-elevated)]/40 flex items-center justify-end gap-2">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
};

export function downloadMarkdown(filename: string, content: string): void {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

