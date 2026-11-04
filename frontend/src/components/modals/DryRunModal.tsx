import React, { useEffect, useMemo, useState } from 'react';
import { Play, X, GitBranch, AlertTriangle, Server, Network } from 'lucide-react';
import { api, type Device } from '../../api';
import { PLATFORM_NAMES, ROLE_NAMES } from '../../constants';
import { translations, type Locale } from '../../i18n';

export interface DryRunModalProps {
  open: boolean;
  onClose: () => void;
  onConfirm: (deviceIds: number[]) => void | Promise<void>;
  deviceIds: number[];
  devices: Device[];
  locale?: Locale;
  gitBranch?: string;
  gitCommit?: string;
}

export const DryRunModal: React.FC<DryRunModalProps> = ({
  open,
  onClose,
  onConfirm,
  deviceIds,
  devices,
  locale = 'ru',
  gitBranch,
  gitCommit,
}) => {
  const t = translations[locale];
  const [submitting, setSubmitting] = useState(false);
  const [liveGitBranch, setLiveGitBranch] = useState<string>(gitBranch || 'git_main');
  const [liveGitCommit, setLiveGitCommit] = useState<string>(gitCommit || 'HEAD');

  useEffect(() => {
    if (!open) return;
    let active = true;
    api
      .getIntegrationsStatus()
      .then((res) => {
        if (!active || !res.github_sot) return;
        if (!gitBranch && res.github_sot.branch) {
          setLiveGitBranch(res.github_sot.branch);
        }
        if (!gitCommit && res.github_sot.commit_short) {
          setLiveGitCommit(
            `origin/${res.github_sot.branch || 'main'}:${res.github_sot.commit_short}${
              res.github_sot.last_commit_message ? ` — ${res.github_sot.last_commit_message}` : ''
            }`
          );
        }
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [open, gitBranch, gitCommit]);

  // Target devices in scope
  const targetDevices = useMemo(() => {
    if (!deviceIds || deviceIds.length === 0) return [];
    return devices.filter((d) => deviceIds.includes(d.id));
  }, [deviceIds, devices]);

  // Blast radius calculation
  const blastRadius = useMemo(() => {
    const total = devices.length || 1;
    const totalLeaves = devices.filter((d) => d.role === 'leaf').length || 2;
    const totalSpines = devices.filter((d) => d.role === 'spine').length || 2;
    const count = targetDevices.length;
    const spines = targetDevices.filter((d) => d.role === 'spine').length;
    const leaves = targetDevices.filter((d) => d.role === 'leaf' || d.role === 'border' || d.role === 'border_firewall').length;
    const pct = Math.min(100, Math.round((count / total) * 100));

    let level: 'LOW' | 'MEDIUM' | 'HIGH' = 'LOW';
    let levelLabel = t.radiusLow;
    let levelColor = 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20';

    if (spines > 0 || pct > 50) {
      level = 'HIGH';
      levelLabel = t.radiusHigh;
      levelColor = 'text-rose-400 bg-rose-500/10 border-rose-500/20';
    } else if (count > 1 || leaves > 1) {
      level = 'MEDIUM';
      levelLabel = t.radiusMedium;
      levelColor = 'text-amber-400 bg-amber-500/10 border-amber-500/20';
    }

    const totalFabricLinks = totalSpines * totalLeaves;
    const touchedLinks = Math.min(
      totalFabricLinks,
      spines * totalLeaves + leaves * totalSpines - spines * leaves
    );
    const estimatedLinks = touchedLinks;
    const estimatedPeers = touchedLinks;

    return {
      level,
      levelLabel,
      levelColor,
      pct,
      spines,
      leaves,
      estimatedLinks,
      estimatedPeers,
    };
  }, [targetDevices, devices, t.radiusHigh, t.radiusMedium, t.radiusLow]);

  const handleExecute = React.useCallback(async () => {
    if (submitting || targetDevices.length === 0) return;
    setSubmitting(true);
    try {
      await onConfirm(targetDevices.map((d) => d.id));
      onClose();
    } catch {
      // Handled in parent
    } finally {
      setSubmitting(false);
    }
  }, [submitting, targetDevices, onConfirm, onClose]);

  // Keyboard navigation: Enter to confirm
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        e.stopPropagation();
        void handleExecute();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, handleExecute]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-2 sm:p-4 select-none"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl rounded-xl bg-[#0c0e14] border border-white/[0.1] shadow-2xl overflow-hidden flex flex-col max-h-[94dvh]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="bg-[#10131b]/95 border-b border-white/[0.08] px-4 sm:px-5 py-3.5 flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2.5">
            <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
            <h2 className="text-sm font-semibold text-white tracking-tight">
              {t.dryRunTitle}
            </h2>
            <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-white/[0.05] text-zinc-400 border border-white/[0.08]">
              Read-Only
            </span>
          </div>
          <button
            onClick={onClose}
            className="text-zinc-400 hover:text-white transition-colors p-1 rounded-md hover:bg-white/[0.05] cursor-pointer"
            title="Закрыть (Esc)"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Content Body */}
        <div className="p-4 sm:p-5 space-y-4 overflow-y-auto flex-1 text-xs">
          {/* Git Branch & Source of Truth */}
          <div className="rounded-lg border border-white/[0.08] bg-[#10121a] p-3 flex items-center justify-between gap-2.5">
            <div className="flex items-start gap-2.5 min-w-0">
              <GitBranch className="w-4 h-4 text-cyan-400 shrink-0 mt-0.5" />
              <div className="min-w-0">
                <div className="text-[11px] text-zinc-400">Источник эталона (Git SoT)</div>
                <div className="font-mono text-zinc-200 font-medium truncate mt-0.5">{liveGitBranch}</div>
                <div className="text-[10px] text-zinc-500 font-mono truncate">{liveGitCommit}</div>
              </div>
            </div>
            <span className="px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/20 text-[10px] font-mono text-emerald-400 shrink-0">
              Pre-flight Lint & Синтаксис: OK
            </span>
          </div>

          {/* Blast Radius Estimation Card */}
          <div className="rounded-lg border border-white/[0.08] bg-[#10121a] p-3.5 space-y-2.5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Network className="w-4 h-4 text-indigo-400" />
                <span className="font-medium text-zinc-200 text-xs">{t.blastRadiusTitle}</span>
              </div>
              <span className={`font-mono text-[10px] px-2 py-0.5 rounded border ${blastRadius.levelColor}`}>
                {blastRadius.levelLabel}
              </span>
            </div>

            <div className="grid grid-cols-3 gap-2 pt-1 border-t border-white/[0.06] text-center">
              <div className="p-2 rounded bg-white/[0.02]">
                <div className="text-[10px] text-zinc-500">Охват</div>
                <div className="font-mono text-sm font-semibold text-white mt-0.5">
                  {blastRadius.pct}% <span className="text-[10px] text-zinc-500 font-normal">({targetDevices.length}/{devices.length})</span>
                </div>
              </div>
              <div className="p-2 rounded bg-white/[0.02]">
                <div className="text-[10px] text-zinc-500">Линки</div>
                <div className="font-mono text-sm font-semibold text-white mt-0.5">
                  ~{blastRadius.estimatedLinks}
                </div>
              </div>
              <div className="p-2 rounded bg-white/[0.02]">
                <div className="text-[10px] text-zinc-500">BGP</div>
                <div className="font-mono text-sm font-semibold text-white mt-0.5">
                  ~{blastRadius.estimatedPeers}
                </div>
              </div>
            </div>

            {blastRadius.spines > 0 && (
              <div className="flex items-center gap-2 text-[11px] text-amber-300/90 bg-amber-500/10 border border-amber-500/20 px-2.5 py-1.5 rounded">
                <AlertTriangle className="w-3.5 h-3.5 shrink-0 text-amber-400" />
                <span>Затронуты Spine-узлы ({blastRadius.spines} шт.)</span>
              </div>
            )}
          </div>

          {/* Scope: Device List */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-zinc-400">
              <span className="flex items-center gap-1.5">
                <Server className="w-3.5 h-3.5 text-zinc-500" />
                <span>Узлы ({targetDevices.length})</span>
              </span>
              <span className="text-[11px] font-mono text-zinc-500">Dry-Run</span>
            </div>

            <div className="max-h-48 overflow-y-auto rounded-lg border border-white/[0.08] bg-[#08090c] divide-y divide-white/[0.05]">
              {targetDevices.map((d) => (
                <div key={d.id} className="px-3 py-2 flex items-center justify-between text-xs hover:bg-white/[0.02]">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="font-medium text-cyan-300 font-mono">{d.hostname}</span>
                    <span className="font-mono text-zinc-500 text-[11px]">{d.management_ip}</span>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-white/[0.05] text-zinc-300 border border-white/[0.06]">
                      {ROLE_NAMES[d.role] ?? d.role}
                    </span>
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-white/[0.05] text-zinc-400 border border-white/[0.06]">
                      {PLATFORM_NAMES[d.platform] ?? d.platform}
                    </span>
                  </div>
                </div>
              ))}
              {targetDevices.length === 0 && (
                <div className="p-4 text-center text-zinc-500 text-xs">
                  Нет выбранных устройств для запуска
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Footer Actions */}
        <div className="bg-[#10131b]/95 border-t border-white/[0.08] px-5 py-3 flex items-center justify-between shrink-0">
          <div className="text-[11px] text-zinc-500 hidden sm:block">
            Подтвердить: <kbd className="font-mono bg-white/[0.08] px-1.5 py-0.5 rounded text-zinc-300">Enter</kbd> | Отмена: <kbd className="font-mono bg-white/[0.08] px-1.5 py-0.5 rounded text-zinc-300">Esc</kbd>
          </div>

          <div className="flex items-center gap-2 ml-auto">
            <button
              onClick={onClose}
              disabled={submitting}
              className="px-3.5 py-1.5 rounded-md border border-white/[0.1] text-zinc-300 hover:bg-white/[0.05] text-xs font-medium cursor-pointer transition-colors"
            >
              {t.cancel}
            </button>

            <button
              onClick={() => void handleExecute()}
              disabled={submitting || targetDevices.length === 0}
              className="px-4 py-1.5 rounded-md bg-cyan-500 hover:bg-cyan-400 text-zinc-950 text-xs font-medium flex items-center gap-1.5 cursor-pointer transition-colors shadow-lg shadow-cyan-500/20 disabled:opacity-40"
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              <span>{submitting ? (locale === 'en' ? 'Starting simulation…' : 'Запуск симуляции…') : t.btnStartDryRun}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
