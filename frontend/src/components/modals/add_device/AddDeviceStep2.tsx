import React from 'react';
import {
  Cpu,
  HardDrive,
  Radio,
  CheckCircle2,
  AlertTriangle,
  Network,
} from 'lucide-react';
import type { DeviceProbeResult, Platform, DeviceRole } from '../../../api';
import { PLATFORM_NAMES, ROLE_NAMES } from '../../../constants';

export interface AddDeviceStep2Props {
  probeResult: DeviceProbeResult;
  hostname: string;
  setHostname: (val: string) => void;
  role: DeviceRole;
  setRole: (val: DeviceRole) => void;
  platform: Platform;
  setPlatform: (val: Platform) => void;
  preferredProtocol?: string;
  setPreferredProtocol?: (val: string) => void;
  probeError: string;
}

const renderSelectOptions = (items: Record<string, string>) =>
  Object.entries(items).map(([k, v]) => (
    <option key={k} value={k}>
      {v}
    </option>
  ));

export const AddDeviceStep2: React.FC<AddDeviceStep2Props> = ({
  probeResult,
  hostname,
  setHostname,
  role,
  setRole,
  platform,
  setPlatform,
  preferredProtocol = 'auto',
  setPreferredProtocol,
  probeError,
}) => {
  return (
    <div className="space-y-4 animate-fade-in">
      <div
        className={`p-4 rounded-xl border flex items-center justify-between ${
          probeResult.reachable
            ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-300'
            : 'bg-rose-500/10 border-rose-500/25 text-rose-300'
        }`}
      >
        <div className="flex items-center gap-3">
          {probeResult.reachable ? (
            <CheckCircle2 className="w-6 h-6 text-emerald-400 shrink-0" />
          ) : (
            <AlertTriangle className="w-6 h-6 text-rose-400 shrink-0" />
          )}
          <div>
            <div className="font-semibold text-xs text-white">
              {probeResult.reachable
                ? 'Оборудование успешно обнаружено'
                : 'Узел не отвечает по указанному порту'}
            </div>
            <div className="text-[11px] text-zinc-400">{probeResult.message}</div>
          </div>
        </div>
        <span className="text-[11px] font-mono uppercase px-2 py-0.5 rounded bg-black/40">
          {probeResult.reachable ? 'SSH Reachable' : 'Unreachable'}
        </span>
      </div>

      {/* Discovered Specs Bento */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
        <div className="p-3 rounded-xl bg-[#0c0e14] border border-white/[0.06]">
          <div className="flex items-center gap-1.5 text-zinc-400 text-[11px] mb-1">
            <Cpu className="w-3.5 h-3.5 text-cyan-400" />
            <span>CPU Cores</span>
          </div>
          <div className="text-base font-bold text-zinc-100 font-mono">
            {probeResult.cpu_cores ? `${probeResult.cpu_cores} vCPU` : '—'}
          </div>
        </div>
        <div className="p-3 rounded-xl bg-[#0c0e14] border border-white/[0.06]">
          <div className="flex items-center gap-1.5 text-zinc-400 text-[11px] mb-1">
            <HardDrive className="w-3.5 h-3.5 text-indigo-400" />
            <span>Память RAM</span>
          </div>
          <div className="text-base font-bold text-zinc-100 font-mono">
            {probeResult.ram_gb ? `${probeResult.ram_gb} GB` : '—'}
          </div>
        </div>
        <div className="p-3 rounded-xl bg-[#0c0e14] border border-white/[0.06]">
          <div className="flex items-center gap-1.5 text-zinc-400 text-[11px] mb-1">
            <HardDrive className="w-3.5 h-3.5 text-emerald-400" />
            <span>Диск NVMe</span>
          </div>
          <div className="text-base font-bold text-zinc-100 font-mono">
            {probeResult.disk_gb ? `${probeResult.disk_gb} GB` : '—'}
          </div>
        </div>
        <div className="p-3 rounded-xl bg-[#0c0e14] border border-white/[0.06]">
          <div className="flex items-center gap-1.5 text-zinc-400 text-[11px] mb-1">
            <Radio className="w-3.5 h-3.5 text-amber-400" />
            <span>Интерфейсы</span>
          </div>
          <div className="text-base font-bold text-zinc-100 font-mono">
            {probeResult.interfaces && probeResult.interfaces.length > 0
              ? `${probeResult.interfaces.length} портов`
              : '—'}
          </div>
        </div>
      </div>

      {/* Device Meta Form */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 pt-1">
        <div>
          <label className="block text-[11px] uppercase tracking-wider font-semibold text-zinc-400 mb-1">
            Хостнейм в фабрике
          </label>
          <input
            type="text"
            value={hostname}
            onChange={(e) => setHostname(e.target.value)}
            placeholder={probeResult.hostname || 'leaf-1'}
            className="w-full bg-[#050608] border border-white/[0.08] focus:border-cyan-500/60 rounded-lg px-3 py-1.5 text-xs text-zinc-100 font-mono focus:outline-none"
          />
        </div>
        <div>
          <label className="block text-[11px] uppercase tracking-wider font-semibold text-zinc-400 mb-1">
            Роль в топологии
          </label>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as DeviceRole)}
            className="w-full bg-[#050608] border border-white/[0.08] focus:border-cyan-500/60 rounded-lg px-3 py-1.5 text-xs text-zinc-200 focus:outline-none"
          >
            {renderSelectOptions(ROLE_NAMES)}
          </select>
        </div>
        <div>
          <label className="block text-[11px] uppercase tracking-wider font-semibold text-zinc-400 mb-1">
            Сетевая ОС / Платформа
          </label>
          <select
            value={platform}
            onChange={(e) => setPlatform(e.target.value as Platform)}
            className="w-full bg-[#050608] border border-white/[0.08] focus:border-cyan-500/60 rounded-lg px-3 py-1.5 text-xs text-zinc-200 focus:outline-none"
          >
            {renderSelectOptions(PLATFORM_NAMES)}
          </select>

        </div>
        <div>
          <label className="block text-[11px] uppercase tracking-wider font-semibold text-zinc-400 mb-1">
            Телеметрия (Override)
          </label>
          <select
            value={preferredProtocol}
            onChange={(e) => setPreferredProtocol?.(e.target.value)}
            className="w-full bg-[#050608] border border-white/[0.08] focus:border-cyan-500/60 rounded-lg px-3 py-1.5 text-xs text-zinc-200 focus:outline-none"
          >
            <option value="auto">Авто-выбор (Auto)</option>
            <option value="gnmi">gNMI Streaming</option>
            <option value="snmp">SNMP Fabric</option>
            <option value="prometheus_exporter">Node Exporter</option>
            <option value="ssh_cli">SSH CLI Fallback</option>
          </select>
        </div>
      </div>

      {/* Safe-First Callout */}
      <div className="p-2.5 rounded-xl bg-cyan-950/20 border border-cyan-500/20 text-cyan-200 text-xs flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Network className="w-4 h-4 text-cyan-400 shrink-0" />
          <span className="font-semibold text-cyan-300">Safe-First: MONITORING_ONLY</span>
        </div>
        <span className="text-[11px] text-zinc-400 font-mono">Сбор телеметрии без мутаций</span>
      </div>

      {/* Error in Step 2 */}
      {probeError && (
        <div className="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/25 text-rose-300 text-xs flex items-center gap-2.5">
          <AlertTriangle className="w-4 h-4 shrink-0 text-rose-400" />
          <span>{probeError}</span>
        </div>
      )}
    </div>
  );
};
