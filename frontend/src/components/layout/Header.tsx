import React, { useState, useEffect, useCallback } from 'react';
import {
  Network,
  Sparkles,
  Sun,
  Moon,
  Globe,
  Shield,
  Share2,
  SlidersHorizontal,
  BarChart3,
  Layers,
  GitBranch,
  RefreshCw,
} from 'lucide-react';
import { api, getErrorMessage, type GitStatusInfo, type UserRole } from '../../api';
import { translations, type Locale } from '../../i18n';

export type AppTheme = 'dark' | 'light';

interface HeaderProps {
  userRole: UserRole;
  apiHealthy: boolean;
  onToggleCopilot: () => void;
  copilotOpen: boolean;
  locale: Locale;
  setLocale: (l: Locale) => void;
  theme: AppTheme;
  setTheme: (t: AppTheme) => void;
  onOpenWelcomeModal?: () => void;
  onOpenShareModal?: () => void;
  activeStand?: string;
  standsCount?: number;
  onOpenStandsModal?: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  userRole,
  apiHealthy,
  onToggleCopilot,
  copilotOpen,
  locale,
  setLocale,
  theme,
  setTheme,
  onOpenWelcomeModal,
  onOpenShareModal,
  activeStand = 'all',
  standsCount = 1,
  onOpenStandsModal,
}) => {
  const t = translations[locale];
  const [gitStatus, setGitStatus] = useState<GitStatusInfo | null>(null);
  const [syncingGit, setSyncingGit] = useState(false);
  const [gitNote, setGitNote] = useState<{ text: string; ok: boolean } | null>(null);

  const loadGitStatus = useCallback(async () => {
    try {
      const st = await api.getGitStatus();
      setGitStatus(st);
    } catch {
      // Ignore if unauthenticated during bootstrap
    }
  }, []);

  useEffect(() => {
    if (apiHealthy) {
      void loadGitStatus();
    }
  }, [apiHealthy, loadGitStatus]);

  useEffect(() => {
    const onGitUpdated = () => {
      void loadGitStatus();
    };
    window.addEventListener('netops-git-updated', onGitUpdated);
    return () => window.removeEventListener('netops-git-updated', onGitUpdated);
  }, [loadGitStatus]);

  const handleGitSync = async () => {
    if (syncingGit || userRole === 'viewer') return;
    setSyncingGit(true);
    setGitNote(null);
    try {
      const res = await api.syncGitSot();
      if (res.git) setGitStatus(res.git);
      const created = res.sync?.created?.length ?? 0;
      const updated = res.sync?.updated?.length ?? 0;
      setGitNote({
        text: created || updated ? `+${created}/~${updated}` : 'Git OK',
        ok: true,
      });
    } catch (err: unknown) {
      setGitNote({ text: getErrorMessage(err, locale === 'en' ? 'Git Error' : 'Ошибка Git'), ok: false });
    } finally {
      setSyncingGit(false);
      window.setTimeout(() => setGitNote(null), 4000);
    }
  };

  const ghostBtn =
    'h-8 flex items-center gap-1.5 px-2.5 rounded-md border border-[var(--border-subtle)] hover:border-[var(--border-hover)] bg-[var(--bg-elevated)] hover:bg-[var(--bg-panel)] text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors cursor-pointer';

  const grafanaUrl =
    typeof window !== 'undefined'
      ? `${window.location.protocol}//${window.location.hostname}:3000/d/croc-dream-noc`
      : 'http://localhost:3000/d/croc-dream-noc';

  return (
    <header className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] sticky top-0 z-50 px-3 sm:px-5 h-14 flex items-center justify-between gap-2 transition-colors">
      {/* Brand & Connection Status (Geist / Linear Monochrome) */}
      <div className="flex items-center gap-2.5 min-w-0">
        <div className="h-7 w-7 rounded-md bg-[var(--bg-elevated)] border border-[var(--border-subtle)] flex items-center justify-center text-[var(--text-primary)] shrink-0">
          <Network className="h-3.5 w-3.5" />
        </div>
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs sm:text-[13px] font-semibold tracking-tight text-[var(--text-primary)] truncate">
            {t.appName}
          </span>
          <span
            className={`inline-flex items-center gap-1.5 px-1.5 py-0.5 rounded text-[10px] font-mono shrink-0 border ${
              apiHealthy
                ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20'
                : 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20'
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                apiHealthy ? 'bg-emerald-400' : 'bg-rose-400'
              }`}
            />
            <span className="hidden sm:inline">{apiHealthy ? t.online : t.offline}</span>
          </span>
          {onOpenStandsModal && (
            <button
              type="button"
              onClick={onOpenStandsModal}
              data-testid="header-stands-selector"
              className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-mono shrink-0 border transition-colors cursor-pointer ${
                activeStand !== 'all'
                  ? 'bg-[var(--bg-panel)] text-[var(--text-primary)] border-[var(--border-strong)]'
                  : 'bg-[var(--bg-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] border-[var(--border-subtle)] hover:border-[var(--border-hover)]'
              }`}
              title={t.switchStandTooltip}
            >
              <Layers className="w-3 h-3 text-[var(--text-secondary)]" />
              <span className="truncate max-w-[140px]">
                {activeStand === 'all'
                  ? `${t.standsLabel} (${standsCount})`
                  : activeStand}
              </span>
            </button>
          )}
        </div>
      </div>

      {/* Right Controls: Unified Monochrome Action Strip */}
      <div className="flex items-center gap-1.5 shrink-0">
        {/* Git SoT Status & 1-Click Sync */}
        {userRole !== 'viewer' ? (
          <button
            type="button"
            onClick={handleGitSync}
            disabled={syncingGit}
            data-testid="header-git-sync-btn"
            className={`${ghostBtn} font-mono ${
              gitNote
                ? gitNote.ok
                  ? 'border-emerald-500/40 text-emerald-600 dark:text-emerald-400'
                  : 'border-rose-500/40 text-rose-600 dark:text-rose-400'
                : ''
            }`}
            title={
              gitStatus?.commit_short
                ? `Git SoT: ${gitStatus.branch || 'main'}@${gitStatus.commit_short} — ${t.syncGitSotTooltip}`
                : t.syncGitSotTooltip
            }
          >
            {syncingGit ? (
              <RefreshCw className="w-3.5 h-3.5 text-[var(--text-secondary)] animate-spin" />
            ) : (
              <GitBranch className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
            )}
            <span className="hidden md:inline text-[11px]">
              {syncingGit
                ? 'Sync…'
                : gitNote
                  ? gitNote.text
                  : gitStatus?.commit_short
                    ? `${gitStatus.branch || 'main'}@${gitStatus.commit_short}`
                    : 'Git Sync'}
            </span>
          </button>
        ) : (
          gitStatus?.commit_short && (
            <span
              className="hidden md:inline-flex h-8 items-center gap-1.5 px-2.5 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elevated)] text-[11px] font-mono text-[var(--text-muted)]"
              title={`Git SoT: ${gitStatus.branch || 'main'}@${gitStatus.commit_short}`}
            >
              <GitBranch className="w-3 h-3 text-[var(--text-secondary)]" />
              <span>{gitStatus.commit_short}</span>
            </span>
          )
        )}

        {/* Grafana Enterprise NOC Wallboard Link */}
        <a
          href={grafanaUrl}
          target="_blank"
          rel="noopener noreferrer"
          className={`${ghostBtn} hidden sm:flex`}
          title={t.openGrafanaTooltip}
        >
          <BarChart3 className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
          <span className="hidden md:inline text-[11px] font-medium">Grafana</span>
        </a>

        {/* AI Copilot Button */}
        <button
          onClick={onToggleCopilot}
          className={`h-8 flex items-center gap-1.5 px-2.5 rounded-md text-xs font-medium cursor-pointer transition-colors border ${
            copilotOpen
              ? 'bg-[var(--text-primary)] text-[var(--bg-base)] border-[var(--text-primary)]'
              : 'bg-[var(--bg-elevated)] hover:bg-[var(--bg-panel)] text-[var(--text-primary)] border-[var(--border-subtle)] hover:border-[var(--border-hover)]'
          }`}
          title={t.aiAssistant}
        >
          <Sparkles className="w-3.5 h-3.5" />
          <span className="tracking-tight hidden sm:inline">{t.aiCopilot}</span>
          <span
            className={`text-[9px] font-mono uppercase px-1 py-0.2 rounded border ${
              copilotOpen
                ? 'bg-black/15 border-black/20 text-current'
                : 'bg-[var(--bg-surface)] text-[var(--text-secondary)] border-[var(--border-subtle)]'
            }`}
          >
            AI
          </span>
        </button>

        {/* Share Button (Owner & Admin only) */}
        {onOpenShareModal && (userRole === 'owner' || userRole === 'admin') && (
          <button
            onClick={onOpenShareModal}
            className={ghostBtn}
            title={t.shareTooltip}
          >
            <Share2 className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
            <span className="hidden sm:inline text-[11px] font-medium">
              {t.shareBtn}
            </span>
          </button>
        )}

        {/* Theme Switcher */}
        <button
          onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          className={`${ghostBtn} hidden sm:flex`}
          title={theme === 'dark' ? t.themeLight : t.themeDark}
        >
          {theme === 'dark' ? (
            <>
              <Sun className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
              <span className="hidden md:inline text-[11px] font-medium">{t.themeLight}</span>
            </>
          ) : (
            <>
              <Moon className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
              <span className="hidden md:inline text-[11px] font-medium">{t.themeDark}</span>
            </>
          )}
        </button>

        {/* Language Switcher (RU / EN) */}
        <button
          onClick={() => setLocale(locale === 'ru' ? 'en' : 'ru')}
          className={`${ghostBtn} font-mono hidden sm:flex`}
          title={t.langSwitch}
        >
          <Globe className="w-3.5 h-3.5 text-[var(--text-muted)]" />
          <span className="font-medium uppercase tracking-wider text-[11px]">
            {locale === 'ru' ? 'RU' : 'EN'}
          </span>
        </button>

        {/* Cluster Settings (Owner) or Read-Only Role Badge (Admin / Operator / Viewer) */}
        {userRole === 'owner' && onOpenWelcomeModal ? (
          <button
            onClick={onOpenWelcomeModal}
            className={`${ghostBtn} font-mono`}
            title={t.openWelcome}
          >
            <SlidersHorizontal className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
            <span className="hidden md:inline text-[11px] text-[var(--text-secondary)]">
              {t.setupPrefix}
            </span>
            <Shield className="w-3 h-3 text-[var(--text-secondary)]" />
            <span className="capitalize font-medium text-[var(--text-primary)] hidden xs:inline">
              {userRole}
            </span>
          </button>
        ) : (
          <div
            className="h-8 flex items-center gap-1.5 px-2.5 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elevated)] text-xs font-mono select-none"
            title={`RBAC Role: ${userRole.toUpperCase()}`}
          >
            <Shield className="w-3 h-3 text-[var(--text-secondary)]" />
            <span className="capitalize font-medium text-[var(--text-primary)]">
              {userRole === 'viewer' ? t.juryReadOnly : userRole}
            </span>
          </div>
        )}
      </div>
    </header>
  );
};
