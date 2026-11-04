import React, { useMemo, useState } from 'react';
import {
  Search,
  Play,
  ShieldCheck,
  RefreshCw,
  FileCheck2,
  ArrowUpDown,
  Plus,
  Trash2,
} from 'lucide-react';
import { api, getErrorMessage } from '../../api';
import type { Device, UserRole } from '../../api';
import { translations, type Locale } from '../../i18n';
import { PLATFORM_NAMES, ROLE_NAMES } from '../../constants';
import { AddDeviceModal } from '../modals/AddDeviceModal';
import { DeviceTableRow } from './device/DeviceTableRow';

interface DeviceListProps {
  devices: Device[];
  loading: boolean;
  userRole?: UserRole;
  locale?: Locale;
  onRefresh: () => void;
  onRunDryRun: (deviceIds: number[]) => void;
  onScanDrift: (deviceIds?: number[]) => void;
  onSyncInventory: () => void;
  onLintIntent: () => void;
  onOpenDevice: (deviceId: number) => void;
  onOpenDiff?: (deviceId?: number) => void;
  onDeleteDevice?: (deviceId: number) => void;
}

type SortKey = 'hostname' | 'role' | 'platform' | 'management_ip' | 'management_mode' | 'oper_status' | 'status';

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'hostname', label: 'Устройство' },
  { key: 'role', label: 'Роль' },
  { key: 'platform', label: 'Платформа' },
  { key: 'management_mode', label: 'Режим' },
  { key: 'oper_status', label: 'Oper Status' },
  { key: 'status', label: 'Intent Status' },
];

const Filter: React.FC<{
  value: string;
  onChange: (v: string) => void;
  label: string;
  options: Record<string, string>;
}> = ({ value, onChange, label, options }) => (
  <select
    value={value}
    onChange={(e) => onChange(e.target.value)}
    className="bg-[var(--bg-card)] border border-[var(--border-subtle)] hover:border-[var(--border-hover)] rounded-md px-2.5 py-1.5 text-xs text-[var(--text-primary)] focus:outline-none cursor-pointer"
  >
    <option value="">{label}: все</option>
    {Object.entries(options).map(([k, v]) => (
      <option key={k} value={k}>
        {v}
      </option>
    ))}
  </select>
);

const ipKey = (ip: string) => ip.split('.').reduce((acc, p) => acc * 256 + Number(p), 0);

