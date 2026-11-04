import React, { useState, useMemo } from 'react';
import {
  AlertTriangle,
  ExternalLink,
  Server,
  Layers,
  Cpu,
  ShieldCheck,
  Plus,
  RefreshCw,
  GitBranch,
} from 'lucide-react';
import { api, getErrorMessage, type Device, type JobSummary, type StandRead, type UserRole } from '../../api';
import { translations, type Locale } from '../../i18n';
import { TopologyMap } from '../charts/TopologyMap';
import { GrafanaTelemetryChart } from '../charts/GrafanaTelemetryChart';
import { BentoCard, Sparkline } from '../layout/ui';
import { PLATFORM_NAMES, ROLE_NAMES } from '../../constants';
import { AddDeviceModal } from '../modals/AddDeviceModal';
import { DeviceDrawer } from '../modals/DeviceDrawer';
import { DashboardRightColumn } from './DashboardRightColumn';

interface DashboardProps {
  devices: Device[];
  stands?: StandRead[];
  activeStand?: string;
  jobs: JobSummary[];
  userRole?: UserRole;
  locale?: Locale;
  onOpenTab: (tab: string) => void;
  onOpenDevice: (id: number) => void;
  onRunDryRun?: (deviceIds: number[]) => void;
  onOpenDiff?: (jobId?: string) => void;
  onSyncInventory?: () => void;
}

