import React from 'react';
import { Cpu, HardDrive, Radio, ShieldAlert } from 'lucide-react';
import { api } from '../../../api';
import type {
  CollectorDevicePlan,
  DeviceDetail,
  DeviceHardwareSpecs,
} from '../../../api';
import { Panel, downloadMarkdown } from '../../layout/ui';
import { PLATFORM_NAMES, ROLE_NAMES } from '../../../constants';
import { GrafanaTelemetryChart } from '../../charts/GrafanaTelemetryChart';

export function exportDevicePassportMarkdown(
  device: DeviceDetail,
  specs: DeviceHardwareSpecs | null
) {
  const md = `# ТЕХНИЧЕСКИЙ ПАСПОРТ УЗЛА: ${device.hostname}
**Платформа**: ${PLATFORM_NAMES[device.platform] ?? device.platform}
**Роль в фабрике**: ${ROLE_NAMES[device.role] ?? device.role}
**Управленческий IP**: ${device.management_ip}:${device.management_port}
**Oper Status**: ${device.oper_status ?? 'UP'}
**Intent Status**: ${device.status}
**Режим управления**: ${device.management_mode}
**Дата выгрузки**: ${new Date().toLocaleString('ru-RU')}

---

## 1. Аппаратная спецификация
- **CPU**: ${specs?.cpu_cores ? `${specs.cpu_cores} vCPU` : '—'}
- **RAM**: ${specs?.ram_gb ? `${specs.ram_gb} GB RAM` : '—'}
- **Disk**: ${specs?.disk_gb ? `${specs.disk_gb} GB` : '—'}
- **Версия ОС**: ${specs?.os_version || 'Телеметрия не получена'}

## 2. Сетевая конфигурация
- **BGP AS**: ${device.intent?.bgp?.asn ?? '—'}
- **Router ID**: ${device.intent?.bgp?.router_id ?? '—'}
- **EVPN / VXLAN VNI**: ${device.intent?.bgp?.evpn ? `Enabled${device.intent.bgp.vxlan_vni ? ` (VNI ${device.intent.bgp.vxlan_vni})` : ''}` : '—'}
- **Сетевые интерфейсы**:
${(device.intent?.interfaces || []).map((i) => `  - \`${i.name}\`: IP ${i.ipv4_address || 'L2'} (${i.mode.toUpperCase()}) ${i.description ? `[${i.description}]` : ''}`).join('\n') || '  - Нет данных'}

