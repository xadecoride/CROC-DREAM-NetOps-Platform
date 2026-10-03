import { useState, useEffect } from 'react';
import { DiffEditor, Editor } from '@monaco-editor/react';
import {
  Terminal,
  Play,
  Bot,
  Server,
  Code2,
  Undo2,
  FileCheck,
} from 'lucide-react';
import { api } from '../api';
import type { JobDiff, DeviceDiff, JobSummary } from '../api';

interface DiffViewerProps {
  jobs: JobSummary[];
  selectedJobId: string | null;
  onSelectJob: (jobId: string) => void;
  onDeploy: (jobId: string) => void;
}

export const DiffViewer: React.FC<DiffViewerProps> = ({
  jobs,
  selectedJobId,
  onSelectJob,
  onDeploy,
}) => {
  const [diffData, setDiffData] = useState<JobDiff | null>(null);
  const [selectedDeviceIndex, setSelectedDeviceIndex] = useState(0);
  const [activeViewMode, setActiveViewMode] = useState<'diff' | 'remediation' | 'rollback'>('diff');
  const [loading, setLoading] = useState(false);
  const [aiAnalysis, setAiAnalysis] = useState<{
    summary: string;
    riskLevel: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
    keyPoints: string[];
    recommendations?: string[];
    provider?: string;
  } | null>(null);
  const [analyzingAi, setAnalyzingAi] = useState(false);

  // Filter jobs that have diffs (usually DRY_RUN or DEPLOY)
  const diffJobs = jobs.filter((j) => j.type === 'DRY_RUN' || j.type === 'DEPLOY');

  useEffect(() => {
    if (!selectedJobId) {
      if (diffJobs.length > 0) {
        onSelectJob(diffJobs[0].id);
      }
      return;
    }

    setLoading(true);
    setAiAnalysis(null);
    api
      .getJobDiff(selectedJobId)
      .then((data) => {
        setDiffData(data);
        setSelectedDeviceIndex(0);
        setLoading(false);
      })
      .catch((err) => {
        console.error('Failed to load diff', err);
        setLoading(false);
      });
  }, [selectedJobId]);

  const activeDeviceDiff: DeviceDiff | undefined = diffData?.devices[selectedDeviceIndex];

  // AI Diff Explainer: invokes backend LLM service (MiMo-V2.6-Flash) with graceful fallback
  const handleAnalyzeWithAI = async () => {
    if (!activeDeviceDiff || !selectedJobId) return;
    setAnalyzingAi(true);

    try {
      const res = await api.explainDiff(selectedJobId.toString(), activeDeviceDiff.hostname);
      setAiAnalysis({
        summary: res.summary,
        riskLevel: res.risk_level,
        keyPoints: res.key_points,
        recommendations: res.recommendations,
        provider: res.provider,
      });
    } catch (err) {
      console.warn('Backend LLM explain endpoint failed, using client heuristic fallback', err);
      const remediation = activeDeviceDiff.remediation_patch || '';
      const hasBgp = remediation.includes('bgp') || remediation.includes('router-id');
      const hasInterface = remediation.includes('interface') || remediation.includes('mtu');
      const hasAcl = remediation.includes('access-list') || remediation.includes('permit');

      let riskLevel: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL' = 'LOW';
      const keyPoints: string[] = [];

      if (!remediation.trim()) {
        keyPoints.push('Конфигурация устройства полностью синхронизирована с Git SoT.');
        keyPoints.push('Патч пустой: нет необходимости применять изменения.');
      } else {
        if (hasBgp) {
          riskLevel = 'MEDIUM';
          keyPoints.push('Внесены изменения в конфигурацию процесса BGP или списки соседей.');
          keyPoints.push('Рекомендуется контроль таймеров сходимости BGP (до 60 секунд).');
        }
        if (hasInterface) {
          keyPoints.push('Модификация параметров физических интерфейсов или MTU.');
        }
        if (hasAcl) {
          riskLevel = 'HIGH';
          keyPoints.push('Правка списков контроля доступа (ACL) — риск блокировки трафика.');
        }
        keyPoints.push('Сгенерирован зеркальный патч отката (Rollback Patch) для экстренного восстановления.');
      }

      setAiAnalysis({
        summary: remediation.trim()
          ? `Анализ диффа для ${activeDeviceDiff.hostname}: обнаружены модификации сетевого намерения.`
          : `Устройство ${activeDeviceDiff.hostname} находится в актуальном эталонном состоянии.`,
        riskLevel,
        keyPoints,
        recommendations: ['Проверьте ping до шлюза после наката'],
        provider: 'Client-side Heuristic Fallback',
      });
    } finally {
      setAnalyzingAi(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Top Selector Toolbar */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 flex flex-col md:flex-row items-center justify-between gap-4">
        <div className="flex items-center space-x-3 w-full md:w-auto">
          <Terminal className="w-5 h-5 text-indigo-400" />
          <div>
            <h2 className="text-sm font-bold text-white">Monaco Diff Viewer & AI Assistant</h2>
            <p className="text-xs text-slate-400">
              Двухпанельное иерархическое сравнение (Running vs Intended)
            </p>
          </div>
        </div>

        {/* Job selector */}
        <div className="flex items-center space-x-3 w-full md:w-auto justify-end">
          <label className="text-xs text-slate-400">Задача:</label>
          <select
            value={selectedJobId || ''}
            onChange={(e) => onSelectJob(e.target.value)}
            className="bg-slate-950 border border-slate-800 rounded-xl px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 font-mono"
          >
            {diffJobs.map((j) => (
              <option key={j.id} value={j.id}>
                {j.type} [{j.status}] - {new Date(j.created_at).toLocaleTimeString()}
              </option>
            ))}
          </select>

          {selectedJobId && (
            <button
              onClick={() => onDeploy(selectedJobId)}
              className="px-3.5 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-semibold flex items-center space-x-1.5 transition shadow-lg shadow-indigo-600/20"
            >
              <Play className="w-3 h-3 fill-current" />
              <span>Деплой</span>
            </button>
          )}
        </div>
      </div>

      {/* Main Diff Work Area */}
      {diffData && diffData.devices.length > 0 ? (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          {/* Left: Device Tabs & AI Summary */}
          <div className="lg:col-span-3 space-y-4">
            {/* Device list for this job */}
            <div className="bg-slate-900 border border-slate-800 rounded-2xl p-3 space-y-1.5">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400 px-2 py-1 block">
                Устройства ({diffData.devices.length})
              </span>
              {diffData.devices.map((dev, idx) => {
                const isSelected = idx === selectedDeviceIndex;
                const hasDiff = Boolean(dev.remediation_patch?.trim());
                return (
                  <button
                    key={dev.hostname}
                    onClick={() => {
                      setSelectedDeviceIndex(idx);
                      setAiAnalysis(null);
                    }}
                    className={`w-full text-left p-2.5 rounded-xl border text-xs flex items-center justify-between transition ${
                      isSelected
                        ? 'bg-indigo-950/40 border-indigo-500/50 text-white font-medium'
                        : 'bg-slate-950/30 border-slate-800/80 text-slate-400 hover:border-slate-700'
                    }`}
                  >
                    <div className="flex items-center space-x-2">
                      <Server className="w-3.5 h-3.5 text-slate-500" />
                      <span className="font-mono">{dev.hostname}</span>
                    </div>
                    {hasDiff ? (
                      <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/20">
                        Δ Изменения
                      </span>
                    ) : (
                      <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                        In Sync
                      </span>
                    )}
                  </button>
                );
              })}
            </div>

            {/* AI Assistant Card */}
            <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-1.5">
                  <Bot className="w-4 h-4 text-cyan-400" />
                  <span className="text-xs font-bold text-white">LLM Risk Assistant</span>
                </div>
                <button
                  onClick={handleAnalyzeWithAI}
                  disabled={analyzingAi}
                  className="px-2.5 py-1 rounded-lg bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-300 text-[11px] font-medium border border-cyan-500/30 transition flex items-center space-x-1"
                >
                  <span>{analyzingAi ? 'Анализ...' : 'Оценить риски'}</span>
                </button>
              </div>

              {aiAnalysis ? (
                <div className="space-y-2.5 text-xs">
                  <div className="flex items-center justify-between">
                    <span className="text-slate-400 text-[11px]">Уровень риска:</span>
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                        aiAnalysis.riskLevel === 'HIGH'
                          ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                          : aiAnalysis.riskLevel === 'MEDIUM'
                          ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40'
                          : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                      }`}
                    >
                      {aiAnalysis.riskLevel} RISK
                    </span>
                  </div>

                  <p className="text-slate-300 text-[11px] leading-relaxed">
                    {aiAnalysis.summary}
                  </p>

                  <div className="space-y-1 pt-1 border-t border-slate-800">
                    <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Факторы риска:</span>
                    {aiAnalysis.keyPoints.map((pt, i) => (
                      <div key={i} className="flex items-start space-x-1.5 text-[11px] text-slate-400">
                        <span className="text-cyan-400 mt-0.5">•</span>
                        <span>{pt}</span>
                      </div>
                    ))}
                  </div>

                  {aiAnalysis.recommendations && aiAnalysis.recommendations.length > 0 && (
                    <div className="space-y-1 pt-1 border-t border-slate-800">
                      <span className="text-[10px] font-semibold text-cyan-400 uppercase tracking-wider">Рекомендации:</span>
                      {aiAnalysis.recommendations.map((rec, i) => (
                        <div key={i} className="flex items-start space-x-1.5 text-[11px] text-slate-300">
                          <span className="text-emerald-400 mt-0.5">✓</span>
                          <span>{rec}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {aiAnalysis.provider && (
                    <div className="pt-1 flex items-center justify-between text-[10px] text-slate-500 border-t border-slate-800/60">
                      <span>Провайдер анализа:</span>
                      <span className="font-mono text-cyan-400/80">{aiAnalysis.provider}</span>
                    </div>
                  )}
                </div>
              ) : (
                <p className="text-[11px] text-slate-500">
                  Нажмите «Оценить риски» для автоматического аудита сгенерированных CLI-команд через языковую модель.
                </p>
              )}
            </div>
          </div>

          {/* Right: Monaco Editor Area */}
          <div className="lg:col-span-9 bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden flex flex-col min-h-[620px]">
            {/* View Mode Bar */}
            <div className="px-4 py-2.5 bg-slate-950/80 border-b border-slate-800 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center space-x-2">
                <button
                  onClick={() => setActiveViewMode('diff')}
                  className={`px-3 py-1 rounded-lg text-xs font-medium transition flex items-center space-x-1.5 ${
                    activeViewMode === 'diff'
                      ? 'bg-indigo-600 text-white'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
                  }`}
                >
                  <Code2 className="w-3.5 h-3.5" />
                  <span>Monaco Side-by-Side</span>
                </button>

                <button
                  onClick={() => setActiveViewMode('remediation')}
                  className={`px-3 py-1 rounded-lg text-xs font-medium transition flex items-center space-x-1.5 ${
                    activeViewMode === 'remediation'
                      ? 'bg-emerald-600 text-white'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
                  }`}
                >
                  <FileCheck className="w-3.5 h-3.5" />
                  <span>Remediation Patch (+)</span>
                </button>

                <button
                  onClick={() => setActiveViewMode('rollback')}
                  className={`px-3 py-1 rounded-lg text-xs font-medium transition flex items-center space-x-1.5 ${
                    activeViewMode === 'rollback'
                      ? 'bg-rose-600 text-white'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
                  }`}
                >
                  <Undo2 className="w-3.5 h-3.5" />
                  <span>Rollback Patch (-)</span>
                </button>
              </div>

              {activeDeviceDiff && (
                <div className="text-xs text-slate-400 font-mono">
                  {activeDeviceDiff.hostname}
                </div>
              )}
            </div>

            {/* Monaco Container */}
            <div className="flex-1 w-full h-[560px]">
              {activeDeviceDiff ? (
                activeViewMode === 'diff' ? (
                  <DiffEditor
                    height="100%"
                    theme="vs-dark"
                    original={activeDeviceDiff.running_config || '# Running config empty'}
                    modified={activeDeviceDiff.intended_config || '# Intended config empty'}
                    language="shell"
                    options={{
                      readOnly: true,
                      renderSideBySide: true,
                      minimap: { enabled: false },
                      fontSize: 12,
                      scrollBeyondLastLine: false,
                    }}
                  />
                ) : activeViewMode === 'remediation' ? (
                  <Editor
                    height="100%"
                    theme="vs-dark"
                    value={
                      activeDeviceDiff.remediation_patch?.trim() ||
                      '! No remediation commands needed (Already in sync)'
                    }
                    language="shell"
                    options={{
                      readOnly: true,
                      minimap: { enabled: false },
                      fontSize: 12,
                      scrollBeyondLastLine: false,
                    }}
                  />
                ) : (
                  <Editor
                    height="100%"
                    theme="vs-dark"
                    value={
                      activeDeviceDiff.rollback_patch?.trim() ||
                      '! No rollback commands needed'
                    }
                    language="shell"
                    options={{
                      readOnly: true,
                      minimap: { enabled: false },
                      fontSize: 12,
                      scrollBeyondLastLine: false,
                    }}
                  />
                )
              ) : (
                <div className="p-16 text-center text-slate-500">
                  Выберите устройство для просмотра различий.
                </div>
              )}
            </div>
          </div>
        </div>
      ) : (
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-16 text-center text-slate-500 space-y-3">
          <Code2 className="w-10 h-10 mx-auto opacity-30 text-indigo-400" />
          <p className="text-sm">
            {loading ? 'Загрузка диффа...' : 'Нет доступных диффов. Запустите Холостой прогон (Dry Run).'}
          </p>
        </div>
      )}
    </div>
  );
};