const DashboardComponent: React.FC<DashboardProps> = ({
  devices,
  stands,
  activeStand,
  jobs,
  userRole,
  locale = 'ru',
  onOpenTab,
  onOpenDevice,
  onRunDryRun,
  onOpenDiff,
  onSyncInventory,
}) => {
  const t = translations[locale];
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [drawerDeviceId, setDrawerDeviceId] = useState<number | null>(null);
  const [syncingGit, setSyncingGit] = useState(false);
  const [gitSyncFeedback, setGitSyncFeedback] = useState<{ text: string; ok: boolean } | null>(null);

  const handleGitSync = async () => {
    if (syncingGit) return;
    setSyncingGit(true);
    setGitSyncFeedback(null);
    try {
      const res = await api.syncGitSot();
      const created = res.sync?.created?.length ?? 0;
      const updated = res.sync?.updated?.length ?? 0;
      setGitSyncFeedback({
        text: created || updated ? `Git +${created}/~${updated}` : 'Git OK',
        ok: true,
      });
      if (onSyncInventory) onSyncInventory();
      window.dispatchEvent(new Event('netops-topology-mutated'));
      window.dispatchEvent(new Event('netops-git-updated'));
    } catch (err: unknown) {
      setGitSyncFeedback({ text: getErrorMessage(err, locale === 'en' ? 'Git Sync Error' : 'Ошибка Git Sync'), ok: false });
      if (onSyncInventory) onSyncInventory();
    } finally {
      setSyncingGit(false);
      window.setTimeout(() => setGitSyncFeedback(null), 4000);
    }
  };

  const { inSyncCount, driftCount, unreachableCount, healthPercent } = useMemo(() => {
    let inSync = 0;
    let drift = 0;
    let unreachable = 0;
    for (const d of devices) {
      if (d.status === 'IN_SYNC') inSync++;
      else if (d.status === 'DRIFT_DETECTED') drift++;
      else if (d.status === 'UNREACHABLE') unreachable++;
    }
    const hp =
      devices.length > 0 && inSync > 0
        ? Math.round((inSync / devices.length) * 100)
        : 0;
    return { inSyncCount: inSync, driftCount: drift, unreachableCount: unreachable, healthPercent: hp };
  }, [devices]);

  const platformsCount = useMemo(() => {
    const acc: Record<string, number> = {};
    for (const d of devices) {
      acc[d.platform] = (acc[d.platform] ?? 0) + 1;
    }
    return Object.entries(acc);
  }, [devices]);

  const rolesCount = useMemo(() => {
    const acc: Record<string, number> = {};
    for (const d of devices) {
      acc[d.role] = (acc[d.role] ?? 0) + 1;
    }
    return Object.entries(acc);
  }, [devices]);

  if (devices.length === 0) {
    return (
      <div className="w-full max-w-[2560px] mx-auto py-10 sm:py-16 space-y-5 animate-fade-in select-none text-center">
        <h1 className="text-lg sm:text-xl font-semibold text-[var(--text-primary)] tracking-tight">
          {t.noActiveDevices}
        </h1>

        {userRole !== 'viewer' && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-lg mx-auto">
            {(userRole === 'admin' || userRole === 'owner') && (
              <button
                onClick={() => setAddModalOpen(true)}
                className="p-4 rounded-lg border border-[var(--border-subtle)] hover:border-[var(--border-hover)] bg-[var(--bg-surface)] transition-colors cursor-pointer flex items-center justify-center gap-2.5 text-xs font-semibold text-[var(--text-primary)]"
              >
                <Plus className="w-4 h-4 text-[var(--text-secondary)] shrink-0" />
                <span>{t.addDeviceSsh}</span>
              </button>
            )}

            <button
              onClick={handleGitSync}
              disabled={syncingGit}
              className="p-4 rounded-lg border border-[var(--border-subtle)] hover:border-[var(--border-hover)] bg-[var(--bg-surface)] transition-colors cursor-pointer flex items-center justify-center gap-2.5 text-xs font-semibold text-[var(--text-primary)] disabled:opacity-50"
            >
              <RefreshCw className={`w-4 h-4 text-[var(--text-secondary)] shrink-0 ${syncingGit ? 'animate-spin' : ''}`} />
              <span>{syncingGit ? 'Git Sync…' : t.syncGitSot}</span>
            </button>
          </div>
        )}

        <AddDeviceModal
          isOpen={addModalOpen}
          onClose={() => setAddModalOpen(false)}
          onSuccess={() => {
            if (onSyncInventory) onSyncInventory();
          }}
          userRole={userRole || 'viewer'}
        />
      </div>
    );
  }

  return (
    <div className="w-full max-w-[2560px] mx-auto space-y-3 2xl:space-y-4 select-none">
      {/* 1. Health KPI Strip */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 sm:gap-3 2xl:gap-4">
        <button
          onClick={() => onOpenTab('devices')}
          className="text-left rounded-lg border border-[var(--border-subtle)] hover:border-[var(--border-hover)] bg-[var(--bg-surface)] px-3.5 py-2.5 transition-colors cursor-pointer flex items-center justify-between"
        >
          <div>
            <div className="text-[11px] text-[var(--text-secondary)] font-medium flex items-center gap-1.5">
              <Server className="w-3.5 h-3.5 text-[var(--text-muted)]" />
              <span>{t.cardDevices}</span>
            </div>
            <div className="text-2xl font-semibold font-mono tabular-nums mt-0.5 text-[var(--text-primary)] tracking-tight">
              {devices.length}
            </div>
          </div>
          <div className="text-right shrink-0">
            <span className="inline-block whitespace-nowrap text-[10px] font-mono tabular-nums px-1.5 py-0.5 rounded bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-[var(--text-secondary)]">
              {healthPercent}% {t.cardDevicesNormal}
            </span>
          </div>
        </button>

        <button
          onClick={() => onOpenTab('drift')}
          className="text-left rounded-lg border border-[var(--border-subtle)] hover:border-[var(--border-hover)] bg-[var(--bg-surface)] px-3.5 py-2.5 transition-colors cursor-pointer flex items-center justify-between"
        >
          <div>
            <div className="text-[11px] text-[var(--text-secondary)] font-medium flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
              <span>{t.cardInSync}</span>
            </div>
            <div className="text-2xl font-semibold font-mono tabular-nums mt-0.5 text-[var(--text-primary)] tracking-tight">
              {inSyncCount}
            </div>
          </div>
          <ShieldCheck className="w-4 h-4 text-[var(--text-muted)]" />
        </button>

        <button
          onClick={() => onOpenTab('drift')}
          className={`text-left rounded-lg border px-3.5 py-2.5 transition-colors cursor-pointer flex items-center justify-between ${
            driftCount > 0
              ? 'border-amber-500/35 bg-amber-500/[0.04] hover:border-amber-500/50'
              : 'border-[var(--border-subtle)] bg-[var(--bg-surface)] hover:border-[var(--border-hover)]'
          }`}
        >
          <div>
            <div className="text-[11px] text-[var(--text-secondary)] font-medium flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full ${driftCount > 0 ? 'bg-amber-400' : 'bg-zinc-500'}`} />
              <span>{t.cardDrift}</span>
            </div>
            <div className={`text-2xl font-semibold font-mono tabular-nums mt-0.5 tracking-tight ${driftCount > 0 ? 'text-amber-600 dark:text-amber-400' : 'text-[var(--text-primary)]'}`}>
              {driftCount}
            </div>
          </div>
          <AlertTriangle className={`w-4 h-4 ${driftCount > 0 ? 'text-amber-500 dark:text-amber-400' : 'text-[var(--text-muted)]'}`} />
        </button>

        <button
          onClick={() => onOpenTab('devices')}
          className={`text-left rounded-lg border px-3.5 py-2.5 transition-colors cursor-pointer flex items-center justify-between ${
            unreachableCount > 0
              ? 'border-rose-500/35 bg-rose-500/[0.04] hover:border-rose-500/50'
              : 'border-[var(--border-subtle)] bg-[var(--bg-surface)] hover:border-[var(--border-hover)]'
          }`}
        >
          <div>
            <div className="text-[11px] text-[var(--text-secondary)] font-medium flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full ${unreachableCount > 0 ? 'bg-rose-400' : 'bg-zinc-500'}`} />
              <span>{t.cardUnreachable}</span>
            </div>
            <div className={`text-2xl font-semibold font-mono tabular-nums mt-0.5 tracking-tight ${unreachableCount > 0 ? 'text-rose-600 dark:text-rose-400' : 'text-[var(--text-primary)]'}`}>
              {unreachableCount}
            </div>
          </div>
          <AlertTriangle className={`w-4 h-4 ${unreachableCount > 0 ? 'text-rose-500 dark:text-rose-400' : 'text-[var(--text-muted)]'}`} />
        </button>
      </div>

      {/* 2. Device Fleet & Live Load Matrix */}
      <BentoCard
        title={t.fabricNodesTitle}
        icon={<Server className="w-3.5 h-3.5 text-[var(--text-secondary)]" />}
        indicator="bg-zinc-500"
        right={
          <div className="flex items-center gap-2">
            {userRole !== 'viewer' && (
              <button
                type="button"
                onClick={handleGitSync}
                disabled={syncingGit}
                className={`px-2 py-0.5 rounded border text-[11px] font-mono flex items-center gap-1 transition-colors cursor-pointer disabled:opacity-50 ${
                  gitSyncFeedback
                    ? gitSyncFeedback.ok
                      ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400'
                      : 'bg-rose-500/10 border-rose-500/30 text-rose-600 dark:text-rose-400'
                    : 'bg-[var(--bg-elevated)] border-[var(--border-subtle)] hover:border-[var(--border-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
                title={t.syncGitSotTooltip}
              >
                {syncingGit ? (
                  <RefreshCw className="w-3 h-3 animate-spin text-[var(--text-secondary)]" />
                ) : (
                  <GitBranch className="w-3 h-3 text-[var(--text-secondary)]" />
                )}
                <span>{syncingGit ? 'Sync…' : gitSyncFeedback ? gitSyncFeedback.text : 'Git Sync'}</span>
              </button>
            )}
            <span className="font-mono text-[11px] text-[var(--text-muted)] tabular-nums">
              {devices.length} {t.nodesCount}
            </span>
            <button
              onClick={() => onOpenTab('devices')}
              className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] flex items-center gap-1 cursor-pointer transition-colors text-xs"
            >
              <span>{t.allDevices}</span>
              <ExternalLink className="w-3 h-3" />
            </button>
          </div>
        }
      >
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-6 gap-2.5 2xl:gap-3.5">
          {devices.map((device) => {
            const currentLoad =
              device.sparkline && device.sparkline.length > 0
                ? device.sparkline[device.sparkline.length - 1]
                : 0;
            const isHigh = currentLoad > 80;
            const isMedium = currentLoad > 55;

            return (
              <div
                key={device.id}
                onClick={() => setDrawerDeviceId(device.id)}
                className="group relative rounded-lg border border-[var(--border-subtle)] hover:border-[var(--border-hover)] bg-[var(--bg-elevated)] hover:bg-[var(--bg-panel)] p-3 transition-colors cursor-pointer flex flex-col justify-between"
              >
                <div>
                  <div className="flex items-center justify-between gap-1.5">
                    <div className="flex items-center gap-2 min-w-0">
                      <span
                        className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                          device.status === 'UNREACHABLE'
                            ? 'bg-rose-400'
                            : device.status === 'DRIFT_DETECTED'
                            ? 'bg-amber-400'
                            : device.status === 'UNKNOWN'
                            ? 'bg-zinc-500'
                            : 'bg-emerald-400'
                        }`}
                      />
                      <span className="font-semibold text-xs text-[var(--text-primary)] truncate tracking-tight">
                        {device.hostname}
                      </span>
                    </div>
                    <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-[var(--bg-surface)] text-[var(--text-secondary)] border border-[var(--border-subtle)] uppercase shrink-0">
                      {device.platform.replace('_', '-')}
                    </span>
                  </div>

                  <div className="flex items-center justify-between text-[11px] font-mono tabular-nums text-[var(--text-muted)] mt-1">
                    <span>{device.management_ip}:{device.management_port}</span>
                    <span className="uppercase text-[10px] px-1 rounded bg-[var(--bg-surface)] text-[var(--text-secondary)] border border-[var(--border-subtle)]">
                      {device.role}
                    </span>
                  </div>
                </div>

                <div className="mt-3 pt-2 border-t border-[var(--border-subtle)]">
                  <div className="flex items-center justify-between text-[10px] font-mono tabular-nums mb-1.5">
                    <span className="text-[var(--text-muted)]">
                      {t.loadCpuLink}
                    </span>
                    <span
                      className={`font-medium ${
                        !device.sparkline || device.sparkline.length === 0
                          ? 'text-[var(--text-muted)]'
                          : isHigh
                          ? 'text-rose-600 dark:text-rose-400'
                          : isMedium
                          ? 'text-amber-600 dark:text-amber-400'
                          : 'text-[var(--text-primary)]'
                      }`}
                    >
                      {device.sparkline && device.sparkline.length > 0
                        ? `${Math.round(currentLoad)}%`
                        : '—'}
                    </span>
                  </div>

                  {device.sparkline && device.sparkline.length > 0 ? (
                    <div className="h-7 w-full flex items-center">
                      <Sparkline
                        points={device.sparkline}
                        width={180}
                        height={26}
                        fullWidth
                        showValue={false}
                      />
                    </div>
                  ) : (
                    <div className="h-7 w-full flex items-center justify-center rounded border border-dashed border-[var(--border-subtle)] text-[10px] font-mono text-[var(--text-muted)]">
                      {t.pollingStatus}
                    </div>
                  )}
                </div>

                <div className="mt-2 pt-2 border-t border-[var(--border-subtle)] flex items-center justify-between text-[10px] font-mono">
                  <span className="text-[var(--text-muted)]">
                    Oper:{' '}
                    <strong
                      className={
                        device.oper_status === 'UP'
                          ? 'text-emerald-600 dark:text-emerald-400'
                          : device.oper_status === 'DOWN'
                          ? 'text-rose-600 dark:text-rose-400'
                          : device.oper_status === 'DEGRADED'
                          ? 'text-amber-600 dark:text-amber-400'
                          : 'text-[var(--text-muted)]'
                      }
                    >
                      {device.oper_status || 'UNKNOWN'}
                    </strong>
                  </span>
                  <span className="text-[var(--text-secondary)] group-hover:text-[var(--text-primary)] flex items-center gap-0.5">
                    {t.inspectorLink}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </BentoCard>

      <GrafanaTelemetryChart
        activeStand={activeStand}
        stands={stands}
        devices={devices}
        locale={locale}
        onOpenDevice={onOpenDevice}
      />

      {/* 3. Bento Grid Central Arena */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-3 2xl:gap-4">
        <div className="xl:col-span-7 space-y-3">
          <BentoCard
            title={t.topologyTitle}
            indicator="bg-zinc-500"
            right={
              <button
                onClick={() => onOpenTab('topology')}
                className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] flex items-center gap-1 cursor-pointer transition-colors text-xs font-medium"
              >
                <span>{t.expandTopologyMap}</span>
                <ExternalLink className="w-3 h-3" />
              </button>
            }
          >
            <TopologyMap
              devices={devices}
              stands={stands}
              activeStand={activeStand}
              locale={locale}
              compact
              userRole={userRole}
              onExpandTopology={() => onOpenTab('topology')}
              onSelectDevice={onOpenDevice}
              onRunDryRun={userRole !== 'viewer' ? onRunDryRun : undefined}
            />
          </BentoCard>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <BentoCard title={t.platformsTitle} icon={<Cpu className="w-3.5 h-3.5 text-[var(--text-secondary)]" />} indicator="bg-zinc-500">
              <div className="space-y-1.5">
                {platformsCount.map(([p, count]) => (
                  <div key={p} className="flex items-center justify-between text-[11px]">
                    <span className="text-[var(--text-secondary)] truncate">{PLATFORM_NAMES[p] ?? p}</span>
                    <span className="font-mono tabular-nums text-[var(--text-primary)] bg-[var(--bg-elevated)] px-1.5 py-0.5 rounded border border-[var(--border-subtle)]">
                      {count}
                    </span>
                  </div>
                ))}
              </div>
            </BentoCard>

            <BentoCard title={t.rolesTitle} icon={<Layers className="w-3.5 h-3.5 text-[var(--text-secondary)]" />} indicator="bg-zinc-500">
              <div className="space-y-1.5">
                {rolesCount.map(([r, count]) => (
                  <div key={r} className="flex items-center justify-between text-[11px]">
                    <span className="text-[var(--text-secondary)] truncate">{ROLE_NAMES[r] ?? r}</span>
                    <span className="font-mono tabular-nums text-[var(--text-primary)] bg-[var(--bg-elevated)] px-1.5 py-0.5 rounded border border-[var(--border-subtle)]">
                      {count}
                    </span>
                  </div>
                ))}
              </div>
            </BentoCard>
          </div>
        </div>

        <DashboardRightColumn
          jobs={jobs}
          locale={locale}
          onOpenTab={onOpenTab}
          onOpenDiff={onOpenDiff}
        />
      </div>

      <DeviceDrawer
        deviceId={drawerDeviceId}
        onClose={() => setDrawerDeviceId(null)}
        userRole={userRole || 'viewer'}
        onOpenDiff={onOpenDiff}
        onRunDryRun={onRunDryRun}
        onOpenFullPage={onOpenDevice}
      />
    </div>
  );
};

export const Dashboard = React.memo(DashboardComponent);
