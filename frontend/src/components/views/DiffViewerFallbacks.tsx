import React, { useState, useEffect } from 'react';
import { Check, Copy, Play, RotateCcw, Lock, Terminal, Send } from 'lucide-react';
import { api, getErrorMessage } from '../../api';
import type { Locale } from '../../i18n';
import type { AppTheme } from '../layout/Header';

export function useMonacoTheme(theme?: AppTheme): 'light' | 'vs-dark' {
  const [domLight, setDomLight] = useState(
    () => typeof document !== 'undefined' && document.documentElement.getAttribute('data-theme') === 'light'
  );

  useEffect(() => {
    if (typeof document === 'undefined') return;
    const syncTheme = () => setDomLight(document.documentElement.getAttribute('data-theme') === 'light');
    syncTheme();
    const obs = new MutationObserver(syncTheme);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] });
    return () => obs.disconnect();
  }, []);

  return theme === 'light' || domLight ? 'light' : 'vs-dark';
}

interface PlainDiffProps {
  original: string;
  modified: string;
  isMobile: boolean;
  theme: AppTheme;
  locale?: Locale;
  canEdit?: boolean;
  onEditModified?: (val: string) => void;
}

export const PlainDiffFallback: React.FC<PlainDiffProps> = ({
  original,
  modified,
  isMobile,
  locale = 'ru',
  canEdit = false,
  onEditModified,
}) => {
  const isEn = locale === 'en';
  const origLines = (original || '').split('\n');
  const modLines = (modified || '').split('\n');

  return (
    <div className="h-full w-full overflow-auto font-mono text-xs bg-[var(--bg-main)] text-[var(--text-primary)]">
      <div className={`grid ${isMobile ? 'grid-cols-1 divide-y' : 'grid-cols-2 divide-x'} divide-[var(--border-subtle)] h-full`}>
        <div>
          <div className="sticky top-0 px-3 py-1.5 font-bold text-[10px] uppercase tracking-wider border-b bg-[var(--bg-elevated)] text-[var(--text-secondary)] border-[var(--border-subtle)]">
            {isEn ? 'Running on Device (Running Config)' : 'Текущая на устройстве (Running Config)'}
          </div>
          <div className="p-2 space-y-0.5">
            {origLines.map((line, idx) => {
              const isDiff = line !== modLines[idx];
              return (
                <div
                  key={`orig-${idx}`}
                  className={`flex items-start px-2 py-0.5 rounded-xs ${isDiff && line ? 'bg-rose-500/15 text-rose-600 dark:text-rose-400 font-medium' : ''}`}
                >
                  <span className="w-8 select-none text-right pr-3 opacity-40 text-[10px]">{idx + 1}</span>
                  <span className="whitespace-pre overflow-x-auto flex-1">{line || ' '}</span>
                </div>
              );
            })}
          </div>
        </div>

        <div className="flex flex-col h-full">
          <div className="sticky top-0 px-3 py-1.5 font-bold text-[10px] uppercase tracking-wider border-b bg-[var(--bg-elevated)] text-[var(--text-secondary)] border-[var(--border-subtle)] flex items-center justify-between">
            <span>{isEn ? 'Target / Editable (Intended Config)' : 'Целевая / Редактируемая (Intended Config)'}</span>
            {canEdit && (
              <span className="text-[10px] text-[var(--text-primary)] normal-case">
                {isEn ? 'Direct edit active' : 'Доступно редактирование'}
              </span>
            )}
          </div>
          {canEdit && onEditModified ? (
            <textarea
              value={modified}
              onChange={(e) => onEditModified(e.target.value)}
              spellCheck={false}
              className="flex-1 w-full min-h-[320px] p-3 font-mono text-xs bg-transparent text-[var(--text-primary)] focus:outline-none resize-none leading-relaxed"
            />
          ) : (
            <div className="p-2 space-y-0.5">
              {modLines.map((line, idx) => {
                const isDiff = line !== origLines[idx];
                return (
                  <div
                    key={`mod-${idx}`}
                    className={`flex items-start px-2 py-0.5 rounded-xs ${isDiff && line ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 font-medium' : ''}`}
                  >
                    <span className="w-8 select-none text-right pr-3 opacity-40 text-[10px]">{idx + 1}</span>
                    <span className="whitespace-pre overflow-x-auto flex-1">{line || ' '}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export const PlainCodeFallback: React.FC<{
  code: string;
  label: string;
  theme?: AppTheme;
  locale?: Locale;
  canEdit?: boolean;
  onEdit?: (val: string) => void;
}> = ({ code, label, locale = 'ru', canEdit = false, onEdit }) => {
  const isEn = locale === 'en';
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const lines = (code || '').split('\n');

  return (
    <div className="relative h-full w-full flex flex-col font-mono text-xs bg-[var(--bg-main)] text-[var(--text-primary)]">
      <div className="flex items-center justify-between px-3 py-1.5 border-b bg-[var(--bg-elevated)] text-[var(--text-secondary)] border-[var(--border-subtle)]">
        <span className="text-[10px] uppercase font-bold tracking-wider">{label}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] bg-[var(--bg-card)] hover:bg-[var(--bg-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] border border-[var(--border-subtle)] transition cursor-pointer"
        >
          {copied ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
          <span>{copied ? (isEn ? 'Copied!' : 'Скопировано!') : isEn ? 'Copy' : 'Копировать'}</span>
        </button>
      </div>
      {canEdit && onEdit ? (
        <textarea
          value={code}
          onChange={(e) => onEdit(e.target.value)}
          spellCheck={false}
          className="flex-1 w-full p-3 font-mono text-xs bg-transparent text-[var(--text-primary)] focus:outline-none resize-none leading-relaxed"
        />
      ) : (
        <div className="flex-1 overflow-auto p-2 space-y-0.5">
          {lines.map((line, idx) => (
            <div key={idx} className="flex items-start px-2 py-0.5">
              <span className="w-8 select-none text-right pr-3 opacity-40 text-[10px]">{idx + 1}</span>
              <span
                className={`whitespace-pre overflow-x-auto flex-1 ${
                  line.startsWith('!')
                    ? 'opacity-50 italic'
                    : line.startsWith('no ') || line.startsWith('undo ')
                      ? 'text-rose-600 dark:text-rose-400 font-semibold'
                      : line.startsWith('interface ') || line.startsWith('router ')
                        ? 'text-[var(--text-primary)] font-bold'
                        : ''
                }`}
              >
                {line || ' '}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

interface ParsedConfigLine {
  trimmed: string;
  section: string;
  isIndented: boolean;
}

function parseConfigSections(text: string): ParsedConfigLine[] {
  const parsed: ParsedConfigLine[] = [];
  let currentSection = '';
  for (const raw of (text || '').split('\n')) {
    const trimmed = raw.trim();
    if (!trimmed || trimmed.startsWith('!') || trimmed.startsWith('#')) continue;
    const isIndented = raw.startsWith(' ') || raw.startsWith('\t');
    if (!isIndented) currentSection = trimmed;
    parsed.push({ trimmed, section: isIndented ? currentSection : '', isIndented });
  }
  return parsed;
}

export function extractCliPatchFromDiff(
  runningConfig: string,
  modifiedConfig: string,
  basePatch: string,
  baselineIntendedConfig?: string
): string {
  const baseText = baselineIntendedConfig !== undefined ? baselineIntendedConfig : runningConfig || '';
  const baseEntries = parseConfigSections(baseText);
  const runEntries = parseConfigSections(runningConfig || '');
  const modEntries = parseConfigSections(modifiedConfig || '');

  const baseKeySet = new Set(baseEntries.map((e) => `${e.section}::${e.trimmed}`));
  const runKeySet = new Set(runEntries.map((e) => `${e.section}::${e.trimmed}`));
  const modKeySet = new Set(modEntries.map((e) => `${e.section}::${e.trimmed}`));
  const modSectionHeaders = new Set(modEntries.filter((e) => !e.isIndented).map((e) => e.trimmed));

  const out: string[] = [];
  let pushedSection = '';

  const ensureSection = (sec: string) => {
    if (sec && pushedSection !== sec) {
      out.push(sec);
      pushedSection = sec;
    } else if (!sec) {
      pushedSection = '';
    }
  };

  for (const entry of baseEntries) {
    const key = `${entry.section}::${entry.trimmed}`;
    if (modKeySet.has(key) || (!entry.isIndented && modSectionHeaders.has(entry.trimmed))) continue;
    const cmdPrefix = entry.trimmed.startsWith('no ') ? entry.trimmed.slice(3).trim() : `no ${entry.trimmed}`;
    ensureSection(entry.section);
    out.push(cmdPrefix);
  }

  for (const entry of modEntries) {
    const key = `${entry.section}::${entry.trimmed}`;
    const isModifiedFromBase = !baseKeySet.has(key);
    const isModifiedFromRunning = !runKeySet.has(key);
    if (isModifiedFromBase || isModifiedFromRunning) {
      if (!entry.isIndented) {
        pushedSection = entry.trimmed;
        if (isModifiedFromBase && isModifiedFromRunning) out.push(entry.trimmed);
      } else {
        ensureSection(entry.section);
        out.push(entry.trimmed);
      }
    }
  }

  return out.length > 0 ? out.join('\n') : basePatch.trim();
}

interface DiffDirectToolbarProps {
  hostname: string;
  locale?: Locale;
  canOperate: boolean;
  isAdmin: boolean;
  hasCustomEdit: boolean;
  changedLinesCount?: number;
  applyingPatch: boolean;
  applyStatus: { ok: boolean; text: string } | null;
  onApplyDirect: () => void;
  onResetEdits: () => void;
}

export const DiffDirectToolbar: React.FC<DiffDirectToolbarProps> = ({
  hostname,
  locale = 'ru',
  canOperate,
  isAdmin,
  hasCustomEdit,
  changedLinesCount = 0,
  applyingPatch,
  applyStatus,
  onApplyDirect,
  onResetEdits,
}) => {
  const isEn = locale === 'en';
  const [cliOpen, setCliOpen] = useState(false);
  const [cliCmd, setCliCmd] = useState('');
  const [cliEdited, setCliEdited] = useState(false);
  const [cliOut, setCliOut] = useState('');
  const [runningCli, setRunningCli] = useState(false);
  const [frozen, setFrozen] = useState(false);
  const [busyAction, setBusyAction] = useState(false);

  // Команда по умолчанию берётся из intent/platforms/<платформа>.yaml (commands.bgp_summary).
  useEffect(() => {
    if (!hostname || cliEdited) return;
    let active = true;
    Promise.all([api.getCollectorPlan(), api.getPlatformCommands()])
      .then(([plan, commands]) => {
        const platform = plan.devices?.[hostname]?.platform;
        const cmd = platform ? commands[platform]?.bgp_summary : undefined;
        if (active && cmd) setCliCmd(cmd);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [hostname, cliEdited]);

  if (!canOperate) return null;

  const handleExecCli = async () => {
    if (!cliCmd.trim() || !hostname) return;
    setRunningCli(true);
    try {
      const res = await api.executeEmergencyCommand(hostname, cliCmd);
      setCliOut(res.output || (isEn ? '(no output)' : '(нет вывода)'));
    } catch (err) {
      setCliOut(`[${isEn ? 'ERROR' : 'ОШИБКА'}]: ${getErrorMessage(err)}`);
    } finally {
      setRunningCli(false);
    }
  };

  const handleToggleFreeze = async () => {
    setBusyAction(true);
    try {
      const st = await api.setFreezeStatus(!frozen, 'Toggle Freeze from Diff & Config');
      setFrozen(Boolean(st.frozen));
    } catch {
      // ignore
    } finally {
      setBusyAction(false);
    }
  };

  const handleSoftReset = async () => {
    setBusyAction(true);
    try {
      const res = await api.softResetFabric();
      setCliOut(`[SOFT RESET]: ${res.message}`);
      setCliOpen(true);
      window.dispatchEvent(new Event('netops-topology-mutated'));
    } catch (err) {
      setCliOut(`[${isEn ? 'ERROR' : 'ОШИБКА'}]: ${getErrorMessage(err)}`);
      setCliOpen(true);
    } finally {
      setBusyAction(false);
    }
  };

  const compactBtnCls =
    'px-2 py-1 rounded bg-[var(--bg-card)] hover:bg-[var(--bg-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] border border-[var(--border-subtle)] text-[11px] flex items-center gap-1 transition cursor-pointer disabled:opacity-50';

  return (
    <div className="mb-3 space-y-2">
      <div className="bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-xl px-3 py-1.5 flex flex-wrap items-center justify-between gap-2 text-xs font-mono">
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-[var(--text-secondary)]">
          <span className="font-medium text-[var(--text-primary)]">
            {isEn
              ? 'Direct edit mode · modify lines on the right or CLI patch'
              : 'Режим прямой правки · измените строки справа или CLI-патч'}
          </span>
          {changedLinesCount > 0 && (
            <span className="px-1.5 py-0.5 rounded bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[10px] text-[var(--text-primary)]">
              {isEn ? `${changedLinesCount} cmd(s)` : `${changedLinesCount} ком.`}
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <button
            onClick={onApplyDirect}
            disabled={applyingPatch}
            data-testid="apply-direct-config-btn"
            className="px-2.5 py-1 rounded bg-[var(--text-primary)] text-[var(--bg-main)] hover:opacity-90 disabled:opacity-50 font-medium text-[11px] flex items-center gap-1.5 cursor-pointer transition"
            title={
              isEn
                ? 'Push edited config / patch directly to device via SSH'
                : 'Накатить отредактированный конфиг / патч прямо на устройство по SSH'
            }
          >
            <Play className="w-3 h-3 fill-current" />
            <span>
              {applyingPatch
                ? isEn
                  ? 'Applying…'
                  : 'Применение…'
                : isEn
                  ? `Apply to ${hostname}`
                  : `Применить на ${hostname}`}
            </span>
          </button>

          {hasCustomEdit && (
            <button onClick={onResetEdits} className={compactBtnCls}>
              <span>{isEn ? 'Reset' : 'Сбросить'}</span>
            </button>
          )}

          {isAdmin && (
            <>
              <button onClick={() => setCliOpen((v) => !v)} className={compactBtnCls}>
                <Terminal className="w-3 h-3" />
                <span>&gt;_ SSH CLI</span>
              </button>
              <button onClick={handleToggleFreeze} disabled={busyAction} className={compactBtnCls}>
                <Lock className="w-3 h-3" />
                <span>{frozen ? 'Unfreeze' : 'Freeze'}</span>
              </button>
              <button
                onClick={handleSoftReset}
                disabled={busyAction}
                className={compactBtnCls}
                title={isEn ? 'Soft reset all fabric nodes to Git SoT' : 'Мягкий сброс всех узлов стенда к эталону Git SoT'}
              >
                <RotateCcw className="w-3 h-3" />
                <span>{isEn ? 'To SoT Baseline' : 'К эталону SoT'}</span>
              </button>
            </>
          )}
        </div>
      </div>

      {applyStatus && (
        <div
          className={`px-3 py-2 rounded-xl text-xs font-mono border ${
            applyStatus.ok
              ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-300'
              : 'bg-rose-500/10 border-rose-500/30 text-rose-600 dark:text-rose-300'
          }`}
        >
          {applyStatus.text}
        </div>
      )}

      {cliOpen && isAdmin && (
        <div className="p-3 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] space-y-2 font-mono text-xs">
          <div className="flex items-center gap-2">
            <span className="text-[var(--text-secondary)] font-semibold">[{hostname}] #</span>
            <input
              type="text"
              value={cliCmd}
              onChange={(e) => {
                setCliEdited(true);
                setCliCmd(e.target.value);
              }}
              onKeyDown={(e) => e.key === 'Enter' && handleExecCli()}
              className="flex-1 bg-[var(--bg-main)] border border-[var(--border-subtle)] rounded px-2.5 py-1 text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--border-hover)]"
              placeholder={isEn ? 'CLI command (e.g. show ip bgp summary)...' : 'Команда CLI (например: show ip bgp summary)...'}
            />
            <button
              onClick={handleExecCli}
              disabled={runningCli}
              className="px-3 py-1 rounded bg-[var(--text-primary)] hover:opacity-90 text-[var(--bg-main)] font-medium flex items-center gap-1 cursor-pointer"
            >
              <Send className="w-3 h-3" />
              <span>{runningCli ? '...' : isEn ? 'Run' : 'Выполнить'}</span>
            </button>
          </div>
          {cliOut && (
            <pre className="p-2.5 rounded bg-[var(--bg-main)] border border-[var(--border-subtle)] text-[var(--text-primary)] max-h-40 overflow-y-auto whitespace-pre-wrap text-[11px]">
              {cliOut}
            </pre>
          )}
        </div>
      )}
    </div>
  );
};

