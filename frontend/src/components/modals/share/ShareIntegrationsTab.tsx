import React from 'react';
import { Database, GitBranch, RefreshCw, Send } from 'lucide-react';
import type { IntegrationsStatus } from '../../../api';

interface ShareIntegrationsTabProps {
  integrations: IntegrationsStatus | null;
  isAdmin?: boolean;
  gitRepoInput: string;
  setGitRepoInput: (v: string) => void;
  gitBranchInput: string;
  setGitBranchInput: (v: string) => void;
  gitPatInput: string;
  setGitPatInput: (v: string) => void;
  syncingGit: boolean;
  onGitSync: () => void;
  telegramNote: string;
  setTelegramNote: (v: string) => void;
  sendingTelegram: boolean;
  onSendTelegram: () => void;
  telegramPreview: string | null;
  ctrlClass: string;
  ghostBtnClass: string;
  primaryBtnClass: string;
}

export const ShareIntegrationsTab: React.FC<ShareIntegrationsTabProps> = ({
  integrations,
  isAdmin = false,
  gitRepoInput,
  setGitRepoInput,
  gitBranchInput,
  setGitBranchInput,
  gitPatInput,
  setGitPatInput,
  syncingGit,
  onGitSync,
  telegramNote,
  setTelegramNote,
  sendingTelegram,
  onSendTelegram,
  telegramPreview,
  ctrlClass,
  ghostBtnClass,
  primaryBtnClass,
}) => (
  <div className="space-y-3">
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      <div className="p-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elevated)]/40 flex flex-col justify-between gap-2.5">
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--text-primary)]">
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  integrations?.github_sot?.github_repo_url ? 'bg-emerald-400' : 'bg-cyan-400'
                }`}
              />
              <GitBranch className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
              <span>GitOps SoT</span>
            </div>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[var(--bg-main)] text-[var(--text-secondary)] border border-[var(--border-subtle)]">
              {integrations?.github_sot?.branch || 'main'} @ {integrations?.github_sot?.commit_short || 'HEAD'}
            </span>
          </div>
          {isAdmin ? (
            <div className="space-y-1.5">
              <input
                type="text"
                value={gitRepoInput}
                onChange={(e) => setGitRepoInput(e.target.value)}
                placeholder="https://github.com/org/netops-sot.git"
                className={`w-full px-2.5 h-7 text-[11px] ${ctrlClass}`}
              />
              <div className="grid grid-cols-2 gap-1.5">
                <input
                  type="text"
                  value={gitBranchInput}
                  onChange={(e) => setGitBranchInput(e.target.value)}
                  placeholder="main"
                  className={`w-full px-2.5 h-7 text-[11px] ${ctrlClass}`}
                />
                <input
                  type="password"
                  value={gitPatInput}
                  onChange={(e) => setGitPatInput(e.target.value)}
                  placeholder="PAT (ghp_...)"
                  className={`w-full px-2.5 h-7 text-[11px] ${ctrlClass}`}
                />
              </div>
            </div>
          ) : (
            <div className="text-[11px] font-mono text-[var(--text-secondary)] truncate">
              {integrations?.github_sot?.github_repo_url || 'Локальный репозиторий'}
            </div>
          )}
        </div>
        <button
          onClick={onGitSync}
          disabled={syncingGit}
          className={`w-full h-8 px-3 text-xs font-mono flex items-center justify-center gap-1.5 ${ghostBtnClass}`}
        >
          <RefreshCw className={`w-3.5 h-3.5 ${syncingGit ? 'animate-spin' : ''}`} />
          <span>{syncingGit ? 'Синхронизация...' : 'Git Pull + Sync'}</span>
        </button>
      </div>

      <div className="p-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elevated)]/40 flex flex-col justify-between gap-2.5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--text-primary)]">
            <Database className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
            <span>База данных</span>
          </div>
          <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[var(--bg-main)] text-[var(--text-secondary)] border border-[var(--border-subtle)] uppercase inline-flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
            <span>{integrations?.database?.engine || 'sqlite'}</span>
          </span>
        </div>
        <div className="grid grid-cols-3 gap-2 text-center font-mono">
          <div className="p-2 rounded-md bg-[var(--bg-main)] border border-[var(--border-subtle)]">
            <div className="text-sm font-bold text-[var(--text-primary)]">
              {integrations?.database?.counts?.devices ?? '—'}
            </div>
            <div className="text-[10px] text-[var(--text-muted)]">Устройств</div>
          </div>
          <div className="p-2 rounded-md bg-[var(--bg-main)] border border-[var(--border-subtle)]">
            <div className="text-sm font-bold text-[var(--text-primary)]">
              {integrations?.database?.counts?.users ?? '—'} / {integrations?.database?.counts?.bound_sessions ?? 0}
            </div>
            <div className="text-[10px] text-[var(--text-muted)]">Сессий</div>
          </div>
          <div className="p-2 rounded-md bg-[var(--bg-main)] border border-[var(--border-subtle)]">
            <div className="text-sm font-bold text-[var(--text-primary)]">
              {integrations?.database?.counts?.jobs ?? '—'}
            </div>
            <div className="text-[10px] text-[var(--text-muted)]">Задач</div>
          </div>
        </div>
      </div>
    </div>

    <div className="p-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elevated)]/40 flex items-center justify-between gap-3 font-mono">
      <div className="space-y-0.5 min-w-0">
        <div className="text-xs font-semibold text-[var(--text-primary)] flex items-center gap-1.5">
          <span
            className={`w-1.5 h-1.5 rounded-full ${
              integrations?.prometheus?.reachable ? 'bg-emerald-400' : 'bg-amber-400'
            }`}
          />
          <span>Телеметрия (Prometheus + VictoriaMetrics)</span>
        </div>
        <div className="text-[10px] text-[var(--text-muted)] truncate">
          {integrations?.prometheus?.url || 'http://prometheus:9090'} · HTTP SD (PostgreSQL) · Интервал{' '}
          {integrations?.prometheus?.collector_interval_sec ?? 10}s
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0 text-[10px]">
        <span className="px-2 py-1 rounded bg-[var(--bg-main)] border border-[var(--border-subtle)] text-[var(--text-secondary)]">
          Targets: {integrations?.prometheus?.up_targets ?? 0}/{integrations?.prometheus?.active_targets ?? 0} UP
        </span>
        <span className="px-2 py-1 rounded bg-[var(--bg-main)] border border-[var(--border-subtle)] text-[var(--text-secondary)]">
          Samples: {integrations?.prometheus?.stored_samples ?? 0}
        </span>
      </div>
    </div>

    <div className="p-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elevated)]/40 space-y-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--text-primary)]">
          <Send className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
          <span className="font-mono">{integrations?.telegram?.bot_username || '—'}</span>
        </div>
        <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[var(--bg-main)] text-[var(--text-secondary)] border border-[var(--border-subtle)] inline-flex items-center gap-1.5">
          <span
            className={`w-1.5 h-1.5 rounded-full ${
              integrations?.telegram?.bot_token_set ? 'bg-emerald-400' : 'bg-amber-400'
            }`}
          />
          <span>
            {integrations?.telegram?.bot_token_set
              ? `Чатов: ${integrations.telegram.chat_ids.length}`
              : 'Токен не задан'}
          </span>
        </span>
      </div>

      <div className="flex gap-2">
        <input
          type="text"
          value={telegramNote}
          onChange={(e) => setTelegramNote(e.target.value)}
          placeholder="Комментарий к сводке..."
          className={`flex-1 min-w-0 px-3 h-8 ${ctrlClass}`}
        />
        <button
          onClick={onSendTelegram}
          disabled={sendingTelegram}
          className={`h-8 px-3 text-xs flex items-center justify-center gap-1.5 shrink-0 ${primaryBtnClass}`}
        >
          <Send className="w-3.5 h-3.5" />
          <span>{sendingTelegram ? 'Отправка...' : 'Отправить'}</span>
        </button>
      </div>

      {telegramPreview && (
        <div className="p-2.5 rounded-md bg-[var(--bg-main)] border border-[var(--border-subtle)] text-[11px] font-mono text-[var(--text-secondary)] whitespace-pre-wrap max-h-32 overflow-y-auto">
          {telegramPreview.replace(/<\/?b>/g, '').replace(/<\/?code>/g, '')}
        </div>
      )}
    </div>
  </div>
);
