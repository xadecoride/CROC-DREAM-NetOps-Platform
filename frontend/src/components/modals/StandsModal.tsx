import React, { useEffect, useState, useCallback } from 'react';
import {
  X, Layers, Server, Plus, Trash2, CheckCircle2, AlertTriangle, WifiOff, RotateCw, Lock, Terminal, Cpu,
} from 'lucide-react';
import { api, getErrorMessage, type StandRead, type UserRole } from '../../api';
import type { Locale } from '../../i18n';
import { CreateStandForm } from './stands/CreateStandForm';

interface StandsModalProps {
  isOpen: boolean; onClose: () => void; userRole: UserRole; locale: Locale;
  activeStand: string; onSelectStand: (slug: string) => void;
  stands: StandRead[]; onRefreshStands: () => Promise<void>;
}

export const StandsModal: React.FC<StandsModalProps> = ({
  isOpen, onClose, userRole, locale, activeStand, onSelectStand, stands, onRefreshStands,
}) => {
  const isEn = locale === 'en';
  const canManage = userRole === 'owner' || userRole === 'admin';

  const [loading, setLoading] = useState(false);
  const [syncingGit, setSyncingGit] = useState(false);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [discoveringSlug, setDiscoveringSlug] = useState<string | null>(null);
  const [probeBanner, setProbeBanner] = useState<{ ok: boolean; text: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleRefresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      await onRefreshStands();
    } catch (err: unknown) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [onRefreshStands]);

  useEffect(() => {
    if (isOpen) void handleRefresh();
  }, [isOpen, handleRefresh]);

  if (!isOpen) return null;

  const totalNodes = stands.reduce((acc, s) => acc + (s.total_devices || 0), 0);

  const handleGitSync = async () => {
    setSyncingGit(true);
    setProbeBanner(null);
    setError(null);
    try {
      const res = await api.syncGitSot();
      setProbeBanner({
        ok: true,
        text: `Git SoT (${res.git?.branch || 'main'}@${res.git?.commit_short || 'HEAD'}): +${res.sync.created.length} / ~${res.sync.updated.length}`,
      });
      await onRefreshStands();
    } catch (err: unknown) {
      setError(getErrorMessage(err, isEn ? 'Git Sync failed' : 'Ошибка синхронизации Git SoT'));
    } finally {
      setSyncingGit(false);
    }
  };

  const handleDiscoverStand = async (targetSlug: string) => {
    setDiscoveringSlug(targetSlug);
    setProbeBanner(null);
    setError(null);
    try {
      const res = await api.discoverStand(targetSlug);
      setProbeBanner({ ok: res.reachable, text: res.message });
      await onRefreshStands();
    } catch (err: unknown) {
      setError(getErrorMessage(err, isEn ? 'Discovery failed' : 'Ошибка авто-обнаружения'));
    } finally {
      setDiscoveringSlug(null);
    }
  };

  const handleDeleteStand = async (targetSlug: string) => {
    setError(null);
    try {
      await api.deleteStand(targetSlug);
      if (activeStand === targetSlug) onSelectStand('all');
      await onRefreshStands();
    } catch (err: unknown) {
      setError(getErrorMessage(err, isEn ? 'Failed to delete stand' : 'Ошибка удаления стенда'));
    }
  };

  return (
    <div
      className="fixed inset-0 z-[70] bg-black/70 backdrop-blur-xs flex items-center justify-center p-4 animate-fade-in"
      onClick={onClose}
      data-testid="stands-modal-backdrop"
    >
      <div
        className="w-full max-w-3xl max-h-[90vh] overflow-y-auto rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] shadow-2xl p-5 sm:p-6 text-[var(--text-primary)] space-y-4"
        onClick={(e) => e.stopPropagation()}
        data-testid="stands-modal"
      >
        <div className="flex items-center justify-between border-b border-[var(--border-subtle)] pb-3.5">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-cyan-400">
              <Layers className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-sm sm:text-base font-semibold tracking-tight text-[var(--text-primary)]">
                {isEn ? 'Stands & DC Fabrics' : 'Стенды и фабрики ЦОД'}
              </h2>
              <p className="text-[11px] text-[var(--text-muted)] font-mono">
                {stands.length} {isEn ? 'stands' : 'площадок'} · {totalNodes} {isEn ? 'nodes' : 'узлов'}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            {canManage && (
              <button
                type="button"
                onClick={() => void handleGitSync()}
                disabled={syncingGit}
                className="px-2.5 py-1 rounded-md bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/30 text-[11px] font-mono text-emerald-300 transition-colors cursor-pointer"
                title={isEn ? 'Sync Git SoT inventory' : 'Синхронизировать инвентарь из Git SoT'}
              >
                {syncingGit ? '...' : 'Git Sync'}
              </button>
            )}
            <button onClick={handleRefresh} disabled={loading} title={isEn ? 'Refresh stands' : 'Обновить'}
              className="p-1.5 rounded-md bg-[var(--bg-elevated)] border border-[var(--border-subtle)] hover:border-[var(--border-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors cursor-pointer">
              <RotateCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button onClick={onClose} title="Esc"
              className="p-1.5 rounded-md text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-elevated)] transition-colors cursor-pointer">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {error && (
          <div className="p-2.5 rounded-lg bg-rose-500/10 border border-rose-500/30 text-xs text-rose-400 font-mono flex items-center justify-between">
            <span>{error}</span>
            <button onClick={() => setError(null)} className="text-rose-300 hover:text-white cursor-pointer">
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {probeBanner && (
          <div
            className={`p-2.5 rounded-lg border text-xs font-mono flex items-center justify-between ${
              probeBanner.ok
                ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
                : 'bg-amber-500/10 border-amber-500/30 text-amber-300'
            }`}
          >
            <span>{probeBanner.text}</span>
            <button onClick={() => setProbeBanner(null)} className="opacity-70 hover:opacity-100 cursor-pointer">
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        <div className="space-y-2.5">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-mono uppercase tracking-wider text-[var(--text-secondary)]">
              {isEn ? 'Connected Stands' : 'Подключённые стенды'} ({stands.length})
            </span>
            {canManage && (
              <button
                onClick={() => setShowCreateForm((v) => !v)}
                data-testid="toggle-create-stand-btn"
                className="px-2.5 py-1 rounded-md bg-[var(--text-primary)] text-[var(--bg-main)] hover:opacity-90 text-xs font-medium flex items-center gap-1.5 transition-opacity cursor-pointer"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>{isEn ? 'New Stand' : 'Новый стенд'}</span>
              </button>
            )}
          </div>

          <button
            type="button"
            onClick={() => { onSelectStand('all'); onClose(); }}
            data-testid="select-stand-all"
            className={`w-full text-left p-2.5 rounded-lg border transition-colors flex items-center justify-between cursor-pointer ${
              activeStand === 'all'
                ? 'bg-cyan-500/10 border-cyan-500/40 text-[var(--text-primary)]'
                : 'bg-[var(--bg-elevated)]/40 border-[var(--border-subtle)] hover:border-[var(--border-hover)]'
            }`}
          >
            <div className="flex items-center gap-2.5">
              <Layers className={`w-4 h-4 ${activeStand === 'all' ? 'text-cyan-400' : 'text-[var(--text-secondary)]'}`} />
              <div className="text-xs font-semibold flex items-center gap-2">
                <span>{isEn ? 'All Stands' : 'Все стенды'}</span>
                {activeStand === 'all' && (
                  <span className="px-1.5 py-0.2 rounded text-[10px] font-mono bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
                    ACTIVE
                  </span>
                )}
              </div>
            </div>
            <span className="text-xs font-mono px-2 py-0.5 rounded bg-[var(--bg-main)] border border-[var(--border-subtle)] text-[var(--text-secondary)]">
              {totalNodes} {isEn ? 'nodes' : 'узлов'}
            </span>
          </button>

          <div className="grid grid-cols-1 gap-2.5">
            {stands.map((st) => {
              const isSelected = activeStand === st.slug;
              const isDefault = Boolean(st.is_default) || st.id === 1;
              const isDown = st.vitality_state === 'DOWN' || st.status === 'OFFLINE';
              return (
                <div
                  key={st.slug}
                  data-testid={`stand-card-${st.slug}`}
                  className={`p-3.5 rounded-lg border transition-colors flex flex-col gap-2.5 ${
                    isSelected
                      ? 'bg-cyan-500/10 border-cyan-500/40'
                      : 'bg-[var(--bg-elevated)]/40 border-[var(--border-subtle)] hover:border-[var(--border-hover)]'
                  }`}
                >
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                    <div
                      className="flex-1 min-w-0 cursor-pointer space-y-1.5"
                      onClick={() => { onSelectStand(st.slug); onClose(); }}
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <Server className={`w-3.5 h-3.5 shrink-0 ${isSelected ? 'text-cyan-400' : 'text-[var(--text-secondary)]'}`} />
                        <span className="text-xs font-semibold text-[var(--text-primary)]">{st.name}</span>
                        <span className="px-1.5 py-0.2 rounded text-[10px] font-mono bg-[var(--bg-main)] border border-[var(--border-subtle)] text-[var(--text-secondary)]">
                          {st.slug}
                        </span>
                        <span
                          className={`px-1.5 py-0.2 rounded text-[10px] font-mono border ${
                            !isDown
                              ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                              : 'bg-rose-500/15 text-rose-400 border-rose-500/30'
                          }`}
                        >
                          {isDown ? 'DOWN (ЛЕЖИТ)' : 'UP (ПОДНЯТА)'}
                        </span>
                        {st.using_backup_fallback && (
                          <span className="px-1.5 py-0.2 rounded text-[10px] font-mono bg-amber-500/15 text-amber-300 border border-amber-500/30">
                            РЕЗЕРВ (.CFG)
                          </span>
                        )}
                        {isSelected && (
                          <span className="px-1.5 py-0.2 rounded text-[10px] font-mono bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
                            ACTIVE
                          </span>
                        )}
                      </div>

                      <div className="flex flex-wrap items-center gap-3 text-[11px] font-mono text-[var(--text-muted)]">
                        <span className="inline-flex items-center gap-1"><Terminal className="w-3 h-3" />{st.driver}</span>
                        {st.jump_host && (
                          <span>jump: {st.jump_user ? `${st.jump_user}@` : ''}{st.jump_host}:{st.jump_port}</span>
                        )}
                        {st.has_password && (
                          <span className="inline-flex items-center gap-1 text-emerald-400"><Lock className="w-3 h-3" />SSH Vault</span>
                        )}
                        <span className="inline-flex items-center gap-1"><Cpu className="w-3 h-3" />queue:{st.queue_name}</span>
                      </div>
                    </div>

                    <div className="flex items-center gap-2 shrink-0">
                      <div className="flex items-center gap-1.5 text-[11px] font-mono">
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" title="IN_SYNC">
                          <CheckCircle2 className="w-3 h-3" />{st.in_sync_devices}
                        </span>
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/20" title="DRIFT_DETECTED">
                          <AlertTriangle className="w-3 h-3" />{st.drifted_devices}
                        </span>
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-rose-500/10 text-rose-400 border border-rose-500/20" title="UNREACHABLE">
                          <WifiOff className="w-3 h-3" />{st.unreachable_devices}
                        </span>
                      </div>

                      {canManage && (
                        <>
                          <button
                            type="button"
                            onClick={async () => {
                              try {
                                const b = await api.backupStand(st.slug);
                                setProbeBanner({ ok: true, text: b.message });
                                await onRefreshStands();
                              } catch (err: unknown) { setError(getErrorMessage(err)); }
                            }}
                            data-testid={`backup-stand-${st.slug}`}
                            className="px-2 py-1 rounded-md bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/30 text-[11px] font-mono text-emerald-300 cursor-pointer"
                            title="Сохранить резервную копию всех узлов стенда на диск"
                          >
                            {isEn ? 'Backup' : 'Бэкап'}
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleDiscoverStand(st.slug)}
                            disabled={discoveringSlug === st.slug}
                            data-testid={`discover-stand-${st.slug}`}
                            className="px-2 py-1 rounded-md bg-cyan-500/10 hover:bg-cyan-500/20 border border-cyan-500/30 text-[11px] font-mono text-cyan-300 cursor-pointer"
                            title={isEn ? 'Auto-discover & sync nodes' : 'Авто-обнаружение и синхронизация узлов стенда'}
                          >
                            {discoveringSlug === st.slug ? '...' : isEn ? 'Discover' : 'Дискавери'}
                          </button>
                        </>
                      )}

                      <button
                        type="button"
                        onClick={() => { onSelectStand(st.slug); onClose(); }}
                        className="px-2.5 py-1 rounded-md bg-[var(--bg-main)] hover:bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-[11px] font-mono text-[var(--text-primary)] cursor-pointer"
                      >
                        {isSelected ? (isEn ? 'Selected' : 'Выбран') : isEn ? 'Select' : 'Выбрать'}
                      </button>

                      {canManage && !isDefault && (
                        <button
                          type="button"
                          onClick={() => handleDeleteStand(st.slug)}
                          data-testid={`delete-stand-${st.slug}`}
                          className="p-1.5 rounded-md bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/20 transition-colors cursor-pointer"
                          title={isEn ? 'Delete stand' : 'Удалить стенд'}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  </div>

                  {isDown && (st.down_reason || st.ai_diagnosis) && (
                    <div className="p-2.5 rounded-md bg-rose-500/10 border border-rose-500/25 text-[11px] font-mono space-y-1">
                      {st.down_reason && <div className="text-rose-300"><strong>Почему лежит:</strong> {st.down_reason}</div>}
                      {st.ai_diagnosis && <div className="text-violet-300"><strong>Диагноз ИИ:</strong> {st.ai_diagnosis}</div>}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {canManage && showCreateForm && (
          <CreateStandForm
            locale={locale}
            onCancel={() => setShowCreateForm(false)}
            onError={(msg) => setError(msg)}
            onProbeResult={(banner) => setProbeBanner(banner)}
            onSuccess={async (newSlug, discBanner) => {
              if (discBanner) setProbeBanner(discBanner);
              setShowCreateForm(false);
              onSelectStand(newSlug);
              void onRefreshStands();
            }}
          />
        )}
      </div>
    </div>
  );
};
