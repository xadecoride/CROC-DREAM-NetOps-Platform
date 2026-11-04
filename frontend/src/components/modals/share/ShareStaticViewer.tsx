import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  QrCode,
  Copy,
  Check,
  RefreshCw,
  Eye,
  AlertCircle,
  ToggleLeft,
  ToggleRight,
} from 'lucide-react';
import QRCode from 'qrcode';
import { api, type StaticViewerStatus } from '../../../api/client';
import { resolveShareBaseUrl } from '../../../api/auth';
import type { BootstrapExposureInfo } from '../../../api/types';

interface ShareStaticViewerProps {
  exposureInfo?: BootstrapExposureInfo | null;
  ctrlClass?: string;
  ghostBtnClass?: string;
  primaryBtnClass?: string;
}

export const ShareStaticViewer: React.FC<ShareStaticViewerProps> = ({
  exposureInfo,
  ctrlClass = 'bg-[var(--bg-main)] border border-[var(--border-subtle)] hover:border-[var(--border-hover)] focus:border-[var(--text-primary)] rounded-md text-xs font-mono text-[var(--text-primary)] focus:outline-none transition-colors',
  ghostBtnClass = 'border border-[var(--border-subtle)] hover:border-[var(--border-hover)] bg-[var(--bg-elevated)]/60 hover:bg-[var(--bg-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] rounded-md font-mono transition-colors cursor-pointer',
  primaryBtnClass = 'bg-[var(--text-primary)] hover:opacity-90 text-[var(--bg-main)] border border-[var(--text-primary)] rounded-md font-semibold transition-opacity cursor-pointer disabled:opacity-40',
}) => {
  const [status, setStatus] = useState<StaticViewerStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionInProgress, setActionInProgress] = useState(false);
  const [copied, setCopied] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [confirmRotate, setConfirmRotate] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await api.getStaticViewerStatus();
      setStatus(data);
      setErrorMsg(null);
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Ошибка загрузки статического QR-доступа');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => void fetchStatus(), 0);
    return () => clearTimeout(timer);
  }, [fetchStatus]);

  // Compute effective viewer link with exposure override
  const effectiveUrl = React.useMemo(() => {
    if (!status?.token) return '';
    const pubUrl = exposureInfo?.public_url || undefined;
    const base = resolveShareBaseUrl(pubUrl);
    return `${base}/?token=${status.token}`;
  }, [status?.token, exposureInfo?.public_url]);

  // Render client-side inline QR code onto canvas
  useEffect(() => {
    if (!canvasRef.current || !effectiveUrl || !status?.enabled) return;
    QRCode.toCanvas(canvasRef.current, effectiveUrl, {
      width: 140,
      margin: 1,
      color: {
        dark: '#000000',
        light: '#ffffff',
      },
      errorCorrectionLevel: 'M',
    }).catch((err) => {
      console.error('Failed to render QR canvas', err);
    });
  }, [effectiveUrl, status?.enabled]);

  const handleCopy = () => {
    if (!effectiveUrl) return;
    navigator.clipboard.writeText(effectiveUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleToggle = async () => {
    if (!status || actionInProgress) return;
    setActionInProgress(true);
    setErrorMsg(null);
    try {
      const updated = await api.toggleStaticViewer(!status.enabled);
      setStatus(updated);
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Ошибка переключения доступа');
    } finally {
      setActionInProgress(false);
    }
  };

  const handleRotate = async () => {
    if (!confirmRotate) {
      setConfirmRotate(true);
      setTimeout(() => setConfirmRotate(false), 5000);
      return;
    }
    setActionInProgress(true);
    setConfirmRotate(false);
    setErrorMsg(null);
    try {
      const rotated = await api.rotateStaticViewer();
      setStatus(rotated);
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Ошибка ротации токена');
    } finally {
      setActionInProgress(false);
    }
  };

  return (
    <div className="p-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elevated)]/40 space-y-3">
      {/* Header bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border-subtle)]/50 pb-2.5">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-md bg-[var(--bg-main)] border border-[var(--border-subtle)] text-emerald-400">
            <QrCode className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold font-mono tracking-wide text-[var(--text-primary)] uppercase">
                Вечный QR-доступ (Viewer)
              </span>
              <span
                className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${
                  status?.enabled
                    ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                    : 'bg-zinc-500/10 border-zinc-500/30 text-zinc-400'
                }`}
              >
                {status?.enabled ? '∞ Вечный · Live' : 'Выкл'}
              </span>
            </div>
          </div>
        </div>

        <button
          onClick={handleToggle}
          disabled={loading || actionInProgress}
          className={`flex items-center gap-1.5 px-2.5 py-1 text-xs ${ghostBtnClass}`}
          title={status?.enabled ? 'Отключить статический доступ' : 'Включить статический доступ'}
        >
          {status?.enabled ? (
            <>
              <ToggleRight className="w-4 h-4 text-emerald-400" />
              <span>Активен</span>
            </>
          ) : (
            <>
              <ToggleLeft className="w-4 h-4 text-zinc-500" />
              <span>Отключен</span>
            </>
          )}
        </button>
      </div>

      {errorMsg && (
        <div className="px-2.5 py-1.5 rounded bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs flex items-center gap-1.5">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* Main Grid: QR Canvas on left, Link and controls on right */}
      <div className="flex flex-col sm:flex-row gap-3 items-center sm:items-start">
        {/* Scannable Inline QR Canvas (pure client-side SVG/Canvas, zero external tracking) */}
        <div className="shrink-0 flex flex-col items-center gap-1">
          <div className="p-2 rounded-lg bg-white border border-[var(--border-subtle)] shadow-sm flex items-center justify-center">
            {status?.enabled ? (
              <canvas ref={canvasRef} className="rounded block" />
            ) : (
              <div className="w-[140px] h-[140px] flex flex-col items-center justify-center text-center p-2 text-zinc-600 bg-zinc-100 rounded">
                <QrCode className="w-8 h-8 opacity-40 mb-1" />
                <span className="text-[11px] font-mono leading-tight">Выключен</span>
              </div>
            )}
          </div>
        </div>

        {/* Link, details, and action buttons */}
        <div className="flex-1 min-w-0 w-full space-y-2.5">
          <div>
            <label className="text-[10px] font-mono text-[var(--text-secondary)] uppercase tracking-wider block mb-1">
              Вечная ссылка для наблюдателей (без истечения срока)
            </label>
            <div className="flex items-center gap-1.5">
              <input
                type="text"
                readOnly
                value={effectiveUrl}
                placeholder="Загрузка ссылки..."
                className={`flex-1 min-w-0 px-2.5 py-1 text-xs select-all ${ctrlClass}`}
              />
              <button
                onClick={handleCopy}
                disabled={!effectiveUrl || !status?.enabled}
                className={`h-7 px-2.5 text-xs flex items-center gap-1 shrink-0 ${primaryBtnClass}`}
                title="Скопировать ссылку доступа"
              >
                {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                <span>{copied ? 'Скопировано' : 'Копировать'}</span>
              </button>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 pt-1 border-t border-[var(--border-subtle)]/40">
            <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-mono text-[var(--text-secondary)]">
              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-[var(--bg-main)] border border-[var(--border-subtle)]">
                <Eye className="w-3 h-3 text-cyan-400" />
                <span>Роль: VIEWER</span>
              </span>
              <span className="px-1.5 py-0.5 rounded bg-[var(--bg-main)] border border-[var(--border-subtle)]">
                Стенды: Все
              </span>
              <span className="px-1.5 py-0.5 rounded bg-[var(--bg-main)] border border-[var(--border-subtle)] text-emerald-400">
                Устройства: Без лимита
              </span>
              <span className="px-1.5 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
                Срок: ∞ Вечный
              </span>
            </div>

            <button
              onClick={handleRotate}
              disabled={actionInProgress}
              className={`h-7 px-2.5 text-xs flex items-center gap-1 text-[11px] ${
                confirmRotate
                  ? 'bg-rose-500/20 border-rose-500/40 text-rose-300 hover:bg-rose-500/30'
                  : ghostBtnClass
              }`}
              title="Сменить токен и мгновенно завершить все существующие сессии зрителей (401)"
            >
              <RefreshCw className={`w-3 h-3 ${actionInProgress ? 'animate-spin' : ''}`} />
              <span>{confirmRotate ? 'Подтвердить ротацию' : 'Ротировать токен'}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
