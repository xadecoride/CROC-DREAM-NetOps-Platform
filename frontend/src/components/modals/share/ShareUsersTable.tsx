import React from 'react';
import {
  Copy,
  Check,
  RefreshCw,
  Unlock,
  Trash2,
  Send,
  CheckCircle2,
  Ban,
} from 'lucide-react';
import { api } from '../../../api';
import type { BootstrapExposureInfo, UserAccount } from '../../../api';

export const TTL_OPTIONS = [
  { value: 0, label: 'Навсегда' },
  { value: 60, label: '1 час' },
  { value: 1440, label: '24 часа' },
  { value: 10080, label: '7 дней' },
];

export function formatExpiry(expiresAt: string | null | undefined): { label: string; expired: boolean } {
  if (!expiresAt) return { label: 'Навсегда', expired: false };
  const diffMs = new Date(expiresAt).getTime() - Date.now();
  if (Number.isNaN(diffMs)) return { label: 'Навсегда', expired: false };
  if (diffMs <= 0) return { label: 'Истёк', expired: true };
  const mins = Math.ceil(diffMs / 60000);
  if (mins < 60) return { label: `${mins}м`, expired: false };
  const hours = Math.floor(mins / 60);
  return hours < 48
    ? { label: `${hours}ч`, expired: false }
    : { label: `${Math.ceil(hours / 24)}д`, expired: false };
}

interface ShareUsersTableProps {
  users: UserAccount[];
  mintedTokens: Record<string, string>;
  exposureInfo: BootstrapExposureInfo | null;
  systemTargets: Array<{ value: string; label: string }>;
  copiedKey: string | null;
  onCopy: (key: string, text: string) => void;
  runUserAction: (fn: () => Promise<void>) => Promise<void>;
  onGenerateVerifyCode: (u: UserAccount) => Promise<void>;
  onRotate: (u: UserAccount) => Promise<void>;
  ctrlClass: string;
  ghostBtnClass: string;
}

