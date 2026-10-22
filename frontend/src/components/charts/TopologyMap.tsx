import React, { useMemo, useState, useEffect, useCallback, useRef } from 'react';
import { Network, ZoomIn, ZoomOut, RotateCcw, RefreshCw } from 'lucide-react';
import { api } from '../../api';
import type { Device, StandRead, TopologyGraphLink, TopologyGraphResponse, TopologyTraceResponse } from '../../api';
import type { Locale } from '../../i18n';
import { PLATFORM_NAMES } from '../../constants';
import { DeviceDetailModal } from '../modals/DeviceDetailModal';
import { TopologyCheckpointBar } from './TopologyCheckpointBar';
import { TopologyCommutationPanel } from './TopologyCommutationPanel';
import { TopologyMacroFleetView, TopologyScaleBar, buildStandSummaries } from './TopologyMacroFleetView';
import { TopologyNodePopover } from './TopologyNodePopover';
import { TopologyToolbar } from './TopologyToolbar';
import {
  W, H, NODE_W, NODE_H, POS_STORAGE_KEY, STATUS_COLOR, VENDOR_BADGE, shortPort,
  computeTopologyLayout, resolveNodeProtocolLabel, useTranslation,
} from './topologyLayout';

interface Props {
  devices: Device[];
  stands?: StandRead[];
  activeStand?: string;
  locale?: Locale;
  compact?: boolean;
  userRole?: string;
  onExpandTopology?: () => void;
  onSelectDevice: (id: number) => void;
  onRunDryRun?: (ids: number[]) => void;
  onSyncStands?: () => Promise<void>;
  onOpenStandsModal?: () => void;
}

