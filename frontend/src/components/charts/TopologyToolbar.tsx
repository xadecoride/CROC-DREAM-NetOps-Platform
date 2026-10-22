import React from 'react';
import { Layers, Plus, RefreshCw, Search, X } from 'lucide-react';
import type { Locale } from '../../i18n';
import { LAYER_FILTERS, useTranslation } from './topologyLayout';

export interface TopologyToolbarProps {
  activeLayer: string;
  onSelectLayer: (layer: string) => void;
  onSyncStands?: () => void;
  syncingStands: boolean;
  onOpenStandsModal?: () => void;
  standCount: number;
  searchQuery: string;
  onSearchChange: (q: string) => void;
  locale?: Locale;
}

export const TopologyToolbar: React.FC<TopologyToolbarProps> = ({
  activeLayer,
  onSelectLayer,
  onSyncStands,
  syncingStands,
  onOpenStandsModal,
  standCount,
  searchQuery,
  onSearchChange,
  locale: propLocale,
}) => {
  const { isEn } = useTranslation(propLocale);

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 pb-1">
      <div className="flex flex-wrap items-center gap-1" data-testid="topology-layer-tabs">
        <Layers className="w-3.5 h-3.5 text-[var(--text-muted)] mr-1" />
        {LAYER_FILTERS.map((lf) => (
          <button
            key={lf.id}
            type="button"
            onClick={() => onSelectLayer(lf.id)}
            data-testid={`layer-tab-${lf.id}`}
            className={`px-2.5 py-1 rounded text-[11px] font-medium transition-colors border cursor-pointer ${
              activeLayer === lf.id
                ? 'bg-[var(--bg-panel)] text-[var(--text-primary)] border-[var(--border-strong)] font-semibold'
                : 'bg-[var(--bg-surface)] text-[var(--text-secondary)] border-[var(--border-subtle)] hover:border-[var(--border-strong)] hover:text-[var(--text-primary)]'
            }`}
          >
            {isEn ? lf.labelEn : lf.label}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {onSyncStands && (
          <button
            type="button"
            onClick={onSyncStands}
            disabled={syncingStands}
            data-testid="topology-sync-stands-btn"
            title={
              isEn
                ? 'Discover and sync stands & switches from Git SoT, Containerlab, and SSH'
                : 'Подхватить новые стенды и коммутаторы из Git SoT, Containerlab и SSH без перезапуска'
            }
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-[var(--bg-elevated)] hover:bg-[var(--bg-panel)] text-[var(--text-primary)] border border-[var(--border-subtle)] hover:border-[var(--border-strong)] text-[11px] font-medium transition-colors cursor-pointer disabled:opacity-50"
          >
            <RefreshCw className={`w-3 h-3 ${syncingStands ? 'animate-spin' : ''}`} />
            <span>
              {syncingStands
                ? isEn
                  ? 'Syncing...'
                  : 'Синхронизация...'
                : isEn
                ? 'Discover Stands'
                : 'Найти стенды'}
            </span>
          </button>
        )}
        {onOpenStandsModal && (
          <button
            type="button"
            onClick={onOpenStandsModal}
            data-testid="topology-add-stand-btn"
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-[var(--bg-surface)] hover:bg-[var(--bg-elevated)] hover:border-[var(--border-strong)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] border border-[var(--border-subtle)] text-[11px] font-medium transition-colors cursor-pointer"
          >
            <Plus className="w-3 h-3 text-[var(--text-muted)]" />
            <span>
              {isEn ? 'Stands' : 'Стенды'} ({standCount})
            </span>
          </button>
        )}
        <div className="relative w-48 sm:w-52">
          <Search className="w-3.5 h-3.5 text-[var(--text-muted)] absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder={isEn ? 'Search node, IP, port...' : 'Поиск узла, IP, порта...'}
            data-testid="topology-search-input"
            className="w-full pl-8 pr-6 py-1 rounded bg-[var(--bg-base)] border border-[var(--border-subtle)] focus:border-[var(--border-strong)] text-[11px] font-mono text-[var(--text-primary)] outline-none"
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => onSearchChange('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--text-muted)] hover:text-[var(--text-primary)] cursor-pointer"
            >
              <X className="w-3 h-3" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