export const ShareUsersTable: React.FC<ShareUsersTableProps> = ({
  users,
  mintedTokens,
  exposureInfo,
  systemTargets,
  copiedKey,
  onCopy,
  runUserAction,
  onGenerateVerifyCode,
  onRotate,
  ctrlClass,
  ghostBtnClass,
}) => (
  <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-card)] overflow-hidden">
    {users.length === 0 ? (
      <div className="p-6 text-center text-xs text-[var(--text-muted)] font-mono">Список пуст</div>
    ) : (
      <div className="divide-y divide-[var(--border-subtle)] max-h-[300px] overflow-y-auto">
        {users.map((u) => {
          const cachedToken = mintedTokens[u.username] || u.verification_code || null;
          const inviteUrl = cachedToken
            ? api.buildInviteUrl(cachedToken, exposureInfo?.public_url || undefined)
            : null;
          const isBound = Boolean(u.bound_client_id);
          const isActive = u.is_active !== false;
          const copyId = `row-${u.username}`;
          const expiryInfo = formatExpiry(u.expires_at);

          return (
            <div
              key={u.id ?? u.username}
              className={`px-3 py-2 space-y-1 transition-colors ${
                !isActive || expiryInfo.expired ? 'opacity-60 bg-[var(--bg-subtle)]' : 'hover:bg-[var(--bg-subtle)]'
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 shrink-0">
                  <span
                    className={`w-2 h-2 rounded-full shrink-0 ${
                      isActive && !expiryInfo.expired ? 'bg-emerald-400' : 'bg-rose-400'
                    }`}
                  />
                  <span className="text-xs font-mono font-semibold text-[var(--text-primary)] truncate w-24 shrink-0">
                    {u.username}
                  </span>

                  <select
                    aria-label={`Роль ${u.username}`}
                    value={u.role}
                    disabled={String(u.role) === 'owner' || u.username === 'owner' || u.username === 'static_viewer'}
                    onChange={(e) =>
                      u.id !== null &&
                      void runUserAction(() =>
                        api
                          .updateUserInvite(u.id!, {
                            role: e.target.value as 'viewer' | 'operator' | 'admin',
                          })
                          .then(() => undefined)
                      )
                    }
                    className={`w-[82px] shrink-0 px-1.5 h-6 text-[10px] uppercase cursor-pointer ${ctrlClass}`}
                  >
                    {(String(u.role) === 'owner' || u.username === 'owner') && (
                      <option value="owner">OWNER</option>
                    )}
                    <option value="viewer">VIEWER</option>
                    <option value="operator">OPERATOR</option>
                    <option value="admin">ADMIN</option>
                  </select>

                  <select
                    aria-label={`Система ${u.username}`}
                    value={u.allowed_system || ''}
                    disabled={u.username === 'owner' || u.username === 'static_viewer'}
                    onChange={(e) =>
                      u.id !== null &&
                      void runUserAction(() =>
                        api
                          .updateUserInvite(u.id!, {
                            allowed_system: e.target.value.trim() || null,
                          })
                          .then(() => undefined)
                      )
                    }
                    className={`w-[108px] shrink-0 px-1.5 h-6 text-[10px] cursor-pointer truncate ${ctrlClass}`}
                  >
                    {systemTargets.map((sys) => (
                      <option key={sys.value} value={sys.value}>
                        {sys.label}
                      </option>
                    ))}
                  </select>

                  <select
                    aria-label={`TTL ${u.username}`}
                    value=""
                    disabled={u.username === 'owner' || u.username === 'static_viewer'}
                    onChange={(e) =>
                      u.id !== null &&
                      e.target.value !== '' &&
                      void runUserAction(() =>
                        api
                          .updateUserInvite(u.id!, {
                            expires_in_minutes: Number(e.target.value),
                          })
                          .then(() => undefined)
                      )
                    }
                    className={`w-[84px] shrink-0 px-1.5 h-6 text-[10px] cursor-pointer ${ctrlClass}`}
                  >
                    <option value="">{u.username === 'static_viewer' ? '∞ Вечный' : expiryInfo.label}</option>
                    {TTL_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="flex items-center gap-1 shrink-0">
                  <button
                    onClick={() => void onGenerateVerifyCode(u)}
                    className={`h-6 px-2 text-[10px] flex items-center gap-1 ${ghostBtnClass}`}
                    title="Код верификации для Telegram"
                  >
                    <Send className="w-2.5 h-2.5" />
                    <span>TG-код</span>
                  </button>

                  <button
                    onClick={() => inviteUrl && onCopy(copyId, inviteUrl)}
                    disabled={!inviteUrl}
                    className={`h-6 w-20 justify-center text-[10px] flex items-center gap-1 disabled:opacity-30 ${ghostBtnClass}`}
                  >
                    {copiedKey === copyId ? <Check className="w-2.5 h-2.5" /> : <Copy className="w-2.5 h-2.5" />}
                    <span>{copiedKey === copyId ? 'ОК' : 'Ссылка'}</span>
                  </button>

                  <button
                    onClick={() =>
                      u.id !== null && void runUserAction(() => api.unbindUserDevice(u.id!).then(() => undefined))
                    }
                    disabled={!isBound}
                    className={`h-6 px-2 text-[10px] flex items-center gap-1 disabled:opacity-25 ${ghostBtnClass}`}
                  >
                    <Unlock className="w-2.5 h-2.5" />
                    <span>Отвязать</span>
                  </button>

                  <button
                    onClick={() =>
                      u.id !== null &&
                      void runUserAction(() =>
                        api.updateUserInvite(u.id!, { is_active: u.is_active === false }).then(() => undefined)
                      )
                    }
                    className={`h-6 w-20 justify-center text-[10px] flex items-center gap-1 ${ghostBtnClass}`}
                  >
                    {isActive ? <Ban className="w-2.5 h-2.5" /> : <CheckCircle2 className="w-2.5 h-2.5" />}
                    <span>{isActive ? 'Блок' : 'Разблок'}</span>
                  </button>

                  <button
                    onClick={() => void onRotate(u)}
                    className={`h-6 w-6 flex items-center justify-center ${ghostBtnClass}`}
                    title="Перевыпустить токен"
                  >
                    <RefreshCw className="w-3 h-3" />
                  </button>

                  <button
                    onClick={() =>
                      u.id !== null && void runUserAction(() => api.deleteUserInvite(u.id!).then(() => undefined))
                    }
                    className={`h-6 w-6 flex items-center justify-center ${ghostBtnClass}`}
                    title="Удалить"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
              </div>

              <div className="flex items-center justify-between gap-2 text-[10px] font-mono text-[var(--text-muted)]">
                <div className="flex items-center gap-1.5">
                  <span className="px-1.5 py-0.5 rounded bg-[var(--bg-elevated)] text-[var(--text-secondary)] border border-[var(--border-subtle)]">
                    {u.telegram_chat_id
                      ? `TG: ${u.telegram_username ? `@${u.telegram_username}` : u.telegram_chat_id}`
                      : 'TG: —'}
                  </span>
                  {u.verification_code && (
                    <>
                      <button
                        onClick={() => onCopy(`rawcode-${u.username}`, u.verification_code!)}
                        className={`px-1.5 py-0.5 text-emerald-400 ${ghostBtnClass}`}
                        title="Скопировать 6-значный код для входа через браузер"
                      >
                        {copiedKey === `rawcode-${u.username}` ? 'ОК' : `Код: ${u.verification_code}`}
                      </button>
                      <button
                        onClick={() => onCopy(`vcode-${u.username}`, `/verify ${u.verification_code}`)}
                        className={`px-1.5 py-0.5 ${ghostBtnClass}`}
                        title="Скопировать команду для Telegram-бота"
                      >
                        /verify {u.verification_code}
                      </button>
                    </>
                  )}
                </div>
                <div className="truncate text-right">
                  {isBound ? `${u.bound_client_id} · ${u.bound_ip || 'local'}` : 'Свободна'}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    )}
  </div>
);
