import React, { useMemo, useState } from 'react';
import { Layers, LayoutGrid, Network, Search, ArrowRightLeft, CheckCircle2, AlertTriangle, WifiOff, ExternalLink, HelpCircle } from 'lucide-react';
import type { Device, StandRead, TopologyGraphLink } from '../../api';

export interface StandSummaryItem {
  slug: string;
  name: string;
  site: string;
  driver: string;
  total: number;
  inSync: number;
  drifted: number;
  unreachable: number;
  spines: number;
  leaves: number;
  servers: number;
  health: 'OK' | 'DRIFT' | 'DOWN' | 'EMPTY' | 'UNKNOWN';
}

interface ScaleBarProps {
  viewScope: 'micro' | 'macro';
  onChangeViewScope: (scope: 'micro' | 'macro') => void;
  standSummaries: StandSummaryItem[];
  focusedStand: string;
  onSelectFocusedStand: (slug: string) => void;
  compareStand: string;
  onSelectCompareStand: (slug: string) => void;
}

export const TopologyScaleBar: React.FC<ScaleBarProps> = ({
  viewScope,
  onChangeViewScope,
  standSummaries,
  focusedStand,
  onSelectFocusedStand,
  compareStand,
  onSelectCompareStand,
}) => {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 rounded-lg bg-[var(--bg-card)]/90 border border-[var(--border-subtle)]">
      {/* Mode Switcher: Macro (All Stands) vs Micro (Inside 1-2 Stands) */}
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] font-mono uppercase tracking-wider text-[var(--text-muted)] mr-1">
          Масштаб:
        </span>
        <button
          type="button"
          onClick={() => onChangeViewScope('micro')}
          data-testid="topo-scope-micro"
          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-medium transition-colors border cursor-pointer ${
            viewScope === 'micro'
              ? 'bg-zinc-800 text-zinc-100 border-zinc-600'
              : 'bg-[var(--bg-main)] text-[var(--text-secondary)] border-[var(--border-subtle)] hover:border-[var(--border-hover)]'
          }`}
        >
          <Network className="w-3.5 h-3.5" />
          <span>Внутри стенда (Clos)</span>
        </button>
        <button
          type="button"
          onClick={() => onChangeViewScope('macro')}
          data-testid="topo-scope-macro"
          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-medium transition-colors border cursor-pointer ${
            viewScope === 'macro'
              ? 'bg-zinc-800 text-zinc-100 border-zinc-600'
              : 'bg-[var(--bg-main)] text-[var(--text-secondary)] border-[var(--border-subtle)] hover:border-[var(--border-hover)]'
          }`}
        >
          <LayoutGrid className="w-3.5 h-3.5" />
          <span>Матрица стендов ({standSummaries.length})</span>
        </button>
      </div>

      {/* Stand Focus & Split DCI Selector (when inside Micro mode) */}
      {viewScope === 'micro' ? (
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          <div className="flex items-center gap-1.5">
            <Layers className="w-3.5 h-3.5 text-zinc-400" />
            <span className="text-[var(--text-muted)] font-mono">Стенд А:</span>
            <select
              value={focusedStand}
              onChange={(e) => {
                const next = e.target.value;
                onSelectFocusedStand(next);
                if (compareStand === next) onSelectCompareStand('');
              }}
              data-testid="topo-focused-stand-select"
              className="px-2 py-1 rounded bg-[var(--bg-main)] border border-[var(--border-subtle)] text-[var(--text-primary)] font-mono text-[11px] outline-none focus:border-zinc-600 cursor-pointer"
            >
              {standSummaries.map((s) => (
                <option key={s.slug} value={s.slug}>
                  {s.slug.toUpperCase()} ({s.total} устр.)
                </option>
              ))}
            </select>
          </div>

          {standSummaries.length > 1 && (
            <div className="flex items-center gap-1.5">
              <ArrowRightLeft className="w-3.5 h-3.5 text-zinc-400" />
              <span className="text-[var(--text-muted)] font-mono">Мост DCI (Стенд Б):</span>
              <select
                value={compareStand}
                onChange={(e) => onSelectCompareStand(e.target.value)}
                data-testid="topo-compare-stand-select"
                className="px-2 py-1 rounded bg-[var(--bg-main)] border border-[var(--border-subtle)] text-[var(--text-primary)] font-mono text-[11px] outline-none focus:border-zinc-600 cursor-pointer"
              >
                <option value="">— Только 1 стенд (полный экран) —</option>
                {standSummaries
                  .filter((s) => s.slug !== focusedStand)
                  .map((s) => (
                    <option key={s.slug} value={s.slug}>
                      + {s.slug.toUpperCase()} ({s.total} устр.)
                    </option>
                  ))}
              </select>
            </div>
          )}
        </div>
      ) : (
        <div className="text-[11px] text-[var(--text-muted)] font-mono">
          Нажмите на любой стенд для перехода к его коммутаторам или выберите 2 стенда для DCI-моста
        </div>
      )}
    </div>
  );
};

