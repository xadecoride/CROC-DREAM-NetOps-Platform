import React, { useState } from 'react';
import { KeyRound, ShieldCheck, ArrowRight, AlertCircle } from 'lucide-react';
import { api, getErrorMessage, type UserRole } from '../../api';
import type { Locale } from '../../i18n';

interface AccessCodeModalProps {
  isOpen: boolean;
  onSuccess: (role: UserRole) => void;
  locale?: Locale;
}

export const AccessCodeModal: React.FC<AccessCodeModalProps> = ({
  isOpen,
  onSuccess,
  locale = 'ru',
}) => {
  const [code, setCode] = useState('');
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const isEn = locale === 'en';

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleaned = code.trim();
    if (!cleaned) {
      setErrorMsg(isEn ? 'Enter your 6-digit code or personal token' : 'Введите 6-значный код или персональный токен');
      return;
    }
    setLoading(true);
    setErrorMsg(null);
    try {
      const res = await api.login(cleaned);
      localStorage.setItem('netops_user_role', res.role);
      localStorage.setItem('netops_onboarding_done', 'true');
      try {
        sessionStorage.setItem('netops_invite_session', '1');
      } catch {
        // ignore
      }
      onSuccess(res.role);
    } catch (err: unknown) {
      setErrorMsg(
        getErrorMessage(
          err,
          isEn
            ? 'Invalid, expired, or already device-bound access code'
            : 'Неверный, истёкший или уже привязанный к другому устройству код доступа'
        )
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4 bg-[var(--bg-main)]/95 backdrop-blur-xl">
      <div className="w-full max-w-md rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-card)] shadow-2xl p-5 space-y-4 text-[var(--text-primary)]">
        <div className="flex items-center justify-between gap-2 border-b border-[var(--border-subtle)] pb-3">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-md bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400">
              <KeyRound className="w-4 h-4" />
            </div>
            <div>
              <div className="text-xs font-semibold tracking-tight">
                {isEn ? 'Participant Access Gate' : 'Вход по персональному коду'}
              </div>
              <div className="text-[10px] font-mono text-[var(--text-muted)]">
                CROC DREAM · Zero-Trust RBAC (1 Code = 1 Device)
              </div>
            </div>
          </div>
          <span className="px-2 py-0.5 rounded border border-emerald-500/30 bg-emerald-500/10 text-[10px] font-mono text-emerald-400 flex items-center gap-1">
            <ShieldCheck className="w-3 h-3" />
            <span>PROTECTED</span>
          </span>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="block text-[11px] font-mono text-[var(--text-secondary)] mb-1.5">
              {isEn
                ? 'Enter the 6-digit code or personal token (crk_...) issued by the Owner:'
                : 'Введите 6-значный код или персональный токен (crk_...), выданный Владельцем:'}
            </label>
            <input
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder={isEn ? 'e.g. 482910 or crk_...' : 'Например: 482910 или crk_...'}
              autoFocus
              className="w-full bg-[var(--bg-main)] border border-[var(--border-subtle)] focus:border-emerald-500/60 rounded-md px-3 py-2 text-xs font-mono text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none transition-colors"
            />
          </div>

          {errorMsg && (
            <div className="px-3 py-2 rounded-md bg-rose-500/10 border border-rose-500/30 text-xs font-mono text-rose-400 flex items-center gap-2">
              <AlertCircle className="w-3.5 h-3.5 shrink-0" />
              <span>{errorMsg}</span>
            </div>
          )}

          <div className="flex items-center justify-between pt-1">
            <span className="text-[10px] font-mono text-[var(--text-muted)]">
              Device ID: {api.getClientId()}
            </span>
            <button
              type="submit"
              disabled={loading}
              className="px-4 py-2 rounded-md bg-[var(--text-primary)] hover:opacity-90 text-[var(--bg-main)] text-xs font-semibold flex items-center gap-1.5 cursor-pointer transition-opacity disabled:opacity-50"
            >
              <span>{loading ? '...' : isEn ? 'Enter Stand' : 'Войти на стенд'}</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
