import { useEffect, useState } from 'react';
import type { Device, TopologyGraphLink, TopologyGraphNode, TopologyGraphResponse } from '../../api';
import { translations, type Locale } from '../../i18n';

export function useTranslation(explicitLocale?: Locale) {
  const [storedLocale, setStoredLocale] = useState<Locale>(() => {
    if (typeof window === 'undefined') return 'ru';
    return (localStorage.getItem('netops_lang') as Locale) === 'en' ? 'en' : 'ru';
  });

  useEffect(() => {
    if (explicitLocale) return;
    const syncLocale = () => {
      const next = (localStorage.getItem('netops_lang') as Locale) === 'en' ? 'en' : 'ru';
      setStoredLocale((prev) => (prev === next ? prev : next));
    };
    window.addEventListener('storage', syncLocale);
    window.addEventListener('click', syncLocale);
    return () => {
      window.removeEventListener('storage', syncLocale);
      window.removeEventListener('click', syncLocale);
    };
  }, [explicitLocale]);

  const locale: Locale = explicitLocale || storedLocale;
  return {
    locale,
    isEn: locale === 'en',
    t: translations[locale],
  };
}

export const W = 960, H = 470, NODE_W = 156, NODE_H = 54;
export const POS_STORAGE_KEY = 'netops_topo_node_positions_v1';
export const STATUS_COLOR: Record<string, string> = {
  IN_SYNC: '#10b981',
  DRIFT_DETECTED: '#d97706',
  UNREACHABLE: '#e11d48',
  IN_PROGRESS: '#3b82f6',
  UNKNOWN: '#64748b',
};

const _VB = {
  bg: 'var(--bg-elevated)',
  stroke: 'var(--border-subtle)',
  text: 'var(--text-secondary)',
};

export const VENDOR_BADGE: Record<string, { bg: string; stroke: string; text: string; label: string }> = {
  arista_eos: { ..._VB, label: 'EOS' },
  cisco_iosxe: { ..._VB, label: 'IOS-XE' },
  huawei_vrp: { ..._VB, label: 'VRP' },
  eltex_esr: { ..._VB, label: 'ESR' },
  eltex_mes: { ..._VB, label: 'MES' },
  juniper_junos: { ..._VB, label: 'JUNOS' },
  yadro_kornfe: { ..._VB, label: 'KORNFE' },
  linux_server: { ..._VB, label: 'LINUX' },
};

export const LAYER_FILTERS = [
  { id: 'ALL', label: 'Все', labelEn: 'All' },
  { id: 'L2_LLDP', label: 'Физика (L2)', labelEn: 'Physical (L2)' },
  { id: 'L3_SUBNET', label: 'Сеть (IP)', labelEn: 'Network (IP)' },
  { id: 'BGP_EVPN', label: 'Маршруты (BGP)', labelEn: 'Routing (BGP)' },
  { id: 'INTER_STAND', label: 'Стенды (DCI)', labelEn: 'Stands (DCI)' },
] as const;

export const shortPort = (p: string) =>
  p.replace(/^GigabitEthernet/i, 'Gi').replace(/^Ethernet/i, 'Eth');

export const linkLoad = (a: Device, b: Device) =>
  Math.max(
    a.sparkline?.length ? a.sparkline[a.sparkline.length - 1] : 0,
    b.sparkline?.length ? b.sparkline[b.sparkline.length - 1] : 0,
  );

export function resolveNodeProtocolLabel(device: Device, meta?: TopologyGraphNode): string {
  const mode = meta?.telemetry_mode || 'auto';
  const rawProto = meta?.telemetry_protocol || 'ssh_cli';
  if (device.platform === 'huawei_vrp' && (mode === 'auto' || rawProto === 'ssh_cli' || rawProto === 'snmp')) {
    return 'CLI';
  }
  if (rawProto === 'gnmi') return 'gNMI';
  if (rawProto === 'snmp') return 'SNMP';
  if (rawProto === 'node_exporter' || rawProto === 'prometheus_exporter') return 'EXP';
  return 'CLI';
}

export interface ActiveLayer {
  roles: string[];
  y: number;
  label: string;
}

export interface StandBox {
  slug: string;
  idx: number;
  x: number;
  y: number;
  w: number;
  h: number;
  count: number;
}

