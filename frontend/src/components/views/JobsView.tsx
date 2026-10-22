import { useEffect, useState, useRef } from 'react';
import {
  Activity,
  Terminal,
  Play,
  Server,
  RefreshCw,
  FileDown,
  Clock,
  User,
} from 'lucide-react';
import { api } from '../../api';
import type { JobDetail, JobSummary, UserRole } from '../../api';
import { translations, type Locale } from '../../i18n';
import { JobStatusBadge, downloadMarkdown } from '../layout/ui';

interface JobsViewProps {
  jobs: JobSummary[];
  selectedJobId: string | null;
  userRole?: UserRole;
  locale?: Locale;
  onSelectJob: (jobId: string) => void;
  onDeploy: (jobId: string) => void;
  onOpenDiff: (jobId: string) => void;
  onRefresh: () => void;
}

export const JobsView: React.FC<JobsViewProps> = ({
  jobs,
  selectedJobId,
  userRole = 'viewer',
  locale = 'ru',
  onSelectJob,
  onDeploy,
  onOpenDiff,
  onRefresh,
}) => {
  const canOperate = userRole !== 'viewer';
  const t = translations[locale];
  const [activeJobDetail, setActiveJobDetail] = useState<JobDetail | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [logFilter, setLogFilter] = useState<'ALL' | 'INFO' | 'WARNING' | 'ERROR'>('ALL');
  const logContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!selectedJobId) return;

    let timer: ReturnType<typeof setTimeout> | null = null;
    let es: EventSource | null = null;
    let isMounted = true;

    const closeStream = () => {
      if (es) {
        es.close();
        es = null;
      }
    };

    const fetchDetail = async () => {
      try {
        const detail = await api.getJob(selectedJobId);
        if (!isMounted) return;
        setActiveJobDetail(detail);
        if (detail.status === 'RUNNING' || detail.status === 'PENDING') {
          if (typeof EventSource !== 'undefined' && !es) {
            const lastLogId = detail.logs.length > 0 ? detail.logs[detail.logs.length - 1].id : 0;
            es = new EventSource(`/api/v1/jobs/${encodeURIComponent(selectedJobId)}/stream?after_id=${lastLogId}`);
            es.onmessage = (ev) => {
              if (!isMounted) return;
              try {
                const payload = JSON.parse(ev.data);
                if (payload && typeof payload.id === 'number') {
                  setActiveJobDetail((prev) => {
                    if (!prev || prev.id !== selectedJobId) return prev;
                    if (prev.logs.some((l) => l.id === payload.id)) return prev;
                    return {
                      ...prev,
                      status: payload.status || prev.status,
                      logs: [
                        ...prev.logs,
                        {
                          id: payload.id,
                          step: payload.step || 'job',
                          hostname: payload.hostname || null,
                          level: payload.level || 'INFO',
                          message: payload.message || '',
                          created_at: payload.created_at || new Date().toISOString(),
                        },
                      ],
                    };
                  });
                }
              } catch {
                // ignore malformed SSE chunk
              }
            };
            es.addEventListener('done', () => {
              closeStream();
              void api
                .getJob(selectedJobId)
                .then((d) => {
                  if (isMounted) setActiveJobDetail(d);
                })
                .catch(() => {});
            });
            es.onerror = () => {
              closeStream();
              if (isMounted) timer = setTimeout(fetchDetail, 1500);
            };
          } else {
            timer = setTimeout(fetchDetail, 1500);
          }
        }
      } catch (err) {
        console.error('Failed to load job detail', err);
      }
    };

    void fetchDetail();

    return () => {
      isMounted = false;
      closeStream();
      if (timer) clearTimeout(timer);
    };
  }, [selectedJobId]);

  useEffect(() => {
    if (autoScroll && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [activeJobDetail?.logs, autoScroll]);

  const getLogStepColor = (step: string) => {
    switch (step) {
      case 'preflight':
        return 'text-cyan-700 dark:text-cyan-300 bg-cyan-500/10 border-cyan-500/30';
      case 'render':
        return 'text-purple-700 dark:text-purple-300 bg-purple-500/10 border-purple-500/30';
      case 'diff':
        return 'text-indigo-700 dark:text-indigo-300 bg-indigo-500/10 border-indigo-500/30';
      case 'apply':
        return 'text-amber-700 dark:text-amber-300 bg-amber-500/10 border-amber-500/30';
      case 'post_check':
        return 'text-emerald-700 dark:text-emerald-300 bg-emerald-500/10 border-emerald-500/30';
      case 'rollback':
        return 'text-rose-700 dark:text-rose-300 bg-rose-500/10 border-rose-500/30';
      case 'collect':
        return 'text-sky-700 dark:text-sky-300 bg-sky-500/10 border-sky-500/30';
      case 'job':
      default:
        return 'text-slate-600 dark:text-zinc-400 bg-slate-200/60 dark:bg-zinc-800/60 border-slate-300 dark:border-white/[0.08]';
    }
  };

  const filteredLogs = activeJobDetail?.logs.filter((log) => {
    if (logFilter === 'ALL') return true;
    return log.level === logFilter;
  }) || [];

  const handleExportAuditReport = () => {
    if (!activeJobDetail) return;
    const targetsText = activeJobDetail.targets
      .map((t) => `- **${t.hostname}**: ${t.status}`)
      .join('\n');
    const logsText = activeJobDetail.logs
      .map((l) => `[${new Date(l.created_at).toISOString()}] [${l.level}] [${l.step}] ${l.hostname ? `[${l.hostname}] ` : ''}${l.message}`)
      .join('\n');

    const content = `# CROC DREAM NetOps Platform — Audit & Compliance Report
**Job ID**: ${activeJobDetail.id}
**Type**: ${activeJobDetail.type}
**Status**: ${activeJobDetail.status}
**Author**: ${activeJobDetail.created_by}
**Timestamp**: ${new Date(activeJobDetail.created_at).toISOString()}
**Source**: ${activeJobDetail.intent_source || 'git_main'}

---

## Target Devices (${activeJobDetail.targets.length})
${targetsText || 'None'}

---

## Execution Logs (${activeJobDetail.logs.length} entries)
\`\`\`
${logsText || 'No logs recorded.'}
\`\`\`

---
*Generated by CROC DREAM NetOps Platform • Continuous Compliance Verification System*
`;
    downloadMarkdown(
      `NetOps_Audit_${activeJobDetail.type}_${activeJobDetail.id.slice(0, 8)}.md`,
      content
    );
  };

  return (
    <div className="w-full max-w-[2560px] mx-auto grid grid-cols-1 lg:grid-cols-12 gap-3 sm:gap-5 2xl:gap-6 items-start">
      <div className="bento-card lg:col-span-4 p-3 sm:p-4 rounded-2xl border border-[var(--border-subtle)] space-y-3 sm:space-y-4 shadow-sm min-w-0">
        <div className="flex items-center justify-between pb-3 border-b border-[var(--border-subtle)]">
          <div className="flex items-center space-x-2">
            <Activity className="w-4 h-4 text-cyan-400" />
            <h3 className="text-xs font-mono font-bold uppercase tracking-wider text-[var(--text-primary)]">
              {t.jobsHistoryTitle} ({jobs.length})
            </h3>
          </div>
          <button
            onClick={onRefresh}
            className="p-1 rounded-lg bg-black/5 dark:bg-zinc-900 hover:bg-black/10 dark:hover:bg-zinc-800 text-[var(--text-muted)] hover:text-[var(--text-primary)] border border-[var(--border-subtle)] transition cursor-pointer"
            title={t.refresh}
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="space-y-2 max-h-[320px] sm:max-h-[520px] 2xl:max-h-[680px] overflow-y-auto pr-1">
          {jobs.length === 0 ? (
            <div className="p-8 text-center text-[var(--text-muted)] font-mono text-xs">
              {t.noJobsYet}
            </div>
          ) : (
            jobs.map((j) => {
              const isSelected = j.id === selectedJobId;
              return (
                <div
                  key={j.id}
                  onClick={() => onSelectJob(j.id)}
                  className={`p-3 rounded-xl border text-xs font-mono transition cursor-pointer ${
                    isSelected
                      ? 'bg-cyan-500/10 border-cyan-500/40 shadow-sm'
                      : 'bg-black/[0.03] dark:bg-zinc-950/40 border-[var(--border-subtle)] hover:border-cyan-500/30'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-bold text-[var(--text-primary)] tracking-wide truncate">{j.type}</span>
                    <JobStatusBadge status={j.status} />
                  </div>
                  <div className="mt-1 flex items-center justify-between text-[11px] text-[var(--text-muted)] gap-2">
                    <span className="truncate">{j.id.slice(0, 13)}...</span>
                    <span className="shrink-0">{new Date(j.created_at).toLocaleTimeString()}</span>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      <div className="bento-card lg:col-span-8 p-3 sm:p-4 rounded-2xl border border-[var(--border-subtle)] space-y-3 sm:space-y-4 shadow-sm min-w-0">
        {activeJobDetail ? (
          <>
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-[var(--border-subtle)]">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-bold font-mono text-[var(--text-primary)]">{activeJobDetail.type}</span>
                  <span className="text-xs text-[var(--text-muted)] font-mono">#{activeJobDetail.id.slice(0, 8)}</span>
                  <JobStatusBadge status={activeJobDetail.status} />
                </div>
                <div className="flex items-center gap-3 text-[11px] font-mono text-[var(--text-muted)] mt-1 truncate">
                  <span className="inline-flex items-center gap-1">
                    <Clock className="w-3 h-3 text-cyan-400/80" />
                    {new Date(activeJobDetail.created_at).toLocaleString()}
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <User className="w-3 h-3 text-cyan-400/80" />
                    {activeJobDetail.created_by}
                  </span>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={handleExportAuditReport}
                  className="px-2.5 py-1.5 rounded-xl bg-black/5 dark:bg-zinc-900 hover:bg-black/10 dark:hover:bg-zinc-800 text-[var(--text-primary)] border border-[var(--border-subtle)] text-xs font-mono flex items-center gap-1.5 transition cursor-pointer"
                  title={t.exportAuditReport}
                >
                  <FileDown className="w-3.5 h-3.5 text-cyan-400" />
                  <span className="hidden sm:inline">{t.exportAuditReport}</span>
                </button>

                <button
                  onClick={() => onOpenDiff(activeJobDetail.id)}
                  className="px-3 py-1.5 rounded-xl bg-black/5 dark:bg-zinc-900 hover:bg-black/10 dark:hover:bg-zinc-800 text-[var(--text-primary)] border border-[var(--border-subtle)] text-xs font-mono transition cursor-pointer"
                >
                  {t.viewDiffBtn}
                </button>

                {canOperate &&
                  activeJobDetail.type === 'DRY_RUN' &&
                  activeJobDetail.status === 'SUCCESS' &&
                  (() => {
                    const hasChanges =
                      activeJobDetail.targets.length > 0
                        ? activeJobDetail.targets.some((tgt) => tgt.has_changes)
                        : !activeJobDetail.logs.some(
                            (l) =>
                              l.step.toLowerCase() === 'diff' &&
                              l.message.includes('matches the intended config')
                          );
                    const isAlreadyDeployed = jobs.some(
                      (j) =>
                        j.type === 'DEPLOY' &&
                        j.parent_job_id === activeJobDetail.id &&
                        j.status !== 'FAILED'
                    );
                    const deployDisabled = !hasChanges || isAlreadyDeployed;
                    return (
                      <button
                        onClick={() => onDeploy(activeJobDetail.id)}
                        disabled={deployDisabled}
                        title={
                          isAlreadyDeployed
                            ? 'Уже развёрнут'
                            : !hasChanges
                            ? 'Нет изменений для деплоя'
                            : 'Применить деплой патча'
                        }
                        className="px-3.5 py-1.5 rounded-xl bg-emerald-500 hover:bg-emerald-400 disabled:opacity-40 disabled:cursor-not-allowed text-zinc-950 font-semibold text-xs font-mono flex items-center space-x-1.5 transition shadow-lg shadow-emerald-500/20 cursor-pointer"
                      >
                        <Play className="w-3 h-3 fill-current" />
                        <span>{isAlreadyDeployed ? 'Развёрнут' : t.applyDeployBtn}</span>
                      </button>
                    );
                  })()}
              </div>
            </div>

            <div className="space-y-2">
              <span className="text-[11px] font-mono uppercase tracking-wider text-zinc-400">
                {t.targetDevicesTitle} ({activeJobDetail.targets.length})
              </span>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 gap-2">
                {activeJobDetail.targets.map((target) => (
                  <div
                    key={target.hostname}
                    className="p-2.5 rounded-xl bg-black/[0.03] dark:bg-zinc-950/60 border border-[var(--border-subtle)] flex items-center justify-between text-xs font-mono"
                  >
                    <div className="flex items-center space-x-2 truncate">
                      <Server className="w-3.5 h-3.5 text-[var(--text-muted)] shrink-0" />
                      <span className="text-[var(--text-primary)] truncate">{target.hostname}</span>
                    </div>
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded shrink-0 ${
                        target.status === 'SUCCESS'
                          ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                          : target.status === 'FAILED'
                          ? 'bg-rose-500/10 text-rose-400 border border-rose-500/20'
                          : target.status === 'PENDING'
                          ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 animate-pulse'
                          : 'bg-zinc-800 text-zinc-400'
                      }`}
                    >
                      {target.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs font-mono">
                <div className="flex items-center space-x-2 text-[var(--text-muted)]">
                  <Terminal className="w-3.5 h-3.5 text-cyan-400" />
                  <span>{t.liveTelemetryTitle}</span>
                </div>
                <div className="flex flex-wrap items-center gap-2 sm:gap-3 text-[11px]">
                  <div className="flex items-center space-x-1 bg-black/[0.03] dark:bg-zinc-950 p-0.5 rounded-lg border border-[var(--border-subtle)]">
                    {(['ALL', 'INFO', 'WARNING', 'ERROR'] as const).map((lvl) => (
                      <button
                        key={lvl}
                        onClick={() => setLogFilter(lvl)}
                        className={`px-2 py-0.5 rounded text-[10px] transition cursor-pointer ${
                          logFilter === lvl
                            ? 'bg-slate-200 dark:bg-zinc-800 text-slate-900 dark:text-white font-bold'
                            : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                        }`}
                      >
                        {lvl}
                      </button>
                    ))}
                  </div>

                  <label className="flex items-center space-x-1.5 cursor-pointer text-[var(--text-muted)] hover:text-[var(--text-primary)]">
                    <input
                      type="checkbox"
                      checked={autoScroll}
                      onChange={(e) => setAutoScroll(e.target.checked)}
                      className="rounded border-slate-300 dark:border-white/[0.2] text-cyan-500 focus:ring-0 accent-cyan-500"
                    />
                    <span>Auto-scroll</span>
                  </label>
                </div>
              </div>

              <div
                ref={logContainerRef}
                className="terminal-window min-h-[360px] sm:min-h-[520px] 2xl:min-h-[680px] max-h-[75vh] p-3 sm:p-3.5 rounded-xl bg-slate-50 dark:bg-zinc-950 font-mono text-[11px] leading-relaxed text-slate-800 dark:text-zinc-300 overflow-y-auto space-y-1.5 border border-slate-200 dark:border-white/[0.06]"
              >
                {filteredLogs.length === 0 ? (
                  <div className="text-zinc-600 italic">{t.emptyLogs}</div>
                ) : (
                  filteredLogs.map((log) => (
                    <div key={log.id} className="flex flex-wrap sm:flex-nowrap items-start gap-1.5 sm:gap-2 hover:bg-black/[0.02] dark:hover:bg-white/[0.02] py-0.5 px-1 rounded">
                      <span className="text-slate-500 dark:text-zinc-500 shrink-0 font-mono">
                        {new Date(log.created_at).toLocaleTimeString()}
                      </span>
                      <span
                        className={`px-1.5 py-0.2 rounded text-[10px] uppercase font-bold shrink-0 ${
                          log.level === 'ERROR'
                            ? 'bg-rose-500/15 text-rose-700 dark:text-rose-400 border border-rose-500/30'
                            : log.level === 'WARNING'
                            ? 'bg-amber-500/15 text-amber-700 dark:text-amber-400 border border-amber-500/30'
                            : 'bg-slate-200/60 dark:bg-zinc-800/60 text-slate-700 dark:text-zinc-400 border border-slate-300 dark:border-white/[0.06]'
                        }`}
                      >
                        {log.level}
                      </span>
                      <span
                        className={`px-1.5 py-0.2 rounded text-[10px] border shrink-0 ${getLogStepColor(
                          log.step
                        )}`}
                      >
                        {log.step}
                      </span>
                      {log.hostname && (
                        <span className="text-cyan-700 dark:text-cyan-400 font-semibold shrink-0">[{log.hostname}]</span>
                      )}
                      <span className="text-slate-800 dark:text-zinc-200 break-all">{log.message}</span>
                    </div>
                  ))
                )}
              </div>
            </div>
          </>
        ) : (
          <div className="min-h-[360px] sm:min-h-[520px] 2xl:min-h-[680px] flex items-center justify-center p-8 text-center text-zinc-500 font-mono text-xs">
            {t.selectJobHint}
          </div>
        )}
      </div>
    </div>
  );
};
