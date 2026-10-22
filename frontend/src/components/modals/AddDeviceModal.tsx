import React, { useEffect, useState } from 'react';
import {
  X,
  Server,
  AlertTriangle,
  ArrowRight,
  ArrowLeft,
  Loader2,
  ShieldAlert,
  CheckCircle2,
} from 'lucide-react';
import { api, getErrorMessage } from '../../api';
import type { Device, DeviceProbeResult, Platform, DeviceRole, StandRead, UserRole } from '../../api';
import { AddDeviceStep2 } from './add_device/AddDeviceStep2';

interface AddDeviceModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (device: Device) => void;
  userRole: UserRole;
  activeStand?: string;
}

export const AddDeviceModal: React.FC<AddDeviceModalProps> = ({
  isOpen,
  onClose,
  onSuccess,
  userRole,
  activeStand,
}) => {
  const [step, setStep] = useState<1 | 2>(1);
  const [ip, setIp] = useState('');
  const [port, setPort] = useState(22);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [proxyJump, setProxyJump] = useState('');
  const [authProfile, setAuthProfile] = useState('lab_creds');
  const [customCreds, setCustomCreds] = useState(true);
  const [stands, setStands] = useState<StandRead[]>([]);
  const [targetStand, setTargetStand] = useState<string>('');

  const [platform, setPlatform] = useState<Platform>('linux_server');
  const [role, setRole] = useState<DeviceRole>('leaf');
  const [hostname, setHostname] = useState('');
  const [preferredProtocol, setPreferredProtocol] = useState<string>('auto');

  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<DeviceProbeResult | null>(null);
  const [probeError, setProbeError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    api
      .getStands()
      .then((list) => {
        setStands(list);
        if (activeStand && activeStand !== 'all') {
          setTargetStand(activeStand);
        } else {
          const def = list.find((s) => s.is_default || s.is_active) || list[0];
          if (def) setTargetStand(def.slug);
        }
      })
      .catch(() => {});
  }, [isOpen, activeStand]);

  if (!isOpen) return null;

  const parseIpAndPort = (rawIp: string, fallbackPort: number) => {
    let cleanIp = rawIp.trim().replace(/^https?:\/\//, '');
    let cleanPort = fallbackPort;
    if (cleanIp.includes(':')) {
      const [hostPart, portPart] = cleanIp.split(':');
      cleanIp = hostPart;
      const p = parseInt(portPart, 10);
      if (!isNaN(p) && p > 0 && p <= 65535) cleanPort = p;
    }
    return { cleanIp, cleanPort };
  };

  const handleIpChange = (val: string) => {
    if (val.trim().includes(':')) {
      const { cleanIp, cleanPort } = parseIpAndPort(val, port);
      setIp(cleanIp);
      setPort(cleanPort);
    } else {
      setIp(val);
    }
  };

  const handleProbe = async () => {
    const { cleanIp, cleanPort } = parseIpAndPort(ip, port);
    if (!cleanIp) {
      setProbeError('Укажите IP-адрес или FQDN устройства');
      return;
    }
    setProbing(true);
    setProbeError('');
    try {
      const effectiveJump = proxyJump.trim() || (targetStand ? `stand:${targetStand}` : undefined);
      const res = await api.probeDevice({
        management_ip: cleanIp,
        management_port: cleanPort,
        username: customCreds ? username : undefined,
        password: customCreds ? password : undefined,
        proxy_jump: effectiveJump,
      });
      setProbeResult(res);
      if (res.hostname) setHostname(res.hostname);
      if (res.detected_platform) setPlatform(res.detected_platform);
      if (res.detected_role) setRole(res.detected_role);
      setStep(2);
    } catch (err: unknown) {
      setProbeError(getErrorMessage(err, 'Ошибка SSH-пробы оборудования'));
    } finally {
      setProbing(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setProbeError('');
    try {
      const { cleanIp, cleanPort } = parseIpAndPort(ip, port);
      const rawHostname = hostname.trim() || probeResult?.hostname || `node-${cleanPort}`;
      const finalHostname = rawHostname.replace(/[^A-Za-z0-9.-]/g, '-').toLowerCase();
      const profileKey = customCreds
        ? `${finalHostname.split('.')[0].replace(/[^a-z0-9_]/g, '_')}_auth`
        : authProfile;
      const chosenStandObj = stands.find((s) => s.slug === targetStand);
      const newDev = await api.createDevice({
        hostname: finalHostname,
        management_ip: cleanIp,
        management_port: cleanPort,
        platform,
        role,
        fabric_id: targetStand || undefined,
        site: chosenStandObj?.site || targetStand || undefined,
        auth_profile: profileKey,
        username: customCreds ? username : undefined,
        password: customCreds ? password : undefined,
        management_mode: 'MONITORING_ONLY',
        proxy_jump: proxyJump.trim() || (targetStand ? `stand:${targetStand}` : null),
        hardware_specs: probeResult
          ? JSON.stringify({
              cpu_cores: probeResult.cpu_cores,
              ram_gb: probeResult.ram_gb,
              disk_gb: probeResult.disk_gb,
              os_version: probeResult.os_version,
              interfaces: probeResult.interfaces,
              lldp_neighbors: probeResult.lldp_neighbors,
            })
          : null,
      });
      if (preferredProtocol && preferredProtocol !== 'auto') {
        try {
          await api.setDeviceCollectorProtocol(
            newDev.hostname,
            preferredProtocol as 'auto' | 'gnmi' | 'snmp' | 'prometheus_exporter' | 'ssh_cli'
          );
        } catch {
          // non-blocking protocol override
        }
      }
      onSuccess(newDev);
      handleClose();
    } catch (err: unknown) {
      setProbeError(getErrorMessage(err, 'Ошибка создания устройства в инвентаре'));
    } finally {
      setSaving(false);
    }
  };

  const handleClose = () => {
    setStep(1);
    setHostname('');
    setPreferredProtocol('auto');
    setProbeResult(null);
    setProbeError('');
    onClose();
  };

  const isPermitted = userRole === 'admin' || userRole === 'owner';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4 bg-black/90 backdrop-blur-xl animate-fade-in">
      <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-lg w-full max-w-2xl overflow-hidden shadow-2xl flex flex-col max-h-[94dvh]">
        {/* Header */}
        <div className="flex items-center justify-between px-4 sm:px-5 py-3.5 border-b border-[var(--border-subtle)] bg-[var(--bg-elevated)] shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-md bg-[var(--bg-subtle)] border border-[var(--border-subtle)] flex items-center justify-center text-[var(--text-primary)]">
              <Server className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-[var(--text-primary)] tracking-tight">
                Добавление узла
              </h2>
              <p className="text-xs text-[var(--text-secondary)]">
                Шаг {step} / 2
              </p>
            </div>
          </div>
          <button
            onClick={handleClose}
            className="text-zinc-400 hover:text-white p-1 rounded-lg hover:bg-white/[0.05] transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-4 sm:p-6 space-y-4 overflow-y-auto flex-1">
          {!isPermitted ? (
            <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/25 text-rose-300 text-xs flex items-center gap-3">
              <ShieldAlert className="w-5 h-5 shrink-0 text-rose-400" />
              <span>
                Недостаточно прав. Добавление оборудования разрешено только ролям{' '}
                <strong>Владелец (Owner)</strong> или <strong>Администратор (Admin)</strong>.
              </span>
            </div>
          ) : (
            <>
              {/* STEP 1: Connection & Credentials */}
              {step === 1 && (
                <div className="space-y-4">
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <div className="sm:col-span-2">
                      <label className="block text-[11px] uppercase tracking-wider font-semibold text-zinc-400 mb-1.5">
                        IP-адрес или хостнейм <span className="text-cyan-400">*</span>
                      </label>
                      <input
                        type="text"
                        value={ip}
                        onChange={(e) => handleIpChange(e.target.value)}
                        placeholder="10.0.10.15"
                        className="w-full bg-[#050608] border border-white/[0.08] focus:border-cyan-500/60 rounded-lg px-3 py-2 text-xs text-zinc-100 font-mono focus:outline-none transition-colors"
                      />
                    </div>
                    <div>
                      <label className="block text-[11px] uppercase tracking-wider font-semibold text-zinc-400 mb-1.5">
                        SSH Порт
                      </label>
                      <input
                        type="number"
                        value={port}
                        onChange={(e) => setPort(Number(e.target.value))}
                        className="w-full bg-[#050608] border border-white/[0.08] focus:border-cyan-500/60 rounded-lg px-3 py-2 text-xs text-zinc-100 font-mono focus:outline-none transition-colors"
                      />
                    </div>
                  </div>

                  {/* Stand / Fabric & Bastion / ProxyJump */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label className="block text-[11px] uppercase tracking-wider font-semibold text-zinc-400 mb-1.5">
                        Целевой стенд / Фабрика ЦОД
                      </label>
                      <select
                        value={targetStand}
                        onChange={(e) => setTargetStand(e.target.value)}
                        className="w-full bg-[#050608] border border-white/[0.08] focus:border-cyan-500/60 rounded-lg px-3 py-2 text-xs text-zinc-200 font-mono focus:outline-none transition-colors"
                      >
                        {stands.map((s) => (
                          <option key={s.slug} value={s.slug}>
                            {s.name} ({s.slug})
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="block text-[11px] uppercase tracking-wider font-semibold text-zinc-400 mb-1.5">
                        Bastion / ProxyJump (Опционально)
                      </label>
                      <input
                        type="text"
                        value={proxyJump}
                        onChange={(e) => setProxyJump(e.target.value)}
                        placeholder={targetStand ? `stand:${targetStand}` : 'user@jump:22'}
                        className="w-full bg-[#050608] border border-white/[0.08] focus:border-cyan-500/60 rounded-lg px-3 py-2 text-xs text-zinc-200 font-mono focus:outline-none transition-colors"
                      />
                    </div>
                  </div>

                  {/* Auth Configuration */}
                  <div className="p-3.5 rounded-xl bg-[#0c0e14] border border-white/[0.06] space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-zinc-300">Аутентификация SSH</span>
                      <label className="flex items-center gap-2 cursor-pointer text-xs text-zinc-400 hover:text-zinc-200">
                        <input
                          type="checkbox"
                          checked={customCreds}
                          onChange={(e) => setCustomCreds(e.target.checked)}
                          className="rounded bg-zinc-900 border-zinc-700"
                        />
                        <span>Кастомные реквизиты</span>
                      </label>
                    </div>

                    {!customCreds ? (
                      <div className="flex items-center gap-3">
                        <select
                          value={authProfile}
                          onChange={(e) => setAuthProfile(e.target.value)}
                          className="bg-[#050608] border border-white/[0.08] rounded-lg px-3 py-1.5 text-xs text-zinc-200 flex-1 focus:outline-none"
                        >
                          <option value="lab_creds">lab_creds</option>
                        </select>
                      </div>
                    ) : (
                      <div className="grid grid-cols-2 gap-3 pt-1">
                        <div>
                          <label className="block text-[11px] text-zinc-400 mb-1">Пользователь</label>
                          <input
                            type="text"
                            value={username}
                            onChange={(e) => setUsername(e.target.value)}
                            placeholder="admin"
                            className="w-full bg-[#050608] border border-white/[0.08] rounded-lg px-3 py-1.5 text-xs text-zinc-100 font-mono focus:outline-none"
                          />
                        </div>
                        <div>
                          <label className="block text-[11px] text-zinc-400 mb-1">Пароль</label>
                          <input
                            type="password"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            placeholder="admin"
                            className="w-full bg-[#050608] border border-white/[0.08] rounded-lg px-3 py-1.5 text-xs text-zinc-100 font-mono focus:outline-none"
                          />
                        </div>
                      </div>
                    )}
                  </div>

                  {probeError && (
                    <div className="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/25 text-rose-300 text-xs flex items-center gap-2.5">
                      <AlertTriangle className="w-4 h-4 shrink-0 text-rose-400" />
                      <span>{probeError}</span>
                    </div>
                  )}
                </div>
              )}

              {/* STEP 2: Probe Results & Confirmation */}
              {step === 2 && probeResult && (
                <AddDeviceStep2
                  probeResult={probeResult}
                  hostname={hostname}
                  setHostname={setHostname}
                  role={role}
                  setRole={setRole}
                  platform={platform}
                  setPlatform={setPlatform}
                  preferredProtocol={preferredProtocol}
                  setPreferredProtocol={setPreferredProtocol}
                  probeError={probeError}
                />
              )}
            </>
          )}
        </div>

        {/* Footer */}
        {isPermitted && (
          <div className="flex items-center justify-between px-6 py-4 border-t border-white/[0.06] bg-[#050608] shrink-0">
            {step === 1 ? (
              <div className="flex items-center justify-between w-full">
                <button
                  onClick={handleClose}
                  className="px-4 py-2 rounded-lg text-xs font-medium text-zinc-400 hover:text-white hover:bg-white/[0.05] transition-colors"
                >
                  Отмена
                </button>
                <button
                  onClick={handleProbe}
                  disabled={probing || !ip.trim()}
                  className="flex items-center gap-2 px-4 py-2 rounded-md text-xs font-medium bg-[var(--text-primary)] text-[var(--bg-main)] hover:opacity-90 transition-all disabled:opacity-40 cursor-pointer"
                >
                  {probing ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      <span>Выполняется SSH Probe…</span>
                    </>
                  ) : (
                    <>
                      <span>Сканировать (SSH Probe)</span>
                      <ArrowRight className="w-3.5 h-3.5" />
                    </>
                  )}
                </button>
              </div>
            ) : (
              <div className="flex items-center justify-between w-full">
                <button
                  onClick={() => setStep(1)}
                  disabled={saving}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium text-zinc-400 hover:text-white hover:bg-white/[0.05] transition-colors cursor-pointer"
                >
                  <ArrowLeft className="w-3.5 h-3.5" />
                  <span>Назад к настройкам</span>
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-xs font-semibold bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-bold transition-all disabled:opacity-40 shadow-lg shadow-emerald-500/20 cursor-pointer"
                >
                  {saving ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      <span>Сохранение в инвентарь…</span>
                    </>
                  ) : (
                    <>
                      <CheckCircle2 className="w-4 h-4" />
                      <span>Добавить в инвентарь (MONITORING_ONLY)</span>
                    </>
                  )}
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
