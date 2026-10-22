import React from 'react';
import {
  ArrowRight,
  CheckCircle2,
  ExternalLink,
  Smartphone,
} from 'lucide-react';
import {
  allConsolePorts,
  consoleConfig,
} from '../../../api';
import {
  inputClass,
  primaryBtnClass,
  type WizardStep,
} from './WelcomeStepStyles';
import { Lbl, StepNav } from './WelcomeParts';

export interface WelcomeOwnerStepProps {
  isEn: boolean;
  recoverMode: boolean;
  setRecoverMode: (v: boolean) => void;
  clusterName: string;
  setClusterName: (v: string) => void;
  exposureMode: 'local_only' | 'public_tunnel';
  setExposureMode: (v: 'local_only' | 'public_tunnel') => void;
  displayPublicHost: string;
  canTransferOwnerFlag: boolean;
  claimingOwner: boolean;
  onClaimOwner: () => void;
  sendingTelegramCode: boolean;
  onRequestTelegramRecoveryCode: () => void;
  telegramRecoverLink: string;
  telegramCodeInput: string;
  setTelegramCodeInput: (v: string) => void;
  recoveryKeyInput: string;
  setRecoveryKeyInput: (v: string) => void;
  recoveryStatusMsg: string;
  onVerifyOwnerRecovery: () => void;
}

export const WelcomeOwnerStep: React.FC<WelcomeOwnerStepProps> = ({
  isEn,
  recoverMode,
  setRecoverMode,
  clusterName,
  setClusterName,
  exposureMode,
  setExposureMode,
  displayPublicHost,
  canTransferOwnerFlag,
  claimingOwner,
  onClaimOwner,
  sendingTelegramCode,
  onRequestTelegramRecoveryCode,
  telegramRecoverLink,
  telegramCodeInput,
  setTelegramCodeInput,
  recoveryKeyInput,
  setRecoveryKeyInput,
  recoveryStatusMsg,
  onVerifyOwnerRecovery,
}) => (
  <div className="space-y-3.5">
    {!recoverMode ? (
      <div className="space-y-3.5">
        <div>
          <Lbl>{isEn ? 'Cluster Name' : 'Имя кластера'}</Lbl>
          <input
            type="text"
            value={clusterName}
            onChange={(e) => setClusterName(e.target.value)}
            className={inputClass}
          />
        </div>

        <div>
          <Lbl>{isEn ? 'Network Exposure' : 'Сетевой контур'}</Lbl>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => setExposureMode('public_tunnel')}
              className={`p-2.5 rounded-lg border text-left cursor-pointer ${
                exposureMode === 'public_tunnel'
                  ? 'border-sky-500/60 bg-sky-500/10'
                  : 'border-[var(--border-subtle)] bg-[var(--bg-main)]'
              }`}
            >
              <div className="text-xs font-semibold">🌐 {isEn ? 'Public Internet Access' : 'Внешний доступ (Интернет)'}</div>
              <div className="text-[10px] font-mono text-[var(--text-muted)] mt-0.5">
                {displayPublicHost}:{allConsolePorts()[0]}..{allConsolePorts().slice(-1)[0]}
              </div>
            </button>

            <button
              type="button"
              onClick={() => setExposureMode('local_only')}
              className={`p-2.5 rounded-lg border text-left cursor-pointer ${
                exposureMode === 'local_only'
                  ? 'border-emerald-500/60 bg-emerald-500/10'
                  : 'border-[var(--border-subtle)] bg-[var(--bg-main)]'
              }`}
            >
              <div className="text-xs font-semibold">🔒 {isEn ? 'Local Only' : 'Только локально'}</div>
              <div className="text-[10px] font-mono text-[var(--text-muted)] mt-0.5">localhost:{consoleConfig().owner_port}</div>
            </button>
          </div>
        </div>

        <div className="flex items-center justify-between pt-1">
          {canTransferOwnerFlag ? (
            <button
              type="button"
              onClick={() => setRecoverMode(true)}
              className="text-[11px] font-mono text-[var(--text-secondary)] hover:text-[var(--text-primary)] underline cursor-pointer"
            >
              {isEn ? 'Transfer Owner rights' : 'Перенести права со старого ПК'}
            </button>
          ) : (
            <span />
          )}
          <button
            type="button"
            onClick={onClaimOwner}
            disabled={claimingOwner}
            className={primaryBtnClass}
          >
            <span>{claimingOwner ? '...' : isEn ? 'Next' : 'Далее'}</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    ) : (
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-amber-500 flex items-center gap-1.5">
            <Smartphone className="w-4 h-4" />
            <span>{isEn ? 'Transfer Owner Authority' : 'Перенос прав Владельца'}</span>
          </span>
          <button
            type="button"
            onClick={() => setRecoverMode(false)}
            className="text-xs font-mono text-[var(--text-secondary)] cursor-pointer"
          >
            ← {isEn ? 'Back' : 'Назад'}
          </button>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] p-3 space-y-2">
            <div className="flex items-center justify-between gap-1.5 text-xs font-medium">
              <span>1. Telegram Code</span>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={onRequestTelegramRecoveryCode}
                  disabled={sendingTelegramCode}
                  className="px-2 py-0.5 rounded bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-500/40 text-[10px] font-mono text-emerald-500 cursor-pointer disabled:opacity-50"
                >
                  {sendingTelegramCode ? '...' : isEn ? 'Send Code' : 'Отправить код'}
                </button>
                {telegramRecoverLink && (
                  <a
                    href={telegramRecoverLink}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[10px] font-mono text-emerald-500 inline-flex items-center gap-1 hover:underline"
                  >
                    <span>Bot</span>
                    <ExternalLink className="w-3 h-3" />
                  </a>
                )}
              </div>
            </div>
            <input
              type="text"
              value={telegramCodeInput}
              onChange={(e) => setTelegramCodeInput(e.target.value)}
              placeholder="6-digit code"
              className={inputClass}
            />
          </div>

          <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] p-3 space-y-2">
            <div className="flex items-center justify-between gap-1.5 text-xs font-medium">
              <span>2. Recovery Key</span>
              <button
                type="button"
                onClick={async () => {
                  try {
                    const txt = await navigator.clipboard.readText();
                    if (txt) setRecoveryKeyInput(txt.trim());
                  } catch {
                    // ignore
                  }
                }}
                className="px-2 py-0.5 rounded border border-[var(--border-subtle)] hover:border-[var(--border-hover)] text-[10px] font-mono text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer"
              >
                {isEn ? 'Paste' : 'Вставить'}
              </button>
            </div>
            <input
              type="text"
              value={recoveryKeyInput}
              onChange={(e) => setRecoveryKeyInput(e.target.value)}
              placeholder="crk_owner_..."
              className={inputClass}
            />
          </div>
        </div>

        {recoveryStatusMsg && (
          <div className="text-xs font-mono text-amber-500 bg-amber-500/10 border border-amber-500/20 rounded px-2.5 py-1.5">
            {recoveryStatusMsg}
          </div>
        )}

        <div className="flex justify-end">
          <button
            type="button"
            onClick={onVerifyOwnerRecovery}
            disabled={claimingOwner}
            className={primaryBtnClass}
          >
            {isEn ? 'Bind This PC →' : 'Привязать этот ПК →'}
          </button>
        </div>
      </div>
    )}
  </div>
);

