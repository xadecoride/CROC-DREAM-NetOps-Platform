import React from 'react';
import { ExternalLink } from 'lucide-react';
import type { CollectorDevicePlan, CollectorFallbackEvent, DeviceDetail } from '../../../api';
import { GrafanaTelemetryChart } from '../../charts/GrafanaTelemetryChart';

export interface DeviceDrawerMetricsTabProps {
  device: DeviceDetail;
  collectorPlan: CollectorDevicePlan | null;
  fallbackEvents: CollectorFallbackEvent[];
  protoSaving: boolean;
  isViewer: boolean;
  onProtocolChange: (mode: 'auto' | 'gnmi' | 'snmp' | 'prometheus_exporter' | 'ssh_cli') => Promise<void>;
}

export const DeviceDrawerMetricsTab: React.FC<DeviceDrawerMetricsTabProps> = ({
  device,
  collectorPlan,
  fallbackEvents,
  protoSaving,
  isViewer,
  onProtocolChange,
}) => {
  const host = typeof window !== 'undefined' ? window.location.hostname || 'localhost' : 'localhost';
  const grafanaUrl = `${typeof window !== 'undefined' ? window.location.protocol : 'http:'}//${host}:3000/d/croc-dream-noc`;

  return (
    <div className="space-y-4">
      <GrafanaTelemetryChart
        hostname={device.hostname}
        devices={[device]}
        compact
      />

      <div className="p-3.5 rounded-xl bg-white/[0.02] border border-white/[0.08] flex items-center justify-between gap-3">
        <div className="text-xs font-mono text-zinc-300">
          Графики метрик (CPU, RAM, трафик интерфейсов) синхронизированы с Prometheus и Grafana
        </div>
        <a
          href={grafanaUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/[0.05] hover:bg-white/[0.1] border border-white/[0.12] text-xs font-mono text-white transition-colors shrink-0"
        >
          <span>Grafana</span>
          <ExternalLink className="w-3.5 h-3.5" />
        </a>
      </div>

      {/* Protocol Capabilities Matrix & Scenario Selector (PostgreSQL system_state) */}
      <div className="p-4 rounded-xl bg-white/[0.02] border border-white/[0.08] space-y-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <div className="text-xs font-semibold text-white font-mono">
              Сценарий сбора метрик и триггеры стенда
            </div>
            <div className="text-[11px] text-zinc-400 font-mono mt-0.5">
              Активный канал:{' '}
              <span className="text-cyan-300 font-semibold uppercase">
                {collectorPlan?.protocol || 'ssh_cli'}
              </span>
              {collectorPlan?.port ? ` (:${collectorPlan.port})` : ''}
              {collectorPlan?.verified_by ? ` · ${collectorPlan.verified_by}` : ''}
            </div>
            {fallbackEvents[0] && (
              <div className="mt-1 text-[11px] font-mono text-amber-300" data-testid="fallback-event">
                ⚠ {new Date(fallbackEvents[0].ts).toLocaleString()} · {fallbackEvents[0].from} →{' '}
                {fallbackEvents[0].to} ({fallbackEvents[0].reason})
              </div>
            )}
          </div>
          <select
            disabled={isViewer || protoSaving}
            value={
              collectorPlan?.selected_mode === 'node_exporter'
                ? 'prometheus_exporter'
                : collectorPlan?.selected_mode || 'auto'
            }
            onChange={(e) =>
              onProtocolChange(
                e.target.value as 'auto' | 'gnmi' | 'snmp' | 'prometheus_exporter' | 'ssh_cli'
              )
            }
            className="px-2.5 py-1.5 rounded-lg bg-[#07080c] border border-white/[0.12] text-xs font-mono text-zinc-200 focus:outline-none focus:border-cyan-400 disabled:opacity-50"
          >
            <option value="auto">Авто (Каскад триггеров)</option>
            <option value="gnmi">gNMI (Streaming)</option>
            <option value="snmp">SNMP v2c/v3 (UDP)</option>
            <option value="prometheus_exporter">Node Exporter (HTTP)</option>
            <option value="ssh_cli">Кастомный SSH CLI (Пул)</option>
          </select>
        </div>

        <div className="grid grid-cols-2 gap-2 pt-1">
          {([
            { key: 'gnmi', label: 'gNMI Stream', desc: 'CLI + gRPC Capabilities' },
            { key: 'snmp', label: 'SNMP v2c/v3', desc: 'UDP BER sysDescr.0' },
            { key: 'prometheus_exporter', label: 'Node Exporter', desc: 'HTTP /metrics' },
            { key: 'ssh_cli', label: 'Custom SSH CLI', desc: 'Пул 1 сессии + YAML парсер' },
          ] as const).map((p) => {
            const info = collectorPlan?.supported_protocols?.[p.key];
            const isSupported = p.key === 'ssh_cli' ? true : Boolean(info?.supported);
            const isActive = (collectorPlan?.protocol || 'ssh_cli') === p.key;
            return (
              <div
                key={p.key}
                className={`p-2.5 rounded-lg border text-[11px] font-mono ${
                  isActive
                    ? 'bg-cyan-500/10 border-cyan-500/30 text-white'
                    : 'bg-white/[0.015] border-white/[0.06] text-zinc-400'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-zinc-200">{p.label}</span>
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded ${
                      isSupported
                        ? 'bg-emerald-500/15 text-emerald-300'
                        : 'bg-zinc-500/15 text-zinc-400'
                    }`}
                  >
                    {isSupported ? 'Доступен' : 'Откл'}
                  </span>
                </div>
                <div className="text-[10px] text-zinc-500 mt-1 truncate">
                  {info?.port ? `Порт :${info.port} · ` : ''}
                  {info?.verified_by || p.desc}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="p-3.5 rounded-xl border border-white/[0.06] bg-white/[0.02] space-y-1.5 text-xs text-zinc-400">
        <span className="text-zinc-300 font-medium font-mono text-[11px]">Аппаратные спецификации:</span>
        <p className="font-mono text-[11px]">
          {device.hardware_specs ||
            `${device.platform} · ${device.management_ip}:${device.management_port} · Портов в Intent: ${device.intent?.interfaces?.length ?? 0}`}
        </p>
      </div>
    </div>
  );
};
