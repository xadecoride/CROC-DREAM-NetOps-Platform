import React from 'react';
import { AlertTriangle, History, RefreshCw, User } from 'lucide-react';
import type { EmergencyAuditLog } from '../../api';

interface EmergencyAuditSectionProps {
  auditLogs: EmergencyAuditLog[];
  isLoadingAudit: boolean;
  onRefreshAudit: () => void;
  resetModal: 'soft' | 'hard' | null;
  setResetModal: (v: 'soft' | 'hard' | null) => void;
  devicesCount: number;
  onSoftReset: () => void;
  onHardReset: () => void;
}

export const EmergencyAuditSection: React.FC<EmergencyAuditSectionProps> = ({
  auditLogs,
  isLoadingAudit,
  onRefreshAudit,
  resetModal,
  setResetModal,
  devicesCount,
  onSoftReset,
  onHardReset,
}) => (
  <>
    <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-card)] p-4 sm:p-5 space-y-4 shadow-sm">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="p-2 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-400">
            <History className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-[var(--text-primary)]">
              Журнал аудита аварийных вмешательств (Audit Trail)
            </h3>
            <p className="text-xs text-[var(--text-muted)]">
              Фиксация низкоуровневых команд, хот-патчей и сбросов администратора с таймстемпом
            </p>
          </div>
        </div>
        <button
          onClick={onRefreshAudit}
          disabled={isLoadingAudit}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-[var(--text-primary)] bg-[var(--bg-elevated)] border border-[var(--border-subtle)] hover:border-[var(--border-hover)] transition-all cursor-pointer disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoadingAudit ? 'animate-spin' : ''}`} />
          Обновить
        </button>
      </div>

      {auditLogs.length === 0 ? (
        <div className="py-8 text-center text-xs text-[var(--text-muted)]">
          Журнал пуст. Экстренные действия еще не фиксировались.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-[var(--border-subtle)] text-[var(--text-muted)]">
                <th className="pb-2.5 font-medium">Время (UTC)</th>
                <th className="pb-2.5 font-medium">Пользователь</th>
                <th className="pb-2.5 font-medium">Узел</th>
                <th className="pb-2.5 font-medium">Действие</th>
                <th className="pb-2.5 font-medium">Команда / Детали</th>
                <th className="pb-2.5 font-medium">Причина</th>
                <th className="pb-2.5 font-medium text-right">Статус</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border-subtle)]">
              {auditLogs.map((entry, idx) => (
                <tr key={idx} className="hover:bg-[var(--bg-elevated)]/40 transition-colors">
                  <td className="py-2.5 font-mono text-[var(--text-muted)] whitespace-nowrap">
                    {new Date(entry.timestamp).toLocaleTimeString([], {
                      hour: '2-digit',
                      minute: '2-digit',
                      second: '2-digit',
                    })}
                  </td>
                  <td className="py-2.5 whitespace-nowrap">
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md font-mono text-[10px] bg-amber-500/10 border border-amber-500/20 text-amber-300">
                      <User className="w-3 h-3" />
                      {entry.user}
                    </span>
                  </td>
                  <td className="py-2.5 font-mono text-[var(--text-secondary)] whitespace-nowrap">{entry.device}</td>
                  <td className="py-2.5 whitespace-nowrap">
                    <span
                      className={`inline-block px-2 py-0.5 rounded-md font-mono text-[10px] uppercase font-semibold ${
                        entry.action === 'cli_command'
                          ? 'bg-sky-500/10 border border-sky-500/20 text-sky-300'
                          : entry.action === 'emergency_patch'
                          ? 'bg-purple-500/10 border border-purple-500/20 text-purple-300'
                          : 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-300'
                      }`}
                    >
                      {entry.action}
                    </span>
                  </td>
                  <td className="py-2.5 font-mono text-[var(--text-primary)] max-w-xs truncate" title={entry.payload}>
                    {entry.payload}
                  </td>
                  <td className="py-2.5 text-[var(--text-muted)] max-w-xs truncate" title={entry.reason}>
                    {entry.reason}
                  </td>
                  <td className="py-2.5 text-right whitespace-nowrap">
                    <span
                      className={`inline-block px-2 py-0.5 rounded-md font-mono text-[10px] font-semibold ${
                        entry.success
                          ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                          : 'bg-red-500/10 border border-red-500/20 text-red-400'
                      }`}
                    >
                      {entry.success ? 'УСПЕШНО' : 'СБОЙ'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>

    {resetModal && (
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-xs animate-fade-in">
        <div className="w-full max-w-md p-6 rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-card)] shadow-2xl text-[var(--text-primary)] space-y-4">
          <div className="flex items-center gap-3">
            <div
              className={`p-2.5 rounded-xl ${
                resetModal === 'soft'
                  ? 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-400'
                  : 'bg-red-500/10 border border-red-500/30 text-red-400'
              }`}
            >
              <AlertTriangle className="w-6 h-6" />
            </div>
            <div>
              <h3 className="text-base font-bold text-white">
                {resetModal === 'soft' ? 'Подтверждение Мягкого сброса' : 'Подтверждение Хард-рестарта Clab'}
              </h3>
              <p className="text-xs text-[var(--text-muted)]">
                {resetModal === 'soft'
                  ? 'Перенакат золотого эталона Git SoT'
                  : `Передеплой всех узлов (${devicesCount}) на стенде`}
              </p>
            </div>
          </div>

          <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
            {resetModal === 'soft' ? (
              <>
                Будет выполнен прогон NetOps pipeline по всем узлам фабрики ({devicesCount}). Все несанкционированные
                команды будут устранены, а недостающие маршруты и интерфейсы восстановлены.
              </>
            ) : (
              <>
                Внимание: будет выполнен полный передеплой топологии стенда к базовому состоянию. Сетевые сессии
                перезапустятся.
              </>
            )}
          </p>

          <div className="flex items-center justify-end gap-2.5 pt-2">
            <button
              onClick={() => setResetModal(null)}
              className="px-4 py-2 rounded-xl text-xs font-medium text-[var(--text-muted)] hover:text-white bg-[var(--bg-elevated)] hover:border-[var(--border-hover)] border border-[var(--border-subtle)] transition-all cursor-pointer"
            >
              Отмена
            </button>
            <button
              onClick={resetModal === 'soft' ? onSoftReset : onHardReset}
              className={`px-5 py-2 rounded-xl text-xs font-semibold text-black transition-all cursor-pointer ${
                resetModal === 'soft'
                  ? 'bg-emerald-400 hover:bg-emerald-300 shadow-lg shadow-emerald-500/20'
                  : 'bg-red-500 hover:bg-red-400 shadow-lg shadow-red-500/20 text-white'
              }`}
            >
              {resetModal === 'soft' ? 'Выполнить Soft Reset' : 'Выполнить Hard Reset'}
            </button>
          </div>
        </div>
      </div>
    )}
  </>
);
