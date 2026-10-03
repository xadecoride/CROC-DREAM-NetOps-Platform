import { useEffect, useState } from 'react';
import {
  Activity,
  CheckCircle2,
  AlertTriangle,
  Clock,
  RotateCw,
  Terminal,
  Play,
  Server,
} from 'lucide-react';
import { api } from '../api';
import type { JobDetail, JobSummary, JobStatus } from '../api';

interface JobsViewProps {
  jobs: JobSummary[];
  loading: boolean;
  selectedJobId: string | null;
  onSelectJob: (jobId: string) => void;
  onDeploy: (jobId: string) => void;
  onOpenDiff: (jobId: string) => void;
  onRefresh: () => void;
}

export const JobsView: React.FC<JobsViewProps> = ({
  jobs,
  loading,
  selectedJobId,
  onSelectJob,
  onDeploy,
  onOpenDiff,
  onRefresh,
}) => {
  const [activeJobDetail, setActiveJobDetail] = useState<JobDetail | null>(null);

  useEffect(() => {
    if (!selectedJobId) return;

    let timer: any = null;
    let isMounted = true;

    const fetchDetail = async () => {
      try {
        const detail = await api.getJob(selectedJobId);
        if (isMounted) {
          setActiveJobDetail(detail);
          if (detail.status === 'RUNNING' || detail.status === 'PENDING') {
            timer = setTimeout(fetchDetail, 1500);
          }
        }
      } catch (err) {
        console.error('Failed to load job detail', err);
      }
    };

    fetchDetail();

    return () => {
      isMounted = false;
      if (timer) clearTimeout(timer);
    };
  }, [selectedJobId]);

  const getStatusBadge = (status: JobStatus) => {
    switch (status) {
      case 'SUCCESS':
        return (
          <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
            <CheckCircle2 className="w-3 h-3" />
            <span>SUCCESS</span>
          </span>
        );
      case 'FAILED':
        return (
          <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/30">
            <AlertTriangle className="w-3 h-3" />
            <span>FAILED</span>
          </span>
        );
      case 'RUNNING':
        return (
          <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/30 animate-pulse">
            <RotateCw className="w-3 h-3 animate-spin" />
            <span>RUNNING</span>
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-slate-800 text-slate-400 border border-slate-700">
            <Clock className="w-3 h-3" />
            <span>PENDING</span>
          </span>
        );
    }
  };

  const getLogStepColor = (step: string) => {
    switch (step) {
      case 'preflight':
        return 'text-cyan-400 bg-cyan-950/40 border-cyan-800/40';
      case 'render':
        return 'text-purple-400 bg-purple-950/40 border-purple-800/40';
      case 'diff':
        return 'text-indigo-400 bg-indigo-950/40 border-indigo-800/40';
      case 'apply':
        return 'text-amber-400 bg-amber-950/40 border-amber-800/40';
      case 'post_check':
        return 'text-emerald-400 bg-emerald-950/40 border-emerald-800/40';
      case 'rollback':
        return 'text-rose-400 bg-rose-950/40 border-rose-800/40';
      default:
        return 'text-slate-400 bg-slate-900 border-slate-800';
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
      {/* Left Column: Job History */}
      <div className="lg:col-span-4 bg-slate-900 border border-slate-800 rounded-2xl p-4 space-y-4">
        <div className="flex items-center justify-between pb-3 border-b border-slate-800">
          <div className="flex items-center space-x-2">
            <Activity className="w-4 h-4 text-indigo-400" />
            <h2 className="text-sm font-bold text-white">История задач</h2>
          </div>
          <button
            onClick={onRefresh}
            className="p-1 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition"
            title="Обновить список"
          >
            <RotateCw className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="space-y-2.5 max-h-[75vh] overflow-y-auto pr-1">
          {jobs.map((job) => {
            const isSelected = job.id === selectedJobId;
            return (
              <div
                key={job.id}
                onClick={() => onSelectJob(job.id)}
                className={`p-3 rounded-xl border text-xs cursor-pointer transition space-y-2 ${
                  isSelected
                    ? 'bg-indigo-950/30 border-indigo-500/50 shadow-sm'
                    : 'bg-slate-950/40 border-slate-800 hover:border-slate-700'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono font-bold text-slate-200">{job.type}</span>
                  {getStatusBadge(job.status)}
                </div>

                <div className="flex items-center justify-between text-slate-400 text-[11px]">
                  <span>Прогресс: {job.progress}%</span>
                  <span className="font-mono text-slate-500">
                    {new Date(job.created_at).toLocaleTimeString()}
                  </span>
                </div>

                {/* Progress bar */}
                <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                  <div
                    className={`h-full transition-all duration-300 ${
                      job.status === 'FAILED'
                        ? 'bg-rose-500'
                        : job.status === 'SUCCESS'
                        ? 'bg-emerald-500'
                        : 'bg-indigo-500 animate-pulse'
                    }`}
                    style={{ width: `${job.progress}%` }}
                  />
                </div>
              </div>
            );
          })}
          {jobs.length === 0 && (
            <div className="p-8 text-center text-slate-500 text-xs">
              {loading ? 'Загрузка задач...' : 'Нет активных или завершенных задач.'}
            </div>
          )}
        </div>
      </div>

      {/* Right Column: Live Job Execution & Logs */}
      <div className="lg:col-span-8 bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-6">
        {activeJobDetail ? (
          <>
            {/* Job Header */}
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-slate-800">
              <div>
                <div className="flex items-center space-x-2.5">
                  <h2 className="text-lg font-bold text-white font-mono">{activeJobDetail.type}</h2>
                  {getStatusBadge(activeJobDetail.status)}
                </div>
                <div className="mt-1 flex items-center space-x-3 text-xs text-slate-400 font-mono">
                  <span>ID: {activeJobDetail.id.slice(0, 8)}...</span>
                  <span>•</span>
                  <span>Инициатор: {activeJobDetail.created_by}</span>
                  <span>•</span>
                  <span>
                    Старт:{' '}
                    {activeJobDetail.started_at
                      ? new Date(activeJobDetail.started_at).toLocaleTimeString()
                      : '—'}
                  </span>
                </div>
              </div>

              {/* Actions */}
              <div className="flex items-center space-x-2">
                <button
                  onClick={() => onOpenDiff(activeJobDetail.id)}
                  className="px-3.5 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium flex items-center space-x-1.5 border border-slate-700 transition"
                >
                  <Terminal className="w-3.5 h-3.5 text-cyan-400" />
                  <span>Посмотреть Diff</span>
                </button>

                {activeJobDetail.type === 'DRY_RUN' &&
                  activeJobDetail.status === 'SUCCESS' && (
                    <button
                      onClick={() => onDeploy(activeJobDetail.id)}
                      className="px-4 py-1.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium flex items-center space-x-1.5 transition shadow-lg shadow-indigo-600/20"
                    >
                      <Play className="w-3.5 h-3.5 fill-current" />
                      <span>Применить (Deploy)</span>
                    </button>
                  )}
              </div>
            </div>

            {/* Target Devices Status */}
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2.5 flex items-center space-x-1.5">
                <Server className="w-3.5 h-3.5 text-indigo-400" />
                <span>Целевые узлы ({activeJobDetail.targets.length})</span>
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                {activeJobDetail.targets.map((t) => (
                  <div
                    key={t.hostname}
                    className="p-3 rounded-xl bg-slate-950/60 border border-slate-800 flex items-center justify-between text-xs"
                  >
                    <div>
                      <span className="font-mono font-bold text-slate-200 block">
                        {t.hostname}
                      </span>
                      <span className="text-[11px] text-slate-500">
                        {t.has_changes ? 'Патч изменений рассчитан' : 'Конфигурация совпадает'}
                      </span>
                    </div>
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                        t.status === 'SUCCESS'
                          ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                          : t.status === 'ROLLED_BACK'
                          ? 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                          : t.status === 'FAILED'
                          ? 'bg-rose-500/10 text-rose-400 border border-rose-500/20'
                          : 'bg-slate-800 text-slate-400'
                      }`}
                    >
                      {t.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Live Step Logs */}
            <div>
              <div className="flex items-center justify-between mb-2.5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 flex items-center space-x-1.5">
                  <Terminal className="w-3.5 h-3.5 text-cyan-400" />
                  <span>Журнал шагов пайплайна ({activeJobDetail.logs.length})</span>
                </h3>
                <span className="text-[11px] text-slate-500 font-mono">
                  {activeJobDetail.status === 'RUNNING' && '● Потоковый опрос воркера'}
                </span>
              </div>

              <div className="bg-slate-950 border border-slate-800 rounded-xl p-3 font-mono text-[11px] max-h-96 overflow-y-auto space-y-1.5">
                {activeJobDetail.logs.map((log) => (
                  <div
                    key={log.id}
                    className="flex items-start space-x-2 py-0.5 text-slate-300 leading-relaxed"
                  >
                    <span className="text-slate-600 select-none">
                      {new Date(log.created_at).toLocaleTimeString()}
                    </span>
                    <span
                      className={`px-1.5 py-0.2 rounded text-[10px] font-semibold border ${getLogStepColor(
                        log.step
                      )}`}
                    >
                      {log.step}
                    </span>
                    {log.hostname && (
                      <span className="text-indigo-400 font-semibold">[{log.hostname}]</span>
                    )}
                    <span
                      className={
                        log.level === 'ERROR'
                          ? 'text-rose-400 font-semibold'
                          : log.level === 'WARNING'
                          ? 'text-amber-400'
                          : 'text-slate-300'
                      }
                    >
                      {log.message}
                    </span>
                  </div>
                ))}
                {activeJobDetail.logs.length === 0 && (
                  <div className="text-slate-600 text-center py-4">
                    Ожидание логов выполнения...
                  </div>
                )}
              </div>
            </div>
          </>
        ) : (
          <div className="p-16 text-center text-slate-500 space-y-3">
            <Activity className="w-10 h-10 mx-auto opacity-30 text-indigo-400" />
            <p className="text-sm">Выберите задачу слева для просмотра хода выполнения и логов</p>
          </div>
        )}
      </div>
    </div>
  );
};
