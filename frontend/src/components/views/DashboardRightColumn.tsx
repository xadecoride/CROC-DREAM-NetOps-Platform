import React from 'react';
import {
  ExternalLink,
  Clock,
} from 'lucide-react';
import type { JobSummary } from '../../api';
import { translations, type Locale } from '../../i18n';
import { BentoCard } from '../layout/ui';

const JOB_COLOR: Record<string, { text: string; bg: string; border: string }> = {
  SUCCESS: { text: 'text-emerald-600 dark:text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/20' },
  FAILED: { text: 'text-rose-600 dark:text-rose-400', bg: 'bg-rose-500/10', border: 'border-rose-500/20' },
  RUNNING: { text: 'text-[var(--text-primary)]', bg: 'bg-[var(--bg-elevated)]', border: 'border-[var(--border-strong)]' },
  PENDING: { text: 'text-[var(--text-secondary)]', bg: 'bg-[var(--bg-subtle)]', border: 'border-[var(--border-subtle)]' },
};

interface DashboardRightColumnProps {
  jobs: JobSummary[];
  locale: Locale;
  onOpenTab: (tab: string) => void;
  onOpenDiff?: (jobId?: string) => void;
}

export const DashboardRightColumn: React.FC<DashboardRightColumnProps> = ({
  jobs,
  locale,
  onOpenTab,
  onOpenDiff,
}) => {
  const t = translations[locale];
  return (
    <div className="xl:col-span-5 space-y-3">
      <BentoCard
        title={t.automationJobsTitle}
        icon={<Clock className="w-3.5 h-3.5 text-[var(--text-muted)]" />}
        indicator="bg-zinc-500"
        right={
          <button
            onClick={() => onOpenTab('jobs')}
            className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] flex items-center gap-1 cursor-pointer transition-colors"
          >
            <span>{t.auditLogBtn}</span>
            <ExternalLink className="w-3 h-3" />
          </button>
        }
      >
        <div className="space-y-1">
          {jobs.slice(0, 5).map((j) => {
            const s = JOB_COLOR[j.status] ?? JOB_COLOR.PENDING;
            return (
              <div
                key={j.id}
                onClick={() => {
                  if (onOpenDiff) onOpenDiff(j.id);
                  else onOpenTab('jobs');
                }}
                className="flex items-center justify-between py-1.5 px-2 rounded-md hover:bg-[var(--bg-elevated)] transition-colors cursor-pointer border border-transparent hover:border-[var(--border-subtle)] text-xs"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-mono text-[var(--text-primary)] truncate">{j.type}</span>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-[var(--text-muted)] text-[11px] font-mono tabular-nums">
                    {new Date(j.created_at).toLocaleTimeString(locale === 'en' ? 'en-US' : 'ru-RU', {
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </span>
                  <span className={`font-mono text-[10px] px-1.5 py-0.5 rounded border ${s.bg} ${s.text} ${s.border}`}>
                    {j.status}
                  </span>
                </div>
              </div>
            );
          })}

          {jobs.length === 0 && <div className="py-4 text-center text-xs text-[var(--text-muted)]">{t.noRunsYet}</div>}
        </div>
      </BentoCard>
    </div>
  );
};
