import React, { useEffect, useRef, useState } from 'react';
import {
  Send,
  Sparkles,
  X,
  Play,
  Wrench,
  GitCompare,
  ShieldCheck,
  ExternalLink,
} from 'lucide-react';
import { api, type CopilotAction, type CopilotReplyWithAction } from '../../api';
export type { CopilotAction, CopilotReplyWithAction };
import { translations, type Locale } from '../../i18n';

interface Message {
  role: 'user' | 'ai';
  text: string;
  provider?: string;
  action?: CopilotAction | null;
}

export interface CopilotPanelProps {
  open: boolean;
  onClose: () => void;
  deviceId: number | null;
  userRole?: import('../../api').UserRole;
  locale?: Locale;
  request: { text: string; n: number } | null;
  activeScreen?: string | null;
  onRunDryRun?: (deviceIds: number[]) => void;
  onOpenDiff?: (deviceId?: number) => void;
  onScanDrift?: (deviceIds?: number[]) => void;
  onOpenDevice?: (deviceId: number) => void;
  onRemediate?: (deviceId: number) => void;
  onOpenTab?: (tab: string) => void;
}

export const CopilotPanel: React.FC<CopilotPanelProps> = ({
  open,
  onClose,
  deviceId,
  userRole = 'viewer',
  locale = 'ru',
  request,
  activeScreen = 'dashboard',
  onRunDryRun,
  onOpenDiff,
  onScanDrift,
  onOpenDevice,
  onRemediate,
  onOpenTab,
}) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const lastRequest = useRef(0);

  const t = translations[locale];

  const getContextLabel = (screen?: string | null): string => {
    switch (screen) {
      case 'diff':
        return t.contextDiff;
      case 'jobs':
        return t.contextJobs;
      case 'devices':
      case 'device':
        return t.contextDevices;
      case 'topology':
        return t.contextTopology;
      case 'drift':
        return t.contextDrift;
      case 'lab':
        return t.contextLab;
      case 'emergency':
        return t.contextEmergency;
      default:
        return t.contextOverview;
    }
  };

  const contextLabel = getContextLabel(activeScreen);

  const send = React.useCallback(async (text: string) => {
    if (!text.trim() || busy) return;
    setMessages((m) => [...m, { role: 'user', text }]);
    setInput('');
    setBusy(true);

    try {
      const reply = await api.askCopilot(text, deviceId, activeScreen);
      setMessages((m) => [
        ...m,
        {
          role: 'ai',
          text: reply.answer,
          provider: reply.provider,
          action: reply.action,
        },
      ]);
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: 'ai', text: `${t.copilotErrorPrefix}: ${(e as Error).message}` },
      ]);
    } finally {
      setBusy(false);
    }
  }, [busy, deviceId, activeScreen, t.copilotErrorPrefix]);

  const handleExecuteAction = (action: CopilotAction) => {
    switch (action.type) {
      case 'dry_run': {
        const ids =
          action.device_ids && action.device_ids.length > 0
            ? action.device_ids
            : action.device_id
            ? [action.device_id]
            : deviceId
            ? [deviceId]
            : [];
        if (onRunDryRun) {
          onRunDryRun(ids);
        }
        break;
      }
      case 'remediate': {
        if (action.device_id && onRemediate) {
          onRemediate(action.device_id);
        } else if (onScanDrift) {
          const targetIds = action.device_ids && action.device_ids.length > 0 ? action.device_ids : undefined;
          onScanDrift(targetIds);
        }
        break;
      }
      case 'open_diff': {
        if (onOpenDiff) {
          onOpenDiff(action.device_id ?? undefined);
        } else if (onOpenTab) {
          onOpenTab('diff');
        }
        break;
      }
      case 'open_device': {
        if (action.device_id && onOpenDevice) {
          onOpenDevice(action.device_id);
        } else if (onOpenTab) {
          onOpenTab('devices');
        }
        break;
      }
      case 'scan_drift': {
        if (onScanDrift) {
          const targetIds = action.device_ids && action.device_ids.length > 0 ? action.device_ids : undefined;
          onScanDrift(targetIds);
        } else if (onOpenTab) {
          onOpenTab('drift');
        }
        break;
      }
    }
  };

  useEffect(() => {
    if (request && request.n !== lastRequest.current) {
      lastRequest.current = request.n;
      void send(request.text);
    }
  }, [request, send]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, busy]);

  const suggestions = [
    t.copilotSuggestion1,
    t.copilotSuggestion2,
    t.copilotSuggestion3,
  ];

  if (!open) return null;

  return (
    <aside className="fixed top-14 bottom-0 right-0 w-full sm:w-[400px] max-w-full z-50 bg-[var(--bg-surface)] border-l border-[var(--border-subtle)] flex flex-col shadow-2xl pb-[env(safe-area-inset-bottom,0px)]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 h-12 border-b border-[var(--border-subtle)] bg-[var(--bg-elevated)] shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <Sparkles className="w-4 h-4 text-[var(--text-secondary)] shrink-0" />
          <span className="text-sm font-semibold text-[var(--text-primary)] tracking-tight truncate">
            {t.copilotTitle}
          </span>
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[var(--bg-surface)] text-[var(--text-secondary)] border border-[var(--border-subtle)] shrink-0">
            {contextLabel}
          </span>
        </div>
        <button
          onClick={onClose}
          className="text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors p-1 rounded hover:bg-[var(--bg-surface)] cursor-pointer shrink-0"
          title={t.copilotCloseTitle}
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Messages Scroll Area */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3.5 text-xs bg-[var(--bg-surface)]">
        {messages.length === 0 && (
          <div className="space-y-4">
            <div className="p-3.5 rounded-xl bg-[var(--bg-elevated)] border border-[var(--border-subtle)] space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-[var(--text-secondary)] shrink-0" />
                  <span className="text-xs font-semibold text-[var(--text-primary)]">
                    {t.copilotTitle}
                  </span>
                </div>
                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[var(--bg-surface)] text-[var(--text-secondary)] border border-[var(--border-subtle)]">
                  {contextLabel}
                </span>
              </div>
              <p className="text-[11px] text-[var(--text-secondary)] leading-relaxed">
                {t.copilotEmptyDesc}
              </p>
            </div>

            <div className="space-y-1.5">
              <span className="block text-[10px] font-mono font-semibold text-[var(--text-muted)] uppercase tracking-wider px-0.5">
                {t.copilotQuickPrompts}
              </span>
              {suggestions.map((s) => (
                <button
                  key={s}
                  onClick={() => void send(s)}
                  className="block w-full text-left px-3 py-2 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] text-[var(--text-primary)] hover:bg-[var(--bg-panel)] hover:border-[var(--border-hover)] transition-colors cursor-pointer text-xs"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={m.role === 'user' ? 'text-right' : 'text-left'}>
            <div
              className={`inline-block max-w-[90%] text-left whitespace-pre-wrap rounded-2xl px-3.5 py-2.5 leading-relaxed ${
                m.role === 'user'
                  ? 'bg-[var(--bg-panel)] border border-[var(--border-strong)] text-[var(--text-primary)] rounded-tr-xs'
                  : 'bg-[var(--bg-elevated)] text-[var(--text-primary)] border border-[var(--border-subtle)] rounded-tl-xs'
              }`}
            >
              <div>{m.text}</div>

              {/* 1-Click Action Resolution Button */}
              {m.action && (userRole !== 'viewer' || m.action.type === 'open_diff' || m.action.type === 'open_device') && (
                <div className="mt-2.5 pt-2 border-t border-[var(--border-subtle)] flex items-center justify-start">
                  <button
                    onClick={() => handleExecuteAction(m.action!)}
                    className={`px-3 py-1.5 rounded text-xs font-mono font-medium flex items-center gap-1.5 transition-colors cursor-pointer ${
                      m.action.type === 'dry_run'
                        ? 'bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30'
                        : m.action.type === 'remediate'
                        ? 'bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30'
                        : 'bg-[var(--bg-panel)] hover:bg-[var(--bg-surface)] text-[var(--text-primary)] border border-[var(--border-strong)]'
                    }`}
                    title={`${t.copilotExecutePrefix}: ${m.action.label}`}
                  >
                    {m.action.type === 'dry_run' && <Play className="w-3.5 h-3.5 fill-current" />}
                    {m.action.type === 'remediate' && <Wrench className="w-3.5 h-3.5" />}
                    {m.action.type === 'open_diff' && <GitCompare className="w-3.5 h-3.5" />}
                    {m.action.type === 'scan_drift' && <ShieldCheck className="w-3.5 h-3.5" />}
                    {m.action.type === 'open_device' && <ExternalLink className="w-3.5 h-3.5" />}
                    <span>{m.action.label}</span>
                  </button>
                </div>
              )}
            </div>

            {m.provider && (
              <div className="text-[10px] text-[var(--text-muted)] font-mono mt-1 px-1">
                {m.provider}
              </div>
            )}
          </div>
        ))}

        {busy && (
          <div className="text-[var(--text-secondary)] text-xs flex items-center gap-2 p-2.5 bg-[var(--bg-elevated)] rounded-lg border border-[var(--border-subtle)]">
            <Sparkles className="w-3.5 h-3.5 text-[var(--text-secondary)] animate-spin" />
            <span className="animate-pulse">{t.copilotThinking}</span>
          </div>
        )}
        <div ref={bottom} />
      </div>

      {/* Input Field */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
        className="p-3 border-t border-[var(--border-subtle)] bg-[var(--bg-elevated)] flex gap-2 shrink-0"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={deviceId ? t.copilotAskDevicePlaceholder : t.copilotPlaceholder}
          className="flex-1 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl px-3.5 py-2 text-xs text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--border-strong)] transition-colors"
        />
        <button
          disabled={busy || !input.trim()}
          className="px-4 rounded-xl bg-[var(--text-primary)] text-[var(--bg-base)] hover:opacity-90 font-medium disabled:opacity-40 cursor-pointer transition-opacity flex items-center justify-center"
          title={t.copilotSendTitle}
        >
          <Send className="w-3.5 h-3.5" />
        </button>
      </form>
    </aside>
  );
};
