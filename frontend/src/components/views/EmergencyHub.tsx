import React, { useState, useEffect } from 'react';
import {
  Terminal as TerminalIcon,
  ShieldAlert,
  Play,
  RotateCcw,
  RefreshCw,
  Send,
  Trash2,
  Zap,
  Lock,
  Copy,
  Check,
} from 'lucide-react';
import { api, getErrorMessage } from '../../api';
import type { EmergencyAuditLog } from '../../api';
import { PLATFORM_CLI_PRESETS } from '../../constants';
import { EmergencyAuditSection } from './EmergencyAuditSection';

interface EmergencyHubProps {
  currentRole: 'operator' | 'admin';
}

export const EmergencyHub: React.FC<EmergencyHubProps> = ({ currentRole }) => {
  const [devices, setDevices] = useState<Array<{ hostname: string; platform: string; label: string }>>([]);
  const [selectedDevice, setSelectedDevice] = useState<string>('');
  const [command, setCommand] = useState<string>('');
  const [platformCommands, setPlatformCommands] = useState<Record<string, Record<string, string>>>({});
  const [cliOutput, setCliOutput] = useState<string>('// Готов к приему команд оператора...');
  const [isExecutingCmd, setIsExecutingCmd] = useState<boolean>(false);

  // Hot Patching
  const [patchText, setPatchText] = useState<string>('');
  const [patchReason, setPatchReason] = useState<string>('Аварийное вмешательство');
  const [isApplyingPatch, setIsApplyingPatch] = useState<boolean>(false);
  const [patchResult, setPatchResult] = useState<{ status: string; message: string } | null>(null);

  // Recovery & Freeze
  const [isSoftResetting, setIsSoftResetting] = useState<boolean>(false);
  const [isHardResetting, setIsHardResetting] = useState<boolean>(false);
  const [resetModal, setResetModal] = useState<'soft' | 'hard' | null>(null);
  const [resetLog, setResetLog] = useState<string | null>(null);
  const [copied, setCopied] = useState<boolean>(false);
  const [isFrozen, setIsFrozen] = useState<boolean>(false);
  const [isTogglingFreeze, setIsTogglingFreeze] = useState<boolean>(false);

  // Audit Logs
  const [auditLogs, setAuditLogs] = useState<EmergencyAuditLog[]>([]);
  const [isLoadingAudit, setIsLoadingAudit] = useState<boolean>(true);

  const loadAudit = async () => {
    setIsLoadingAudit(true);
    try {
      const logs = await api.getEmergencyAudit();
      setAuditLogs([...logs].reverse());
    } catch (err) {
      console.error('Failed to load audit logs', err);
    } finally {
      setIsLoadingAudit(false);
    }
  };

  useEffect(() => {
    let active = true;
    api
      .getFreezeStatus()
      .then((st) => {
        if (active) setIsFrozen(Boolean(st.frozen));
      })
      .catch(() => {});
    api
      .getDevices()
      .then((list) => {
        if (!active) return;
        const mapped = list.map((d) => ({
          hostname: d.hostname,
          platform: d.platform,
          label: `${d.hostname} (${d.platform})`,
        }));
        setDevices(mapped);
        setSelectedDevice((prev) =>
          mapped.length === 0 ? '' : mapped.some((m) => m.hostname === prev) ? prev : mapped[0].hostname
        );
      })
      .catch(() => {});
    api
      .getEmergencyAudit()
      .then((logs) => {
        if (active) setAuditLogs([...logs].reverse());
      })
      .catch((err) => {
        console.error('Failed to load audit logs', err);
      })
      .finally(() => {
        if (active) setIsLoadingAudit(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const handleToggleFreeze = async () => {
    setIsTogglingFreeze(true);
    try {
      const next = !isFrozen;
      const st = await api.setFreezeStatus(
        next,
        next ? 'Аварийная заморозка фабрики через Emergency Hub' : 'Снятие заморозки фабрики'
      );
      setIsFrozen(Boolean(st.frozen));
      setResetLog(
        st.frozen
          ? '[FREEZE АКТИВИРОВАН]: Все плановые пайплайны деплоя и ремедиации заблокированы.'
          : '[FREEZE СНЯТ]: Штатные пайплайны деплоя разблокированы.'
      );
      loadAudit();
    } catch (err: unknown) {
      setResetLog(`[ОШИБКА FREEZE]: ${getErrorMessage(err)}`);
    } finally {
      setIsTogglingFreeze(false);
    }
  };

  const activeDeviceObj = devices.find((d) => d.hostname === selectedDevice) || devices[0];

  // Команда по умолчанию — commands.bgp_summary из intent/platforms/<платформа>.yaml.
  const activePlatform = activeDeviceObj?.platform;
  useEffect(() => {
    api.getPlatformCommands().then(setPlatformCommands).catch(() => undefined);
  }, []);
  useEffect(() => {
    const cmd = activePlatform ? platformCommands[activePlatform]?.bgp_summary : undefined;
    if (cmd) setCommand((prev) => prev || cmd);
  }, [activePlatform, platformCommands]);
  const activePresets =
    (activeDeviceObj && PLATFORM_CLI_PRESETS[activeDeviceObj.platform]) || PLATFORM_CLI_PRESETS.arista_eos;

  const handleRunCommand = async (cmdToRun = command) => {
    if (!cmdToRun.trim() || !selectedDevice) return;
    setIsExecutingCmd(true);
    setCliOutput((prev) => `${prev}\n\n$ [${selectedDevice}] # ${cmdToRun}\nВыполнение...`);
    try {
      const res = await api.executeEmergencyCommand(selectedDevice, cmdToRun);
      setCliOutput((prev) => `${prev}\n${res.output || '(нет вывода)'}`);
    } catch (err: unknown) {
      setCliOutput((prev) => `${prev}\n[ОШИБКА]: ${getErrorMessage(err)}`);
    } finally {
      setIsExecutingCmd(false);
      loadAudit();
    }
  };

  const handleApplyPatch = async () => {
    if (!patchText.trim() || !selectedDevice) return;
    setIsApplyingPatch(true);
    setPatchResult(null);
    try {
      const res = await api.applyEmergencyPatch(selectedDevice, patchText, patchReason);
      setPatchResult({
        status: 'ok',
        message: `Патч успешно применен на ${selectedDevice} (${res.applied_lines.length} строк).`,
      });
      setCliOutput((prev) => `${prev}\n\n[EMERGENCY PATCH APPLIED on ${selectedDevice}]:\n${res.output}`);
    } catch (err: unknown) {
      setPatchResult({
        status: 'error',
        message: `Сбой применения патча: ${getErrorMessage(err)}`,
      });
    } finally {
      setIsApplyingPatch(false);
      loadAudit();
    }
  };

  const handleSoftReset = async () => {
    setIsSoftResetting(true);
    setResetModal(null);
    setResetLog('Запуск мягкого сброса фабрики к эталонному Git SoT Intent...');
    try {
      const res = await api.softResetFabric();
      setResetLog(`[МЯГКИЙ СБРОС ЗАВЕРШЕН]: ${res.message}\nДетали: ${res.details}`);
    } catch (err: unknown) {
      setResetLog(`[ОШИБКА МЯГКОГО СБРОСА]: ${getErrorMessage(err)}`);
    } finally {
      setIsSoftResetting(false);
    }
  };

  const handleHardReset = async () => {
    setIsHardResetting(true);
    setResetModal(null);
    setResetLog('Передеплой топологии стенда (reconfigure)...');
    try {
      const res = await api.hardResetFabric();
      setResetLog(`[ХАРД-СБРОС ЗАВЕРШЕН]: ${res.message}\n\nЛог:\n${res.details}`);
    } catch (err: unknown) {
      setResetLog(`[ОШИБКА ХАРД-СБРОСА]: ${getErrorMessage(err)}`);
    } finally {
      setIsHardResetting(false);
    }
  };

  const handleCopyCli = () => {
    navigator.clipboard.writeText(cliOutput);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // If non-admin, show strict RBAC authorization barrier without client-side self-elevation
  if (currentRole !== 'admin') {
    return (
      <div className="max-w-4xl mx-auto p-8 animate-fade-in">
        <div className="p-8 rounded-2xl border border-amber-500/20 bg-amber-500/[0.04] text-center space-y-3">
          <div className="w-12 h-12 mx-auto rounded-2xl bg-amber-500/10 border border-amber-500/30 flex items-center justify-center text-amber-400">
            <Lock className="w-6 h-6" />
          </div>
          <h2 className="text-lg font-bold text-white">Доступ ограничен (403 RBAC)</h2>
          <p className="text-xs font-mono text-slate-400 max-w-sm mx-auto">
            Требуется роль <strong className="text-amber-300">Admin</strong> или <strong className="text-amber-300">Owner</strong>.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-7xl mx-auto p-3 sm:p-6 animate-fade-in text-[var(--text-primary)]">
      {/* Top Banner */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 p-4 rounded-lg border border-amber-500/25 bg-amber-500/[0.04]">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-500 dark:text-amber-400">
            <ShieldAlert className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-base font-bold text-slate-900 dark:text-white tracking-tight">Аварийный пульт</h1>
              <span className="px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-amber-500/20 text-amber-700 dark:text-amber-300 border border-amber-500/30">
                ADMIN
              </span>
            </div>
            <p className="text-[11px] font-mono text-slate-600 dark:text-slate-400 mt-0.5">
              Прямой SSH CLI · Хот-патчи · Сброс фабрики
            </p>
          </div>
        </div>

        {/* 2-Tier Recovery & Freeze Quick Buttons */}
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={handleToggleFreeze}
            disabled={isTogglingFreeze}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all cursor-pointer ${
              isFrozen
                ? 'text-cyan-700 dark:text-cyan-200 bg-cyan-500/20 border-cyan-500/40'
                : 'text-amber-700 dark:text-amber-300 bg-amber-500/10 hover:bg-amber-500/20 border-amber-500/30'
            }`}
          >
            <Lock className="w-3.5 h-3.5" />
            <span>{isFrozen ? 'Снять Freeze' : 'Freeze'}</span>
          </button>
          <button
            onClick={() => setResetModal('soft')}
            disabled={isSoftResetting || isHardResetting}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold text-emerald-700 dark:text-emerald-300 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/30 transition-all cursor-pointer"
          >
            <RotateCcw className={`w-3.5 h-3.5 ${isSoftResetting ? 'animate-spin' : ''}`} />
            Soft Reset
          </button>
          <button
            onClick={() => setResetModal('hard')}
            disabled={isSoftResetting || isHardResetting}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold text-rose-700 dark:text-red-300 bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/30 transition-all cursor-pointer"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isHardResetting ? 'animate-spin' : ''}`} />
            Hard Reset
          </button>
        </div>
      </div>

      {/* Recovery Log Feedback (if any) */}
      {resetLog && (
        <div className="p-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] text-xs font-mono text-[var(--text-primary)] whitespace-pre-wrap leading-relaxed max-h-48 overflow-y-auto">
          {resetLog}
        </div>
      )}

      {/* Main Grid: CLI Terminal & Patch Studio */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column: Interactive Web CLI (7 cols) */}
        <div className="lg:col-span-7 flex flex-col rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-card)] overflow-hidden shadow-sm">
          {/* CLI Header */}
          <div className="p-4 border-b border-[var(--border-subtle)] bg-[var(--bg-main)]/50 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <TerminalIcon className="w-4 h-4 text-cyan-400" />
              <span className="text-xs font-bold uppercase tracking-wider text-[var(--text-primary)]">
                Interactive Web CLI
              </span>
            </div>

            {/* Device Selector */}
            <select
              value={selectedDevice}
              onChange={(e) => {
                setSelectedDevice(e.target.value);
                const dev = devices.find((d) => d.hostname === e.target.value);
                const first = dev && (platformCommands[dev.platform]?.bgp_summary ?? PLATFORM_CLI_PRESETS[dev.platform]?.[0]);
                if (first) setCommand(first);
              }}
              className="px-3 py-1.5 rounded-lg text-xs font-mono bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-[var(--text-primary)] focus:outline-none focus:border-cyan-500"
            >
              {devices.length === 0 ? (
                <option value="" className="bg-[var(--bg-elevated)] text-[var(--text-primary)]">
                  Нет подключенных устройств
                </option>
              ) : (
                devices.map((d) => (
                  <option key={d.hostname} value={d.hostname} className="bg-[var(--bg-elevated)] text-[var(--text-primary)]">
                    {d.label}
                  </option>
                ))
              )}
            </select>
          </div>

          {/* Quick Command Pills */}
          <div className="px-4 py-2 border-b border-[var(--border-subtle)] bg-[var(--bg-main)]/30 flex flex-wrap gap-1.5 items-center">
            <span className="text-[11px] font-mono text-[var(--text-muted)] mr-1">Шаблоны:</span>
            {activePresets.map((preset) => (
              <button
                key={preset}
                onClick={() => {
                  setCommand(preset);
                  handleRunCommand(preset);
                }}
                className="px-2 py-0.5 rounded text-[11px] font-mono bg-white dark:bg-white/[0.04] hover:bg-cyan-50 dark:hover:bg-cyan-500/20 text-slate-700 dark:text-slate-300 hover:text-cyan-700 dark:hover:text-cyan-300 border border-slate-200 dark:border-white/[0.08] transition-all cursor-pointer shadow-sm"
              >
                {preset}
              </button>
            ))}
          </div>

          {/* Terminal Screen */}
          <div className="relative flex-1 p-4 bg-[#0a0c10] font-mono text-xs text-emerald-400 min-h-[340px] max-h-[460px] overflow-y-auto whitespace-pre-wrap selection:bg-emerald-500/30 leading-relaxed">
            {cliOutput}
            <div className="absolute top-3 right-3 flex items-center gap-1.5">
              <button
                onClick={handleCopyCli}
                title="Копировать вывод"
                className="p-1.5 rounded bg-white/10 hover:bg-white/20 text-slate-300 hover:text-white transition-all cursor-pointer"
              >
                {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
              <button
                onClick={() => setCliOutput(`// Очищено. Узел: ${selectedDevice}`)}
                title="Очистить терминал"
                className="p-1.5 rounded bg-white/10 hover:bg-white/20 text-slate-300 hover:text-white transition-all cursor-pointer"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>

          {/* Command Input Bar */}
          <div className="p-3 border-t border-[var(--border-subtle)] bg-[var(--bg-main)]/30 flex items-center gap-2">
            <span className="text-xs font-mono text-cyan-400 pl-2">#</span>
            <input
              type="text"
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleRunCommand()}
              placeholder="Введите команду (например: show ip bgp summary)..."
              disabled={isExecutingCmd}
              className="flex-1 bg-transparent border-0 text-xs font-mono text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none"
            />
            <button
              onClick={() => handleRunCommand()}
              disabled={isExecutingCmd || !command.trim()}
              className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-semibold text-black bg-cyan-400 hover:bg-cyan-300 disabled:opacity-50 transition-all cursor-pointer shadow-md shadow-cyan-500/20"
            >
              {isExecutingCmd ? (
                <div className="w-3.5 h-3.5 border-2 border-black/30 border-t-black rounded-full animate-spin" />
              ) : (
                <Send className="w-3.5 h-3.5" />
              )}
              Отправить
            </button>
          </div>
        </div>

        {/* Right Column: Emergency Patch Studio (5 cols) */}
        <div className="lg:col-span-5 flex flex-col rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-card)] overflow-hidden shadow-sm">
          <div className="p-4 border-b border-[var(--border-subtle)] bg-[var(--bg-main)]/50 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Zap className="w-4 h-4 text-amber-400" />
              <span className="text-xs font-bold uppercase tracking-wider text-[var(--text-primary)]">
                Хот-патч CLI
              </span>
            </div>
            <span className="text-[11px] font-mono text-cyan-400 font-semibold">
              {selectedDevice}
            </span>
          </div>

          <div className="p-4 space-y-4 flex-1 flex flex-col justify-between">
            <div className="space-y-3">
              <div>
                <label className="block text-[11px] font-mono text-[var(--text-muted)] uppercase tracking-wider mb-1">
                  Причина (Audit):
                </label>
                <input
                  type="text"
                  value={patchReason}
                  onChange={(e) => setPatchReason(e.target.value)}
                  placeholder="Обоснование..."
                  className="w-full px-3 py-1.5 rounded-lg text-xs font-mono bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-cyan-500"
                />
              </div>

              <div>
                <label className="block text-[11px] font-mono text-[var(--text-muted)] uppercase tracking-wider mb-1">
                  Команды CLI:
                </label>
                <textarea
                  rows={8}
                  value={patchText}
                  onChange={(e) => setPatchText(e.target.value)}
                  placeholder={
                    activeDeviceObj?.platform === 'huawei_vrp'
                      ? 'interface GE1/0/1\n undo shutdown\n quit'
                      : activeDeviceObj?.platform === 'cisco_iosxe'
                      ? 'interface GigabitEthernet2\n no shutdown\n exit'
                      : 'interface Ethernet1\n no shutdown\n exit'
                  }
                  className="w-full p-3 rounded-lg text-xs font-mono bg-[var(--bg-main)]/60 border border-[var(--border-subtle)] text-amber-300 placeholder-[var(--text-muted)] focus:outline-none focus:border-amber-400"
                />
              </div>

              {patchResult && (
                <div
                  className={`p-3 rounded-xl text-xs font-mono ${
                    patchResult.status === 'ok'
                      ? 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-300'
                      : 'bg-red-500/10 border border-red-500/30 text-red-300'
                  }`}
                >
                  {patchResult.message}
                </div>
              )}
            </div>

            <div className="pt-2">
              <button
                onClick={handleApplyPatch}
                disabled={isApplyingPatch || !patchText.trim()}
                className="w-full flex items-center justify-center gap-2 py-2.5 rounded-md font-semibold text-xs text-black bg-amber-500 hover:bg-amber-400 disabled:opacity-50 transition-all cursor-pointer"
              >
                {isApplyingPatch ? (
                  <div className="w-4 h-4 border-2 border-black/30 border-t-black rounded-full animate-spin" />
                ) : (
                  <Play className="w-4 h-4 fill-current" />
                )}
                Применить на {selectedDevice}
              </button>
            </div>
          </div>
        </div>
      </div>

      <EmergencyAuditSection
        auditLogs={auditLogs}
        isLoadingAudit={isLoadingAudit}
        onRefreshAudit={loadAudit}
        resetModal={resetModal}
        setResetModal={setResetModal}
        devicesCount={devices.length}
        onSoftReset={handleSoftReset}
        onHardReset={handleHardReset}
      />
    </div>
  );
};