const TopologyMapComponent: React.FC<Props> = ({
  devices, stands, activeStand = 'all', locale: propLocale, compact = false,
  userRole, onExpandTopology, onSelectDevice, onRunDryRun, onSyncStands, onOpenStandsModal,
}) => {
  const { locale, isEn } = useTranslation(propLocale);
  const [hover, setHover] = useState<number | null>(null);
  const [popoverId, setPopoverId] = useState<number | null>(null);
  const [inspectDeviceId, setInspectDeviceId] = useState<number | null>(null);
  const [dryRunRunning, setDryRunRunning] = useState<boolean>(false);
  const [dryRunMsg, setDryRunMsg] = useState<string | null>(null);
  const [drainBusy, setDrainBusy] = useState<boolean>(false);
  const [protocolBusy, setProtocolBusy] = useState<boolean>(false);
  const [syncingStands, setSyncingStands] = useState<boolean>(false);

  const [graphData, setGraphData] = useState<TopologyGraphResponse | null>(null);
  const [activeLayer, setActiveLayer] = useState<string>('ALL');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedLink, setSelectedLink] = useState<TopologyGraphLink | null>(null);
  const [traceSrc, setTraceSrc] = useState<number>(0);
  const [traceDst, setTraceDst] = useState<number>(0);
  const [traceResult, setTraceResult] = useState<TopologyTraceResponse | null>(null);

  const standSummaries = useMemo(() => buildStandSummaries(devices, stands), [devices, stands]);
  const [viewScope, setViewScope] = useState<'micro' | 'macro'>('micro');
  const [focusedStand, setFocusedStand] = useState<string>('');
  const [compareStand, setCompareStand] = useState<string>('');

  const effectiveFocusedStand = useMemo(() => {
    if (activeStand && activeStand !== 'all') return activeStand;
    if (focusedStand && standSummaries.some((s) => s.slug === focusedStand)) return focusedStand;
    return standSummaries[0]?.slug || 'croc-dc1-pod1';
  }, [activeStand, focusedStand, standSummaries]);

  const microDevices = useMemo(() => {
    if (compact) {
      return devices.filter((d) => (d.fabric_id || d.site || 'croc-dc1-pod1') === effectiveFocusedStand);
    }
    const allowed = new Set([effectiveFocusedStand, ...(compareStand ? [compareStand] : [])]);
    return devices.filter((d) => allowed.has(d.fabric_id || d.site || 'croc-dc1-pod1'));
  }, [devices, compact, effectiveFocusedStand, compareStand]);

  const [zoom, setZoom] = useState<number>(1);
  const [pan, setPan] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [customPos, setCustomPos] = useState<Record<number, { x: number; y: number }>>(() => {
    try { return JSON.parse(localStorage.getItem(POS_STORAGE_KEY) || '{}'); } catch { return {}; }
  });
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragRef = useRef<{
    type: 'node' | 'pan'; nodeId?: number; startClientX: number; startClientY: number; origX: number; origY: number; moved: boolean;
  } | null>(null);

  const defaultEndpoints = useMemo(() => {
    const pool = microDevices.length >= 2 ? microDevices : devices;
    const leaves = pool.filter((d) => d.role === 'leaf');
    if (leaves.length >= 2) return [leaves[0].id, leaves[1].id] as const;
    if (pool.length >= 2) return [pool[0].id, pool[pool.length - 1].id] as const;
    return [pool[0]?.id ?? 0, pool[0]?.id ?? 0] as const;
  }, [microDevices, devices]);

  const effectiveSrc = traceSrc && devices.some((d) => d.id === traceSrc) ? traceSrc : defaultEndpoints[0];
  const effectiveDst = traceDst && devices.some((d) => d.id === traceDst) ? traceDst : defaultEndpoints[1];

  const fetchGraphAndTrace = useCallback(async () => {
    if (!devices.length) return;
    try {
      const g = await api.getTopologyGraph();
      setGraphData(g);
      setSelectedLink((prev) => (prev ? g.links.find((l) => l.id === prev.id) ?? null : null));
      if (!compact && effectiveSrc && effectiveDst && effectiveSrc !== effectiveDst) {
        const tr = await api.traceTopologyPath(effectiveSrc, effectiveDst);
        setTraceResult(tr);
      }
    } catch {
      // Fallback to local rendering if offline
    }
  }, [devices.length, compact, effectiveSrc, effectiveDst]);

  useEffect(() => { void fetchGraphAndTrace(); }, [fetchGraphAndTrace]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => { if (e.key === 'Escape') { setPopoverId(null); setSelectedLink(null); } };
    const handleSyncPos = () => { try { setCustomPos(JSON.parse(localStorage.getItem(POS_STORAGE_KEY) || '{}')); } catch { /* ignore */ } };
    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('storage', handleSyncPos);
    window.addEventListener('netops-topo-pos-sync', handleSyncPos);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('storage', handleSyncPos);
      window.removeEventListener('netops-topo-pos-sync', handleSyncPos);
    };
  }, []);

  const hasBorder = microDevices.some((d) => ['border', 'border_firewall'].includes(d.role));
  const hasServer = microDevices.some((d) => d.role === 'server' || d.platform === 'linux_server');

  const activeLayers = useMemo(() => {
    const layers = [];
    if (hasBorder) layers.push({ roles: ['border', 'border_firewall'], y: 34, label: 'BORDER' });
    layers.push({ roles: ['spine'], y: hasBorder ? 132 : hasServer ? 46 : 86, label: 'SPINE' });
    layers.push({ roles: ['leaf'], y: hasBorder ? 252 : hasServer ? 204 : 272, label: 'LEAF' });
    if (hasServer) layers.push({ roles: ['server'], y: 366, label: isEn ? 'SERVERS' : 'СЕРВЕРЫ' });
    return layers;
  }, [hasBorder, hasServer, isEn]);

  const { nodes, resolvedLinks, standBoxes, layerLabels } = useMemo(() => {
    return computeTopologyLayout(microDevices, activeLayers, graphData, activeLayer, searchQuery, customPos);
  }, [microDevices, activeLayers, graphData, activeLayer, searchQuery, customPos]);

  const handleMouseMove = useCallback((e: React.MouseEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag || !svgRef.current) return;
    const dx = e.clientX - drag.startClientX, dy = e.clientY - drag.startClientY;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) drag.moved = true;
    const rect = svgRef.current.getBoundingClientRect();
    const scaleX = W / Math.max(1, rect.width) / zoom, scaleY = H / Math.max(1, rect.height) / zoom;

    if (drag.type === 'node' && drag.nodeId !== undefined) {
      const nextX = Math.max(68 + NODE_W / 2, Math.min(W - NODE_W / 2 - 16, Math.round(drag.origX + dx * scaleX)));
      const nextY = Math.max(30, Math.min(H - NODE_H - 16, Math.round(drag.origY + dy * scaleY)));
      setCustomPos((prev) => {
        const next = { ...prev, [drag.nodeId!]: { x: nextX, y: nextY } };
        try { localStorage.setItem(POS_STORAGE_KEY, JSON.stringify(next)); } catch { /* ignore */ }
        return next;
      });
    } else if (drag.type === 'pan') {
      setPan({ x: Math.round(drag.origX + dx * scaleX * zoom), y: Math.round(drag.origY + dy * scaleY * zoom) });
    }
  }, [zoom]);

  const handleMouseUp = useCallback(() => {
    if (dragRef.current?.type === 'node' && dragRef.current.moved) window.dispatchEvent(new Event('netops-topo-pos-sync'));
    dragRef.current = null;
  }, []);

  const handleResetLayout = () => {
    setCustomPos({}); setZoom(1); setPan({ x: 0, y: 0 });
    try { localStorage.removeItem(POS_STORAGE_KEY); } catch { /* ignore */ }
    window.dispatchEvent(new Event('netops-topo-pos-sync'));
  };

  const handleSyncStandsClick = async () => {
    if (!onSyncStands) return;
    setSyncingStands(true);
    try { await onSyncStands(); await fetchGraphAndTrace(); } catch { /* ignore */ } finally { setSyncingStands(false); }
  };

  const popoverNode = popoverId !== null ? nodes.find((n) => n.d.id === popoverId) : undefined;
  const tracedLinkIds = useMemo(() => new Set(compact ? [] : traceResult?.links_used || []), [compact, traceResult]);

  const handleDryRun = async (deviceId: number) => {
    if (onRunDryRun) { onRunDryRun([deviceId]); setPopoverId(null); return; }
    setDryRunRunning(true); setDryRunMsg(null);
    try {
      await api.createDryRun([deviceId]);
      setDryRunMsg(isEn ? 'Dry-run started' : 'Dry-run запущен');
      setTimeout(() => { setDryRunMsg(null); setPopoverId(null); }, 1200);
    } catch { setDryRunMsg(isEn ? 'Launch error' : 'Ошибка запуска'); } finally { setDryRunRunning(false); }
  };

  const handleToggleDrain = async (deviceId: number, nextDrained: boolean) => {
    setDrainBusy(true);
    try {
      await api.toggleNodeDrain(
        deviceId, nextDrained,
        nextDrained ? (isEn ? 'BGP Drain from topology map' : 'BGP Drain с карты топологии') : (isEn ? 'Restored to service' : 'Возврат в работу'),
      );
      await fetchGraphAndTrace();
      window.dispatchEvent(new Event('netops-topology-mutated'));
    } catch { /* ignore */ } finally { setDrainBusy(false); }
  };

  const handleRefreshGraphAndDevices = useCallback(async () => {
    await fetchGraphAndTrace();
    window.dispatchEvent(new Event('netops-topology-mutated'));
  }, [fetchGraphAndTrace]);

  if (!devices.length) {
    return (
      <div className="flex flex-col items-center justify-center p-10 text-center rounded-xl border border-dashed border-[var(--border-subtle)] bg-[var(--bg-surface)] gap-3">
        <Network className="w-6 h-6 text-[var(--text-muted)]" />
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">
          {isEn ? 'No connected devices in the selected stand' : 'Нет подключенных устройств в выбранном стенде'}
        </h3>
        {onSyncStands && (
          <button type="button" onClick={handleSyncStandsClick} disabled={syncingStands}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--bg-elevated)] hover:bg-[var(--bg-panel)] border border-[var(--border-subtle)] hover:border-[var(--border-strong)] text-[var(--text-primary)] text-xs font-medium cursor-pointer">
            <RefreshCw className={`w-3.5 h-3.5 ${syncingStands ? 'animate-spin' : ''}`} />
            <span>{syncingStands ? (isEn ? 'Discovering stands...' : 'Поиск стендов...') : (isEn ? 'Discover & Sync Stands' : 'Найти и подхватить стенды (Sync)')}</span>
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="relative space-y-2.5">
      {!compact && (
        <>
          <TopologyToolbar
            activeLayer={activeLayer} onSelectLayer={setActiveLayer}
            onSyncStands={onSyncStands ? handleSyncStandsClick : undefined} syncingStands={syncingStands}
            onOpenStandsModal={onOpenStandsModal} standCount={standSummaries.length}
            searchQuery={searchQuery} onSearchChange={setSearchQuery} locale={locale}
          />
          <TopologyScaleBar
            viewScope={viewScope} onChangeViewScope={setViewScope} standSummaries={standSummaries}
            focusedStand={effectiveFocusedStand} onSelectFocusedStand={setFocusedStand}
            compareStand={compareStand} onSelectCompareStand={setCompareStand}
          />
          <TopologyCheckpointBar onReloadGraph={handleRefreshGraphAndDevices} userRole={userRole} />
        </>
      )}

      {!compact && viewScope === 'macro' ? (
        <TopologyMacroFleetView
          standSummaries={standSummaries} links={graphData?.links || []}
          focusedStand={effectiveFocusedStand} compareStand={compareStand}
          onDrillDownStand={(slug) => { setFocusedStand(slug); setViewScope('micro'); }}
          onCompareWithStand={(slug) => { setCompareStand(slug); setViewScope('micro'); }}
        />
      ) : (
        <div className="relative w-full overflow-hidden rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-base)]">
          {(!compact || Object.keys(customPos).length > 0) && (
            <div className="absolute top-2.5 right-2.5 z-10 flex items-center gap-1 bg-[var(--bg-surface)]/95 backdrop-blur-md border border-[var(--border-subtle)] rounded-lg p-1 shadow-sm">
              {!compact && (
                <>
                  <button type="button" onClick={() => setZoom((z) => Math.max(0.65, Number((z - 0.15).toFixed(2))))} title={isEn ? 'Zoom out' : 'Отдалить'}
                    className="p-1 rounded hover:bg-[var(--bg-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer">
                    <ZoomOut className="w-3.5 h-3.5" />
                  </button>
                  <span className="px-1.5 text-[10px] font-mono text-[var(--text-muted)] select-none">{Math.round(zoom * 100)}%</span>
                  <button type="button" onClick={() => setZoom((z) => Math.min(2.0, Number((z + 0.15).toFixed(2))))} title={isEn ? 'Zoom in' : 'Приблизить'}
                    className="p-1 rounded hover:bg-[var(--bg-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer">
                    <ZoomIn className="w-3.5 h-3.5" />
                  </button>
                  <div className="w-px h-3.5 bg-[var(--border-subtle)] mx-0.5" />
                </>
              )}
              <button type="button" onClick={handleResetLayout} title={isEn ? 'Auto-layout (reset drag & zoom)' : 'Авто-раскладка (сбросить перетаскивание и масштаб)'}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-[var(--bg-elevated)] text-[10px] font-mono text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer">
                <RotateCcw className="w-3 h-3" />
                <span>{isEn ? 'Auto' : 'Авто'}</span>
              </button>
            </div>
          )}

          <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`}
            onMouseDown={(e) => {
              if (compact || e.target !== svgRef.current) return;
              dragRef.current = { type: 'pan', startClientX: e.clientX, startClientY: e.clientY, origX: pan.x, origY: pan.y, moved: false };
            }}
            onMouseMove={handleMouseMove} onMouseUp={handleMouseUp} onMouseLeave={handleMouseUp}
            onWheel={(e) => {
              if (compact || (!e.ctrlKey && !e.metaKey)) return;
              e.preventDefault();
              setZoom((z) => Math.max(0.65, Math.min(2.0, Number((z + (e.deltaY < 0 ? 0.12 : -0.12)).toFixed(2)))));
            }}
            className={`w-full min-w-[660px] h-auto select-none ${!compact ? 'cursor-grab active:cursor-grabbing' : ''}`}
          >
            <g transform={`translate(${W / 2 + pan.x}, ${H / 2 + pan.y}) scale(${zoom}) translate(${-W / 2}, ${-H / 2})`}>
              {standBoxes.map((sb) => (
                <g key={sb.slug} className="pointer-events-none">
                  <rect x={sb.x} y={sb.y} width={sb.w} height={sb.h} rx="10"
                    fill="var(--bg-subtle)" stroke="var(--border-subtle)" strokeWidth="1" strokeDasharray="5 4" />
                  <text x={sb.x + 10} y={sb.y + 13} fontSize="8.5" fontFamily="monospace" fontWeight="700" fill="var(--text-muted)">
                    {`${isEn ? 'STAND' : 'СТЕНД'}: ${sb.slug.toUpperCase()} (${sb.count})`}
                  </text>
                </g>
              ))}

              {layerLabels.map((l) => (
                <text key={l.label} x={8} y={l.y + NODE_H / 2 + 3} fontSize="9" fill="var(--text-muted)" className="uppercase font-mono tracking-wider pointer-events-none">
                  {l.label}
                </text>
              ))}

              {resolvedLinks.map(({ lnk, a, b, pa, pb, x1, y1, x2, y2, sameLayer }) => {
                const active = hover === a.id || hover === b.id || popoverId === a.id || popoverId === b.id || selectedLink?.id === lnk.id;
                const isTraced = tracedLinkIds.has(lnk.id);
                const isShut = lnk.admin_state === 'SHUTDOWN';
                const midX = (x1 + x2) / 2, midY = sameLayer ? Math.max(12, pa.y - 18) : (y1 + y2) / 2;
                const cy1 = Math.round(y1 + (y2 - y1) * 0.42), cy2 = Math.round(y2 - (y2 - y1) * 0.42);
                const path = sameLayer ? `M${pa.x},${pa.y} Q${midX},${midY} ${pb.x},${pb.y}` : `M${x1},${y1} C${x1},${cy1} ${x2},${cy2} ${x2},${y2}`;
                const hasUnreachable = a.status === 'UNREACHABLE' || b.status === 'UNREACHABLE';
                const hasDrift = a.status === 'DRIFT_DETECTED' || b.status === 'DRIFT_DETECTED';

                const strokeColor = isShut ? '#e11d48' : selectedLink?.id === lnk.id ? '#3b82f6' : isTraced ? '#10b981'
                  : hasUnreachable ? '#e11d48' : hasDrift ? '#d97706' : active ? 'var(--text-primary)' : 'var(--border-strong)';
                const strokeDash = isShut ? '3 3' : lnk.is_inter_stand ? '6 3' : hasUnreachable ? '4 3' : hasDrift ? '6 3' : undefined;
                const topIsSource = pa.y <= pb.y;
                const pTop = shortPort(topIsSource ? lnk.source_port : lnk.target_port);
                const pBot = shortPort(topIsSource ? lnk.target_port : lnk.source_port);

                return (
                  <g key={lnk.id}
                    onClick={() => { if (compact && onExpandTopology) onExpandTopology(); else setSelectedLink(selectedLink?.id === lnk.id ? null : lnk); }}
                    className="cursor-pointer" opacity={hover === null && popoverId === null ? 1 : active || isTraced ? 1 : 0.22}>
                    <path d={path} stroke="transparent" strokeWidth={12} fill="none" />
                    <path d={path} stroke={strokeColor} strokeWidth={selectedLink?.id === lnk.id || active ? 2.4 : isTraced ? 2.2 : 1.6} strokeDasharray={strokeDash} fill="none" />
                    {active && !sameLayer && (
                      <>
                        <text x={x1 + 5} y={y1 + 10} fontSize="7" fontFamily="monospace" fill="var(--text-secondary)">{pTop}</text>
                        <text x={x2 + 5} y={y2 - 4} fontSize="7" fontFamily="monospace" fill="var(--text-secondary)">{pBot}</text>
                      </>
                    )}
                    {isTraced && !isShut && !hasUnreachable && (
                      <circle r="2.8" fill="#10b981" opacity="0.95"><animateMotion dur="2.6s" repeatCount="indefinite" path={path} /></circle>
                    )}
                  </g>
                );
              })}

              {resolvedLinks.map(({ lnk, a, b, bx, by }) => {
                const active = hover === a.id || hover === b.id || popoverId === a.id || popoverId === b.id || selectedLink?.id === lnk.id;
                const isTraced = tracedLinkIds.has(lnk.id);
                const isShut = lnk.admin_state === 'SHUTDOWN';
                const showEdgeBadge = isShut || active || (!compact && activeLayer !== 'ALL');
                if (!showEdgeBadge) return null;

                return (
                  <g key={`badge-${lnk.id}`} transform={`translate(${bx}, ${by})`}
                    onClick={() => { if (compact && onExpandTopology) onExpandTopology(); else setSelectedLink(selectedLink?.id === lnk.id ? null : lnk); }}
                    className="cursor-pointer" opacity={hover === null && popoverId === null ? 1 : active || isTraced ? 1 : 0.25}>
                    <rect x="-44" y="-7" width="88" height="14" rx="3" fill="var(--bg-surface)"
                      stroke={isShut ? '#e11d48' : selectedLink?.id === lnk.id || active ? 'var(--border-strong)' : 'var(--border-subtle)'} strokeWidth="1" />
                    <text x="0" y="2.2" fontSize="7.2" fontFamily="monospace" textAnchor="middle" fill={isShut ? '#e11d48' : isTraced ? '#10b981' : 'var(--text-primary)'}>
                      {isShut ? (isEn ? 'SHUTDOWN' : 'ВЫКЛ (SHUT)')
                        : activeLayer === 'BGP_EVPN' && lnk.bgp_asn ? lnk.bgp_asn
                        : activeLayer === 'L3_SUBNET' && lnk.subnet ? lnk.subnet
                        : `${shortPort(lnk.source_port)} ↔ ${shortPort(lnk.target_port)}`}
                    </text>
                  </g>
                );
              })}

              {nodes.map(({ d, meta, x, y }) => {
                const isDrained = Boolean(meta?.drained);
                const nodeLinks = resolvedLinks.filter((r) => r.a.id === d.id || r.b.id === d.id);
                const shutLinks = nodeLinks.filter((r) => r.lnk.admin_state === 'SHUTDOWN');
                const totCount = meta?.total_links ?? nodeLinks.length;
                const shutCount = meta?.shutdown_links ?? shutLinks.length;
                const allLinksShut = totCount > 0 && shutCount === totCount;
                const someLinksShut = shutCount > 0 && !allLinksShut;
                const effectiveStatus = allLinksShut ? 'UNREACHABLE' : (someLinksShut || isDrained) ? 'DRIFT_DETECTED' : (meta?.status || d.status);
                const color = STATUS_COLOR[effectiveStatus] ?? STATUS_COLOR.UNKNOWN;
                const isSelected = popoverId === d.id;
                const isHovered = hover === d.id;
                const matchesSearch = !searchQuery.trim() ||
                  `${d.hostname} ${d.management_ip} ${d.management_port || ''} ${d.platform} ${d.role} ${d.fabric_id || ''} ${meta?.bgp?.asn || ''}`
                    .toLowerCase().includes(searchQuery.trim().toLowerCase());

                const addrWithPort = d.management_port && d.management_port !== 22
                  ? `${d.management_ip}:${d.management_port}`
                  : d.management_ip;

                const statusSubline = allLinksShut || effectiveStatus === 'UNREACHABLE'
                  ? (isEn ? 'OFFLINE · ISOLATED' : 'ВЫКЛ · ИЗОЛИРОВАН')
                  : isDrained
                  ? (isEn ? 'DRAIN · TRAFFIC SHIFT' : 'DRAIN · УВОД ТРАФИКА')
                  : someLinksShut
                  ? (isEn ? `DEGRADED · DOWN ${shutCount}/${totCount}` : `РАССИНХРОН · ВЫКЛ ${shutCount}/${totCount}`)
                  : effectiveStatus === 'DRIFT_DETECTED'
                  ? (isEn ? `DRIFT · ${addrWithPort}` : `ДРЕЙФ · ${addrWithPort}`)
                  : addrWithPort;
                const sublineColor = allLinksShut || effectiveStatus === 'UNREACHABLE'
                  ? '#e11d48'
                  : isDrained || someLinksShut || effectiveStatus === 'DRIFT_DETECTED'
                  ? '#d97706'
                  : 'var(--text-secondary)';

                const asnOrRole = meta?.bgp?.asn ? `AS${meta.bgp.asn}` : d.role.toUpperCase();
                const loadVal = Math.round(meta?.load_pct ?? (d.sparkline?.[d.sparkline.length - 1] ?? 12));
                const syncTag = effectiveStatus === 'UNREACHABLE' ? 'DOWN' : effectiveStatus === 'DRIFT_DETECTED' ? 'DRIFT' : 'SYNC';
                const telemetryLine = `${asnOrRole} · :${d.management_port || 22} · CPU ${loadVal}% · ${syncTag}`;
                const protoLabel = resolveNodeProtocolLabel(d, meta);

                return (
                  <g key={d.id} transform={`translate(${x - NODE_W / 2},${y})`}
                    onMouseDown={(e) => {
                      e.stopPropagation();
                      dragRef.current = { type: 'node', nodeId: d.id, startClientX: e.clientX, startClientY: e.clientY, origX: x, origY: y, moved: false };
                    }}
                    onClick={() => { if (!dragRef.current?.moved) setPopoverId(popoverId === d.id ? null : d.id); }}
                    onDoubleClick={() => onSelectDevice(d.id)}
                    onMouseEnter={() => setHover(d.id)} onMouseLeave={() => setHover(null)}
                    className="cursor-pointer"
                    opacity={!matchesSearch ? 0.2 : hover === null && popoverId === null ? 1 : isHovered || isSelected ? 1 : 0.45}
                  >
                    <rect width={NODE_W} height={NODE_H} rx="6" fill="var(--bg-surface)"
                      stroke={
                        isSelected ? 'var(--text-primary)'
                        : allLinksShut || effectiveStatus === 'UNREACHABLE' ? '#e11d48'
                        : isDrained || someLinksShut ? '#d97706'
                        : isHovered ? 'var(--border-strong)'
                        : 'var(--border-subtle)'
                      }
                      strokeWidth={isSelected || isHovered || allLinksShut || isDrained || someLinksShut ? '1.6' : '1.1'}
                      strokeDasharray={allLinksShut || isDrained ? '4 2' : undefined} />
                    <circle cx="11" cy="15" r="3" fill={color} />
                    <text x="20" y="18" fontSize="10.5" fontWeight="600" fill="var(--text-primary)" letterSpacing="-0.01em">
                      {d.hostname.split('.')[0]}
                    </text>
                    <text x="20" y="29" fontSize="8" fill="var(--text-muted)" fontFamily="monospace">
                      {PLATFORM_NAMES[d.platform] ?? d.platform}
                    </text>
                    <text x="20" y="40" fontSize="7.6" fill={sublineColor} fontFamily="monospace">
                      {statusSubline}
                    </text>
                    <text x="20" y="49.5" fontSize="7" fill="var(--text-muted)" fontFamily="monospace">
                      {telemetryLine}
                    </text>
                    {VENDOR_BADGE[d.platform] && (
                      <g transform={`translate(${NODE_W - 43}, 6)`}>
                        <rect width="37" height="11.5" rx="2.5" fill={VENDOR_BADGE[d.platform].bg} stroke={VENDOR_BADGE[d.platform].stroke} strokeWidth="0.8" />
                        <text x="18.5" y="8.2" fontSize="7" fontWeight="700" textAnchor="middle" fill={VENDOR_BADGE[d.platform].text} fontFamily="monospace">
                          {VENDOR_BADGE[d.platform].label}
                        </text>
                      </g>
                    )}
                    <g transform={`translate(${NODE_W - 43}, 20)`}>
                      <rect width="37" height="11" rx="2.5" fill="var(--bg-elevated)" stroke="var(--border-subtle)" strokeWidth="0.8" />
                      <text x="18.5" y="7.8" fontSize="6.8" fontWeight="700" textAnchor="middle" fill="var(--text-secondary)" fontFamily="monospace">
                        {protoLabel}
                      </text>
                    </g>
                  </g>
                );
              })}
            </g>
          </svg>
        </div>
      )}

      {!compact && (
        <TopologyCommutationPanel
          devices={devices} links={resolvedLinks.map((r) => r.lnk)} selectedLink={selectedLink} onSelectLink={setSelectedLink}
          traceResult={traceResult} traceSrc={effectiveSrc} traceDst={effectiveDst}
          onChangeTrace={(s, t) => {
            setTraceSrc(s); setTraceDst(t);
            const stA = devices.find((d) => d.id === s)?.fabric_id || devices.find((d) => d.id === s)?.site || '';
            const stB = devices.find((d) => d.id === t)?.fabric_id || devices.find((d) => d.id === t)?.site || '';
            if (stA) setFocusedStand(stA);
            if (stB && stB !== stA) setCompareStand(stB);
          }}
          onRefreshGraph={handleRefreshGraphAndDevices} userRole={userRole} locale={locale}
        />
      )}

      {popoverNode && (
        <TopologyNodePopover
          device={{ ...popoverNode.d, status: (popoverNode.meta?.status as Device['status']) || popoverNode.d.status }}
          meta={popoverNode.meta} onClose={() => setPopoverId(null)}
          onSelectDevice={onSelectDevice} onInspectIntent={setInspectDeviceId}
          onRunDryRun={onRunDryRun ? handleDryRun : undefined} dryRunRunning={dryRunRunning} dryRunMsg={dryRunMsg}
          onSetTraceSource={(id) => { setTraceSrc(id); if (compact && onExpandTopology) onExpandTopology(); }}
          onSetTraceTarget={(id) => { setTraceDst(id); if (compact && onExpandTopology) onExpandTopology(); }}
          onToggleDrain={handleToggleDrain} drainBusy={drainBusy}
          onChangeProtocol={async (hostname, proto) => {
            setProtocolBusy(true);
            try {
              await api.setDeviceCollectorProtocol(hostname, proto as 'auto' | 'gnmi' | 'snmp' | 'prometheus_exporter' | 'ssh_cli');
              await fetchGraphAndTrace();
            } catch { /* ignore */ } finally { setProtocolBusy(false); }
          }}
          protocolBusy={protocolBusy} userRole={userRole} locale={locale}
        />
      )}

      {inspectDeviceId !== null && <DeviceDetailModal deviceId={inspectDeviceId} onClose={() => setInspectDeviceId(null)} />}
    </div>
  );
};

export const TopologyMap = React.memo(TopologyMapComponent);
