import React from 'react';
import { Check, Copy, Globe, ShieldCheck } from 'lucide-react';
import type { PublicConsoleUrls } from '../../../api';

interface PublicConsolesBannerProps {
  consoles: PublicConsoleUrls;
  copiedKey: string | null;
  onCopy: (key: string, text: string) => void;
  ghostBtnClass: string;
}

export const PublicConsolesBanner: React.FC<PublicConsolesBannerProps> = ({
  consoles,
  copiedKey,
  onCopy,
  ghostBtnClass,
}) => (
  <div className="p-2.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elevated)]/40 flex flex-col sm:flex-row sm:items-center justify-between gap-2 font-mono">
    <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--text-primary)] min-w-0">
      <Globe className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
      <span>Шлюз:</span>
      <span className="text-emerald-400 truncate">{consoles.publicGateUrl}</span>
    </div>

    <div className="flex items-center gap-1.5 shrink-0">
      <span className="px-2 py-1 rounded border border-emerald-500/30 bg-emerald-500/10 text-[10px] text-emerald-400 inline-flex items-center gap-1">
        <ShieldCheck className="w-3 h-3" />
        <span>1 Код = 1 ПК</span>
      </span>
      <button
        type="button"
        onClick={() => onCopy('pub-gate-url', consoles.publicGateUrl)}
        className={`h-7 px-2.5 text-[10px] flex items-center gap-1 ${ghostBtnClass}`}
        title="Скопировать адрес интернет-шлюза (для ввода 6-значного кода)"
      >
        {copiedKey === 'pub-gate-url' ? (
          <Check className="w-3 h-3 text-emerald-400" />
        ) : (
          <Copy className="w-3 h-3" />
        )}
        <span>{copiedKey === 'pub-gate-url' ? 'Скопировано' : 'Шлюз'}</span>
      </button>
    </div>
  </div>
);
