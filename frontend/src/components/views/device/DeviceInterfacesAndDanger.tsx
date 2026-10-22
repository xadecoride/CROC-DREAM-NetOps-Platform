import React from 'react';
import type { DeviceDetail } from '../../../api';

const Th: React.FC<{ children:React.ReactNode }> = ({ children }) => (
  <th className="px-3 py-2 text-left text-[11px] uppercase tracking-wide font-medium text-zinc-500">
    {children}
  </th>
);

interface DeviceInterfacesAndDangerProps {
  device: DeviceDetail;
}

export const DeviceInterfacesAndDanger: React.FC<DeviceInterfacesAndDangerProps> = ({
  device,
}) => (
  <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-card)] overflow-hidden">
    <div className="px-4 py-2.5 bg-[var(--bg-elevated)]/60 border-b border-[var(--border-subtle)] flex items-center justify-between">
      <span className="text-xs font-medium text-[var(--text-primary)] tracking-tight">
        Сетевые интерфейсы (декларативный профиль Git SoT)
      </span>
      <span className="text-[11px] text-[var(--text-muted)] font-mono">
        {device.intent?.interfaces.length || 0} настроено в SoT
      </span>
    </div>
    <table className="w-full text-[13px]">
      <thead className="bg-white/[0.02]">
        <tr>
          <Th>Интерфейс</Th>
          <Th>Описание</Th>
          <Th>IPv4 адрес</Th>
          <Th>MTU</Th>
          <Th>Режим</Th>
          <Th>Состояние</Th>
        </tr>
      </thead>
      <tbody className="divide-y divide-white/[0.04]">
        {device.intent?.interfaces.map((i) => (
          <tr key={i.name} className="hover:bg-white/[0.02] transition-colors">
            <td className="px-3 py-2 text-[var(--text-primary)] font-mono font-medium">{i.name}</td>
            <td className="px-3 py-2 text-[var(--text-secondary)]">{i.description ?? '—'}</td>
            <td className="px-3 py-2 font-mono text-[var(--text-primary)]">{i.ipv4_address ?? '—'}</td>
            <td className="px-3 py-2 font-mono text-[var(--text-secondary)]">{i.mtu}</td>
            <td className="px-3 py-2 text-[var(--text-secondary)] font-mono uppercase text-xs">{i.mode}</td>
            <td className="px-3 py-2">
              {device.status === 'UNREACHABLE' || device.oper_status === 'DOWN' ? (
                <span
                  className="inline-flex items-center gap-1.5 text-rose-400 text-xs font-mono"
                  title="Устройство недоступно по SSH; в Git SoT интерфейс объявлен как enabled"
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-rose-400" /> unreachable ({i.enabled ? 'SoT: up' : 'SoT: down'})
                </span>
              ) : i.enabled ? (
                <span className="inline-flex items-center gap-1.5 text-[var(--text-primary)] text-xs font-mono">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" /> up
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 text-[var(--text-muted)] text-xs font-mono">
                  <span className="w-1.5 h-1.5 rounded-full bg-zinc-600" /> shutdown
                </span>
              )}
            </td>
          </tr>
        ))}
        {!device.intent?.interfaces.length && (
          <tr>
            <td colSpan={6} className="px-3 py-8 text-center text-[var(--text-muted)]">
              Интерфейсы еще не импортированы в Intent
            </td>
          </tr>
        )}
      </tbody>
    </table>
  </div>
);
