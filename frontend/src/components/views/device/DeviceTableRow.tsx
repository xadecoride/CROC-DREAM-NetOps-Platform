import React, { useState } from 'react';
import {
  ExternalLink,
  GitCompare,
  Play,
  Trash2,
  Zap,
} from 'lucide-react';
import { api, getErrorMessage } from '../../../api';
import type { Device, UserRole } from '../../../api';
import type { Locale } from '../../../i18n';
import {
  OperStatusBadge,
  StatusBadge,
  ManagementModeBadge,
  Sparkline,
  StateTimeline,
} from '../../layout/ui';
import { PLATFORM_NAMES, ROLE_NAMES } from '../../../constants';

export interface DeviceTableRowProps {
  device: Device;
  selected: boolean;
  onToggleSelect: (id: number) => void;
  onOpenDevice: (id: number) => void;
  onOpenDiff?: (id: number) => void;
  onRunDryRun: (deviceIds: number[]) => void;
  onDelete: (id: number) => Promise<void>;
  onRefresh: () => void;
  userRole?: UserRole;
  locale?: Locale;
  deleteConfirmText: string;
  deleteBtnText: string;
}

export const DeviceTableRow: React.FC<DeviceTableRowProps> = ({
  device,
  selected,
  onToggleSelect,
  onOpenDevice,
  onOpenDiff,
  onRunDryRun,
  onDelete,
  onRefresh,
  userRole,
  locale = 'ru',
  deleteConfirmText,
  deleteBtnText,
}) => {
  const [promoting, setPromoting] = useState(false);

  const handlePromote = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setPromoting(true);
    try {
      await api.promoteDevice(device.id);
      onRefresh();
    } catch (err: unknown) {
      alert(`Ошибка активации управления: ${getErrorMessage(err)}`);
    } finally {
      setPromoting(false);
    }
  };

  const handleDelete = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (window.confirm(`${deleteConfirmText} (${device.hostname})`)) {
      await onDelete(device.id);
    }
  };

  return (
    <tr
      key={device.id}
      onClick={() => onOpenDevice(device.id)}
      className="hover:bg-[var(--bg-elevated)]/40 cursor-pointer transition-colors group"
    >
      {/* Select Checkbox */}
      <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggleSelect(device.id)}
          className="rounded border-[var(--border-hover)] cursor-pointer accent-zinc-200"
        />
      </td>

      {/* Device Hostname & IP */}
      <td className="px-3 py-2">
        <div className="flex flex-col">
          <span className="font-semibold text-[var(--text-primary)] font-mono tracking-tight whitespace-nowrap group-hover:underline">
            {device.hostname}
          </span>
          <span className="font-mono text-[11px] text-[var(--text-muted)]">
            {device.management_ip}:{device.management_port}
          </span>
        </div>
      </td>

      {/* Role Pill */}
      <td className="px-3 py-2">
        <span className="inline-block px-2 py-0.5 rounded text-[11px] font-mono bg-[var(--bg-elevated)] text-[var(--text-secondary)] border border-[var(--border-subtle)]">
          {ROLE_NAMES[device.role] ?? device.role}
        </span>
      </td>

      {/* Platform Pill */}
      <td className="px-3 py-2">
        <span className="inline-block px-2 py-0.5 rounded text-[11px] font-mono bg-[var(--bg-subtle)] text-[var(--text-muted)] border border-[var(--border-subtle)]">
          {PLATFORM_NAMES[device.platform] ?? device.platform}
        </span>
      </td>

      {/* Management Mode Pill & Promote button */}
      <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-1.5">
          <ManagementModeBadge mode={device.management_mode} />
          {device.management_mode === 'MONITORING_ONLY' && (userRole === 'admin' || userRole === 'owner') && (
            <button
              onClick={handlePromote}
              disabled={promoting}
              className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-[var(--bg-elevated)] hover:border-[var(--border-hover)] text-[var(--text-primary)] border border-[var(--border-subtle)] transition-colors flex items-center gap-1 cursor-pointer"
              title="Активировать прямое управление (Promote to MANAGED)"
            >
              <Zap className="w-2.5 h-2.5" />
              <span>{promoting ? '…' : 'В управление'}</span>
            </button>
          )}
        </div>
      </td>

      {/* Oper Status */}
      <td className="px-3 py-2">
        <OperStatusBadge status={device.oper_status ?? 'UP'} />
      </td>

      {/* Intent Status */}
      <td className="px-3 py-2">
        <StatusBadge status={device.status} />
      </td>

      {/* Micro-Sparkline */}
      <td className="px-3 py-2 hidden xl:table-cell" onClick={(e) => e.stopPropagation()}>
        <div className="sparkline" data-testid="sparkline">
          {device.sparkline && device.sparkline.length > 0 ? (
            <Sparkline
              className="sparkline"
              points={device.sparkline}
              width={76}
              height={20}
            />
          ) : (
            <span className="text-[10px] font-mono text-[var(--text-muted)]">N/A</span>
          )}
        </div>
      </td>

      {/* 24h State Timeline */}
      <td className="px-3 py-2 hidden xl:table-cell" onClick={(e) => e.stopPropagation()}>
        <div className="state-timeline" data-testid="state-timeline">
          {device.state_timeline && device.state_timeline.length > 0 ? (
            <StateTimeline states={device.state_timeline} />
          ) : (
            <span className="text-[10px] font-mono text-[var(--text-muted)]">N/A</span>
          )}
        </div>
      </td>

      {/* 1-Click Action Buttons */}
      <td className="px-3 py-2 text-right" onClick={(e) => e.stopPropagation()}>
        <div className="inline-flex items-center gap-1 whitespace-nowrap">
          <button
            onClick={() => onOpenDevice(device.id)}
            className="px-2 py-1 rounded bg-[var(--bg-elevated)] hover:border-[var(--border-hover)] text-[var(--text-primary)] text-[11px] font-mono flex items-center gap-1 transition-colors cursor-pointer border border-[var(--border-subtle)]"
            title="Открыть карточку устройства"
          >
            <ExternalLink className="w-3 h-3 text-[var(--text-secondary)]" />
            <span className="hidden 2xl:inline">{locale === 'en' ? 'Details' : 'Карточка'}</span>
          </button>

          <button
            onClick={() => {
              if (onOpenDiff) onOpenDiff(device.id);
              else onOpenDevice(device.id);
            }}
            className="px-2 py-1 rounded bg-[var(--bg-elevated)] hover:border-[var(--border-hover)] text-[var(--text-primary)] text-[11px] font-mono flex items-center gap-1 transition-colors cursor-pointer border border-[var(--border-subtle)]"
            title="Сверить конфигурации (Diff)"
          >
            <GitCompare className="w-3 h-3 text-[var(--text-secondary)]" />
            <span className="hidden 2xl:inline">Diff</span>
          </button>

          {userRole !== 'viewer' && (
            <button
              onClick={() => onRunDryRun([device.id])}
              className="px-2 py-1 rounded bg-[var(--text-primary)] text-[var(--bg-main)] hover:opacity-90 text-[11px] font-mono font-medium flex items-center gap-1 transition-opacity cursor-pointer"
              title="Запустить префлайт Dry-run"
            >
              <Play className="w-3 h-3 fill-current" />
              <span className="hidden 2xl:inline">Dry-run</span>
            </button>
          )}

          {(userRole === 'admin' || userRole === 'owner') && (
            <button
              onClick={handleDelete}
              className="px-2 py-1 rounded bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/25 text-rose-400 text-[11px] font-mono flex items-center gap-1 transition-colors cursor-pointer"
              title={deleteBtnText}
            >
              <Trash2 className="w-3 h-3" />
              <span className="hidden 2xl:inline">{deleteBtnText}</span>
            </button>
          )}
        </div>
      </td>
    </tr>
  );
};
