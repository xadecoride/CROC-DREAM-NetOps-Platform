import React, { useState, useEffect, useMemo, useRef } from 'react';
import { DiffEditor, Editor } from '@monaco-editor/react';
import { Terminal, Play, Server, Code2, Undo2, FileCheck, GitBranch } from 'lucide-react';
import { api, getErrorMessage } from '../../api';
import type { JobDiff, DeviceDiff, JobSummary, RiskExplanation } from '../../api';
import { translations, type Locale } from '../../i18n';
import type { AppTheme } from '../layout/Header';
import { IntentEditor } from './IntentEditor';
import { PlainDiffFallback, PlainCodeFallback, DiffDirectToolbar, extractCliPatchFromDiff, useMonacoTheme } from './DiffViewerFallbacks';
import { DiffAiRiskCard } from './DiffAiRiskCard';

interface DiffViewerProps {
  jobs: JobSummary[];
  selectedJobId: string | null;
  targetDeviceId?: number | null;
  userRole?: import('../../api').UserRole;
  locale?: Locale;
  theme?: AppTheme;
  onSelectJob: (jobId: string) => void;
  onDeploy: (jobId: string) => void;
}

const DiffViewerComponent: React.FC<DiffViewerProps> = ({
  jobs, selectedJobId, targetDeviceId = null, userRole = 'viewer', locale = 'ru', theme = 'dark', onSelectJob, onDeploy,
}) => {
  const canOperate = userRole !== 'viewer';
  const isAdmin = userRole === 'admin' || userRole === 'owner';
  const isEn = locale === 'en';
  const t = translations[locale];
  const monacoTheme = useMonacoTheme(theme);

  const [diffData, setDiffData] = useState<JobDiff | null>(null);
  const [selectedDeviceIndex, setSelectedDeviceIndex] = useState(0);
  const [activeViewMode, setActiveViewMode] = useState<'diff' | 'remediation' | 'rollback' | 'intent'>('diff');
  const [isDirectEditOpen, setIsDirectEditOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [prevJobKey, setPrevJobKey] = useState<string>('');
  const [committingGit, setCommittingGit] = useState(false);
  const [gitCommitNote, setGitCommitNote] = useState('');
  const [gitCommitError, setGitCommitError] = useState(false);

  const [editedConfigs, setEditedConfigs] = useState<Record<string, string>>({});
  const [editedPatches, setEditedPatches] = useState<Record<string, string>>({});
  const [applyingPatch, setApplyingPatch] = useState(false);
  const [applyStatus, setApplyStatus] = useState<{ ok: boolean; text: string } | null>(null);

  const [aiAnalysis, setAiAnalysis] = useState<RiskExplanation | null>(null);
  const [analyzingAi, setAnalyzingAi] = useState(false);
  const [isMobile, setIsMobile] = useState(() => typeof window !== 'undefined' && window.innerWidth < 768);

  const diffJobs = useMemo(
    () => jobs.filter((j) => j.type === 'DRY_RUN' || j.type === 'DEPLOY' || j.type === 'DRIFT_SCAN' || j.type === 'DRIFT_REMEDIATE'),
    [jobs]
  );

  const selectedDiffJob = useMemo(() => (selectedJobId ? diffJobs.find((j) => j.id === selectedJobId) : undefined), [selectedJobId, diffJobs]);
  const currentJobKey = `${selectedJobId ?? ''}:${selectedDiffJob?.status ?? ''}`;

  if (currentJobKey !== prevJobKey) {
    setPrevJobKey(currentJobKey);
    setAiAnalysis(null);
    setGitCommitNote('');
    setGitCommitError(false);
    setApplyStatus(null);
    setDiffData(null);
    setLoading(Boolean(selectedDiffJob));
  }

  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useEffect(() => {
    if (!selectedDiffJob) {
      const readyJob = diffJobs.find((j) => j.status === 'SUCCESS' || j.status === 'FAILED') || diffJobs[0];
      if (readyJob) onSelectJob(readyJob.id);
      return;
    }
    if (selectedDiffJob.status === 'RUNNING' || selectedDiffJob.status === 'PENDING') return;

    let active = true;
    api
      .getJobDiff(selectedDiffJob.id)
      .then((data) => {
        if (!active) return;
        setDiffData(data);
        const matchIdx = targetDeviceId !== null ? data.devices.findIndex((d) => d.device_id === targetDeviceId) : -1;
        setSelectedDeviceIndex(matchIdx >= 0 ? matchIdx : 0);
        setLoading(false);
      })
      .catch((err) => {
        if (!active) return;
        console.warn('Job diff pending or unavailable', err);
        setDiffData(null);
        setLoading(false);
      });
    return () => { active = false; };
  }, [selectedDiffJob, diffJobs, onSelectJob, targetDeviceId]);

  const canDirectEdit = canOperate && isDirectEditOpen;

  const diffEditorOptions = useMemo(
    () => ({ readOnly: !canDirectEdit, originalEditable: false, renderSideBySide: !isMobile, minimap: { enabled: false }, fontSize: isMobile ? 11 : 12, scrollBeyondLastLine: false }),
    [canDirectEdit, isMobile]
  );

  const singleEditorOptions = useMemo(
    () => ({ readOnly: !canDirectEdit, minimap: { enabled: false }, fontSize: 12, scrollBeyondLastLine: false }),
    [canDirectEdit]
  );

  const activeDeviceDiff: DeviceDiff | undefined = diffData?.devices[selectedDeviceIndex];
  const hostKey = activeDeviceDiff?.hostname || '';
  const defaultModifiedConfig = activeDeviceDiff?.intended_config || activeDeviceDiff?.running_config || '# Intended config empty';
  const currentModifiedConfig = hostKey && editedConfigs[hostKey] !== undefined ? editedConfigs[hostKey] : defaultModifiedConfig;
  const defaultRemediationPatch =
    activeDeviceDiff?.remediation_patch?.trim() ||
    (isEn ? '! Enter CLI commands to apply on device' : '! Введите CLI-команды для применения на устройстве');
  const currentRemediationPatch = hostKey && editedPatches[hostKey] !== undefined ? editedPatches[hostKey] : defaultRemediationPatch;
  const hasCustomEdit = Boolean(hostKey && (editedConfigs[hostKey] !== undefined || editedPatches[hostKey] !== undefined));

  const updateEditedConfig = (val: string) => {
    if (!hostKey || !activeDeviceDiff) return;
    setEditedConfigs((prev) => ({ ...prev, [hostKey]: val }));
    const extracted = extractCliPatchFromDiff(activeDeviceDiff.running_config || '', val, activeDeviceDiff.remediation_patch || '', defaultModifiedConfig);
    setEditedPatches((prev) => ({ ...prev, [hostKey]: extracted }));
  };

  const updateEditedConfigRef = useRef(updateEditedConfig);
  useEffect(() => {
    updateEditedConfigRef.current = updateEditedConfig;
  });

  const updateEditedPatch = (val: string) => {
    if (hostKey) setEditedPatches((prev) => ({ ...prev, [hostKey]: val }));
  };

  const resetEditsForDevice = () => {
    if (!hostKey) return;
    setEditedConfigs((prev) => { const n = { ...prev }; delete n[hostKey]; return n; });
    setEditedPatches((prev) => { const n = { ...prev }; delete n[hostKey]; return n; });
    setApplyStatus(null);
  };

  const resolvedCliLines = useMemo(() => {
    if (!activeDeviceDiff) return [];
    const rawPatch =
      editedPatches[hostKey] ??
      (editedConfigs[hostKey] !== undefined
        ? extractCliPatchFromDiff(activeDeviceDiff.running_config || '', editedConfigs[hostKey], activeDeviceDiff.remediation_patch || '', defaultModifiedConfig)
        : activeDeviceDiff.remediation_patch || '');
    return rawPatch.split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('!') && !l.startsWith('#'));
  }, [activeDeviceDiff, editedPatches, editedConfigs, hostKey, defaultModifiedConfig]);

  const handleApplyDirect = async () => {
    if (!activeDeviceDiff) return;
    if (resolvedCliLines.length === 0) {
      setApplyStatus({
        ok: false,
        text: isEn
          ? 'Modify config lines on the right or enter CLI commands in Remediation tab.'
          : 'Измените строки конфига справа или введите CLI-команды во вкладке «Патч наката».',
      });
      return;
    }
    setApplyingPatch(true);
    setApplyStatus(null);
    try {
      const res = await api.applyEmergencyPatch(
        activeDeviceDiff.hostname,
        resolvedCliLines.join('\n'),
        isEn ? 'Direct configuration edit from Diff Studio' : 'Прямая правка конфигурации из Diff & Config'
      );
      setApplyStatus({
        ok: true,
        text: isEn
          ? `Applied to ${activeDeviceDiff.hostname} (${res.applied_lines.length} cmd(s)). Drift scan started.`
          : `Применено на ${activeDeviceDiff.hostname} (${res.applied_lines.length} команд). Скан синхронизации запущен.`,
      });
      if (activeDeviceDiff.device_id) {
        const scan = await api.scanDrift([activeDeviceDiff.device_id]);
        onSelectJob(scan.job_id);
      }
      window.dispatchEvent(new Event('netops-topology-mutated'));
    } catch (err) {
      setApplyStatus({ ok: false, text: `${isEn ? 'Apply error' : 'Ошибка применения'}: ${getErrorMessage(err)}` });
    } finally {
      setApplyingPatch(false);
    }
  };

  const handleAnalyzeWithAI = async () => {
    if (!activeDeviceDiff || !selectedJobId) return;
    setAnalyzingAi(true);
    try {
      setAiAnalysis(await api.explainDiff(selectedJobId.toString(), activeDeviceDiff.hostname));
    } catch (err) {
      setAiAnalysis({
        risk_level: 'MEDIUM',
        summary: getErrorMessage(err, isEn ? 'Risk assessment error' : 'Ошибка оценки рисков'),
        key_points: [isEn ? 'Failed to receive analyzer response' : 'Не удалось получить ответ от анализатора'],
        recommendations: [isEn ? 'Review remediation patch before deploy' : 'Проверьте патч наката перед деплоем'],
        is_safe: false,
        provider: 'Fallback Guard',
      });
    } finally {
      setAnalyzingAi(false);
    }
  };

  const handleCommitDiffToGit = async () => {
    if (!selectedJobId) return;
    setCommittingGit(true);
    setGitCommitNote('');
    setGitCommitError(false);
    try {
      const res = await api.commitDiffToGit(selectedJobId);
      setGitCommitError(!res.pushed);
      setGitCommitNote(
        res.pushed
          ? `Pushed: ${res.branch}@${res.commit_short}`
          : `${isEn ? 'Local' : 'Локально'} ${res.commit_short} (Push: ${res.push_output || (isEn ? 'error' : 'ошибка')})`
      );
      window.dispatchEvent(new Event('netops-git-updated'));
      window.dispatchEvent(new Event('netops-topology-mutated'));
    } catch (err: unknown) {
      setGitCommitError(true);
      setGitCommitNote(getErrorMessage(err, isEn ? 'Git push error' : 'Ошибка Git push'));
    } finally {
      setCommittingGit(false);
    }
  };

  const canDeployAny = Boolean(hasCustomEdit || diffData?.devices.some((d) => Boolean(d.remediation_patch?.trim())));

  return (
    <div className="w-full max-w-[2560px] mx-auto space-y-4 2xl:space-y-6">
      <div className="p-3 sm:p-4 rounded-2xl bg-[var(--bg-card)] border border-[var(--border-subtle)] flex flex-col md:flex-row md:items-center justify-between gap-3">
        <div className="flex items-center space-x-3 min-w-0">
          <div className="p-2 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-subtle)] shrink-0">
            <Terminal className="w-4 h-4 text-[var(--text-primary)]" />
          </div>
          <div className="flex flex-wrap items-center gap-2 min-w-0">
            <h2 className="text-sm font-bold font-mono text-[var(--text-primary)]">{t.diffTitle}</h2>
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[var(--bg-elevated)] text-[var(--text-secondary)] border border-[var(--border-subtle)]">
              hier_config AST · Live Edit
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 w-full md:w-auto md:justify-end">
          <label className="text-xs font-mono text-[var(--text-muted)] shrink-0">{isEn ? 'Job:' : 'Задача:'}</label>
          <select
            value={selectedJobId || ''}
            onChange={(e) => onSelectJob(e.target.value)}
            disabled={diffJobs.length === 0}
            className="w-full sm:w-auto sm:min-w-[200px] bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-xl px-3 py-1.5 text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--border-hover)] font-mono cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed shadow-sm"
          >
            {diffJobs.length === 0 ? (
              <option value="">{isEn ? 'No jobs (run Dry-run)' : 'Нет задач (нужен Dry-run)'}</option>
            ) : (
              diffJobs.map((j) => (
                <option key={j.id} value={j.id} className="bg-[var(--bg-elevated)] text-[var(--text-primary)]">
                  {j.type} [{j.status}] - {new Date(j.created_at).toLocaleTimeString()}
                </option>
              ))
            )}
          </select>

          {canOperate && selectedJobId && (
            <button
              onClick={handleCommitDiffToGit}
              disabled={committingGit}
              data-testid="commit-diff-git-btn"
              className={`px-3 py-1.5 rounded-xl text-xs font-mono font-semibold flex items-center space-x-1.5 transition cursor-pointer disabled:opacity-50 border ${
                gitCommitError
                  ? 'bg-rose-500/15 hover:bg-rose-500/25 text-rose-600 dark:text-rose-300 border-rose-500/35'
                  : gitCommitNote
                    ? 'bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-600 dark:text-emerald-300 border-emerald-500/35'
                    : 'bg-[var(--bg-elevated)] hover:bg-[var(--bg-subtle)] text-[var(--text-primary)] border-[var(--border-subtle)] hover:border-[var(--border-hover)]'
              }`}
              title={isEn ? 'Commit Diff snapshot and push to Git' : 'Сделать git commit снапшота Diff и отправить в Git'}
            >
              <GitBranch className="w-3.5 h-3.5 shrink-0" />
              <span>{committingGit ? 'Git Commit…' : gitCommitNote || (isEn ? 'Commit to Git' : 'Коммит в Git')}</span>
            </button>
          )}

          {canOperate && selectedJobId && (
            <button
              onClick={() => (hasCustomEdit ? handleApplyDirect() : onDeploy(selectedJobId))}
              disabled={!canDeployAny || applyingPatch}
              title={
                !canDeployAny
                  ? isEn ? 'All devices in sync' : 'Все устройства в синхроне (In Sync)'
                  : isEn ? 'Deploy baseline or custom edits' : 'Запустить деплой эталона или правок'
              }
              className="px-3.5 py-1.5 bg-[var(--text-primary)] hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed text-[var(--bg-main)] rounded-xl text-xs font-semibold flex items-center space-x-1.5 transition cursor-pointer"
            >
              <Play className="w-3 h-3 fill-current shrink-0" />
              <span>{isEn ? 'Deploy' : 'Деплой'}</span>
            </button>
          )}
        </div>
      </div>

      {diffData && diffData.devices.length > 0 ? (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 2xl:gap-6">
          <div className="lg:col-span-3 space-y-4 min-w-0">
            <div className="p-3 rounded-2xl bg-[var(--bg-card)] border border-[var(--border-subtle)] space-y-1.5">
              <span className="text-[11px] font-mono font-semibold uppercase tracking-wider text-[var(--text-muted)] px-2 py-1 block">
                {isEn ? 'Devices' : 'Устройства'} ({diffData.devices.length})
              </span>
              {diffData.devices.map((dev, idx) => {
                const isSelected = idx === selectedDeviceIndex;
                const hasDiff = Boolean(dev.remediation_patch?.trim() || editedPatches[dev.hostname] || editedConfigs[dev.hostname]);
                return (
                  <button
                    key={dev.hostname}
                    onClick={() => {
                      setSelectedDeviceIndex(idx);
                      setAiAnalysis(null);
                      setApplyStatus(null);
                    }}
                    className={`w-full text-left p-2.5 rounded-xl border text-xs font-mono flex items-center justify-between transition cursor-pointer ${
                      isSelected
                        ? 'bg-[var(--bg-elevated)] border-[var(--border-hover)] text-[var(--text-primary)] font-medium'
                        : 'bg-[var(--bg-main)]/50 border-[var(--border-subtle)] text-[var(--text-secondary)] hover:border-[var(--border-hover)]'
                    }`}
                  >
                    <div className="flex items-center space-x-2 truncate">
                      <Server className="w-3.5 h-3.5 text-[var(--text-muted)] shrink-0" />
                      <span className="truncate text-[var(--text-primary)]">{dev.hostname}</span>
                    </div>
                    {hasDiff ? (
                      <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20 shrink-0">
                        {isEn ? 'Δ Drift' : 'Δ Изменения'}
                      </span>
                    ) : (
                      <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 shrink-0">
                        In Sync
                      </span>
                    )}
                  </button>
                );
              })}
            </div>

            <DiffAiRiskCard
              analyzingAi={analyzingAi}
              aiAnalysis={aiAnalysis}
              locale={locale}
              onAnalyzeWithAI={handleAnalyzeWithAI}
            />
          </div>

          <div className="lg:col-span-9 p-3 sm:p-4 rounded-2xl bg-[var(--bg-card)] border border-[var(--border-subtle)] overflow-hidden flex flex-col min-h-[360px] sm:min-h-[520px] 2xl:min-h-[680px] min-w-0">
            <div className="px-3 py-2 bg-[var(--bg-main)]/80 rounded-xl border border-[var(--border-subtle)] flex flex-wrap items-center justify-between gap-2 mb-3">
              <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
                {(
                  [
                    { id: 'diff', label: t.modeDiff, Icon: Code2 },
                    { id: 'remediation', label: t.modeRemediation, Icon: FileCheck },
                    { id: 'rollback', label: t.modeRollback, Icon: Undo2 },
                    { id: 'intent', label: 'Intent (YAML)', Icon: GitBranch },
                  ] as const
                ).map((tab) => (
                  <button
                    key={tab.id}
                    onClick={() => setActiveViewMode(tab.id)}
                    className={`px-2.5 sm:px-3 py-1 rounded-lg text-xs font-mono transition flex items-center space-x-1.5 cursor-pointer border ${
                      activeViewMode === tab.id
                        ? 'bg-[var(--bg-elevated)] text-[var(--text-primary)] border-[var(--border-hover)] font-semibold'
                        : 'border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                    }`}
                  >
                    <tab.Icon className="w-3.5 h-3.5" />
                    <span>{tab.label}</span>
                  </button>
                ))}
              </div>

              <div className="flex items-center space-x-2">
                {canOperate && activeViewMode !== 'intent' && activeViewMode !== 'rollback' && (
                  <button
                    onClick={() => setIsDirectEditOpen((v) => !v)}
                    data-testid="toggle-direct-edit-btn"
                    className={`px-2.5 py-1 rounded-lg text-xs font-mono transition flex items-center space-x-1.5 border cursor-pointer ${
                      isDirectEditOpen
                        ? 'bg-[var(--bg-card)] border-[var(--border-hover)] text-[var(--text-primary)] font-semibold'
                        : 'bg-[var(--bg-elevated)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                    }`}
                    title={isEn ? 'Toggle direct configuration edit mode' : 'Включить или выключить режим прямой правки'}
                  >
                    <span>{isEn ? '✎ Direct Edit' : '✎ Прямая правка'}</span>
                  </button>
                )}
                {activeDeviceDiff && <div className="text-xs text-[var(--text-muted)] font-mono truncate">{activeDeviceDiff.hostname}</div>}
              </div>
            </div>

            {activeDeviceDiff && isDirectEditOpen && activeViewMode !== 'intent' && (
              <DiffDirectToolbar
                hostname={activeDeviceDiff.hostname}
                locale={locale}
                canOperate={canOperate}
                isAdmin={isAdmin}
                hasCustomEdit={hasCustomEdit}
                changedLinesCount={resolvedCliLines.length}
                applyingPatch={applyingPatch}
                applyStatus={applyStatus}
                onApplyDirect={handleApplyDirect}
                onResetEdits={resetEditsForDevice}
              />
            )}

            <div className="flex-1 w-full h-[360px] sm:h-[520px] 2xl:h-[680px] rounded-xl overflow-hidden border border-[var(--border-subtle)]">
              {!activeDeviceDiff ? (
                <div className="p-16 text-center text-[var(--text-muted)] font-mono">
                  {isEn ? 'Select a device to inspect configuration diff.' : 'Выберите устройство для просмотра различий.'}
                </div>
              ) : activeViewMode === 'intent' ? (
                activeDeviceDiff.device_id ? (
                  <IntentEditor deviceId={activeDeviceDiff.device_id} canEdit={isAdmin} theme={theme} locale={locale} />
                ) : (
                  <div className="p-8 text-center text-[var(--text-muted)] font-mono">
                    {isEn ? 'Device is not bound to DB.' : 'Устройство не привязано к БД.'}
                  </div>
                )
              ) : activeViewMode === 'diff' ? (
                <DiffEditor
                  key={`diff-${hostKey}-${selectedJobId}`}
                  height="100%"
                  theme={monacoTheme}
                  original={activeDeviceDiff.running_config || '# Running config empty'}
                  modified={currentModifiedConfig}
                  language="shell"
                  keepCurrentOriginalModel={true}
                  keepCurrentModifiedModel={true}
                  onMount={(editor) => {
                    const mod = editor.getModifiedEditor();
                    mod.onDidChangeModelContent(() => updateEditedConfigRef.current(mod.getValue()));
                  }}
                  loading={
                    <PlainDiffFallback
                      original={activeDeviceDiff.running_config || '# Running config empty'}
                      modified={currentModifiedConfig}
                      isMobile={isMobile}
                      theme={theme}
                      locale={locale}
                      canEdit={canDirectEdit}
                      onEditModified={updateEditedConfig}
                    />
                  }
                  options={diffEditorOptions}
                />
              ) : (
                (() => {
                  const isRem = activeViewMode === 'remediation';
                  const codeVal = isRem
                    ? currentRemediationPatch
                    : activeDeviceDiff.rollback_patch?.trim() || '! No rollback commands needed';
                  const label = isRem
                    ? isEn ? 'Remediation Patch (CLI)' : 'Патч наката (Remediation)'
                    : isEn ? 'Rollback Patch' : 'Патч отката (Rollback)';
                  return (
                    <Editor
                      key={`${activeViewMode}-${hostKey}-${selectedJobId}`}
                      height="100%"
                      theme={monacoTheme}
                      value={codeVal}
                      onChange={isRem && canDirectEdit ? (val) => updateEditedPatch(val ?? '') : undefined}
                      language="shell"
                      keepCurrentModel={true}
                      loading={
                        <PlainCodeFallback
                          code={codeVal}
                          label={label}
                          theme={theme}
                          locale={locale}
                          canEdit={isRem && canDirectEdit}
                          onEdit={isRem ? updateEditedPatch : undefined}
                        />
                      }
                      options={isRem ? singleEditorOptions : { ...singleEditorOptions, readOnly: true }}
                    />
                  );
                })()
              )}
            </div>
          </div>
        </div>
      ) : (
        <div className="min-h-[360px] sm:min-h-[520px] 2xl:min-h-[680px] flex flex-col items-center justify-center p-12 rounded-2xl bg-[var(--bg-card)] border border-[var(--border-subtle)] text-center text-[var(--text-muted)] space-y-3 font-mono">
          <Code2 className="w-8 h-8 mx-auto opacity-30 text-[var(--text-secondary)]" />
          <p className="text-sm">{loading ? t.loading : t.noDiffFound}</p>
        </div>
      )}
    </div>
  );
};

export const DiffViewer = React.memo(DiffViewerComponent);

