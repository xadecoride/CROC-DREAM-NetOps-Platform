import React from 'react';
import { ExternalLink, Play, X, Cpu, Route, ShieldAlert, RotateCcw } from 'lucide-react';
import type { Device, TopologyGraphNode } from '../../api';
import type { Locale } from '../../i18n';
import { StatusBadge } from '../layout/ui';
import { PLATFORM_NAMES, ROLE_NAMES } from '../../constants';
import { resolveNodeProtocolLabel, useTranslation } from './topologyLayout';

interface TopologyNodePopoverProps {
  device: Device;
  meta?: TopologyGraphNode;
  onClose: () => void;
  onSelectDevice: (id: number) => void;
  onInspectIntent: (id: number) => void;
  onRunDryRun?: (id: number) => void;
  dryRunRunning: boolean;
  dryRunMsg: string | null;
  onSetTraceSource?: (id: number) => void;
  onSetTraceTarget?: (id: number) => void;
  onToggleDrain?: (id: number, nextDrained: boolean) => void;
  drainBusy?: boolean;
  onChangeProtocol?: (hostname: string, protocol: string) => Promise<void>;
  protocolBusy?: boolean;
  userRole?: string;
  locale?: Locale;
}

export const TopologyNodePopover: React.FC<TopologyNodePopoverProps> = ({
  device,
  meta,
  onClose,
  onSelectDevice,
  onInspectIntent,
  onRunDryRun,
  dryRunRunning,
  dryRunMsg,
  onSetTraceSource,
  onSetTraceTarget,
  onToggleDrain,
  drainBusy = false,
  onChangeProtocol,
  protocolBusy = false,
  userRole,
  locale: propLocale,
}) => {
  const { isEn } = useTranslation(propLocale);
  const isViewer = userRole === 'viewer';
  const protoBadge = resolveNodeProtocolLabel(device, meta);
  const selectedMode = meta?.telemetry_mode || 'auto';
  const readOnlyTitle = isEn ? 'Unavailable in read-only mode' : 'Недоступно в режиме просмотра';

  return (
    <div
      className="fixed inset-0 z-[45] flex items-center justify-center bg-black/45 backdrop-blur-[2px]"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-80 max-w-[92vw] max-h-[90vh] overflow-y-auto rounded-lg shadow-2xl p-4 bg-[var(--bg-surface)] text-[var(--text-primary)] border border-[var(--border-subtle)] text-xs animate-fade-in"
      >
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="font-semibold text-[var(--text-primary)]">{device.hostname}</span>
            {meta?.drained && (
              <span
                data-testid="node-drained-badge"
                className="px-1.5 py-0.5 rounded bg-amber-500/15 border border-amber-500/40 text-amber-600 dark:text-amber-300 text-[9px] font-mono font-bold tracking-wide"
              >
                BGP GSHUT · DRAINED
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-elevated)] cursor-pointer"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="space-y-1 mb-3 text-[11px] text-[var(--text-secondary)]">
          <div className="flex justify-between">
            <span>{isEn ? 'IP / Port:' : 'IP / Порт:'}</span>
            <span className="font-mono text-[var(--text-primary)]">
              {device.management_ip}:{device.management_port}
            </span>
          </div>
          <div className="flex justify-between">
            <span>{isEn ? 'Platform:' : 'Платформа:'}</span>
            <span className="text-[var(--text-primary)]">
              {PLATFORM_NAMES[device.platform] ?? device.platform}
            </span>
          </div>
          <div className="flex justify-between">
            <span>{isEn ? 'Role / Stand:' : 'Роль / Стенд:'}</span>
            <span className="text-[var(--text-primary)] font-mono">
              {isEn ? device.role.toUpperCase() : (ROLE_NAMES[device.role] ?? device.role)}
              {device.fabric_id ? ` · ${device.fabric_id}` : ''}
            </span>
          </div>
          {meta?.load_pct !== undefined && (
            <div className="flex justify-between">
              <span>{isEn ? 'Node Load:' : 'Нагрузка узла:'}</span>
              <span className="font-mono text-[var(--text-primary)] font-semibold">
                {meta.load_pct}%
              </span>
            </div>
          )}
          {meta?.bgp && (
            <div className="flex justify-between">
              <span>BGP / Router-ID:</span>
              <span className="text-[var(--text-primary)] font-mono">
                AS{meta.bgp.asn} ({meta.bgp.router_id})
              </span>
            </div>
          )}
          {meta?.total_links !== undefined && meta.total_links > 0 && (
            <div className="flex justify-between">
              <span>{isEn ? 'Links:' : 'Связи (линки):'}</span>
              <span
                className={`font-mono ${
                  meta.shutdown_links === meta.total_links
                    ? 'text-rose-500 font-semibold'
                    : (meta.shutdown_links || 0) > 0
                    ? 'text-amber-500 font-semibold'
                    : 'text-emerald-500'
                }`}
              >
                {meta.shutdown_links === meta.total_links
                  ? isEn
                    ? `ALL DOWN (${meta.shutdown_links}/${meta.total_links})`
                    : `ВЫКЛ ВСЕ (${meta.shutdown_links}/${meta.total_links})`
                  : (meta.shutdown_links || 0) > 0
                  ? isEn
                    ? `DOWN ${meta.shutdown_links} of ${meta.total_links}`
                    : `ВЫКЛ ${meta.shutdown_links} из ${meta.total_links}`
                  : isEn
                  ? `ALL UP (${meta.total_links}/${meta.total_links})`
                  : `ВСЕ АКТИВНЫ (${meta.total_links}/${meta.total_links})`}
              </span>
            </div>
          )}
          <div className="flex justify-between items-center pt-1">
            <span>{isEn ? 'Status:' : 'Статус:'}</span>
            <StatusBadge status={device.status} />
          </div>
          <div className="flex justify-between items-center">
            <span>{isEn ? 'Transaction:' : 'Транзакция:'}</span>
            <span className="text-[var(--text-primary)] font-mono text-[10px]">
              {device.platform === 'huawei_vrp'
                ? 'commit trial'
                : device.platform === 'cisco_iosxe'
                ? 'commit confirmed'
                : 'commit timer'}
            </span>
          </div>
          <div className="pt-1.5 mt-1.5 border-t border-[var(--border-subtle)] space-y-1">
            <div className="flex items-center justify-between">
              <span>{isEn ? 'Metrics Protocol:' : 'Протокол метрик:'}</span>
              <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-[var(--text-primary)] uppercase">
                {protoBadge}
                {meta?.telemetry_port ? ` :${meta.telemetry_port}` : ''}
              </span>
            </div>
            {onChangeProtocol && (
              <select
                data-testid="popover-protocol-select"
                disabled={isViewer || protocolBusy}
                value={selectedMode === 'node_exporter' ? 'prometheus_exporter' : selectedMode}
                onChange={(e) => void onChangeProtocol(device.hostname, e.target.value)}
                className="w-full px-2 py-1 rounded bg-[var(--bg-base)] border border-[var(--border-subtle)] text-[11px] font-mono text-[var(--text-primary)] focus:outline-none focus:border-[var(--border-strong)] disabled:opacity-50 cursor-pointer"
              >
                <option value="auto">
                  {isEn ? 'Auto (Cascade gNMI → SNMP → CLI)' : 'Авто (Каскад gNMI → SNMP → SSH)'}
                </option>
                <option value="gnmi">gNMI Streaming (:6030)</option>
                <option value="snmp">SNMP v2c/v3 (:161)</option>
                <option value="prometheus_exporter">Node Exporter (:9100)</option>
                <option value="ssh_cli">
                  {isEn ? `SSH CLI Pool (:${device.management_port})` : `SSH CLI Пул (:${device.management_port})`}
                </option>
              </select>
            )}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-1.5 pt-2 border-t border-[var(--border-subtle)]">
          {onSetTraceSource && (
            <button
              type="button"
              data-testid="popover-trace-src-btn"
              onClick={() => {
                onSetTraceSource(device.id);
                onClose();
              }}
              className="flex items-center justify-center gap-1 px-2 py-1.5 rounded bg-[var(--bg-elevated)] hover:bg-[var(--bg-panel)] border border-[var(--border-subtle)] hover:border-[var(--border-strong)] text-[var(--text-primary)] text-[11px] font-medium transition-all cursor-pointer"
            >
              <Route className="w-3 h-3" />
              <span>{isEn ? 'From Here (A)' : 'Отсюда (А)'}</span>
            </button>
          )}

          {onSetTraceTarget && (
            <button
              type="button"
              data-testid="popover-trace-dst-btn"
              onClick={() => {
                onSetTraceTarget(device.id);
                onClose();
              }}
              className="flex items-center justify-center gap-1 px-2 py-1.5 rounded bg-[var(--bg-elevated)] hover:bg-[var(--bg-panel)] border border-[var(--border-subtle)] hover:border-[var(--border-strong)] text-[var(--text-primary)] text-[11px] font-medium transition-all cursor-pointer"
            >
              <Route className="w-3 h-3" />
              <span>{isEn ? 'To Here (B)' : 'Сюда (Б)'}</span>
            </button>
          )}

          <button
            onClick={() => {
              onSelectDevice(device.id);
              onClose();
            }}
            className="flex items-center justify-center gap-1 px-2 py-1.5 rounded bg-[var(--bg-elevated)] hover:bg-[var(--bg-panel)] border border-[var(--border-subtle)] hover:border-[var(--border-strong)] text-[var(--text-primary)] text-[11px] font-medium transition-all cursor-pointer"
          >
            <ExternalLink className="w-3 h-3" />
            <span>{isEn ? 'Passport' : 'Паспорт'}</span>
          </button>

          {onRunDryRun && (
            <button
              onClick={() => onRunDryRun(device.id)}
              disabled={dryRunRunning || isViewer}
              title={isViewer ? readOnlyTitle : undefined}
              className={`flex items-center justify-center gap-1 px-2 py-1.5 rounded bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-[var(--text-primary)] text-[11px] font-medium transition-all ${
                isViewer
                  ? 'opacity-40 cursor-not-allowed'
                  : 'hover:bg-[var(--bg-panel)] hover:border-[var(--border-strong)] disabled:opacity-50 cursor-pointer'
              }`}
            >
              <Play className="w-3 h-3" />
              <span>{dryRunRunning ? (isEn ? 'Running...' : 'Запуск...') : 'Dry-run'}</span>
            </button>
          )}

          {onToggleDrain && (
            <button
              type="button"
              disabled={drainBusy || isViewer}
              data-testid="popover-toggle-drain-btn"
              onClick={() => {
                if (!isViewer && onToggleDrain) onToggleDrain(device.id, !meta?.drained);
              }}
              title={isViewer ? readOnlyTitle : undefined}
              className={`col-span-2 flex items-center justify-center gap-1.5 px-2 py-1.5 rounded border text-[11px] font-medium transition-all ${
                isViewer ? 'opacity-40 cursor-not-allowed ' : 'cursor-pointer disabled:opacity-50 '
              }${
                meta?.drained
                  ? 'bg-emerald-500/10 hover:bg-emerald-500/20 border-emerald-500/30 text-emerald-600 dark:text-emerald-300'
                  : 'bg-amber-500/10 hover:bg-amber-500/20 border-amber-500/30 text-amber-600 dark:text-amber-300'
              }`}
            >
              {meta?.drained ? (
                <>
                  <RotateCcw className="w-3 h-3" />
                  <span>{isEn ? 'Restore Traffic (Undrain)' : 'Вернуть трафик'}</span>
                </>
              ) : (
                <>
                  <ShieldAlert className="w-3 h-3" />
                  <span>{isEn ? 'Drain Traffic (GSHUT)' : 'Увести трафик (Drain)'}</span>
                </>
              )}
            </button>
          )}

          <button
            onClick={() => {
              onInspectIntent(device.id);
              onClose();
            }}
            className="col-span-2 flex items-center justify-center gap-1.5 px-2 py-1.5 rounded bg-[var(--bg-elevated)] hover:bg-[var(--bg-panel)] border border-[var(--border-subtle)] hover:border-[var(--border-strong)] text-[var(--text-primary)] text-[11px] font-medium transition-all cursor-pointer"
          >
            <Cpu className="w-3 h-3" />
            <span>{isEn ? 'Intent Config (YAML)' : 'Конфиг (YAML)'}</span>
          </button>
        </div>

        {dryRunMsg && (
          <div className="mt-2 text-center text-[10px] text-[var(--text-secondary)] font-mono animate-fade-in">
            {dryRunMsg}
          </div>
        )}
      </div>
    </div>
  );
};
