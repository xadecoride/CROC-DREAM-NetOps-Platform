import React, { useEffect, useState, useCallback } from 'react';
import { ArrowLeft, Play, ShieldCheck, Sparkles, Zap, Trash2, FileDown } from 'lucide-react';
import { api, getErrorMessage } from '../../api';
import type { CollectorDevicePlan, DeviceDetail, DeviceHardwareSpecs, UserRole } from '../../api';
import type { Locale } from '../../i18n';
import { StatusBadge, OperStatusBadge, ManagementModeBadge } from '../layout/ui';
import { DeviceOverviewSection, exportDevicePassportMarkdown } from './device/DeviceOverviewSection';
import { DeviceInterfacesAndDanger } from './device/DeviceInterfacesAndDanger';
import { DeviceSshTerminalTab, DeviceCopilotTab } from './device/DeviceTerminalAndCopilot';

interface Props {
  deviceId: number;
  userRole?: UserRole;
  locale?: Locale;
  onBack: () => void;
  onRunDryRun: (ids: number[]) => void;
  onScanDrift: (ids?: number[]) => void;
  onAskCopilot: (question: string) => void;
  onDeleteDevice?: (id: number) => void;
}

const TABS = ['Обзор', 'SSH', 'Copilot'] as const;
type Tab = (typeof TABS)[number];