export interface WelcomeGithubStepProps {
  isEn: boolean;
  githubRepoUrl: string;
  setGithubRepoUrl: (v: string) => void;
  githubPat: string;
  setGithubPat: (v: string) => void;
  githubBranch: string;
  setGithubBranch: (v: string) => void;
  githubConnected: boolean;
  githubCommitShort: string;
  githubError: boolean;
  savingGithub: boolean;
  githubSavedMsg: string;
  onConnectGithub: (advance: boolean) => void;
  setWizardStep: (step: WizardStep) => void;
}

export const WelcomeGithubStep: React.FC<WelcomeGithubStepProps> = ({
  isEn,
  githubRepoUrl,
  setGithubRepoUrl,
  githubPat,
  setGithubPat,
  githubBranch,
  setGithubBranch,
  githubConnected,
  githubCommitShort,
  githubError,
  savingGithub,
  githubSavedMsg,
  onConnectGithub,
  setWizardStep,
}) => (
  <div className="space-y-3.5">
    <div className="flex items-center justify-between gap-2 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-main)] px-3 py-2 text-xs font-mono">
      <div className="flex items-center gap-2 min-w-0">
        <span
          className={`w-2 h-2 rounded-full shrink-0 ${
            githubError ? 'bg-rose-400' : githubConnected ? 'bg-emerald-400' : 'bg-amber-400'
          }`}
        />
        <span className="text-[var(--text-primary)] font-semibold truncate">
          {githubRepoUrl.trim() || (isEn ? 'Local Git SoT' : 'Локальный Git SoT')}
        </span>
      </div>
      <span className="px-2 py-0.5 rounded bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-[10px] text-[var(--text-secondary)] shrink-0">
        {githubBranch || 'main'}@{githubCommitShort || 'HEAD'}
      </span>
    </div>

    <div>
      <Lbl>{isEn ? 'GitHub Repository URL (SoT)' : 'Репозиторий GitHub (SoT)'}</Lbl>
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={githubRepoUrl}
          onChange={(e) => setGithubRepoUrl(e.target.value)}
          placeholder="https://github.com/org/netops-sot.git"
          className={inputClass}
        />
        <button
          type="button"
          onClick={() => onConnectGithub(false)}
          disabled={savingGithub}
          className="px-3 py-1.5 rounded-md bg-emerald-500 hover:bg-emerald-400 disabled:opacity-50 text-zinc-950 text-xs font-semibold shrink-0 cursor-pointer"
        >
          {savingGithub ? '...' : isEn ? 'Connect & Sync' : 'Синхронизировать'}
        </button>
      </div>
    </div>

    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
      <div>
        <Lbl>{isEn ? 'GitHub Token (PAT)' : 'Токен GitHub (PAT)'}</Lbl>
        <input
          type="password"
          value={githubPat}
          onChange={(e) => setGithubPat(e.target.value)}
          placeholder="ghp_..."
          className={inputClass}
        />
      </div>
      <div>
        <Lbl>{isEn ? 'Branch' : 'Ветка'}</Lbl>
        <input
          type="text"
          value={githubBranch}
          onChange={(e) => setGithubBranch(e.target.value)}
          placeholder="main"
          className={inputClass}
        />
      </div>
    </div>

    {githubSavedMsg && (
      <div
        className={`text-xs font-mono rounded px-3 py-1.5 border ${
          githubError
            ? 'text-rose-400 bg-rose-500/10 border-rose-500/30'
            : 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30'
        }`}
      >
        {githubSavedMsg}
      </div>
    )}

    <StepNav onBack={() => setWizardStep(1)} onNext={() => onConnectGithub(true)} disabled={savingGithub} />
  </div>
);

