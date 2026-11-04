import React, { useState } from 'react';
import {
  LayoutDashboard,
  Layers,
  Activity,
  Terminal,
  MoreHorizontal,
  ShieldCheck,
  FlaskConical,
  AlertTriangle,
  Sparkles,
  Search,
  Sun,
  Moon,
  Globe,
  X,
  Share2,
} from 'lucide-react';
import { translations, type Locale } from '../../i18n';
import type { AppTheme } from './Header';
import type { UserRole } from '../../api';

interface MobileNavProps {
  activeTab: string;
  setActiveTab: (tab: string) => void;
  locale?: Locale;
  theme: AppTheme;
  setTheme: (t: AppTheme) => void;
  userRole: UserRole;
  setLocale: (l: Locale) => void;
  onToggleCopilot: () => void;
  onOpenSearch: () => void;
  onOpenShare?: () => void;
}

export const MobileNav: React.FC<MobileNavProps> = ({
  activeTab,
  setActiveTab,
  locale = 'ru',
  theme,
  setTheme,
  userRole,
  setLocale,
  onToggleCopilot,
  onOpenSearch,
  onOpenShare,
}) => {
  const t = translations[locale];
  const [moreMenuOpen, setMoreMenuOpen] = useState(false);

  const mainTabs = [
    { id: 'dashboard', label: t.tabDashboard, icon: LayoutDashboard },
    { id: 'devices', label: t.tabDevices, icon: Layers },
    { id: 'jobs', label: t.tabJobs, icon: Activity },
    { id: 'diff', label: 'Diff & Guard', icon: Terminal },
  ];

  const handleSelectTab = (id: string) => {
    setActiveTab(id);
    setMoreMenuOpen(false);
  };

  return (
    <>
      {/* Mobile Bottom Navigation Bar */}
      <nav className="lg:hidden fixed bottom-0 inset-x-0 z-40 bg-[var(--bg-card)]/95 backdrop-blur-xl border-t border-[var(--border-subtle)] pb-[env(safe-area-inset-bottom,0.5rem)] pt-1.5 px-2 shadow-2xl transition-colors">
        <div className="flex items-center justify-around max-w-md mx-auto">
          {mainTabs.map(({ id, label, icon: Icon }) => {
            const isActive = activeTab === id;
            return (
              <button
                key={id}
                onClick={() => handleSelectTab(id)}
                className={`flex flex-col items-center justify-center flex-1 py-1.5 px-1 min-h-[48px] rounded-xl transition-all cursor-pointer ${
                  isActive
                    ? 'text-cyan-600 dark:text-cyan-400 font-semibold'
                    : 'text-slate-500 dark:text-zinc-400 hover:text-slate-800 dark:hover:text-zinc-200'
                }`}
              >
                <div className="relative">
                  <Icon className={`w-5 h-5 transition-transform ${isActive ? 'scale-110' : ''}`} />
                  {isActive && (
                    <span className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-1.5 h-1.5 rounded-full bg-cyan-500 shadow-sm" />
                  )}
                </div>
                <span className="text-[10px] mt-1 tracking-tight truncate max-w-[68px]">
                  {label}
                </span>
              </button>
            );
          })}

          {/* More Menu Trigger */}
          <button
            onClick={() => setMoreMenuOpen(true)}
            className={`flex flex-col items-center justify-center flex-1 py-1.5 px-1 min-h-[48px] rounded-xl transition-all cursor-pointer ${
              ['drift', 'lab', 'emergency'].includes(activeTab) || moreMenuOpen
                ? 'text-cyan-600 dark:text-cyan-400 font-semibold'
                : 'text-slate-500 dark:text-zinc-400 hover:text-slate-800 dark:hover:text-zinc-200'
            }`}
          >
            <MoreHorizontal className="w-5 h-5" />
            <span className="text-[10px] mt-1 tracking-tight">
              {locale === 'en' ? 'More' : 'Ещё'}
            </span>
          </button>
        </div>
      </nav>

      {/* More Options Mobile Bottom Sheet */}
      {moreMenuOpen && (
        <div className="lg:hidden fixed inset-0 z-50 flex flex-col justify-end">
          {/* Backdrop */}
          <div
            onClick={() => setMoreMenuOpen(false)}
            className="absolute inset-0 bg-black/60 backdrop-blur-sm animate-fade-in"
          />

          {/* Sheet Container */}
          <div className="relative w-full max-h-[85vh] overflow-y-auto bg-[var(--bg-card)] border-t border-[var(--border-subtle)] rounded-t-3xl p-5 shadow-2xl pb-[calc(env(safe-area-inset-bottom,1rem)+1.5rem)] text-[var(--text-primary)] animate-slide-up">
            {/* Drag Handle */}
            <div className="w-12 h-1 bg-slate-300 dark:bg-zinc-700 rounded-full mx-auto mb-4" />

            <div className="flex items-center justify-between pb-3 border-b border-[var(--border-subtle)]">
              <div>
                <h3 className="text-base font-bold tracking-tight">
                  {locale === 'en' ? 'Quick Navigation & Tools' : 'Навигация и инструменты'}
                </h3>
                <p className="text-xs text-[var(--text-muted)]">CROC NetOps Cockpit Mobile</p>
              </div>
              <button
                onClick={() => setMoreMenuOpen(false)}
                className="p-1.5 rounded-lg bg-black/5 dark:bg-white/[0.05] text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Quick Actions Grid */}
            <div className="mt-4 grid grid-cols-2 gap-2.5">
              <button
                onClick={() => {
                  setMoreMenuOpen(false);
                  onToggleCopilot();
                }}
                className="flex items-center gap-2.5 p-3 rounded-xl border border-cyan-500/30 bg-cyan-500/10 text-cyan-700 dark:text-cyan-300 font-medium text-xs hover:bg-cyan-500/20 cursor-pointer"
              >
                <Sparkles className="w-4 h-4 text-cyan-500" />
                <span>AI Copilot (ИИ)</span>
              </button>

              <button
                onClick={() => {
                  setMoreMenuOpen(false);
                  onOpenSearch();
                }}
                className="flex items-center gap-2.5 p-3 rounded-xl border border-[var(--border-subtle)] bg-black/5 dark:bg-white/[0.04] text-[var(--text-primary)] font-medium text-xs hover:bg-black/10 dark:hover:bg-white/[0.08] cursor-pointer"
              >
                <Search className="w-4 h-4 text-slate-500 dark:text-zinc-400" />
                <span>{locale === 'en' ? 'Quick Search' : 'Поиск Ctrl+K'}</span>
              </button>
            </div>

            {/* Extended Sections */}
            <div className="mt-4 space-y-1">
              <div className="px-2 py-1 text-[11px] font-mono uppercase text-[var(--text-muted)] font-semibold">
                {locale === 'en' ? 'Platform Modules' : 'Модули платформы'}
              </div>

              <button
                onClick={() => handleSelectTab('drift')}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs cursor-pointer transition-colors ${
                  activeTab === 'drift'
                    ? 'bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 font-semibold'
                    : 'text-[var(--text-primary)] hover:bg-black/5 dark:hover:bg-white/[0.04]'
                }`}
              >
                <ShieldCheck className="w-4 h-4 text-emerald-500" />
                <span>{t.tabDrift}</span>
              </button>

              {userRole !== 'viewer' && (
                <button
                  onClick={() => handleSelectTab('lab')}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs cursor-pointer transition-colors ${
                    activeTab === 'lab'
                      ? 'bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 font-semibold'
                      : 'text-[var(--text-primary)] hover:bg-black/5 dark:hover:bg-white/[0.04]'
                  }`}
                >
                  <FlaskConical className="w-4 h-4 text-amber-500" />
                  <span>{t.tabLab}</span>
                </button>
              )}

              {(userRole === 'admin' || userRole === 'owner') && (
                <button
                  onClick={() => handleSelectTab('emergency')}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs cursor-pointer transition-colors ${
                    activeTab === 'emergency'
                      ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400 font-semibold'
                      : 'text-[var(--text-primary)] hover:bg-black/5 dark:hover:bg-white/[0.04]'
                  }`}
                >
                  <AlertTriangle className="w-4 h-4 text-rose-500" />
                  <span>{locale === 'en' ? 'Emergency Hub' : 'Аварийный хаб'}</span>
                </button>
              )}
            </div>

            {/* Mobile Controls & Preferences */}
            <div className="mt-5 pt-4 border-t border-[var(--border-subtle)] space-y-3">
              <div className="px-2 text-[11px] font-mono uppercase text-[var(--text-muted)] font-semibold">
                {locale === 'en' ? 'Settings & Session' : 'Настройки и профиль'}
              </div>

              <div className="flex items-center justify-between px-2">
                <span className="text-xs font-medium text-[var(--text-secondary)]">
                  {locale === 'en' ? 'Appearance' : 'Тема оформления'}
                </span>
                <button
                  onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-black/5 dark:bg-white/[0.05] border border-[var(--border-subtle)] text-xs font-medium cursor-pointer"
                >
                  {theme === 'dark' ? (
                    <>
                      <Sun className="w-3.5 h-3.5 text-amber-400" />
                      <span>{t.themeLight}</span>
                    </>
                  ) : (
                    <>
                      <Moon className="w-3.5 h-3.5 text-indigo-500" />
                      <span>{t.themeDark}</span>
                    </>
                  )}
                </button>
              </div>

              <div className="flex items-center justify-between px-2">
                <span className="text-xs font-medium text-[var(--text-secondary)]">
                  {locale === 'en' ? 'Interface Language' : 'Язык системы'}
                </span>
                <button
                  onClick={() => setLocale(locale === 'ru' ? 'en' : 'ru')}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-black/5 dark:bg-white/[0.05] border border-[var(--border-subtle)] text-xs font-mono font-semibold cursor-pointer"
                >
                  <Globe className="w-3.5 h-3.5 text-cyan-500" />
                  <span>{locale === 'ru' ? 'РУССКИЙ (RU)' : 'ENGLISH (EN)'}</span>
                </button>
              </div>

              <div className="flex items-center justify-between px-2">
                <span className="text-xs font-medium text-[var(--text-secondary)]">
                  {locale === 'en' ? 'Security Role (RBAC)' : 'Роль доступа (RBAC)'}
                </span>
                <span className="px-2.5 py-1 rounded-lg text-[11px] font-mono uppercase bg-cyan-500/15 text-cyan-400 border border-cyan-500/30 font-semibold">
                  {userRole}
                </span>
              </div>

              {onOpenShare && (userRole === 'owner' || userRole === 'admin') && (
                <button
                  onClick={() => {
                    setMoreMenuOpen(false);
                    onOpenShare();
                  }}
                  className="w-full flex items-center justify-center gap-2 mt-2 py-2.5 rounded-xl border border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 font-medium text-xs cursor-pointer"
                >
                  <Share2 className="w-4 h-4" />
                  <span>{locale === 'en' ? 'Share Live Access' : 'Поделиться доступом (Жюри / Команда)'}</span>
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
};
