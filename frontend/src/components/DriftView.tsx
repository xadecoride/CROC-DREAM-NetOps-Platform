import React, { useEffect, useState } from 'react';
import {
  ShieldCheck,
  ShieldAlert,
  RotateCw,
  Server,
  Wrench,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Terminal,
} from 'lucide-react';
import { api } from '../api';
import type { DriftReportItem } from '../api';

interface DriftViewProps {
  onRemediate: (deviceId: number) => void;
  onScanDrift: () => void;
}

export const DriftView: React.FC<DriftViewProps> = ({ onRemediate, onScanDrift }) => {
  const [report, setReport] = useState<DriftReportItem[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchReport = () => {
    setLoading(true);
    api
      .getDriftReport()
      .then((data) => {
        setReport(data);
        setLoading(false);
      })
      .catch((err) => {
        console.error('Failed to load drift report', err);
        setLoading(false);
      });
  };

  useEffect(() => {
    fetchReport();
  }, []);

  const inSyncCount = report.filter((r) => r.status === 'IN_SYNC').length;
  const driftCount = report.filter((r) => r.status === 'DRIFT_DETECTED').length;
  const complianceRate =
    report.length > 0 ? Math.round((inSyncCount / report.length) * 100) : 100;

  return (
    <div className="space-y-6">
      {/* Top Banner & Summary */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 flex items-center justify-between">
          <div>
            <span className="text-xs font-medium text-slate-400">Общий комплаенс фабрики</span>
            <div className="mt-2 text-3xl font-extrabold text-white">{complianceRate}%</div>
            <p className="mt-1 text-[11px] text-slate-500">
              {inSyncCount} из {report.length} устройств в синхронизации
            </p>
          </div>
          <div
            className={`p-3 rounded-2xl border ${
              complianceRate === 100
                ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                : 'bg-amber-500/10 text-amber-400 border-amber-500/20'
            }`}
          >
            {complianceRate === 100 ? (
              <ShieldCheck className="w-8 h-8" />
            ) : (
              <ShieldAlert className="w-8 h-8" />
            )}
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 flex items-center justify-between">
          <div>
            <span className="text-xs font-medium text-amber-400">Дрейф конфигурации</span>
            <div className="mt-2 text-3xl font-extrabold text-amber-300">{driftCount}</div>
            <p className="mt-1 text-[11px] text-amber-500/80">Несанкционированные изменения</p>
          </div>
          <div className="p-3 rounded-2xl bg-amber-500/10 text-amber-400 border border-amber-500/20">
            <AlertTriangle className="w-8 h-8" />
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 flex flex-col justify-between">
          <div>
            <span className="text-xs font-medium text-slate-400">Фоновый робот (Drift Engine)</span>
            <p className="mt-1 text-xs text-slate-300">
              Celery Beat каждые 15 минут проверяет running-config всех нод.
            </p>
          </div>
          <div className="flex items-center space-x-2 mt-4">
            <button
              onClick={onScanDrift}
              className="flex-1 px-3 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-semibold flex items-center justify-center space-x-1.5 transition"
            >
              <RotateCw className="w-3.5 h-3.5" />
              <span>Запустить внеочередной скан</span>
            </button>
            <button
              onClick={fetchReport}
              className="p-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl transition"
              title="Обновить отчет"
            >
              <RotateCw className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Report Cards by Device */}
      <div className="space-y-4">
        <h3 className="text-sm font-bold text-white flex items-center space-x-2">
          <Terminal className="w-4 h-4 text-indigo-400" />
          <span>Детализация расхождений по оборудованию</span>
        </h3>

        {report.map((item) => (
          <div
            key={item.device_id}
            className={`bg-slate-900 border rounded-2xl p-5 space-y-4 transition ${
              item.status === 'DRIFT_DETECTED'
                ? 'border-amber-500/40 bg-amber-950/10'
                : 'border-slate-800'
            }`}
          >
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-3 border-b border-slate-800/80">
              <div className="flex items-center space-x-3">
                <div className="p-2 rounded-xl bg-slate-800 text-slate-300">
                  <Server className="w-4 h-4" />
                </div>
                <div>
                  <h4 className="text-sm font-bold text-white font-mono">{item.hostname}</h4>
                  <div className="flex items-center space-x-2 text-[11px] text-slate-400">
                    <Clock className="w-3 h-3 text-slate-500" />
                    <span>Проверено: {new Date(item.checked_at).toLocaleTimeString()}</span>
                  </div>
                </div>
              </div>

              <div className="flex items-center space-x-3">
                <span
                  className={`px-2.5 py-0.5 rounded-full text-xs font-semibold ${
                    item.status === 'IN_SYNC'
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                      : item.status === 'DRIFT_DETECTED'
                      ? 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                      : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'
                  }`}
                >
                  {item.status}
                </span>

                {item.status === 'DRIFT_DETECTED' && (
                  <button
                    onClick={() => onRemediate(item.device_id)}
                    className="px-3.5 py-1.5 bg-amber-500 hover:bg-amber-400 text-slate-950 rounded-xl text-xs font-bold flex items-center space-x-1.5 transition shadow-lg shadow-amber-500/20"
                  >
                    <Wrench className="w-3.5 h-3.5" />
                    <span>Устранить дрейф (Remediate)</span>
                  </button>
                )}
              </div>
            </div>

            {/* Unauthorized & Missing lines */}
            {item.status === 'DRIFT_DETECTED' ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
                {/* Unauthorized lines */}
                <div className="p-3.5 rounded-xl bg-slate-950/80 border border-rose-900/40 space-y-2">
                  <span className="text-[11px] font-bold text-rose-400 uppercase tracking-wider block">
                    Несанкционированные строки (+) ({item.unauthorized_lines.length})
                  </span>
                  <div className="space-y-1">
                    {item.unauthorized_lines.map((line, i) => (
                      <div key={i} className="text-rose-300/90 bg-rose-950/30 px-2 py-1 rounded">
                        + {line}
                      </div>
                    ))}
                    {item.unauthorized_lines.length === 0 && (
                      <span className="text-slate-500 italic">Нет лишних строк</span>
                    )}
                  </div>
                </div>

                {/* Missing lines */}
                <div className="p-3.5 rounded-xl bg-slate-950/80 border border-amber-900/40 space-y-2">
                  <span className="text-[11px] font-bold text-amber-400 uppercase tracking-wider block">
                    Отсутствующие строки из эталона (-) ({item.missing_lines.length})
                  </span>
                  <div className="space-y-1">
                    {item.missing_lines.map((line, i) => (
                      <div key={i} className="text-amber-300/90 bg-amber-950/30 px-2 py-1 rounded">
                        - {line}
                      </div>
                    ))}
                    {item.missing_lines.length === 0 && (
                      <span className="text-slate-500 italic">Нет пропущенных строк</span>
                    )}
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-xs text-emerald-400/90 flex items-center space-x-2">
                <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                <span>Рабочая конфигурация на 100% соответствует эталону в Git.</span>
              </div>
            )}
          </div>
        ))}

        {report.length === 0 && (
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-16 text-center text-slate-500 space-y-2">
            <ShieldCheck className="w-10 h-10 mx-auto opacity-30 text-indigo-400" />
            <p className="text-sm">
              {loading ? 'Загрузка отчета о дрейфе...' : 'Отчет о дрейфе пуст. Запустите сканирование.'}
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