export const DevicePage: React.FC<Props> = ({
  deviceId,
  userRole = 'viewer',
  onBack,
  onRunDryRun,
  onScanDrift,
  onAskCopilot,
  onDeleteDevice,
}) => {
  const [device, setDevice] = useState<DeviceDetail | null>(null);
  const [error, setError] = useState('');
  const [prevDeviceId, setPrevDeviceId] = useState(deviceId);
  const [tab, setTab] = useState<Tab>('Обзор');
  const [promoting, setPromoting] = useState(false);

  if (deviceId !== prevDeviceId) {
    setPrevDeviceId(deviceId);
    setDevice(null);
    setError('');
  }

  // Terminal State
  const [terminalOutput, setTerminalOutput] = useState<Array<{ command: string; output: string; time: string }>>([]);
  const [commandInput, setCommandInput] = useState('');
  const [terminalRunning, setTerminalRunning] = useState(false);

  // Device-scoped Copilot state
  const [copilotHistory, setCopilotHistory] = useState<Array<{ role: 'user' | 'assistant'; text: string }>>([]);
  const [copilotInput, setCopilotInput] = useState('');
  const [copilotLoading, setCopilotLoading] = useState(false);

  const [collectorPlan, setCollectorPlan] = useState<CollectorDevicePlan | null>(null);

  const fetchDevice = useCallback(() => {
    api.getDevice(deviceId)
      .then((d) => setDevice(d))
      .catch((e) => setError(e.message));
  }, [deviceId]);

  useEffect(() => {
    let active = true;
    api.getDevice(deviceId)
      .then((d) => {
        if (!active) return;
        setDevice(d);
        api.getCollectorPlan()
          .then((plan) => {
            if (active && plan.devices?.[d.hostname]) {
              setCollectorPlan(plan.devices[d.hostname]);
            }
          })
          .catch(() => {});
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [deviceId]);

  if (error) return <div className="text-rose-400 p-6">{error}</div>;
  if (!device) return <div className="text-zinc-500 p-6">…</div>;

  const isServer = device.platform === 'linux_server' || device.role === 'server';
  const isManaged = device.management_mode === 'MANAGED';
  const canOperate = userRole !== 'viewer';
  const canAdmin = userRole === 'admin' || userRole === 'owner';
  const visibleTabs = TABS.filter((t) => t !== 'SSH' || canAdmin);

  let specs: DeviceHardwareSpecs | null = null;
  if (device.hardware_specs) {
    try {
      specs = JSON.parse(device.hardware_specs) as DeviceHardwareSpecs;
    } catch {
      // fallback
    }
  }

  const handlePromote = async () => {
    setPromoting(true);
    try {
      const updated = await api.promoteDevice(device.id);
      setDevice((prev) => (prev ? { ...prev, management_mode: updated.management_mode } : null));
    } catch (err: unknown) {
      alert(`Ошибка перевода в управление: ${getErrorMessage(err)}`);
    } finally {
      setPromoting(false);
    }
  };

  const handleRunCommand = async (cmdToRun?: string) => {
    const cmd = (cmdToRun || commandInput).trim();
    if (!cmd) return;

    const time = new Date().toLocaleTimeString('ru-RU');

    if (cmd === 'clear') {
      setTerminalOutput([]);
      setCommandInput('');
      return;
    }

    if (!isManaged && (cmd.startsWith('conf') || cmd.startsWith('system-view') || cmd.startsWith('rm') || cmd.startsWith('reboot'))) {
      setTerminalOutput((prev) => [
        ...prev,
        {
          command: cmd,
          output: `[SECURITY GUARD FAIL-CLOSED] Заблокировано: MONITORING_ONLY. Нужен MANAGED.`,
          time,
        },
      ]);
      setCommandInput('');
      return;
    }

    setTerminalRunning(true);
    setCommandInput('');

    try {
      const res = await api.executeEmergencyCommand(device.hostname, cmd);
      setTerminalOutput((prev) => [
        ...prev,
        {
          command: cmd,
          output: res.output?.trim() || (res.failed ? '[ERROR] Команда завершилась ошибкой' : '[OK] (пустой ответ устройства)'),
          time,
        },
      ]);
    } catch (err: unknown) {
      setTerminalOutput((prev) => [
        ...prev,
        {
          command: cmd,
          output: `[SSH ERROR] ${getErrorMessage(err, 'Ошибка соединения с узлом')}`,
          time,
        },
      ]);
    } finally {
      setTerminalRunning(false);
    }
  };

  const handleAskCopilot = async (text: string) => {
    if (!text.trim()) return;
    const userMsg = text.trim();
    setCopilotHistory((prev) => [...prev, { role: 'user', text: userMsg }]);
    setCopilotInput('');
    setCopilotLoading(true);

    try {
      const prompt = `Контекст устройства: ${device.hostname} (${device.platform}, ${device.role}, IP: ${device.management_ip}, режим: ${device.management_mode}). Запрос: ${userMsg}`;
      const reply = await api.askCopilot(prompt, device.id, 'device');
      setCopilotHistory((prev) => [...prev, { role: 'assistant', text: reply.answer }]);
    } catch (err: unknown) {
      setCopilotHistory((prev) => [
        ...prev,
        { role: 'assistant', text: `Ошибка получения ответа: ${getErrorMessage(err)}` },
      ]);
    } finally {
      setCopilotLoading(false);
    }
  };

  const handleExportPassport = () => {
    if (device) exportDevicePassportMarkdown(device, specs);
  };

  const btn =
    'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium cursor-pointer transition-colors';

  return (
    <div className="space-y-5 animate-fade-in">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="flex items-center gap-1 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] px-2 py-1 rounded-md hover:bg-[var(--bg-elevated)] transition-colors cursor-pointer"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>←</span>
          </button>
          <div className="h-4 w-px bg-[var(--border-subtle)]" />
          <div className="flex items-center gap-2.5">
            <h1 className="text-xl font-semibold text-[var(--text-primary)] tracking-tight font-mono">{device.hostname}</h1>
            <StatusBadge status={device.status} />
            <OperStatusBadge status={device.oper_status ?? 'UP'} />
            <ManagementModeBadge mode={device.management_mode} />
          </div>
        </div>

        <div className="flex items-center gap-2">
          {device.management_mode === 'MONITORING_ONLY' && canAdmin && (
            <button
              onClick={handlePromote}
              disabled={promoting}
              className={`${btn} bg-[var(--bg-elevated)] text-[var(--text-primary)] border border-[var(--border-hover)] hover:bg-white/[0.08]`}
              title="MANAGED"
            >
              <Zap className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
              <span>{promoting ? '…' : 'Promote'}</span>
            </button>
          )}

          {canOperate && (
            <>
              <button
                onClick={() => onRunDryRun([device.id])}
                className={`${btn} bg-[var(--text-primary)] text-[var(--bg-main)] hover:opacity-90 font-semibold`}
              >
                <Play className="w-3.5 h-3.5 fill-current" /> Dry-run
              </button>

              <button
                onClick={() => onScanDrift([device.id])}
                className={`${btn} border border-[var(--border-subtle)] hover:border-[var(--border-hover)] bg-[var(--bg-card)] text-[var(--text-primary)]`}
              >
                <ShieldCheck className="w-3.5 h-3.5 text-[var(--text-secondary)]" /> Drift
              </button>
            </>
          )}

          <button
            onClick={() => {
              setTab('Copilot');
              const q = `Оцени надежность и состояние узла ${device.hostname}`;
              handleAskCopilot(q);
              if (onAskCopilot) onAskCopilot(q);
            }}
            className={`${btn} border border-[var(--border-subtle)] hover:border-[var(--border-hover)] bg-[var(--bg-card)] text-[var(--text-primary)]`}
          >
            <Sparkles className="w-3.5 h-3.5 text-[var(--text-secondary)]" /> Copilot
          </button>

          <button
            onClick={handleExportPassport}
            className={`${btn} border border-[var(--border-subtle)] hover:border-[var(--border-hover)] bg-[var(--bg-card)] text-[var(--text-primary)]`}
            title="Passport .md"
          >
            <FileDown className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
            <span>.md</span>
          </button>

          {canAdmin && (
            <button
              onClick={async () => {
                const confirmMsg = `Вы действительно хотите удалить устройство ${device.hostname}?`;
                if (window.confirm(confirmMsg)) {
                  try {
                    if (onDeleteDevice) {
                      await onDeleteDevice(device.id);
                    } else {
                      await api.deleteDevice(device.id);
                      onBack();
                    }
                  } catch (err: unknown) {
                    alert(`Ошибка удаления: ${getErrorMessage(err)}`);
                  }
                }
              }}
              className={`${btn} border border-[var(--border-subtle)] hover:border-rose-500/40 bg-[var(--bg-card)] hover:bg-rose-500/10 text-[var(--text-secondary)] hover:text-rose-400`}
              title="Удалить"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      <div className="flex gap-2 border-b border-[var(--border-subtle)]">
        {visibleTabs.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2.5 text-xs font-medium border-b-2 -mb-px cursor-pointer transition-colors ${
              tab === t
                ? 'border-[var(--text-primary)] text-[var(--text-primary)] bg-[var(--bg-elevated)]/40'
                : 'border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === 'Обзор' && (
        <div className="space-y-6">
          <DeviceOverviewSection
            device={device}
            specs={specs}
            isServer={isServer}
            canAdmin={canAdmin}
            promoting={promoting}
            onPromote={handlePromote}
            onRefreshDevice={fetchDevice}
            collectorPlan={collectorPlan}
          />

          <DeviceInterfacesAndDanger device={device} />
        </div>
      )}

      {tab === 'SSH' && canAdmin && (
        <DeviceSshTerminalTab
          device={device}
          isServer={isServer}
          isManaged={isManaged}
          terminalRunning={terminalRunning}
          terminalOutput={terminalOutput}
          commandInput={commandInput}
          setCommandInput={setCommandInput}
          onRunCommand={handleRunCommand}
        />
      )}

      {tab === 'Copilot' && (
        <DeviceCopilotTab
          device={device}
          copilotHistory={copilotHistory}
          copilotInput={copilotInput}
          setCopilotInput={setCopilotInput}
          copilotLoading={copilotLoading}
          onAskCopilot={handleAskCopilot}
        />
      )}
    </div>
  );
};