export const DeviceList: React.FC<DeviceListProps> = ({
  devices,
  loading,
  userRole,
  locale = 'ru',
  onRefresh,
  onRunDryRun,
  onScanDrift,
  onSyncInventory,
  onLintIntent,
  onOpenDevice,
  onOpenDiff,
  onDeleteDevice,
}) => {
  const t = translations[locale];
  const [query, setQuery] = useState('');
  const [platform, setPlatform] = useState('');
  const [role, setRole] = useState('');
  const [status, setStatus] = useState('');
  const [operStatus, setOperStatus] = useState('');
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'hostname', dir: 1 });
  const [selected, setSelected] = useState<number[]>([]);
  const [addModalOpen, setAddModalOpen] = useState(false);

  const handleDelete = async (id: number) => {
    try {
      if (onDeleteDevice) {
        await onDeleteDevice(id);
      } else {
        await api.deleteDevice(id);
        await onRefresh();
      }
    } catch (err: unknown) {
      alert(`${t.deleteError}: ${getErrorMessage(err)}`);
    }
  };

  const handleBulkDelete = async () => {
    if (!window.confirm(`${t.deleteConfirm} (${selected.length})`)) return;
    try {
      const results = await Promise.allSettled(selected.map((id) => api.deleteDevice(id)));
      const succeeded: number[] = [];
      const failed: string[] = [];
      results.forEach((res, idx) => {
        if (res.status === 'fulfilled') {
          succeeded.push(selected[idx]);
        } else {
          failed.push(getErrorMessage(res.reason, 'Error'));
        }
      });
      setSelected((prev) => prev.filter((id) => !succeeded.includes(id)));
      await onRefresh();
      if (failed.length > 0) {
        alert(`${t.deleteError}: ${failed.length} / ${selected.length} ${locale === 'en' ? 'failed' : 'не удалось удалить'}`);
      }
    } catch (err: unknown) {
      alert(`${t.deleteError}: ${getErrorMessage(err)}`);
    }
  };

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = devices.filter(
      (d) =>
        (!platform || d.platform === platform) &&
        (!role || d.role === role) &&
        (!status || d.status === status) &&
        (!operStatus || (d.oper_status ?? 'UP') === operStatus)
    );
    if (q) {
      const tokens = q.split(/\s+/).filter(Boolean);
      return filtered.filter((d) => {
        const hay = `${d.hostname} ${d.management_ip} ${d.platform} ${PLATFORM_NAMES[d.platform] ?? ''} ${d.role} ${ROLE_NAMES[d.role] ?? ''} ${d.status} ${d.site ?? ''}`.toLowerCase();
        return tokens.every((tok) => hay.includes(tok));
      });
    }
    return [...filtered].sort((a, b) => {
      if (sort.key === 'management_ip') {
        const x = ipKey(a.management_ip);
        const y = ipKey(b.management_ip);
        return (x > y ? 1 : x < y ? -1 : 0) * sort.dir;
      }
      const x = String(sort.key === 'oper_status' ? (a.oper_status ?? 'UP') : a[sort.key]);
      const y = String(sort.key === 'oper_status' ? (b.oper_status ?? 'UP') : b[sort.key]);
      return (x > y ? 1 : x < y ? -1 : 0) * sort.dir;
    });
  }, [devices, query, platform, role, status, operStatus, sort]);

  const toggle = (id: number) =>
    setSelected((s) => (s.includes(id) ? s.filter((i) => i !== id) : [...s, id]));
  const allSelected = rows.length > 0 && rows.every((d) => selected.includes(d.id));
  const targets = selected.length ? selected : rows.map((d) => d.id);
  const scope = selected.length
    ? `${locale === 'en' ? 'selected' : 'выбрано'}: ${selected.length}`
    : locale === 'en'
    ? 'all in list'
    : 'все в списке';

  const btn =
    'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium cursor-pointer transition-colors disabled:opacity-40';

  return (
    <div className="w-full max-w-[2560px] mx-auto space-y-4">
      {/* Title & Top Toolbar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5">
        <div>
          <h1 className="text-lg font-semibold text-[var(--text-primary)] tracking-tight">
            {locale === 'en' ? 'Devices' : 'Устройства'}{' '}
            <span className="text-[var(--text-muted)] font-normal text-sm ml-1 font-mono tabular-nums">
              {rows.length} / {devices.length}
            </span>
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
          {(userRole === 'admin' || userRole === 'owner') && selected.length > 0 && (
            <button
              onClick={handleBulkDelete}
              className={`${btn} bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/30 text-rose-400 font-semibold`}
            >
              <Trash2 className="w-3.5 h-3.5" />
              <span>{t.deleteSelectedBtn} ({selected.length})</span>
            </button>
          )}
          {(userRole === 'admin' || userRole === 'owner') && (
            <button
              onClick={() => setAddModalOpen(true)}
              className={`${btn} bg-[var(--text-primary)] text-[var(--bg-main)] hover:opacity-90 font-semibold`}
            >
              <Plus className="w-3.5 h-3.5" /> {t.addDeviceBtn}
            </button>
          )}
          {userRole !== 'viewer' && (
            <>
              <button
                onClick={() => {
                  onSyncInventory();
                  window.dispatchEvent(new Event('netops-git-updated'));
                }}
                className={`${btn} border border-[var(--border-subtle)] hover:border-[var(--border-hover)] bg-[var(--bg-card)] text-[var(--text-primary)]`}
              >
                <RefreshCw className="w-3.5 h-3.5 text-[var(--text-secondary)]" /> {t.syncInventoryBtn}
              </button>
              <button
                onClick={onLintIntent}
                className={`${btn} border border-[var(--border-subtle)] hover:border-[var(--border-hover)] bg-[var(--bg-card)] text-[var(--text-primary)]`}
              >
                <FileCheck2 className="w-3.5 h-3.5 text-[var(--text-secondary)]" /> Lint
              </button>
            </>
          )}
        </div>
      </div>

      {/* Filter Toolbar */}
      <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
        <div className="relative w-full sm:w-auto">
          <Search className="w-3.5 h-3.5 text-[var(--text-muted)] absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Имя или IP…"
            className="bg-[var(--bg-card)] border border-[var(--border-subtle)] hover:border-[var(--border-hover)] rounded-md pl-8 pr-3 py-1.5 text-xs text-[var(--text-primary)] w-full sm:w-48 focus:outline-none focus:border-[var(--text-secondary)]"
          />
        </div>
        <Filter value={role} onChange={setRole} label="Роль" options={ROLE_NAMES} />
        <Filter value={platform} onChange={setPlatform} label="Платформа" options={PLATFORM_NAMES} />
        <Filter
          value={operStatus}
          onChange={setOperStatus}
          label="Oper"
          options={{ UP: 'UP', DOWN: 'DOWN', DEGRADED: 'DEGRADED' }}
        />
        <Filter
          value={status}
          onChange={setStatus}
          label="Intent"
          options={{
            IN_SYNC: 'В синхроне',
            DRIFT_DETECTED: 'Дрейф',
            UNREACHABLE: 'Недоступно',
            UNKNOWN: 'Неизвестно',
          }}
        />

        <div className="w-full sm:w-auto sm:ml-auto flex flex-wrap items-center gap-1.5 sm:gap-2">
          {userRole !== 'viewer' && (
            <>
              <span className="text-[11px] text-[var(--text-muted)] font-mono tabular-nums">{scope}</span>
              <button
                disabled={!targets.length}
                onClick={() => onRunDryRun(targets)}
                className={`${btn} bg-[var(--text-primary)] text-[var(--bg-main)] hover:opacity-90 font-semibold`}
              >
                <Play className="w-3.5 h-3.5 fill-current" /> Dry-run
              </button>
              <button
                disabled={!targets.length}
                onClick={() => onScanDrift(selected.length ? selected : undefined)}
                className={`${btn} border border-[var(--border-subtle)] hover:border-[var(--border-hover)] bg-[var(--bg-card)] text-[var(--text-primary)]`}
              >
                <ShieldCheck className="w-3.5 h-3.5 text-[var(--text-secondary)]" /> Скан дрейфа
              </button>
            </>
          )}
          <button
            onClick={onRefresh}
            className={`${btn} text-[var(--text-secondary)] hover:text-[var(--text-primary)] p-2`}
            title="Обновить"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* High-Density Inventory Table */}
      <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-card)] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-xs border-collapse tabular-nums">
            <thead>
              <tr className="bg-[var(--bg-elevated)]/60 text-[11px] uppercase tracking-wide text-[var(--text-muted)] border-b border-[var(--border-subtle)]">
                <th className="w-10 px-3 py-2.5">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={() => setSelected(allSelected ? [] : rows.map((d) => d.id))}
                    className="rounded border-[var(--border-hover)] cursor-pointer accent-zinc-200"
                  />
                </th>
                {COLUMNS.map((c) => (
                  <th key={c.key} className="px-3 py-2.5 font-medium">
                    <button
                      onClick={() =>
                        setSort((s) => ({
                          key: c.key,
                          dir: s.key === c.key ? (-s.dir as 1 | -1) : 1,
                        }))
                      }
                      className="flex items-center gap-1 uppercase tracking-wide cursor-pointer hover:text-[var(--text-primary)] transition-colors"
                    >
                      {c.label}
                      <ArrowUpDown
                        className={`w-3 h-3 ${sort.key === c.key ? 'text-[var(--text-primary)]' : 'opacity-35'}`}
                      />
                    </button>
                  </th>
                ))}
                <th className="px-3 py-2.5 font-medium hidden xl:table-cell">Нагрузка</th>
                <th className="px-3 py-2.5 font-medium hidden xl:table-cell">24ч</th>
                <th className="px-3 py-2.5 font-medium text-right">Действия</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border-subtle)]">
              {rows.map((d) => (
                <DeviceTableRow
                  key={d.id}
                  device={d}
                  selected={selected.includes(d.id)}
                  onToggleSelect={toggle}
                  onOpenDevice={onOpenDevice}
                  onOpenDiff={onOpenDiff}
                  onRunDryRun={onRunDryRun}
                  onDelete={handleDelete}
                  onRefresh={onRefresh}
                  userRole={userRole}
                  locale={locale}
                  deleteConfirmText={t.deleteConfirm}
                  deleteBtnText={t.deleteDeviceBtn}
                />
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={11} className="px-6 py-12 text-center">
                    <div className="max-w-md mx-auto space-y-3">
                      <h3 className="text-sm font-semibold text-[var(--text-primary)]">
                        {devices.length ? 'Нет совпадений' : 'Нет устройств'}
                      </h3>
                      {!devices.length && userRole !== 'viewer' && (
                        <div className="flex flex-wrap items-center justify-center gap-2 pt-1">
                          {(userRole === 'admin' || userRole === 'owner') && (
                            <button
                              onClick={() => setAddModalOpen(true)}
                              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold bg-[var(--text-primary)] text-[var(--bg-main)] hover:opacity-90 transition-opacity cursor-pointer"
                            >
                              <Plus className="w-3.5 h-3.5" /> + Добавить
                            </button>
                          )}
                          <button
                            onClick={onSyncInventory}
                            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border border-[var(--border-subtle)] text-[var(--text-primary)] hover:border-[var(--border-hover)] transition-colors cursor-pointer"
                          >
                            <RefreshCw className="w-3.5 h-3.5" /> Синхронизировать
                          </button>
                        </div>
                      )}
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <AddDeviceModal
        isOpen={addModalOpen}
        onClose={() => setAddModalOpen(false)}
        onSuccess={() => onRefresh()}
        userRole={userRole || 'viewer'}
      />
    </div>
  );
};
