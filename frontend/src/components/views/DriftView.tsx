import { useEffect, useState, useMemo } from 'react';
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
  FileDown,
} from 'lucide-react';
import { api } from '../../api';
import type { Device, DriftReportItem, UserRole } from '../../api';
import { translations, type Locale } from '../../i18n';
import { downloadMarkdown } from '../layout/ui';

interface DriftViewProps {
  userRole?: UserRole;
  locale?: Locale;
  devices?: Device[];
  onRemediate: (deviceId: number) => void;
  onScanDrift: () => void;
}

export const DriftView: React.FC<DriftViewProps> = ({ userRole = 'viewer', locale = 'ru', devices, onRemediate, onScanDrift }) => {
  const canOperate = userRole !== 'viewer';
  const t = translations[locale];
  const [rawReport, setReport] = useState<DriftReportItem[]>([]);
  const [loading, setLoading] = useState(true);

  const report = useMemo(() => {
    if (!devices) return rawReport;
    const allowedIds = new Set(devices.map((d) => d.id));
    return rawReport.filter((r) => allowedIds.has(r.device_id));
  }, [rawReport, devices]);

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
    let active = true;
    api
      .getDriftReport()
      .then((data) => {
        if (!active) return;
        setReport(data);
        setLoading(false);
      })
      .catch((err) => {
        if (!active) return;
        console.error('Failed to load drift report', err);
        setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const inSyncCount = report.filter((r) => r.status === 'IN_SYNC').length;
  const driftCount = report.filter((r) => r.status === 'DRIFT_DETECTED').length;
  const complianceRate =
    report.length > 0 ? Math.round((inSyncCount / report.length) * 100) : 100;

  const handleExportComplianceAct = () => {
    const listText = report
      .map(
        (r) =>
          `### ${r.hostname} — ${r.status}\n- **Checked At**: ${new Date(r.checked_at).toISOString()}\n- **Unauthorized lines**: ${r.unauthorized_lines?.length || 0}\n- **Missing lines**: ${r.missing_lines?.length || 0}\n${r.remediation_patch ? `\`\`\`\n${r.remediation_patch}\n\`\`\`\n` : ''}`
      )
      .join('\n');

    const act = `# АКТ ПРОВЕРКИ КОМПЛАЕНСА СЕТЕВОЙ ФАБРИКИ (NETOPS AUDIT ACT)
**Дата аудита**: ${new Date().toLocaleString()}
**Общий комплаенс**: ${complianceRate}% (${inSyncCount} / ${report.length} в эталоне)
**Выявлено расхождений**: ${driftCount}

---

## Детализация по оборудованию
${listText || 'Данные отсутствуют'}

---
*Документ сформирован автоматически платформой CROC DREAM NetOps Platform*
`;
    downloadMarkdown(
      `NetOps_Compliance_Act_${new Date().toISOString().slice(0, 10)}.md`,
      act
    );
  };

  return (
    <div className="w-full max-w-[2560px] mx-auto space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3.5">
        <div className="p-4 rounded-lg bg-[var(--bg-card)] border border-[var(--border-subtle)] flex items-center justify-between">
          <div>
            <span className="text-xs text-[var(--text-secondary)] font-medium">{t.complianceTitle}</span>
            <div className="mt-1.5 text-2xl font-semibold font-mono tabular-nums text-[var(--text-primary)] tracking-tight">
              {complianceRate}%
            </div>
            <p className="mt-1 text-[11px] font-mono tabular-nums text-[var(--text-muted)]">
              {inSyncCount} / {report.length} {t.nodesInStandard}
            </p>
          </div>
          <div
            className={`p-2.5 rounded-lg border ${
              complianceRate === 100
                ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                : 'bg-amber-500/10 text-amber-400 border-amber-500/20'
            }`}
          >
            {complianceRate === 100 ? (
              <ShieldCheck className="w-5 h-5" />
            ) : (
              <ShieldAlert className="w-5 h-5" />
            )}
          </div>
        </div>

        <div className="p-4 rounded-lg bg-[var(--bg-card)] border border-[var(--border-subtle)] flex items-center justify-between">
          <div>
            <span className="text-xs text-[var(--text-secondary)] font-medium">{t.driftDetectedTitle}</span>
            <div className={`mt-1.5 text-2xl font-semibold font-mono tabular-nums tracking-tight ${driftCount > 0 ? 'text-amber-400' : 'text-[var(--text-primary)]'}`}>
              {driftCount}
            </div>
            <p className="mt-1 text-[11px] font-mono tabular-nums text-[var(--text-muted)]">
              {driftCount} {t.requireRemediation}
            </p>
          </div>
          <div className="p-2.5 rounded-lg bg-[var(--bg-elevated)] text-[var(--text-secondary)] border border-[var(--border-subtle)]">
            <AlertTriangle className={`w-5 h-5 ${driftCount > 0 ? 'text-amber-400' : 'text-[var(--text-muted)]'}`} />
          </div>
        </div>

        <div className="p-4 rounded-lg bg-[var(--bg-card)] border border-[var(--border-subtle)] flex flex-col justify-between gap-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-mono text-[var(--text-secondary)]">GitOps Continuous Audit</span>
            <button
              onClick={handleExportComplianceAct}
              className="px-2 py-0.5 rounded text-[11px] font-mono text-[var(--text-primary)] bg-[var(--bg-elevated)] hover:border-[var(--border-hover)] border border-[var(--border-subtle)] flex items-center gap-1 transition-colors cursor-pointer shrink-0"
              title={t.exportComplianceAct}
            >
              <FileDown className="w-3 h-3 text-[var(--text-secondary)]" />
              <span>{locale === 'en' ? 'Export Act' : 'Акт (MD)'}</span>
            </button>
          </div>
          <div className="flex items-center gap-2">
            {canOperate && (
              <button
                onClick={onScanDrift}
                className="flex-1 px-3 py-1.5 bg-[var(--text-primary)] text-[var(--bg-main)] hover:opacity-90 rounded-md text-xs font-medium flex items-center justify-center gap-1.5 transition-opacity cursor-pointer"
              >
                <RotateCw className="w-3.5 h-3.5" />
                <span>{t.scanDriftBtn}</span>
              </button>
            )}
            <button
              onClick={fetchReport}
              className="p-1.5 bg-[var(--bg-elevated)] hover:border-[var(--border-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] rounded-md border border-[var(--border-subtle)] transition-colors cursor-pointer"
              title={t.refresh}
            >
              <RotateCw className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>

      <div className="space-y-3">
        <h3 className="text-xs font-semibold font-mono uppercase tracking-wider text-[var(--text-secondary)] flex items-center gap-2">
          <Terminal className="w-3.5 h-3.5 text-[var(--text-muted)]" />
          <span>{locale === 'en' ? 'Node Drift' : 'Расхождения по узлам'}</span>
        </h3>

        {report.map((item) => (
          <div
            key={item.device_id}
            className={`p-4 rounded-lg bg-[var(--bg-card)] border transition-colors ${
              item.status === 'DRIFT_DETECTED'
                ? 'border-amber-500/35 bg-amber-500/[0.02]'
                : 'border-[var(--border-subtle)]'
            }`}
          >
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-3 border-b border-[var(--border-subtle)]">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-md bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-[var(--text-secondary)]">
                  <Server className="w-4 h-4" />
                </div>
                <div>
                  <h4 className="text-sm font-semibold text-[var(--text-primary)] font-mono">{item.hostname}</h4>
                  <div className="flex items-center gap-1.5 text-[11px] font-mono tabular-nums text-[var(--text-muted)]">
                    <Clock className="w-3 h-3" />
                    <span>{new Date(item.checked_at).toLocaleTimeString()}</span>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <span
                  className={`px-2 py-0.5 rounded text-[11px] font-mono font-medium border ${
                    item.status === 'IN_SYNC'
                      ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20'
                      : item.status === 'DRIFT_DETECTED'
                      ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20'
                      : 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20'
                  }`}
                >
                  {item.status}
                </span>

                {item.status === 'DRIFT_DETECTED' && item.risk_level && (
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-mono font-semibold tracking-wider border ${
                      item.risk_level === 'LOW'
                        ? 'bg-[var(--bg-elevated)] text-[var(--text-secondary)] border-[var(--border-subtle)]'
                        : 'bg-rose-500/10 text-rose-400 border-rose-500/25'
                    }`}
                  >
                    {item.risk_level === 'LOW' ? 'SAFE' : 'RISKY'}
                  </span>
                )}

                {canOperate && item.status === 'DRIFT_DETECTED' && (
                  <button
                    onClick={() => onRemediate(item.device_id)}
                    className="px-3 py-1.5 bg-[var(--text-primary)] text-[var(--bg-main)] hover:opacity-90 rounded-md text-xs font-mono font-semibold flex items-center gap-1.5 transition-opacity cursor-pointer"
                  >
                    <Wrench className="w-3.5 h-3.5" />
                    <span>{locale === 'en' ? 'Remediate' : 'Устранить'}</span>
                  </button>
                )}
              </div>
            </div>

            {item.status === 'DRIFT_DETECTED' ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs font-mono pt-3">
                <div className="p-3 rounded-md bg-[var(--bg-main)] border border-rose-500/20 space-y-2">
                  <span className="text-[11px] font-semibold text-rose-500 dark:text-rose-400 uppercase tracking-wider block">
                    + Лишние строки ({item.unauthorized_lines.length})
                  </span>
                  <div className="space-y-1">
                    {item.unauthorized_lines.map((line, i) => (
                      <div key={i} className="text-rose-600 dark:text-rose-300 bg-rose-500/10 px-2 py-1 rounded">
                        + {line}
                      </div>
                    ))}
                    {item.unauthorized_lines.length === 0 && (
                      <span className="text-[var(--text-muted)] italic">Нет</span>
                    )}
                  </div>
                </div>

                <div className="p-3 rounded-md bg-[var(--bg-main)] border border-amber-500/20 space-y-2">
                  <span className="text-[11px] font-semibold text-amber-500 dark:text-amber-400 uppercase tracking-wider block">
                    − Пропущено из Git ({item.missing_lines.length})
                  </span>
                  <div className="space-y-1">
                    {item.missing_lines.map((line, i) => (
                      <div key={i} className="text-amber-600 dark:text-amber-300 bg-amber-500/10 px-2 py-1 rounded">
                        - {line}
                      </div>
                    ))}
                    {item.missing_lines.length === 0 && (
                      <span className="text-[var(--text-muted)] italic">Нет</span>
                    )}
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-xs font-mono text-[var(--text-secondary)] flex items-center gap-2 pt-2.5">
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                <span>100% соответствие Git SoT</span>
              </div>
            )}
          </div>
        ))}

        {report.length === 0 && (
          <div className="p-12 rounded-lg bg-[var(--bg-card)] border border-[var(--border-subtle)] text-center text-[var(--text-muted)] space-y-2 font-mono text-xs">
            <ShieldCheck className="w-6 h-6 mx-auto opacity-40" />
            <p>
              {loading ? 'Загрузка…' : 'Нет данных · Запустите скан'}
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
