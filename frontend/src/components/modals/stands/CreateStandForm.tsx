import React, { useState } from 'react';
import { api, getErrorMessage, type StandCreatePayload } from '../../../api';
import type { Locale } from '../../../i18n';

export interface CreateStandFormProps {
  onSuccess: (newSlug: string, banner: { ok: boolean; text: string } | null) => Promise<void> | void;
  onCancel: () => void;
  onError: (msg: string | null) => void;
  onProbeResult: (banner: { ok: boolean; text: string } | null) => void;
  locale: Locale;
}

export const CreateStandForm: React.FC<CreateStandFormProps> = ({
  onSuccess,
  onCancel,
  onError,
  onProbeResult,
  locale,
}) => {
  const isEn = locale === 'en';
  const [submitting, setSubmitting] = useState(false);
  const [probing, setProbing] = useState(false);

  const [slug, setSlug] = useState('');
  const [name, setName] = useState('');
  const [site, setSite] = useState('');
  const [driver, setDriver] = useState<'scrapli' | 'offline'>('scrapli');
  const [jumpHost, setJumpHost] = useState('');
  const [jumpPort, setJumpPort] = useState<number>(22);
  const [jumpUser, setJumpUser] = useState('root');
  const [jumpPassword, setJumpPassword] = useState('');
  const [queueName, setQueueName] = useState('default');
  const [description, setDescription] = useState('');
  const [autoDiscover, setAutoDiscover] = useState(true);
  const [standPlatform, setStandPlatform] = useState('auto');

  const handleProbeForm = async () => {
    setProbing(true);
    onProbeResult(null);
    onError(null);
    try {
      const res = await api.probeStand({
        jump_host: driver === 'offline' ? null : jumpHost.trim() || null,
        jump_port: Number(jumpPort) || 22,
      });
      onProbeResult({ ok: res.reachable, text: res.message });
    } catch (err: unknown) {
      onError(getErrorMessage(err, isEn ? 'Probe failed' : 'Ошибка проверки связи'));
    } finally {
      setProbing(false);
    }
  };

  const handleCreateStand = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!slug.trim() || !name.trim()) return;
    setSubmitting(true);
    onError(null);
    try {
      const payload: StandCreatePayload = {
        slug: slug.trim().toLowerCase(),
        name: name.trim(),
        site: site.trim() || null,
        driver,
        jump_host: jumpHost.trim() || null,
        jump_port: Number(jumpPort) || 22,
        jump_user: jumpUser.trim() || null,
        jump_password: jumpPassword || null,
        queue_name: queueName.trim() || 'default',
        description: description.trim() || null,
        port_map: standPlatform !== 'auto' ? { platform: standPlatform } : undefined,
      };
      const created = await api.createStand(payload);
      setSlug('');
      setName('');
      setSite('');
      setJumpHost('');
      setJumpPort(22);
      setJumpPassword('');
      setDescription('');
      setStandPlatform('auto');
      let discBanner: { ok: boolean; text: string } | null = null;
      if (autoDiscover) {
        const disc = await api.discoverStand(created.slug);
        discBanner = { ok: disc.reachable, text: disc.message };
      }
      await onSuccess(created.slug, discBanner);
    } catch (err: unknown) {
      onError(getErrorMessage(err, isEn ? 'Failed to create stand' : 'Ошибка подключения стенда'));
    } finally {
      setSubmitting(false);
    }
  };

  const inputClass =
    'w-full px-2.5 py-1.5 rounded-md bg-[var(--bg-main)] border border-[var(--border-subtle)] focus:border-[var(--border-hover)] text-xs text-[var(--text-primary)] font-mono outline-none transition-colors';
  const labelClass = 'block text-[11px] font-mono text-[var(--text-secondary)] mb-1';

  return (
    <form
      onSubmit={handleCreateStand}
      data-testid="create-stand-form"
      className="p-4 rounded-xl bg-[var(--bg-elevated)]/60 border border-[var(--border-subtle)] space-y-3"
    >
      <div className="text-xs font-semibold font-mono uppercase tracking-wider text-cyan-400">
        {isEn ? '+ New Stand / Pod' : '+ Новый стенд / ЦОД'}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
        <div>
          <label className={labelClass}>Slug ID *</label>
          <input
            required
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            placeholder="dc2-spb-pod1"
            className={inputClass}
            data-testid="stand-slug-input"
          />
        </div>
        <div>
          <label className={labelClass}>{isEn ? 'Name *' : 'Название *'}</label>
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="ЦОД СПб — Pod 1"
            className={inputClass}
            data-testid="stand-name-input"
          />
        </div>
        <div>
          <label className={labelClass}>{isEn ? 'Site' : 'Площадка'}</label>
          <input
            value={site}
            onChange={(e) => setSite(e.target.value)}
            placeholder="SPB-DC2"
            className={inputClass}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-4 gap-2.5">
        <div>
          <label className={labelClass}>{isEn ? 'Driver' : 'Драйвер'}</label>
          <select
            value={driver}
            onChange={(e) => setDriver(e.target.value as 'scrapli' | 'offline')}
            className={inputClass}
          >
            <option value="scrapli">scrapli (SSH)</option>
            <option value="offline">offline (Эмуляция)</option>
          </select>
        </div>
        <div>
          <label className={labelClass}>Jump Host</label>
          <input
            value={jumpHost}
            onChange={(e) => setJumpHost(e.target.value)}
            placeholder="10.20.30.10"
            className={inputClass}
          />
        </div>
        <div>
          <label className={labelClass}>Jump Port</label>
          <input
            type="number"
            value={jumpPort}
            onChange={(e) => setJumpPort(Number(e.target.value))}
            placeholder="22"
            className={inputClass}
          />
        </div>
        <div>
          <label className={labelClass}>Queue</label>
          <input
            value={queueName}
            onChange={(e) => setQueueName(e.target.value)}
            placeholder="default"
            className={inputClass}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-4 gap-2.5">
        <div>
          <label className={labelClass}>Jump User</label>
          <input
            value={jumpUser}
            onChange={(e) => setJumpUser(e.target.value)}
            placeholder="root"
            className={inputClass}
          />
        </div>
        <div>
          <label className={labelClass}>{isEn ? 'SSH Password' : 'SSH Пароль'}</label>
          <input
            type="password"
            value={jumpPassword}
            onChange={(e) => setJumpPassword(e.target.value)}
            placeholder="••••••••"
            className={inputClass}
          />
        </div>
        <div>
          <label className={labelClass}>{isEn ? 'Platform Override' : 'Платформа (Override)'}</label>
          <select
            value={standPlatform}
            onChange={(e) => setStandPlatform(e.target.value)}
            className={inputClass}
          >
            <option value="auto">{isEn ? 'Auto (heuristic)' : 'Авто (эвристика)'}</option>
            <option value="arista_eos">Arista EOS</option>
            <option value="cisco_iosxe">Cisco IOS-XE</option>
            <option value="huawei_vrp">Huawei VRP</option>
            <option value="linux_server">Linux Server</option>
          </select>
        </div>
        <div>
          <label className={labelClass}>{isEn ? 'Description' : 'Описание'}</label>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="CLOS Pod"
            className={inputClass}
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
        <label className="flex items-center gap-2 text-xs font-mono text-[var(--text-secondary)] cursor-pointer">
          <input
            type="checkbox"
            checked={autoDiscover}
            onChange={(e) => setAutoDiscover(e.target.checked)}
            className="rounded border-[var(--border-subtle)]"
          />
          <span>{isEn ? 'Auto-discover nodes' : 'Авто-дискавери узлов'}</span>
        </label>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void handleProbeForm()}
            disabled={probing}
            className="px-2.5 py-1.5 rounded-md border border-cyan-500/30 bg-cyan-500/10 text-xs font-mono text-cyan-300 hover:bg-cyan-500/20 cursor-pointer"
          >
            {probing ? '...' : isEn ? 'Probe SSH' : 'Проверить SSH'}
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="px-2.5 py-1.5 rounded-md border border-[var(--border-subtle)] text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer"
          >
            {isEn ? 'Cancel' : 'Отмена'}
          </button>
          <button
            type="submit"
            disabled={submitting}
            data-testid="submit-create-stand-btn"
            className="px-3 py-1.5 rounded-md bg-[var(--text-primary)] text-[var(--bg-main)] hover:opacity-90 text-xs font-mono font-semibold cursor-pointer"
          >
            {submitting ? '...' : isEn ? 'Connect' : 'Подключить'}
          </button>
        </div>
      </div>
    </form>
  );
};
