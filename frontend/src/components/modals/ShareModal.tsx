import React, { useState, useEffect, useCallback } from 'react';
import {
  Copy,
  Check,
  Users,
  Plus,
  RefreshCw,
  GitBranch,
  KeyRound,
  CheckCircle2,
  AlertCircle,
} from 'lucide-react';
import { api, getErrorMessage } from '../../api';
import type {
  BootstrapExposureInfo,
  UserRole,
  UserAccount,
  UserInviteCreated,
  IntegrationsStatus,
} from '../../api';
import type { Locale } from '../../i18n';
import { Modal } from '../layout/ui';
import { PublicConsolesBanner } from './share/PublicConsolesBanner';
import { ShareIntegrationsTab } from './share/ShareIntegrationsTab';
import { ShareStaticViewer } from './share/ShareStaticViewer';
import { ShareUsersTable, TTL_OPTIONS } from './share/ShareUsersTable';

interface ShareModalProps {
  isOpen: boolean;
  onClose: () => void;
  userRole: UserRole;
  locale?: Locale;
}

const ctrlClass =
  'bg-[var(--bg-main)] border border-[var(--border-subtle)] hover:border-[var(--border-hover)] focus:border-[var(--text-primary)] rounded-md text-xs font-mono text-[var(--text-primary)] focus:outline-none transition-colors';

const ghostBtnClass =
  'border border-[var(--border-subtle)] hover:border-[var(--border-hover)] bg-[var(--bg-elevated)]/60 hover:bg-[var(--bg-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] rounded-md font-mono transition-colors cursor-pointer';

const primaryBtnClass =
  'bg-[var(--text-primary)] hover:opacity-90 text-[var(--bg-main)] border border-[var(--text-primary)] rounded-md font-semibold transition-opacity cursor-pointer disabled:opacity-40';

