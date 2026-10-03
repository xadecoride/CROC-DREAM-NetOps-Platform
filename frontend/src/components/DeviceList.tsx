import { useState } from 'react';
import {
  Server,
  Play,
  RotateCw,
  Search,
  CheckCircle2,
  AlertTriangle,
  HelpCircle,
  ExternalLink,
  ShieldCheck,
  RefreshCw,
} from 'lucide-react';
import type { Device, DeviceStatus } from '../api';
import { DeviceDetailModal } from './DeviceDetailModal';

interface DeviceListProps {
  devices: Device[];
  loading: boolean;
  onRefresh: () => void;
  onRunDryRun: (deviceIds: number[]) => void;
  onScanDrift: (deviceIds?: number[]) => void;
  onSyncInventory: () => void;
  onLintIntent: () => void;
}

export const DeviceList: React.FC<DeviceListProps> = ({
  devices,
  loading,
  onRefresh,
  onRunDryRun,
  onScanDrift,
  onSyncInventory,
  onLintIntent,
}) => {
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [selectedDeviceModalId, setSelectedDeviceModalId] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  const toggleSelectAll = () => {
    if (selectedIds.length === devices.length) {
      setSelectedIds([]);
    } else {
      setSelectedIds(devices.map((d) => d.id));
    }
  };

  const toggleSelectOne = (id: number) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]
    );
  };

  const filteredDevices = devices.filter(
    (d) =>
      d.hostname.toLowerCase().includes(searchQuery.toLowerCase()) ||
      d.management_ip.includes(searchQuery) ||
      d.platform.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const inSyncCount = devices.filter((d) => d.status === 'IN_SYNC').length;
  const driftCount = devices.filter((d) => d.status === 'DRIFT_DETECTED').length;
  const unreachableCount = devices.filter((d) => d.status === 'UNREACHABLE').length;

  const getStatusBadge = (status: DeviceStatus) => {
    switch (status) {
      case 'IN_SYNC':
        return (
          <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
            <CheckCircle2 className="w-3.5 h-3.5" />
            <span>IN_SYNC</span>
          </span>
        );
      case 'DRIFT_DETECTED':
        return (
          <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-500/10 text-amber-400 border border-amber-500/30">
            <AlertTriangle className="w-3.5 h-3.5" />
            <span>DRIFT DETECTED</span>
          </span>
        );
      case 'UNREACHABLE':
        return (
          <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-rose-500/10 text-rose-400 border border-rose-500/30">
            <AlertTriangle className="w-3.5 h-3.5" />
            <span>UNREACHABLE</span>
          </span>
        );
      case 'IN_PROGRESS':
        return (
          <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-indigo-500/10 text-indigo-400 border border-indigo-500/30 animate-pulse">
            <RotateCw className="w-3.5 h-3.5 animate-spin" />
            <span>IN PROGRESS</span>
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-800 text-slate-400 border border-slate-700">
            <HelpCircle className="w-3.5 h-3.5" />
            <span>UNKNOWN</span>
          </span>
        );
    }
  };

  return (
    <div className="space-y-6">
      {/* Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-400">Всего узлов фабрики</span>
            <Server className="w-4 h-4 text-indigo-400" />
          </div>
          <div className="mt-2 text-2xl font-bold text-white">{devices.length}</div>
          <div className="mt-1 text-[11px] text-slate-500">2x Arista cEOS, 2x Cisco IOS-XE</div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-emerald-400">В синхронизации</span>
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="mt-2 text-2xl font-bold text-emerald-300">{inSyncCount}</div>
          <div className="mt-1 text-[11px] text-emerald-500/80">Running = Intended</div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-amber-400">Обнаружен дрейф</span>
            <AlertTriangle className="w-4 h-4 text-amber-400" />
          </div>
          <div className="mt-2 text-2xl font-bold text-amber-300">{driftCount}</div>
          <div className="mt-1 text-[11px] text-amber-500/80">Требуется remediate</div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-rose-400">Недоступно</span>
            <AlertTriangle className="w-4 h-4 text-rose-400" />
          </div>
          <div className="mt-2 text-2xl font-bold text-rose-300">{unreachableCount}</div>
          <div className="mt-1 text-[11px] text-rose-500/80">Ошибки SSH сессии</div>
        </div>
      </div>

      {/* Action Toolbar */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 flex flex-col md:flex-row items-center justify-between gap-4">
        {/* Search */}
        <div className="relative w-full md:w-72">
          <Search className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
          <input
            type="text"
            placeholder="Поиск по имени, IP или ОС..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-4 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 transition"
          />
        </div>

        {/* Action buttons */}
        <div className="flex flex-wrap items-center gap-2.5 w-full md:w-auto justify-end">
          <button
            onClick={onRefresh}
            className="p-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-xl transition border border-slate-700/60"
            title="Обновить таблицу устройств"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>

          <button
            onClick={onSyncInventory}
            className="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-xl text-xs font-medium flex items-center space-x-1.5 transition border border-slate-700/60"
            title="Импортировать inventory.yaml из Git"
          >
            <RotateCw className="w-3.5 h-3.5" />
            <span>Git Sync</span>
          </button>

          <button
            onClick={onLintIntent}
            className="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-xl text-xs font-medium flex items-center space-x-1.5 transition border border-slate-700/60"
            title="Pre-flight валидация моделей intent через Pydantic"
          >
            <ShieldCheck className="w-3.5 h-3.5 text-cyan-400" />
            <span>Pre-flight Lint</span>
          </button>

          <button
            onClick={() => onScanDrift(selectedIds.length > 0 ? selectedIds : undefined)}
            className="px-3.5 py-2 bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 border border-amber-500/30 rounded-xl text-xs font-medium flex items-center space-x-1.5 transition"
            title="Запустить внеочередной опрос дрейфа"
          >
            <RotateCw className="w-3.5 h-3.5" />
            <span>Скан дрейфа {selectedIds.length > 0 && `(${selectedIds.length})`}</span>
          </button>

          <button
            onClick={() => onRunDryRun(selectedIds.length > 0 ? selectedIds : devices.map((d) => d.id))}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-medium flex items-center space-x-2 transition shadow-lg shadow-indigo-600/20"
            title="Запустить Dry-Run и иерархический расчет патчей"
          >
            <Play className="w-3.5 h-3.5 fill-current" />
            <span>
              Холостой прогон (Dry Run) {selectedIds.length > 0 ? `(${selectedIds.length})` : '(Все)'}
            </span>
          </button>
        </div>
      </div>

      {/* Devices Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs text-slate-300">
            <thead className="bg-slate-950/60 text-slate-400 uppercase text-[11px] tracking-wider border-b border-slate-800 font-semibold">
              <tr>
                <th className="p-4 w-10">
                  <input
                    type="checkbox"
                    checked={devices.length > 0 && selectedIds.length === devices.length}
                    onChange={toggleSelectAll}
                    className="rounded border-slate-700 bg-slate-800 text-indigo-600 focus:ring-0 cursor-pointer"
                  />
                </th>
                <th className="p-4">Устройство (Hostname)</th>
                <th className="p-4">Management IP</th>
                <th className="p-4">Платформа ОС</th>
                <th className="p-4">Роль</th>
                <th className="p-4">Статус комплаенса</th>
                <th className="p-4 text-right">Действия</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/80">
              {filteredDevices.map((device) => {
                const isSelected = selectedIds.includes(device.id);
                return (
                  <tr
                    key={device.id}
                    className={`hover:bg-slate-800/40 transition ${
                      isSelected ? 'bg-indigo-950/20' : ''
                    }`}
                  >
                    <td className="p-4">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => toggleSelectOne(device.id)}
                        className="rounded border-slate-700 bg-slate-800 text-indigo-600 focus:ring-0 cursor-pointer"
                      />
                    </td>
                    <td className="p-4">
                      <div className="flex items-center space-x-2.5">
                        <div className="p-2 rounded-lg bg-slate-800 text-slate-300 font-mono">
                          <Server className="w-4 h-4" />
                        </div>
                        <div>
                          <div
                            onClick={() => setSelectedDeviceModalId(device.id)}
                            className="font-bold text-white hover:text-indigo-400 cursor-pointer font-mono flex items-center space-x-1"
                          >
                            <span>{device.hostname}</span>
                            <ExternalLink className="w-3 h-3 text-slate-500" />
                          </div>
                          <div className="text-[11px] text-slate-500 font-mono">
                            Auth profile: {device.auth_profile}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="p-4 font-mono text-slate-300">
                      {device.management_ip}:{device.management_port}
                    </td>
                    <td className="p-4">
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-[11px] font-mono font-medium ${
                          device.platform === 'cisco_iosxe'
                            ? 'bg-blue-500/10 text-blue-300 border border-blue-500/20'
                            : device.platform === 'arista_eos'
                            ? 'bg-purple-500/10 text-purple-300 border border-purple-500/20'
                            : 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20'
                        }`}
                      >
                        {device.platform}
                      </span>
                    </td>
                    <td className="p-4">
                      <span className="capitalize text-slate-300 font-medium">{device.role}</span>
                    </td>
                    <td className="p-4">{getStatusBadge(device.status)}</td>
                    <td className="p-4 text-right space-x-2">
                      <button
                        onClick={() => setSelectedDeviceModalId(device.id)}
                        className="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] font-medium transition"
                      >
                        Параметры
                      </button>
                      <button
                        onClick={() => onRunDryRun([device.id])}
                        className="px-2.5 py-1 rounded-lg bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 text-[11px] font-medium border border-indigo-500/30 transition"
                      >
                        Dry-Run
                      </button>
                    </td>
                  </tr>
                );
              })}
              {filteredDevices.length === 0 && (
                <tr>
                  <td colSpan={7} className="p-8 text-center text-slate-500 text-sm">
                    {loading ? 'Загрузка устройств...' : 'Устройства не найдены.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Device Detail Modal */}
      {selectedDeviceModalId !== null && (
        <DeviceDetailModal
          deviceId={selectedDeviceModalId}
          onClose={() => setSelectedDeviceModalId(null)}
        />
      )}
    </div>
  );
};