---
*Документ сформирован платформой CROC DREAM NetOps Platform*
`;
  downloadMarkdown(
    `Passport_${device.hostname}_${new Date().toISOString().slice(0, 10)}.md`,
    md
  );
}

const Field: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="flex justify-between py-1.5 text-[13px] border-b border-white/[0.04] last:border-0">
    <span className="text-zinc-400">{label}</span>
    <span className="text-zinc-100 font-mono text-right">{children}</span>
  </div>
);

interface DeviceOverviewSectionProps {
  device: DeviceDetail;
  specs: DeviceHardwareSpecs | null;
  isServer: boolean;
  canAdmin: boolean;
  promoting: boolean;
  onPromote: () => void;
  onRefreshDevice?: () => void;
  collectorPlan: CollectorDevicePlan | null;
}

const Stat: React.FC<{ icon: React.ReactNode; label: string; value: string }> = ({ icon, label, value }) => (
  <div className="p-4 rounded-lg bg-[var(--bg-card)] border border-[var(--border-subtle)]" title={label}>
    <div className="text-[var(--text-muted)] mb-1">{icon}</div>
    <div className="text-lg font-semibold text-[var(--text-primary)] font-mono tabular-nums">{value}</div>
  </div>
);

export const DeviceOverviewSection: React.FC<DeviceOverviewSectionProps> = ({
  device,
  specs,
  isServer,
  canAdmin,
  promoting,
  onPromote,
  onRefreshDevice,
  collectorPlan,
}) => {
  const [restoring, setRestoring] = React.useState(false);
  const [restoreMsg, setRestoreMsg] = React.useState<string | null>(null);
  const isDown =
    device.vitality_state === 'DOWN' ||
    device.oper_status === 'DOWN' ||
    device.status === 'UNREACHABLE';

  const handleRestoreBackup = async () => {
    setRestoring(true);
    setRestoreMsg(null);
    try {
      const res = await api.restoreDeviceBackup(device.id);
      setRestoreMsg(res.message);
      if (onRefreshDevice) onRefreshDevice();
    } catch (err: unknown) {
      setRestoreMsg(err instanceof Error ? err.message : 'Ошибка бэкапа');
    } finally {
      setRestoring(false);
    }
  };

  const protocol = (collectorPlan?.protocol || 'ssh_cli').toUpperCase();
  const port = collectorPlan?.port ?? device.management_port;

  return (
    <>
      <div
        data-testid="device-vitality-banner"
        className={`p-4 rounded-lg border flex flex-col gap-2.5 ${
          isDown
            ? 'bg-rose-500/10 border-rose-500/30 text-rose-100'
            : 'bg-emerald-500/[0.06] border-emerald-500/20 text-zinc-100'
        }`}
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <span className="relative flex h-2.5 w-2.5">
              <span
                className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${
                  isDown ? 'bg-rose-400' : 'bg-emerald-400'
                }`}
              />
              <span
                className={`relative inline-flex rounded-full h-2.5 w-2.5 ${
                  isDown ? 'bg-rose-500' : 'bg-emerald-500'
                }`}
              />
            </span>
            <span className="text-xs font-mono font-semibold tracking-tight uppercase">
              {isDown ? 'DOWN' : 'UP'}
            </span>
            {device.using_backup_fallback && (
              <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-amber-500/15 text-amber-300 border border-amber-500/30">
                .CFG
              </span>
            )}
            {device.last_backup_at && (
              <span className="text-[11px] font-mono text-zinc-400">
                {new Date(device.last_backup_at).toLocaleString('ru-RU')}
              </span>
            )}
          </div>

          {canAdmin && device.backup_available !== false && (
            <button
              type="button"
              onClick={handleRestoreBackup}
              disabled={restoring}
              data-testid="restore-device-backup-btn"
              className={`px-3 py-1.5 rounded-md text-xs font-mono font-semibold transition-colors cursor-pointer ${
                isDown
                  ? 'bg-rose-500 hover:bg-rose-400 text-white'
                  : 'bg-[var(--bg-elevated)] hover:bg-white/[0.08] text-[var(--text-primary)] border border-[var(--border-subtle)]'
              }`}
            >
              {restoring ? '…' : 'Restore .cfg'}
            </button>
          )}
        </div>

        {isDown && (
          <div className="space-y-1.5 pt-1 border-t border-rose-500/20 text-xs font-mono">
            <div className="text-rose-300">
              {device.down_reason || `${device.management_ip}:${device.management_port} — нет ответа`}
            </div>
            {device.ai_diagnosis && (
              <div className="text-violet-200 bg-violet-500/10 border border-violet-500/25 rounded-md p-2.5 text-[11px]">
                {device.ai_diagnosis}
              </div>
            )}
          </div>
        )}

        {restoreMsg && (
          <div className="text-xs font-mono text-emerald-300 bg-emerald-500/10 border border-emerald-500/30 rounded px-2.5 py-1.5">
            {restoreMsg}
          </div>
        )}
      </div>

      {device.management_mode === 'MONITORING_ONLY' && (
        <div
          className="p-3 rounded-lg bg-[var(--bg-elevated)] border border-amber-500/30 flex items-center justify-between gap-3"
          title="Мутации и деплой заблокированы"
        >
          <div className="flex items-center gap-2.5 text-xs font-semibold text-[var(--text-primary)] font-mono">
            <ShieldAlert className="w-5 h-5 text-amber-400 shrink-0" />
            MONITORING_ONLY · read-only
          </div>
          {canAdmin && (
            <button
              onClick={onPromote}
              disabled={promoting}
              className="px-3 py-1.5 rounded-md text-xs font-semibold bg-[var(--text-primary)] text-[var(--bg-main)] hover:opacity-90 transition-colors cursor-pointer shrink-0"
            >
              {promoting ? '…' : 'Promote'}
            </button>
          )}
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat icon={<Cpu className="w-4 h-4" />} label="CPU" value={specs?.cpu_cores ? `${specs.cpu_cores} vCPU` : '—'} />
        <Stat icon={<HardDrive className="w-4 h-4" />} label="RAM" value={specs?.ram_gb ? `${specs.ram_gb} GB` : '—'} />
        <Stat icon={<HardDrive className="w-4 h-4" />} label={isServer ? 'Disk' : 'Flash'} value={specs?.disk_gb ? `${specs.disk_gb} GB` : '—'} />
        <Stat icon={<Radio className="w-4 h-4" />} label="Ports" value={String(specs?.interfaces ? specs.interfaces.length : device.intent?.interfaces.length ?? 0)} />
      </div>

      <GrafanaTelemetryChart
        hostname={device.hostname}
        devices={[device]}
        compact
      />

      <div className="grid md:grid-cols-2 gap-4">
        <Panel title="Доступ">
          <Field label="Роль">{ROLE_NAMES[device.role] ?? device.role}</Field>
          <Field label="ОС">
            {PLATFORM_NAMES[device.platform] ?? device.platform}
            {specs?.os_version ? ` (${specs.os_version})` : ''}
          </Field>
          <Field label="IP">
            <span className="text-[var(--text-primary)]">{device.management_ip}:{device.management_port}</span>
          </Field>
          <Field label="Jump">
            {device.proxy_jump ?? (device.platform === 'huawei_vrp' ? 'Containerlab' : '—')}
          </Field>
          <Field label="Профиль">{device.auth_profile}</Field>
          <Field label="Телеметрия">{protocol}:{port}</Field>
          <Field label="Проверка">
            {device.last_checked_at ? new Date(device.last_checked_at).toLocaleString('ru-RU') : '—'}
          </Field>
        </Panel>

        <Panel title="Intent · BGP">
          <Field label="Ports">{device.intent?.interfaces.length ?? 0}</Field>
          <Field label="ASN">{device.intent?.bgp?.asn ? `AS ${device.intent.bgp.asn}` : '—'}</Field>
          <Field label="Router ID">{device.intent?.bgp?.router_id ?? '—'}</Field>
          <Field label="Peers">{device.intent?.bgp?.neighbors.length ?? 0}</Field>
          <Field label="ACL">{device.intent?.acls.length ?? 0}</Field>
          <Field label="Issues">
            {device.intent_issues.length > 0 ? (
              <span className="text-amber-400 font-semibold">{device.intent_issues.length}</span>
            ) : (
              <span className="text-emerald-400">OK</span>
            )}
          </Field>
        </Panel>
      </div>
    </>
  );
};