export const ShareModal: React.FC<ShareModalProps> = ({
  isOpen,
  onClose,
  userRole,
  locale = 'ru',
}) => {
  const isAdmin = userRole === 'admin' || userRole === 'owner';
  const [activeTab, setActiveTab] = useState<'links' | 'integrations'>(
    isAdmin ? 'links' : 'integrations'
  );
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [users, setUsers] = useState<UserAccount[]>([]);
  const [loading, setLoading] = useState(false);
  const [prefix, setPrefix] = useState('jury');
  const [targetRole, setTargetRole] = useState<'viewer' | 'operator' | 'admin'>('viewer');
  const [targetSystem, setTargetSystem] = useState('');
  const [targetTtlMinutes, setTargetTtlMinutes] = useState(0);
  const [batchCount, setBatchCount] = useState(1);
  const [mintedTokens, setMintedTokens] = useState<Record<string, string>>({});
  const [newlyCreated, setNewlyCreated] = useState<UserInviteCreated[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [integrations, setIntegrations] = useState<IntegrationsStatus | null>(null);
  const [exposureInfo, setExposureInfo] = useState<BootstrapExposureInfo | null>(null);
  const [deviceTargets, setDeviceTargets] = useState<Array<{ value: string; label: string }>>([]);
  const [syncingGit, setSyncingGit] = useState(false);
  const [gitRepoInput, setGitRepoInput] = useState('');
  const [gitBranchInput, setGitBranchInput] = useState('main');
  const [gitPatInput, setGitPatInput] = useState('');
  const [telegramNote, setTelegramNote] = useState('');
  const [sendingTelegram, setSendingTelegram] = useState(false);
  const [telegramPreview, setTelegramPreview] = useState<string | null>(null);

  const refreshAll = useCallback(async (showSpinner = false) => {
    if (showSpinner) setLoading(true);
    try {
      const [integData, devList, bootStatus] = await Promise.all([
        api.getIntegrationsStatus(),
        api.getDevices().catch(() => []),
        api.getBootstrapStatus().catch(() => null),
      ]);
      const userList = await api.getUsers().catch(() => integData.sessions || []);
      setUsers(userList);
      setIntegrations(integData);
      const remoteUrl = integData.github_sot?.remote_url || integData.github_sot?.github_repo_url || '';
      setGitRepoInput((prev) => prev || remoteUrl);
      setGitBranchInput((prev) => prev || integData.github_sot?.branch || 'main');
      if (bootStatus?.exposure) {
        setExposureInfo(bootStatus.exposure);
      }
      const fabrics = Array.from(
        new Set(devList.map((d) => d.fabric_id).filter((f): f is string => Boolean(f)))
      );
      setDeviceTargets([
        ...fabrics.map((f) => ({ value: `fabric:${f}`, label: `Фабрика: ${f}` })),
        ...devList.map((d) => ({ value: d.hostname, label: d.hostname })),
      ]);
      if (integData.user_tokens) {
        setMintedTokens((prev) => ({ ...prev, ...integData.user_tokens }));
      }
    } catch (err: unknown) {
      setErrorMsg(getErrorMessage(err, 'Ошибка загрузки'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    const t0 = setTimeout(() => void refreshAll(false), 0);
    const timer = setInterval(() => {
      if (document.visibilityState !== 'hidden') void refreshAll(false);
    }, 8000);
    return () => {
      clearTimeout(t0);
      clearInterval(timer);
    };
  }, [isOpen, refreshAll]);

  if (!isOpen) return null;

  const systemTargets = [{ value: '', label: 'Вся фабрика' }, ...deviceTargets];
  const publicConsoles = api.getPublicConsoles(exposureInfo);

  const handleCopy = (key: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const handleBatchGenerate = async () => {
    setErrorMsg(null);
    setStatusMsg(null);
    const cleanPrefix = prefix.trim() || 'jury';
    const sysScope = targetSystem.trim() || null;
    const ttlArg = targetTtlMinutes > 0 ? targetTtlMinutes : null;
    try {
      const created =
        batchCount === 1
          ? [
              await api.createUserInvite(
                `${cleanPrefix}-${Math.floor(100 + Math.random() * 900)}`,
                targetRole,
                sysScope,
                ttlArg
              ),
            ]
          : await api.createBatchInvites(cleanPrefix, targetRole, batchCount, sysScope, ttlArg);

      const updatedMap = { ...mintedTokens };
      for (const item of created) updatedMap[item.username] = item.token;
      setMintedTokens(updatedMap);
      setNewlyCreated(created);
      setStatusMsg(`Создано: ${created.length}`);
      await refreshAll();
    } catch (err: unknown) {
      setErrorMsg(getErrorMessage(err, 'Ошибка создания'));
    }
  };

  const runUserAction = async (fn: () => Promise<void>) => {
    setErrorMsg(null);
    try {
      await fn();
      await refreshAll();
    } catch (err: unknown) {
      setErrorMsg(getErrorMessage(err));
    }
  };

  const handleGenerateVerifyCode = async (u: UserAccount) => {
    if (u.id === null) return;
    await runUserAction(async () => {
      const res = await api.generateUserVerifyCode(u.id!);
      const code = res.verification_code || '';
      handleCopy(`vcode-${u.username}`, code);
      setStatusMsg(`${u.username}: код ${code}`);
    });
  };

  const handleRotate = async (u: UserAccount) => {
    if (u.id === null) return;
    await runUserAction(async () => {
      const rotated = await api.rotateUserToken(u.id!);
      const updatedMap = { ...mintedTokens, [rotated.username]: rotated.token };
      setMintedTokens(updatedMap);
      setNewlyCreated([rotated]);
      setStatusMsg(`Токен ${rotated.username} обновлён`);
    });
  };

  const handleGitSync = async () => {
    setSyncingGit(true);
    setErrorMsg(null);
    setStatusMsg(null);
    try {
      if (isAdmin && gitRepoInput.trim()) {
        const cfg = await api.connectGitRepo({
          repo_url: gitRepoInput.trim(),
          branch: gitBranchInput.trim() || 'main',
          pat: gitPatInput.trim() || undefined,
        });
        const gitInfo = cfg.git as (typeof cfg.git & { connected?: boolean; message?: string }) | undefined;
        if (gitInfo?.connected === false) {
          setErrorMsg(gitInfo.message || 'Ошибка подключения к Git');
          return;
        }
      }
      const res = await api.syncGitSot();
      setStatusMsg(
        `Git Sync (${res.git?.branch || 'main'}@${res.git?.commit_short || 'HEAD'}): +${res.sync.created.length} / ~${res.sync.updated.length}`
      );
      await refreshAll();
    } catch (err: unknown) {
      setErrorMsg(getErrorMessage(err, 'Ошибка Git Sync'));
    } finally {
      setSyncingGit(false);
    }
  };

  const handleSendTelegram = async () => {
    setSendingTelegram(true);
    setErrorMsg(null);
    try {
      const res = await api.sendTelegramSummary(telegramNote);
      setTelegramPreview(res.message_preview);
      setStatusMsg(res.sent ? `Отправлено (${res.chat_ids.length})` : res.hint || 'Превью сформировано');
      await refreshAll();
    } catch (err: unknown) {
      setErrorMsg(getErrorMessage(err, 'Ошибка отправки'));
    } finally {
      setSendingTelegram(false);
    }
  };

  const boundCount = users.filter((u) => Boolean(u.bound_client_id)).length;
  const restrictedCount = users.filter((u) => u.is_active === false).length;

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      title={locale === 'en' ? 'Access & Integrations' : 'Доступ и интеграции'}
      icon={
        <div className="w-7 h-7 rounded-md bg-[var(--bg-elevated)] border border-[var(--border-subtle)] flex items-center justify-center text-[var(--text-primary)]">
          <Users className="w-3.5 h-3.5" />
        </div>
      }
      badge={<span className="text-xs text-[var(--text-muted)] font-mono">{api.getCanvasName()}</span>}
      maxWidth="max-w-4xl"
      footer={
        <div className="w-full flex items-center justify-between text-[11px] text-[var(--text-muted)] font-mono">
          <div className="flex items-center gap-2">
            <span className="text-[var(--text-primary)] uppercase font-semibold">{userRole}</span>
            <span>·</span>
            <span className="text-[var(--text-secondary)]">{api.getClientId()}</span>
          </div>
          <button onClick={onClose} className={`px-3.5 py-1 text-xs font-sans ${ghostBtnClass}`}>
            {locale === 'en' ? 'Close' : 'Закрыть'}
          </button>
        </div>
      }
    >
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-2 border-b border-[var(--border-subtle)] pb-2.5">
          <div className="flex items-center gap-1.5">
            {isAdmin && (
              <button
                onClick={() => setActiveTab('links')}
                className={`px-3 py-1.5 rounded-md text-xs font-medium flex items-center gap-1.5 transition-colors cursor-pointer border ${
                  activeTab === 'links'
                    ? 'bg-[var(--text-primary)] text-[var(--bg-main)] border-[var(--text-primary)]'
                    : 'bg-[var(--bg-elevated)]/40 text-[var(--text-secondary)] hover:text-[var(--text-primary)] border-[var(--border-subtle)]'
                }`}
              >
                <KeyRound className="w-3.5 h-3.5" />
                <span>Участники ({users.length})</span>
              </button>
            )}
            <button
              onClick={() => setActiveTab('integrations')}
              className={`px-3 py-1.5 rounded-md text-xs font-medium flex items-center gap-1.5 transition-colors cursor-pointer border ${
                activeTab === 'integrations' || !isAdmin
                  ? 'bg-[var(--text-primary)] text-[var(--bg-main)] border-[var(--text-primary)]'
                  : 'bg-[var(--bg-elevated)]/40 text-[var(--text-secondary)] hover:text-[var(--text-primary)] border-[var(--border-subtle)]'
              }`}
            >
              <GitBranch className="w-3.5 h-3.5" />
              <span>Telegram · Git · БД</span>
            </button>
          </div>

          <div className="flex items-center gap-1.5 text-[11px] font-mono shrink-0">
            <span className="px-2 py-0.5 rounded-md bg-[var(--bg-elevated)] text-[var(--text-secondary)] border border-[var(--border-subtle)]">
              Онлайн: <strong className="text-[var(--text-primary)]">{boundCount}</strong>
            </span>
            {restrictedCount > 0 && (
              <span className="px-2 py-0.5 rounded-md bg-[var(--bg-elevated)] text-[var(--text-secondary)] border border-[var(--border-subtle)]">
                Блок: <strong className="text-[var(--text-primary)]">{restrictedCount}</strong>
              </span>
            )}
            <button onClick={() => void refreshAll()} className={`p-1.5 ${ghostBtnClass}`} title="Обновить">
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {errorMsg && (
          <div className="px-3 py-2 rounded-md bg-[var(--bg-elevated)] border border-[var(--border-hover)] text-xs text-[var(--text-primary)] flex items-center gap-2">
            <AlertCircle className="w-3.5 h-3.5 shrink-0 text-rose-400" />
            <span className="truncate">{errorMsg}</span>
          </div>
        )}

        {statusMsg && (
          <div className="px-3 py-2 rounded-md bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-xs text-[var(--text-primary)] flex items-center gap-2">
            <CheckCircle2 className="w-3.5 h-3.5 shrink-0 text-emerald-400" />
            <span className="truncate">{statusMsg}</span>
          </div>
        )}

        {activeTab === 'links' && isAdmin ? (
          <div className="space-y-3">
            <PublicConsolesBanner
              consoles={publicConsoles}
              copiedKey={copiedKey}
              onCopy={handleCopy}
              ghostBtnClass={ghostBtnClass}
            />

            <ShareStaticViewer
              exposureInfo={exposureInfo}
              ctrlClass={ctrlClass}
              ghostBtnClass={ghostBtnClass}
              primaryBtnClass={primaryBtnClass}
            />

            <div className="p-2.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elevated)]/40 space-y-2.5">
              <div className="grid grid-cols-2 sm:grid-cols-[1fr_110px_140px_110px_68px_auto] gap-2 items-center">
                <input
                  type="text"
                  value={prefix}
                  onChange={(e) => setPrefix(e.target.value)}
                  placeholder="Имя / префикс"
                  className={`w-full min-w-0 px-2.5 h-8 ${ctrlClass}`}
                />
                <select
                  value={targetRole}
                  onChange={(e) => setTargetRole(e.target.value as 'viewer' | 'operator' | 'admin')}
                  className={`w-full min-w-0 px-2 h-8 cursor-pointer ${ctrlClass}`}
                >
                  <option value="viewer">VIEWER</option>
                  <option value="operator">OPERATOR</option>
                  <option value="admin">ADMIN</option>
                </select>
                <select
                  value={targetSystem}
                  onChange={(e) => setTargetSystem(e.target.value)}
                  className={`w-full min-w-0 px-2 h-8 cursor-pointer ${ctrlClass}`}
                >
                  {systemTargets.map((sys) => (
                    <option key={sys.value} value={sys.value}>
                      {sys.label}
                    </option>
                  ))}
                </select>
                <select
                  value={targetTtlMinutes}
                  onChange={(e) => setTargetTtlMinutes(Number(e.target.value))}
                  className={`w-full min-w-0 px-2 h-8 cursor-pointer ${ctrlClass}`}
                >
                  {TTL_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
                <select
                  value={batchCount}
                  onChange={(e) => setBatchCount(Number(e.target.value))}
                  className={`w-full min-w-0 px-2 h-8 cursor-pointer ${ctrlClass}`}
                >
                  <option value={1}>×1</option>
                  <option value={3}>×3</option>
                  <option value={5}>×5</option>
                </select>
                <button
                  onClick={handleBatchGenerate}
                  className={`h-8 px-3 text-xs flex items-center justify-center gap-1.5 whitespace-nowrap shrink-0 ${primaryBtnClass}`}
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>Создать</span>
                </button>
              </div>

              {newlyCreated.length > 0 && (
                <div className="p-2 rounded-md bg-[var(--bg-main)] border border-[var(--border-subtle)] space-y-1 max-h-28 overflow-y-auto">
                  {newlyCreated.map((item) => {
                    const url = api.buildInviteUrl(item.token, exposureInfo?.public_url || undefined);
                    return (
                      <div key={item.username} className="flex items-center gap-1.5 text-xs font-mono">
                        <span className="text-[var(--text-primary)] w-24 truncate shrink-0">{item.username}</span>
                        {item.verification_code && (
                          <button
                            onClick={() => handleCopy(`new-code-${item.username}`, item.verification_code!)}
                            className={`px-2 py-0.5 text-[11px] shrink-0 text-emerald-400 ${ghostBtnClass}`}
                            title="Скопировать 6-значный код доступа"
                          >
                            {copiedKey === `new-code-${item.username}` ? 'ОК' : `Код: ${item.verification_code}`}
                          </button>
                        )}
                        <input readOnly value={url} className={`flex-1 min-w-0 px-2 py-0.5 text-[11px] ${ctrlClass}`} />
                        <button
                          onClick={() => handleCopy(`new-${item.username}`, url)}
                          className={`px-2 py-0.5 text-[11px] shrink-0 ${ghostBtnClass}`}
                        >
                          {copiedKey === `new-${item.username}` ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            <ShareUsersTable
              users={users}
              mintedTokens={mintedTokens}
              exposureInfo={exposureInfo}
              systemTargets={systemTargets}
              copiedKey={copiedKey}
              onCopy={handleCopy}
              runUserAction={runUserAction}
              onGenerateVerifyCode={handleGenerateVerifyCode}
              onRotate={handleRotate}
              ctrlClass={ctrlClass}
              ghostBtnClass={ghostBtnClass}
            />
          </div>
        ) : (
          <ShareIntegrationsTab
            integrations={integrations}
            isAdmin={isAdmin}
            gitRepoInput={gitRepoInput}
            setGitRepoInput={setGitRepoInput}
            gitBranchInput={gitBranchInput}
            setGitBranchInput={setGitBranchInput}
            gitPatInput={gitPatInput}
            setGitPatInput={setGitPatInput}
            syncingGit={syncingGit}
            onGitSync={handleGitSync}
            telegramNote={telegramNote}
            setTelegramNote={setTelegramNote}
            sendingTelegram={sendingTelegram}
            onSendTelegram={handleSendTelegram}
            telegramPreview={telegramPreview}
            ctrlClass={ctrlClass}
            ghostBtnClass={ghostBtnClass}
            primaryBtnClass={primaryBtnClass}
          />
        )}
      </div>
    </Modal>
  );
};