export interface ResolvedLink {
  lnk: TopologyGraphLink;
  a: Device;
  b: Device;
  pa: { x: number; y: number };
  pb: { x: number; y: number };
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  bx: number;
  by: number;
  sameLayer: boolean;
}

export interface LayoutNode {
  d: Device;
  meta?: TopologyGraphNode;
  x: number;
  y: number;
}

export function computeTopologyLayout(
  microDevices: Device[],
  activeLayers: ActiveLayer[],
  graphData: TopologyGraphResponse | null,
  activeLayer: string,
  searchQuery: string,
  customPos: Record<number, { x: number; y: number }>,
): {
  nodes: LayoutNode[];
  resolvedLinks: ResolvedLink[];
  standBoxes: StandBox[];
  layerLabels: { label: string; y: number }[];
} {
  const autoPos = new Map<number, { x: number; y: number }>();
  const standSlugs = Array.from(new Set(microDevices.map((d) => d.fabric_id || d.site || 'croc-dc1-pod1')));
  const padX = 74, podW = (W - padX - 24) / Math.max(1, standSlugs.length);

  standSlugs.forEach((slug, sIdx) => {
    const podStartX = padX + sIdx * podW;
    const podDevices = microDevices.filter((d) => (d.fabric_id || d.site || 'croc-dc1-pod1') === slug);
    activeLayers.forEach((layer) => {
      const list = podDevices.filter(
        (d) => layer.roles.includes(d.role) || (layer.roles.includes('server') && d.platform === 'linux_server'),
      );
      list.forEach((d, i) =>
        autoPos.set(d.id, { x: Math.round(podStartX + ((i + 1) * podW) / (list.length + 1)), y: layer.y }),
      );
    });
  });

  const clampPt = (p: { x: number; y: number }) => ({
    x: Math.max(68 + NODE_W / 2, Math.min(W - NODE_W / 2 - 16, p.x)),
    y: Math.max(30, Math.min(H - NODE_H - 16, p.y)),
  });
  const pos = new Map<number, { x: number; y: number }>();
  autoPos.forEach((pt, id) => pos.set(id, customPos[id] ? clampPt(customPos[id]) : pt));

  const computedLayerLabels = activeLayers.map((l) => {
    const ys = microDevices
      .filter((d) => l.roles.includes(d.role) || (l.roles.includes('server') && d.platform === 'linux_server'))
      .map((d) => pos.get(d.id)?.y)
      .filter((y): y is number => y !== undefined);
    return { label: l.label, y: ys.length ? Math.round(ys.reduce((a, b) => a + b, 0) / ys.length) : l.y };
  });

  const boxes = standSlugs
    .map((slug, idx) => {
      const pts = microDevices
        .filter((d) => (d.fabric_id || d.site || 'croc-dc1-pod1') === slug && pos.has(d.id))
        .map((d) => pos.get(d.id)!);
      if (!pts.length) return null;
      const minX = Math.max(62, Math.min(...pts.map((p) => p.x - NODE_W / 2)) - 14);
      const maxX = Math.min(W - 8, Math.max(...pts.map((p) => p.x + NODE_W / 2)) + 14);
      const minY = Math.max(6, Math.min(...pts.map((p) => p.y)) - 22);
      const maxY = Math.min(H - 6, Math.max(...pts.map((p) => p.y + NODE_H)) + 12);
      return { slug, idx, x: minX, y: minY, w: maxX - minX, h: maxY - minY, count: pts.length };
    })
    .filter((b): b is NonNullable<typeof b> => b !== null);

  const byId = new Map(microDevices.map((d) => [d.id, d]));
  let rawLinks: TopologyGraphLink[] = [];
  if (graphData && graphData.links.length > 0) {
    rawLinks = graphData.links.filter((l) => pos.has(l.source_id) && pos.has(l.target_id));
  } else {
    const spine = microDevices.filter((d) => d.role === 'spine');
    const leaf = microDevices.filter((d) => d.role === 'leaf');
    const border = microDevices.filter((d) => ['border', 'border_firewall'].includes(d.role));
    const server = microDevices.filter((d) => d.role === 'server' || d.platform === 'linux_server');
    const torLeaves = leaf.slice(0, 2);
    const pairs: [Device, Device][] = [
      ...border.flatMap((b) => spine.map((s): [Device, Device] => [b, s])),
      ...spine.flatMap((s) =>
        leaf.filter((l) => (l.fabric_id || '') === (s.fabric_id || '')).map((l): [Device, Device] => [s, l]),
      ),
      ...server.flatMap((srv) => torLeaves.map((l): [Device, Device] => [l, srv])),
    ];
    rawLinks = pairs.map(([a, b], idx) => ({
      id: `link-${Math.min(a.id, b.id)}-${Math.max(a.id, b.id)}`,
      source_id: a.id,
      target_id: b.id,
      source_hostname: a.hostname,
      target_hostname: b.hostname,
      source_port: `eth${idx + 1}`,
      target_port: `eth${idx + 1}`,
      subnet: null,
      layer: 'L3_SUBNET',
      layers: ['L2_LLDP', 'L3_SUBNET', 'BGP_EVPN'],
      protocol: 'Clos',
      bgp_asn: null,
      vxlan_vni: null,
      is_inter_stand: (a.fabric_id || '') !== (b.fabric_id || ''),
      source_stand: a.fabric_id || '',
      target_stand: b.fabric_id || '',
      load_pct: linkLoad(a, b),
      admin_state: 'UP',
      oper_status: 'UP',
    }));
  }

  const filteredLinks = rawLinks.filter((l) => {
    if (activeLayer !== 'ALL' && !(l.layers || [l.layer]).includes(activeLayer) && l.layer !== activeLayer) return false;
    if (searchQuery.trim()) {
      const hay = `${l.source_hostname} ${l.target_hostname} ${l.source_port} ${l.target_port} ${l.subnet || ''} ${l.bgp_asn || ''} ${l.protocol} ${l.source_stand} ${l.target_stand}`.toLowerCase();
      if (!hay.includes(searchQuery.trim().toLowerCase())) return false;
    }
    return true;
  });

  const placedBadges: { x: number; y: number }[] = [];
  const builtLinks = filteredLinks.map((lnk) => {
    const a = byId.get(lnk.source_id)!;
    const b = byId.get(lnk.target_id)!;
    const pa = pos.get(lnk.source_id)!;
    const pb = pos.get(lnk.target_id)!;
    const sameLayer = Math.abs(pa.y - pb.y) < 15;
    const topFirst = pa.y <= pb.y;
    const x1 = topFirst ? pa.x : pb.x;
    const y1 = (topFirst ? pa.y : pb.y) + NODE_H;
    const x2 = topFirst ? pb.x : pa.x;
    const y2 = topFirst ? pb.y : pa.y;
    const dx = x2 - x1;
    const baseT = sameLayer ? 0.5 : Math.abs(dx) >= 320 ? (dx > 0 ? 0.22 : 0.78) : Math.abs(dx) > 25 ? (dx > 0 ? 0.34 : 0.66) : 0.5;
    const ptAt = (t: number) => ({
      x: sameLayer ? (pa.x + pb.x) / 2 : x1 + (x2 - x1) * t,
      y: sameLayer ? Math.max(14, pa.y - 18) : y1 + (y2 - y1) * t,
    });
    const free = (cx: number, cy: number) => !placedBadges.some((p) => Math.abs(p.x - cx) < 78 && Math.abs(p.y - cy) < 16);
    let { x: bx, y: by } = ptAt(baseT);
    const hitT = [baseT, 0.22, 0.78, 0.35, 0.65, 0.5, 0.16, 0.84, 0.28, 0.72, 0.42, 0.58].find((ct) => {
      const c = ptAt(ct);
      return free(c.x, c.y);
    });
    if (hitT !== undefined) ({ x: bx, y: by } = ptAt(hitT));
    else {
      const dyHit = [-16, 16, -32, 32].find((oy) => free(bx, Math.max(14, Math.min(H - 14, by + oy))));
      if (dyHit !== undefined) by = Math.max(14, Math.min(H - 14, by + dyHit));
    }
    placedBadges.push({ x: bx, y: by });
    return { lnk, a, b, pa, pb, x1, y1, x2, y2, bx: Math.round(bx), by: Math.round(by), sameLayer };
  });

  const nodeMetaMap = new Map((graphData?.nodes || []).map((n) => [n.id, n]));
  return {
    nodes: microDevices.filter((d) => pos.has(d.id)).map((d) => ({ d, meta: nodeMetaMap.get(d.id), ...pos.get(d.id)! })),
    resolvedLinks: builtLinks,
    standBoxes: boxes,
    layerLabels: computedLayerLabels,
  };
}
