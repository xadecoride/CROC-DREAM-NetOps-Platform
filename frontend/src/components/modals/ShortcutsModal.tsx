import React from 'react';
import { X, Command, Shield } from 'lucide-react';
import { type Locale } from '../../i18n';

interface ShortcutsModalProps {
  isOpen: boolean;
  onClose: () => void;
  locale?: Locale;
}

export const ShortcutsModal: React.FC<ShortcutsModalProps> = ({ isOpen, onClose, locale = 'ru' }) => {
  if (!isOpen) return null;

  const isEn = locale === 'en';

  const shortcuts = [
    {
      keys: ['Ctrl', 'K'],
      label: isEn ? 'Quick Search & Command Palette' : 'Быстрый поиск и палитра команд',
      desc: isEn ? 'Instantly navigate across views and factory nodes' : 'Мгновенный переход к экранам и узлам фабрики',
    },
    {
      keys: ['Esc'],
      label: isEn ? 'Close Active Overlay / Modal' : 'Закрыть активное окно / панель',
      desc: isEn ? 'Strict stack priority: Dry-Run → Palette → Copilot' : 'Приоритетный порядок: Dry-Run → Палитра → Copilot',
    },
    {
      keys: ['Enter'],
      label: isEn ? 'Execute Dry-Run Simulation' : 'Запуск симуляции Dry-Run',
      desc: isEn ? 'Trigger preflight when Dry-Run dialog is active' : 'Мгновенный запуск при открытом окне Dry-Run',
    },
    {
      keys: ['?'],
      label: isEn ? 'Keyboard Shortcuts Cheatsheet' : 'Справка по горячим клавишам',
      desc: isEn ? 'Toggle this quick help dialog anytime' : 'Открыть / закрыть это справочное окно',
    },
  ];

  const roles = [
    {
      role: 'Admin',
      badge: 'bg-rose-500/15 text-rose-400 border-rose-500/30',
      desc: isEn ? 'Full control: node promotion, inventory CRUD, hard reset' : 'Полный доступ: перевод в Managed, CRUD узлов, hard reset',
    },
    {
      role: 'Operator',
      badge: 'bg-cyan-500/15 text-cyan-400 border-cyan-500/30',
      desc: isEn ? 'Operations: Dry-Run, Deploy, Drift scan, Live SSH CLI' : 'Эксплуатация: Dry-Run, деплой, скан/фикс дрейфа, живой SSH',
    },
    {
      role: 'Viewer',
      badge: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
      desc: isEn ? 'Read-only: topology, Monaco diff, telemetry' : 'Аудит и наблюдение: топология, Monaco Diff, телеметрия',
    },
  ];

  return (
    <div
      className="fixed inset-0 z-[70] bg-black/70 backdrop-blur-xs flex items-center justify-center p-4 animate-fade-in"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-2xl bg-[#0c0e14] border border-white/[0.1] shadow-2xl p-6 text-zinc-200 space-y-6"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Titlebar */}
        <div className="flex items-center justify-between border-b border-white/[0.08] pb-4">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-xl bg-white/[0.04] border border-white/[0.08] text-cyan-400">
              <Command className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-base font-bold text-white tracking-tight">
                {isEn ? 'Keyboard Shortcuts & Roles' : 'Горячие клавиши и роли платформы'}
              </h2>
              <p className="text-[11px] text-zinc-400 font-mono">
                CROC DREAM NetOps Cockpit
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-zinc-400 hover:text-white hover:bg-white/[0.06] transition-colors cursor-pointer"
            title="Закрыть (Esc)"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Shortcuts List */}
        <div className="space-y-3">
          <div className="text-xs font-semibold text-zinc-400 uppercase tracking-wider font-mono">
            {isEn ? 'Global Shortcuts' : 'Сочетания клавиш'}
          </div>
          <div className="grid gap-2">
            {shortcuts.map((s, idx) => (
              <div
                key={idx}
                className="flex items-center justify-between p-2.5 rounded-xl bg-white/[0.02] border border-white/[0.06]"
              >
                <div>
                  <div className="text-xs font-medium text-white">{s.label}</div>
                  <div className="text-[11px] text-zinc-500">{s.desc}</div>
                </div>
                <div className="flex items-center gap-1">
                  {s.keys.map((k, kidx) => (
                    <kbd
                      key={kidx}
                      className="px-2 py-0.5 rounded text-[11px] font-mono font-semibold bg-white/[0.08] border border-white/[0.12] text-zinc-200 shadow-2xs"
                    >
                      {k}
                    </kbd>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Roles Capabilities */}
        <div className="space-y-3 pt-2 border-t border-white/[0.06]">
          <div className="text-xs font-semibold text-zinc-400 uppercase tracking-wider font-mono flex items-center gap-1.5">
            <Shield className="w-3.5 h-3.5 text-indigo-400" />
            <span>{isEn ? 'Role Permissions (RBAC)' : 'Матрица прав доступа (RBAC)'}</span>
          </div>
          <div className="space-y-1.5">
            {roles.map((r, idx) => (
              <div
                key={idx}
                className="flex items-center justify-between p-2 rounded-lg bg-white/[0.02] border border-white/[0.04] text-xs"
              >
                <span className={`px-2 py-0.5 rounded font-mono font-medium text-[11px] border ${r.badge}`}>
                  {r.role}
                </span>
                <span className="text-[11px] text-zinc-400 text-right max-w-[320px]">
                  {r.desc}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Footer Hint */}
        <div className="pt-2 border-t border-white/[0.06] flex items-center justify-between text-[11px] font-mono text-zinc-500">
          <span>{isEn ? 'Press Esc to dismiss' : 'Нажмите Esc для закрытия'}</span>
          <span className="text-emerald-400 font-medium">100% Zero-Latency NetOps</span>
        </div>
      </div>
    </div>
  );
};
