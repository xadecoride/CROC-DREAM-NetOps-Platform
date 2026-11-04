import React, { useEffect, useState } from 'react';
import {
  X,
  Network,
  FileCode,
  Play,
  ExternalLink,
  Maximize2,
} from 'lucide-react';
import { api } from '../../api';
import type { CollectorDevicePlan, CollectorFallbackEvent, DeviceDetail, UserRole } from '../../api';
import { StatusBadge } from '../layout/ui';
import { PLATFORM_NAMES } from '../../constants';
import { DeviceDrawerMetricsTab } from './device_drawer/DeviceDrawerMetricsTab';

interface DeviceDrawerProps {
  deviceId: number | null;
  onClose: () => void;
  userRole: UserRole;
  onOpenDiff?: (jobId?: string) => void;
  onRunDryRun?: (ids: number[]) => void;
  onOpenFullPage?: (id: number) => void;
}

export const DeviceDrawer: React.FC<DeviceDrawerProps> = ({
  deviceId,
  onClose,
  userRole,
  onOpenDiff,
  onRunDryRun,
  onOpenFullPage,
}) => {
  const [device, setDevice] = useState<DeviceDetail | null>(null);
  const [collectorPlan, setCollectorPlan] = useState<CollectorDevicePlan | null>(null);
  const [fallbackEvents, setFallbackEvents] = useState<CollectorFallbackEvent[]>([]);
  const [protoSaving, setProtoSaving] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(false);
  const [prevDeviceId, setPrevDeviceId] = useState<number | null>(deviceId);
  const [activeTab, setActiveTab] = useState<'interfaces' | 'bgp' | 'config' | 'metrics'>('interfaces');
  const [dryRunLoading, setDryRunLoading] = useState<boolean>(false);
  const [dryRunDone, setDryRunDone] = useState<boolean>(false);

  if (deviceId !== prevDeviceId) {
    setPrevDeviceId(deviceId);
    setDevice(null);
    setCollectorPlan(null);
    setLoading(Boolean(deviceId));
  }

  useEffect(() => {
    if (!deviceId) return;
    let active = true;
    Promise.all([api.getDevice(deviceId), api.getCollectorPlan().catch(() => null)])
      .then(([d, plan]) => {
        if (!active) return;
        setDevice(d);
        if (plan?.devices?.[d.hostname]) {
          setCollectorPlan(plan.devices[d.hostname]);
        }
      })
      .catch(() => {
        if (active) setDevice(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [deviceId]);

  const hostname = device?.hostname;
  useEffect(() => {
    if (!hostname) return;
    const tick = () => {
      if (document.visibilityState !== 'visible') return;
      api
        .getCollectorPlan()
        .then((plan) => {
          if (plan.devices?.[hostname]) setCollectorPlan(plan.devices[hostname]);
          setFallbackEvents((plan.events ?? []).filter((e) => e.hostname === hostname));
        })
        .catch(() => undefined);
    };
    tick();
    const timer = window.setInterval(tick, 15000);
    return () => window.clearInterval(timer);
  }, [hostname]);

  // Priority escape listener
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && deviceId) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [deviceId, onClose]);

  if (!deviceId) return null;

  const isViewer = userRole === 'viewer';

  const handleDryRun = async () => {
    if (onRunDryRun && device) {
      onRunDryRun([device.id]);
      return;
    }
    setDryRunLoading(true);
    try {
      await api.createDryRun([deviceId]);
      setDryRunDone(true);
      setTimeout(() => setDryRunDone(false), 2000);
    } catch {
      // noop
    } finally {
      setDryRunLoading(false);
    }
  };

  const handleProtocolChange = async (val: 'auto' | 'gnmi' | 'snmp' | 'prometheus_exporter' | 'ssh_cli') => {
    if (!device) return;
    setProtoSaving(true);
    try {
      const updated = await api.setDeviceCollectorProtocol(device.hostname, val);
      if (updated?.devices?.[device.hostname]) {
        setCollectorPlan(updated.devices[device.hostname]);
      }
    } catch {
      // noop
    } finally {
      setProtoSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 overflow-hidden pointer-events-none">
      {/* Dimmed backdrop */}
      <div
        onClick={onClose}
        className="absolute inset-0 bg-black/50 backdrop-blur-sm pointer-events-auto transition-opacity duration-300 animate-fade-in"
      />

      {/* Sliding Side Drawer / Mobile Bottom Sheet */}
      <div className="absolute inset-x-0 bottom-0 sm:inset-y-0 sm:right-0 sm:left-auto max-w-full flex sm:pl-10 pointer-events-auto">
        <aside className="w-full sm:w-screen sm:max-w-xl max-h-[90vh] sm:max-h-full rounded-t-3xl sm:rounded-none bg-[#0b0d13] border-t sm:border-t-0 sm:border-l border-white/[0.08] shadow-2xl flex flex-col text-zinc-200 animate-slide-left overflow-hidden">
          {/* Mobile Drag Indicator */}
          <div className="w-12 h-1 bg-zinc-600/50 rounded-full mx-auto my-2 sm:hidden shrink-0" />
          {/* Header */}
          <div className="px-5 sm:px-6 py-4 sm:py-5 border-b border-white/[0.08] flex items-center justify-between bg-[#0e111a]/80">
            <div className="flex items-center gap-3 min-w-0">
              <div className="w-10 h-10 rounded-xl bg-white/[0.05] border border-white/[0.1] flex items-center justify-center shrink-0">
                <Network className="w-5 h-5 text-zinc-300" />
              </div>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h2 className="text-base font-bold text-white tracking-tight truncate">
                    {device?.hostname || `Узел #${deviceId}`}
                  </h2>
                  {device && <StatusBadge status={device.status} />}
                </div>
                <div className="text-xs text-zinc-400 font-mono mt-0.5 flex items-center gap-2">
                  <span>{device?.management_ip}:{device?.management_port}</span>
                  <span>•</span>
                  <span>{device ? PLATFORM_NAMES[device.platform] || device.platform : ''}</span>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2">
              {onOpenFullPage && device && (
                <button
                  onClick={() => {
                    onClose();
                    onOpenFullPage(device.id);
                  }}
                  className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-white/[0.08] transition-colors cursor-pointer"
                  title="Паспорт устройства на весь экран"
                >
                  <Maximize2 className="w-4 h-4 text-cyan-400" />
                </button>
              )}
              <button
                onClick={onClose}
                className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-white/[0.08] transition-colors cursor-pointer"
                title="Закрыть (Esc)"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
          </div>

          {/* Quick Actions Strip */}
          <div className="px-6 py-2.5 bg-white/[0.015] border-b border-white/[0.06] flex items-center justify-between text-xs">
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-mono text-zinc-400">
                Роль: <strong className="text-zinc-200 uppercase">{device?.role}</strong>
              </span>
              {device?.rack && (
                <span className="text-[10px] px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-300 font-mono border border-cyan-500/20">
                  {device.rack}{device.unit ? ` • U${device.unit}` : ''}
                </span>
              )}
              <span className="text-[10px] px-2 py-0.5 rounded bg-white/[0.04] text-zinc-400 font-mono border border-white/[0.06]">
                {device?.management_mode || 'MONITORING_ONLY'}
              </span>
            </div>

            <div className="flex items-center gap-2">
              {isViewer ? (
                <span className="text-[10px] font-mono text-zinc-500 bg-white/[0.03] px-2 py-0.5 rounded border border-white/[0.05]">
                  Только чтение (Жюри)
                </span>
              ) : (
                <>
                  <button
                    onClick={handleDryRun}
                    disabled={dryRunLoading}
                    className="px-2.5 py-1 rounded-lg bg-white/[0.06] hover:bg-white/[0.12] border border-white/[0.1] text-zinc-200 text-xs font-mono flex items-center gap-1.5 transition-colors cursor-pointer disabled:opacity-50"
                  >
                    <Play className="w-3 h-3 text-cyan-400" />
                    <span>{dryRunDone ? 'Запущено!' : 'Dry-Run'}</span>
                  </button>
                  {onOpenDiff && (
                    <button
                      onClick={() => {
                        onClose();
                        onOpenDiff();
                      }}
                      className="px-2.5 py-1 rounded-lg bg-white/[0.06] hover:bg-white/[0.12] border border-white/[0.1] text-zinc-200 text-xs font-mono flex items-center gap-1.5 transition-colors cursor-pointer"
                    >
                      <FileCode className="w-3 h-3 text-indigo-400" />
                      <span>Diff</span>
                    </button>
                  )}
                </>
              )}
            </div>
          </div>

          {/* Navigation Tabs */}
          <div className="flex items-center px-6 border-b border-white/[0.06] bg-[#0c0e14] text-xs font-medium">
            <button
              onClick={() => setActiveTab('interfaces')}
              className={`py-3 px-3 border-b-2 font-mono transition-colors cursor-pointer ${
                activeTab === 'interfaces'
                  ? 'border-cyan-400 text-white font-semibold'
                  : 'border-transparent text-zinc-400 hover:text-zinc-200'
              }`}
            >
              Интерфейсы ({device?.intent?.interfaces?.length ?? 0})
            </button>
            <button
              onClick={() => setActiveTab('bgp')}
              className={`py-3 px-3 border-b-2 font-mono transition-colors cursor-pointer ${
                activeTab === 'bgp'
                  ? 'border-cyan-400 text-white font-semibold'
                  : 'border-transparent text-zinc-400 hover:text-zinc-200'
              }`}
            >
              BGP ({device?.intent?.bgp ? `AS${device.intent.bgp.asn}` : 'откл'})
            </button>
            <button
              onClick={() => setActiveTab('config')}
              className={`py-3 px-3 border-b-2 font-mono transition-colors cursor-pointer ${
                activeTab === 'config'
                  ? 'border-cyan-400 text-white font-semibold'
                  : 'border-transparent text-zinc-400 hover:text-zinc-200'
              }`}
            >
              Конфигурация
            </button>
            <button
              onClick={() => setActiveTab('metrics')}
              className={`py-3 px-3 border-b-2 font-mono transition-colors cursor-pointer ${
                activeTab === 'metrics'
                  ? 'border-cyan-400 text-white font-semibold'
                  : 'border-transparent text-zinc-400 hover:text-zinc-200'
              }`}
            >
              Телеметрия
            </button>
          </div>

          {/* Drawer Content Body */}
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            {loading ? (
              <div className="py-20 text-center text-xs font-mono text-zinc-500">
                Загрузка паспорта узла...
              </div>
            ) : !device ? (
              <div className="py-20 text-center text-xs font-mono text-zinc-500">
                Информация об узле не найдена.
              </div>
            ) : (
              <>
                {/* TAB 1: INTERFACES */}
                {activeTab === 'interfaces' && (
                  <div className="space-y-3">
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400 font-mono">
                      Физические и L3 интерфейсы
                    </h3>
                    {device.intent?.interfaces && device.intent.interfaces.length > 0 ? (
                      <div className="space-y-2">
                        {device.intent.interfaces.map((iface) => (
                          <div
                            key={iface.name}
                            className="p-3 rounded-xl bg-white/[0.02] border border-white/[0.06] flex items-center justify-between text-xs"
                          >
                            <div>
                              <div className="flex items-center gap-2 font-mono font-semibold text-white">
                                <span className={`w-2 h-2 rounded-full ${iface.enabled ? 'bg-emerald-400' : 'bg-zinc-500'}`} />
                                <span>{iface.name}</span>
                                <span className="text-[10px] text-zinc-500 uppercase px-1.5 py-0.2 rounded bg-white/[0.04]">
                                  {iface.mode}
                                </span>
                                {iface.access_vlan && (
                                  <span className="text-[10px] text-amber-300 px-1.5 py-0.2 rounded bg-amber-500/10 border border-amber-500/20">
                                    VLAN {iface.access_vlan}
                                  </span>
                                )}
                                {iface.ospf_area !== null && iface.ospf_area !== undefined && (
                                  <span className="text-[10px] text-emerald-300 px-1.5 py-0.2 rounded bg-emerald-500/10 border border-emerald-500/20">
                                    OSPF {iface.ospf_area}
                                  </span>
                                )}
                              </div>
                              <div className="text-[11px] text-zinc-400 font-mono mt-0.5">
                                {iface.ipv4_address || 'Unnumbered / L2'} {iface.description ? `— ${iface.description}` : ''}
                              </div>
                            </div>
                            <div className="text-right text-[11px] font-mono text-zinc-400">
                              MTU {iface.mtu}
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="p-4 rounded-xl border border-white/[0.06] bg-white/[0.01] text-xs text-zinc-400">
                        Интерфейсы еще не сняты с оборудования. Запустите Probe или синхронизацию инвентаря.
                      </div>
                    )}
                  </div>
                )}

                {/* TAB 2: BGP */}
                {activeTab === 'bgp' && (
                  <div className="space-y-3">
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400 font-mono">
                      BGP пиринги и автономная система
                    </h3>
                    {device.intent?.bgp ? (
                      <div className="space-y-3">
                        <div className="p-3 rounded-xl bg-white/[0.02] border border-white/[0.06] grid grid-cols-3 gap-2 text-xs font-mono">
                          <div>
                            <span className="text-zinc-500">Локальный ASN:</span>
                            <div className="text-white font-bold text-sm mt-0.5">AS{device.intent.bgp.asn}</div>
                          </div>
                          <div>
                            <span className="text-zinc-500">Router ID:</span>
                            <div className="text-cyan-300 font-bold text-sm mt-0.5">{device.intent.bgp.router_id}</div>
                          </div>
                          <div>
                            <span className="text-zinc-500">EVPN / VXLAN:</span>
                            <div className="text-purple-300 font-bold text-sm mt-0.5">
                              {device.intent.bgp.evpn
                                ? `VNI ${device.intent.bgp.vxlan_vni || 'L2/L3'}`
                                : 'eBGP IPv4'}
                            </div>
                          </div>
                        </div>

                        <div className="space-y-2">
                          <span className="text-[11px] font-mono text-zinc-500">BGP соседи:</span>
                          {device.intent.bgp.neighbors.map((nbr) => (
                            <div
                              key={nbr.peer_ip}
                              className="p-3 rounded-xl bg-white/[0.02] border border-white/[0.06] flex items-center justify-between text-xs font-mono"
                            >
                              <div>
                                <div className="text-white font-semibold flex items-center gap-2">
                                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                                  <span>{nbr.peer_ip}</span>
                                  <span className="text-zinc-400">AS{nbr.remote_asn}</span>
                                </div>
                                <div className="text-[11px] text-zinc-400 mt-0.5">
                                  {nbr.description || 'eBGP Spine/Leaf peer'}
                                </div>
                              </div>
                              <div
                                className={`text-right text-[10px] px-2 py-0.5 rounded border ${
                                  device.status === 'UNREACHABLE' || device.oper_status === 'DOWN'
                                    ? 'text-rose-400 bg-rose-500/10 border-rose-500/20'
                                    : 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
                                }`}
                              >
                                {device.status === 'UNREACHABLE' || device.oper_status === 'DOWN'
                                  ? 'Unreachable'
                                  : 'Established'}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : (
                      <div className="p-4 rounded-xl border border-white/[0.06] bg-white/[0.01] text-xs text-zinc-400">
                        BGP процесс не описан для данного узла.
                      </div>
                    )}
                  </div>
                )}

                {/* TAB 3: CONFIG */}
                {activeTab === 'config' && (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-mono text-zinc-400">Целевой интент (Jinja2 / SoT)</span>
                      {onOpenDiff && (
                        <button
                          onClick={() => {
                            onClose();
                            onOpenDiff();
                          }}
                          className="text-cyan-400 hover:text-cyan-300 flex items-center gap-1 font-mono text-[11px] cursor-pointer"
                        >
                          <span>Сравнить в Monaco Diff</span>
                          <ExternalLink className="w-3 h-3" />
                        </button>
                      )}
                    </div>
                    <div className="p-4 rounded-xl bg-[#07080c] border border-white/[0.08] font-mono text-xs text-zinc-300 max-h-[420px] overflow-y-auto leading-relaxed">
                      <pre className="whitespace-pre-wrap select-all font-mono text-[11px]">
                        {JSON.stringify(device.intent || { message: 'Интент загружается...' }, null, 2)}
                      </pre>
                    </div>
                  </div>
                )}

                {/* TAB 4: METRICS */}
                {activeTab === 'metrics' && (
                  <DeviceDrawerMetricsTab
                    device={device}
                    collectorPlan={collectorPlan}
                    fallbackEvents={fallbackEvents}
                    protoSaving={protoSaving}
                    isViewer={isViewer}
                    onProtocolChange={handleProtocolChange}
                  />
                )}
              </>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
};