export interface WelcomeTelegramStepProps {
  isEn: boolean;
  telegramBotToken: string;
  setTelegramBotToken: (v: string) => void;
  savingTelegram: boolean;
  telegramLinked: boolean;
  telegramLinkedUser: string;
  telegramChatId: string;
  telegramBotUsername: string;
  telegramClaimLink: string;
  telegramSavedMsg: string;
  onSaveTelegram: (advance: boolean) => void;
  onUnlinkTelegram: () => void;
  setWizardStep: (step: WizardStep) => void;
}

export const WelcomeTelegramStep: React.FC<WelcomeTelegramStepProps> = ({
  isEn,
  telegramBotToken,
  setTelegramBotToken,
  savingTelegram,
  telegramLinked,
  telegramLinkedUser,
  telegramChatId,
  telegramBotUsername,
  telegramClaimLink,
  telegramSavedMsg,
  onSaveTelegram,
  onUnlinkTelegram,
  setWizardStep,
}) => (
  <div className="space-y-3.5">
    <div>
      <Lbl>Telegram Bot Token (@BotFather)</Lbl>
      <div className="flex items-center gap-2">
        <input
          type="password"
          value={telegramBotToken}
          onChange={(e) => setTelegramBotToken(e.target.value)}
          placeholder="8993240454:AAHnq-..."
          className={inputClass}
        />
        <button
          type="button"
          onClick={() => onSaveTelegram(false)}
          disabled={savingTelegram || !telegramBotToken.trim()}
          className="px-3 py-1.5 rounded-md bg-emerald-500 hover:bg-emerald-400 disabled:opacity-50 text-zinc-950 text-xs font-semibold shrink-0 cursor-pointer"
        >
          {savingTelegram ? '...' : isEn ? 'Connect' : 'Подключить'}
        </button>
      </div>
    </div>

    {telegramLinked ? (
      <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 p-3 flex items-center justify-between gap-2 text-xs font-mono text-emerald-500">
        <span className="flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>
            Owner: <strong>{telegramLinkedUser ? `@${telegramLinkedUser}` : 'owner'}</strong>
            {telegramChatId ? ` (${telegramChatId})` : ''}
          </span>
        </span>
        <span className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={onUnlinkTelegram}
            className="px-2 py-0.5 rounded border border-emerald-500/40 hover:bg-emerald-500/20 text-[10px] font-bold cursor-pointer"
          >
            {isEn ? 'Change' : 'Сменить'}
          </button>
          <span className="px-2 py-0.5 rounded bg-emerald-500 text-zinc-950 text-[10px] font-bold">OWNER</span>
        </span>
      </div>
    ) : (
      <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-main)] p-2.5 flex items-center justify-between gap-2 text-xs font-mono text-[var(--text-secondary)]">
        <span>
          {telegramBotUsername
            ? `${telegramBotUsername} · /start`
            : isEn
              ? 'Add a bot token to get the bot link'
              : 'Укажите токен бота — появится ссылка'}
        </span>
        {telegramClaimLink && (
          <a
            href={telegramClaimLink}
            target="_blank"
            rel="noreferrer"
            className="px-2.5 py-1 rounded bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-500/40 text-[11px] font-mono text-emerald-500 inline-flex items-center gap-1 shrink-0"
          >
            <span>{isEn ? 'Open Bot (/start)' : 'Открыть бота (/start)'}</span>
            <ExternalLink className="w-3 h-3" />
          </a>
        )}
      </div>
    )}

    {telegramSavedMsg && (
      <div className="text-xs font-mono text-emerald-500 bg-emerald-500/10 border border-emerald-500/20 rounded px-3 py-1.5">
        {telegramSavedMsg}
      </div>
    )}

    <StepNav onBack={() => setWizardStep(2)} onNext={() => onSaveTelegram(true)} disabled={savingTelegram} />
  </div>
);