interface MacroFleetViewProps {
  standSummaries: StandSummaryItem[];
  links: TopologyGraphLink[];
  focusedStand: string;
  compareStand: string;
  onDrillDownStand: (slug: string) => void;
  onCompareWithStand: (slug: string) => void;
}

export const TopologyMacroFleetView: React.FC<MacroFleetViewProps> = ({
  standSummaries,
  links,
  focusedStand,
  compareStand,
  onDrillDownStand,
  onCompareWithStand,
}) => {
  const [filterMode, setFilterMode] = useState<'ALL' | 'ISSUES' | 'DCI'>('ALL');
  const [query, setQuery] = useState('');

  // Aggregate inter-stand links between pairs of stands
  const interStandEdges = useMemo(() => {
    const map = new Map<string, { a: string; b: string; count: number; shutCount: number; maxLoad: number }>();
    links.forEach((l) => {
      const sa = l.source_stand || '';
      const sb = l.target_stand || '';
      if (!sa || !sb || sa === sb) return;
      const [x, y] = sa < sb ? [sa, sb] : [sb, sa];
      const key = `${x}__${y}`;
      const cur = map.get(key) || { a: x, b: y, count: 0, shutCount: 0, maxLoad: 0 };
      cur.count += 1;
      if (l.admin_state === 'SHUTDOWN') cur.shutCount += 1;
      cur.maxLoad = Math.max(cur.maxLoad, l.load_pct || 0);
      map.set(key, cur);
    });
    return Array.from(map.values());
  }, [links]);

  const dciConnectedSlugs = useMemo(() => {
    const set = new Set<string>();
    interStandEdges.forEach((e) => { set.add(e.a); set.add(e.b); });
    return set;
  }, [interStandEdges]);

  const filteredStands = useMemo(() => {
    return standSummaries.filter((s) => {
      if (filterMode === 'ISSUES' && s.health === 'OK') return false;
      if (filterMode === 'DCI' && !dciConnectedSlugs.has(s.slug)) return false;
      if (query.trim()) {
        const q = query.trim().toLowerCase();
        if (!`${s.slug} ${s.name} ${s.site} ${s.driver}`.toLowerCase().includes(q)) return false;
      }
      return true;
    });
  }, [standSummaries, filterMode, dciConnectedSlugs, query]);

  // Macro SVG coordinates for up to 24 visible stands on the DCI Super-Graph
  const macroGraphNodes = useMemo(() => {
    const visible = filteredStands.slice(0, 24);
    const cols = Math.min(4, Math.max(1, visible.length));
    const rows = Math.ceil(visible.length / cols);
    const W = 960;
    const H = Math.max(190, rows * 105 + 30);
    const map = new Map<string, { item: StandSummaryItem; x: number; y: number }>();
    visible.forEach((item, idx) => {
      const r = Math.floor(idx / cols);
      const c = idx % cols;
      const colsInRow = r === rows - 1 ? visible.length - r * cols : cols;
      const x = ((c + 1) * W) / (colsInRow + 1);
      const y = 36 + r * 98;
      map.set(item.slug, { item, x, y });
    });
    return { W, H, map, list: Array.from(map.values()) };
  }, [filteredStands]);

  const issuesCount = standSummaries.filter((s) => s.health === 'DRIFT' || s.health === 'DOWN').length;

  return (
    <div className="space-y-3" data-testid="topology-macro-fleet-view">
      {/* 1. Macro Super-Spine / DCI Graph (Each Stand = 1 Collapsed Pod Node) */}
      <div className="rounded-xl border border-[var(--border-subtle)] bg-[#08090a] p-3">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-[var(--text-primary)]">
              Мега-граф ЦОД (Межстендовая магистраль DCI)
            </span>
            <span className="px-2 py-0.5 rounded bg-zinc-900 text-zinc-300 border border-zinc-700 text-[10px] font-mono">
              DCI магистралей: {interStandEdges.length}
            </span>
          </div>
          <span className="text-[11px] text-[var(--text-muted)] font-mono">
            Клик по узлу стенда → открыть его Spine/Leaf коммутаторы
          </span>
        </div>

        <div className="w-full overflow-x-auto">
          <svg viewBox={`0 0 ${macroGraphNodes.W} ${macroGraphNodes.H}`} className="w-full min-w-[640px] h-auto select-none">
            {interStandEdges.map((edge) => {
              const na = macroGraphNodes.map.get(edge.a);
              const nb = macroGraphNodes.map.get(edge.b);
              if (!na || !nb) return null;
              const midX = (na.x + nb.x) / 2;
              const midY = (na.y + nb.y) / 2 + 28;
              const allShut = edge.shutCount >= edge.count;
              return (
                <g key={`${edge.a}-${edge.b}`}>
                  <line
                    x1={na.x}
                    y1={na.y + 32}
                    x2={nb.x}
                    y2={nb.y + 32}
                    stroke={allShut ? '#f43f5e' : '#71717a'}
                    strokeWidth="2"
                    strokeDasharray={allShut ? '4 4' : '7 4'}
                  />
                  <g transform={`translate(${midX}, ${midY})`}>
                    <rect x="-48" y="-8" width="96" height="16" rx="4" fill="#08090a" stroke="rgba(255,255,255,0.18)" strokeWidth="0.9" />
                    <text x="0" y="3" fontSize="8" fontFamily="monospace" textAnchor="middle" fill="#d4d4d8">
                      {allShut ? 'DCI SHUT' : `DCI ×${edge.count} (${edge.maxLoad}%)`}
                    </text>
                  </g>
                </g>
              );
            })}

            {macroGraphNodes.list.map(({ item, x, y }) => {
              const BW = 190, BH = 64;
              const stroke =
                item.health === 'DOWN' ? '#f43f5e' : item.health === 'DRIFT' ? '#f59e0b' : item.slug === focusedStand ? '#e4e4e7' : 'rgba(255,255,255,0.16)';
              const dotColor =
                item.health === 'DOWN' ? '#f43f5e' : item.health === 'DRIFT' ? '#f59e0b' : item.health === 'OK' ? '#10b981' : '#71717a';

              return (
                <g
                  key={item.slug}
                  transform={`translate(${x - BW / 2}, ${y})`}
                  onClick={() => onDrillDownStand(item.slug)}
                  className="cursor-pointer"
                >
                  <rect width={BW} height={BH} rx="8" style={{ fill: 'var(--bg-card)' }} stroke={stroke} strokeWidth="1.5" />
                  <circle cx="14" cy="18" r="4" fill={dotColor} />
                  <text x="25" y="21" fontSize="11" fontWeight="700" style={{ fill: 'var(--text-primary)' }} fontFamily="monospace">
                    {item.slug.toUpperCase()}
                  </text>
                  <text x="14" y="38" fontSize="9" style={{ fill: 'var(--text-muted)' }} fontFamily="monospace">
                    {`Устройств: ${item.total} (${item.spines} Sp · ${item.leaves} Lf)`}
                  </text>
                  <text x="14" y="53" fontSize="8.5" fill={dotColor} fontFamily="monospace">
                    {item.health === 'OK'
                      ? `В СИНХР. (${item.inSync}/${item.total})`
                      : item.health === 'DRIFT'
                        ? `ДРЕЙФ: ${item.drifted}`
                        : item.health === 'DOWN'
                          ? `НЕДОСТУПНО: ${item.unreachable}`
                          : item.health === 'UNKNOWN'
                            ? `НЕ ПРОВЕРЕН (${item.total})`
                            : 'ПУСТО (0)'}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      </div>

      {/* 2. Fleet Matrix for 10–100+ Stands with Search & Problem Filter */}
      <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-card)]/70 p-3 space-y-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={() => setFilterMode('ALL')}
              className={`px-2.5 py-1 rounded text-[11px] font-medium border cursor-pointer ${
                filterMode === 'ALL'
                  ? 'bg-zinc-800 text-zinc-100 border-zinc-600'
                  : 'bg-[var(--bg-main)] text-[var(--text-secondary)] border-[var(--border-subtle)]'
              }`}
            >
              Все стенды ({standSummaries.length})
            </button>
            <button
              type="button"
              onClick={() => setFilterMode('ISSUES')}
              className={`px-2.5 py-1 rounded text-[11px] font-medium border cursor-pointer ${
                filterMode === 'ISSUES'
                  ? 'bg-amber-500/15 text-amber-300 border-amber-500/40'
                  : 'bg-[var(--bg-main)] text-[var(--text-secondary)] border-[var(--border-subtle)]'
              }`}
            >
              С проблемами ({issuesCount})
            </button>
            <button
              type="button"
              onClick={() => setFilterMode('DCI')}
              className={`px-2.5 py-1 rounded text-[11px] font-medium border cursor-pointer ${
                filterMode === 'DCI'
                  ? 'bg-zinc-800 text-zinc-100 border-zinc-600'
                  : 'bg-[var(--bg-main)] text-[var(--text-secondary)] border-[var(--border-subtle)]'
              }`}
            >
              Связанные по DCI ({dciConnectedSlugs.size})
            </button>
          </div>

          <div className="relative w-56">
            <Search className="w-3.5 h-3.5 text-[var(--text-muted)] absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Фильтр из 100 стендов..."
              className="w-full pl-8 pr-3 py-1 rounded bg-[var(--bg-main)] border border-[var(--border-subtle)] text-[11px] font-mono text-[var(--text-primary)] outline-none focus:border-zinc-600"
            />
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 max-h-72 overflow-y-auto pr-1">
          {filteredStands.map((st) => {
            const isFocus = st.slug === focusedStand;
            const isComp = st.slug === compareStand;
            return (
              <div
                key={st.slug}
                className={`p-2.5 rounded-lg border transition-colors flex flex-col justify-between gap-2 ${
                  isFocus || isComp
                    ? 'bg-zinc-900 border-zinc-600'
                    : 'bg-[var(--bg-main)]/80 border-[var(--border-subtle)] hover:border-[var(--border-hover)]'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="text-xs font-bold font-mono text-[var(--text-primary)] flex items-center gap-1.5">
                      <span>{st.slug.toUpperCase()}</span>
                      {st.health === 'OK' && <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />}
                      {st.health === 'DRIFT' && <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />}
                      {st.health === 'DOWN' && <WifiOff className="w-3.5 h-3.5 text-rose-400" />}
                      {st.health === 'UNKNOWN' && <HelpCircle className="w-3.5 h-3.5 text-zinc-400" />}
                      {st.health === 'EMPTY' && <HelpCircle className="w-3.5 h-3.5 text-zinc-600" />}
                    </div>
                    <div className="text-[10px] text-[var(--text-muted)] truncate">{st.name}</div>
                  </div>
                  <span className="px-1.5 py-0.5 rounded bg-white/[0.05] text-[10px] font-mono text-[var(--text-secondary)]">
                    {st.total} устр.
                  </span>
                </div>

                <div className="flex items-center justify-between text-[10px] font-mono text-[var(--text-muted)]">
                  <span>{st.spines} Spine · {st.leaves} Leaf</span>
                  <span className={st.drifted > 0 ? 'text-amber-300' : 'text-emerald-400'}>
                    {st.inSync}/{st.total} OK
                  </span>
                </div>

                <div className="flex items-center gap-1.5 pt-1 border-t border-white/[0.05]">
                  <button
                    type="button"
                    onClick={() => onDrillDownStand(st.slug)}
                    className="flex-1 inline-flex items-center justify-center gap-1 px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-[10px] font-medium cursor-pointer"
                  >
                    <ExternalLink className="w-3 h-3" />
                    <span>Открыть стенд</span>
                  </button>
                  {st.slug !== focusedStand && (
                    <button
                      type="button"
                      onClick={() => onCompareWithStand(st.slug)}
                      title="Вывести рядом со Стендом А для проверки DCI связей"
                      className="px-2 py-1 rounded bg-zinc-900 hover:bg-zinc-800 border border-zinc-700 text-zinc-300 text-[10px] font-medium cursor-pointer"
                    >
                      + Мост DCI
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export function buildStandSummaries(devices: Device[], stands?: StandRead[]): StandSummaryItem[] {
  const bySlug = new Map<string, StandSummaryItem>();
  (stands || []).forEach((s) => {
    bySlug.set(s.slug, {
      slug: s.slug,
      name: s.name || s.slug,
      site: s.site || s.slug,
      driver: s.driver || 'scrapli',
      total: 0,
      inSync: 0,
      drifted: 0,
      unreachable: 0,
      spines: 0,
      leaves: 0,
      servers: 0,
      health: 'EMPTY',
    });
  });

  devices.forEach((d) => {
    const slug = d.fabric_id || d.site || 'croc-dc1-pod1';
    const item = bySlug.get(slug) || {
      slug,
      name: `Стенд ${slug}`,
      site: d.site || slug,
      driver: 'scrapli',
      total: 0,
      inSync: 0,
      drifted: 0,
      unreachable: 0,
      spines: 0,
      leaves: 0,
      servers: 0,
      health: 'UNKNOWN' as const,
    };
    item.total += 1;
    if (d.status === 'IN_SYNC') item.inSync += 1;
    else if (d.status === 'DRIFT_DETECTED') item.drifted += 1;
    else if (d.status === 'UNREACHABLE') item.unreachable += 1;

    if (d.role === 'spine') item.spines += 1;
    else if (d.role === 'leaf') item.leaves += 1;
    else if (d.role === 'server' || d.platform === 'linux_server') item.servers += 1;
    bySlug.set(slug, item);
  });

  bySlug.forEach((item) => {
    if (item.total === 0) item.health = 'EMPTY';
    else if (item.unreachable > 0) item.health = 'DOWN';
    else if (item.drifted > 0) item.health = 'DRIFT';
    else if (item.inSync === 0) item.health = 'UNKNOWN';
    else item.health = 'OK';
  });

  return Array.from(bySlug.values());
}
