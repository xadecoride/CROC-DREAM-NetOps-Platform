import React, { useEffect, useMemo, useState, useCallback } from 'react';
import { Activity, ExternalLink, RefreshCw } from 'lucide-react';
import { api, type Device, type StandDeviceTelemetrySeries, type StandRead } from '../../api';

export interface GrafanaTelemetryChartProps {
  mode?: 'stand' | 'device';
  devices?: Device[];
  device?: Device;
  hostname?: string;
  fabricId?: string;
  activeStand?: string;
  stands?: StandRead[];
  locale?: string;
  onOpenDevice?: (id: number) => void;
  points?: number;
  title?: string;
  compact?: boolean;
}

type MetricKey = 'cpu_pct' | 'memory_pct' | 'uplink_util_pct';

interface ChartSeries {
  id: string;
  label: string;
  color: string;
  points: number[];
  last: number;
  avg: number;
  max: number;
}

const STAND_PALETTE = [
  '#38bdf8', '#a855f7', '#10b981', '#f59e0b', '#ec4899', '#22d3ee', '#84cc16', '#f97316',
];

const METRIC_META: Record<MetricKey, { label: string; short: string; promExpr: string; color: string }> = {
  cpu_pct: {
    label: 'Switch & Server CPU Utilization (%)',
    short: 'CPU %',
    promExpr: 'netops_device_cpu_utilization_percent',
    color: '#a855f7',
  },
  memory_pct: {
    label: 'Switch & Server Memory Utilization (%)',
    short: 'RAM %',
    promExpr: 'netops_device_memory_utilization_percent',
    color: '#10b981',
  },
  uplink_util_pct: {
    label: 'Uplink & Fabric Interface Load (%)',
    short: 'Uplink %',
    promExpr: 'netops_device_uplink_utilization_percent',
    color: '#f59e0b',
  },
};

function buildFallbackSeries(dev: Device, pointsCount: number): {
  cpu_pct: number[];
  memory_pct: number[];
  uplink_util_pct: number[];
} {
  const base = dev.sparkline && dev.sparkline.length > 0 ? dev.sparkline.slice(-pointsCount) : [];
  return { cpu_pct: base, memory_pct: base, uplink_util_pct: base };
}

function buildSmoothSvgPath(pts: { x: number; y: number }[]): string {
  if (pts.length === 0) return '';
  if (pts.length === 1) return `M${pts[0].x.toFixed(1)},${pts[0].y.toFixed(1)}`;
  let d = `M${pts[0].x.toFixed(1)},${pts[0].y.toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i];
    const p1 = pts[i + 1];
    const cx = ((p0.x + p1.x) / 2).toFixed(1);
    d += ` C${cx},${p0.y.toFixed(1)} ${cx},${p1.y.toFixed(1)} ${p1.x.toFixed(1)},${p1.y.toFixed(1)}`;
  }
  return d;
}

export const GrafanaTelemetryChart: React.FC<GrafanaTelemetryChartProps> = ({
  mode: propMode,
  devices = [],
  device: propDevice,
  hostname,
  fabricId: propFabricId,
  activeStand,
  points = 24,
  title,
  compact = false,
}) => {
  const device = useMemo(
    () =>
      propDevice ||
      (hostname ? devices.find((d) => d.hostname === hostname) : undefined) ||
      (hostname && devices.length === 1 ? devices[0] : undefined),
    [propDevice, hostname, devices]
  );
  const mode = propMode || (device || hostname ? 'device' : 'stand');
  const fabricId = propFabricId || (activeStand && activeStand !== 'all' ? activeStand : undefined);

  const [selectedMetric, setSelectedMetric] = useState<MetricKey>('cpu_pct');
  const [remoteRows, setRemoteRows] = useState<StandDeviceTelemetrySeries[]>([]);
  const [timestamps, setTimestamps] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  const loadTelemetry = useCallback(async () => {
    setLoading(true);
    try {
      const targetFabric = mode === 'device' ? (device?.fabric_id || undefined) : fabricId;
      const res = await api.getStandTelemetrySeries(targetFabric, points);
      const rawDevices = res?.devices;
      const list = Array.isArray(rawDevices)
        ? rawDevices
        : rawDevices && typeof rawDevices === 'object'
        ? Object.values(rawDevices)
        : [];
      if (list.length > 0) {
        setRemoteRows(list);
        if (Array.isArray(res.timestamps)) setTimestamps(res.timestamps);
        return;
      }
    } catch {
      // Fallback to per-device series or sparklines if stand-series is warming up
    } finally {
      setLoading(false);
    }

    if (mode === 'device' && device) {
      try {
        const [c, m, u] = await Promise.all([
          api.getDeviceTelemetrySeries(device.hostname, 'cpu_pct', points, device.role),
          api.getDeviceTelemetrySeries(device.hostname, 'memory_pct', points, device.role),
          api.getDeviceTelemetrySeries(device.hostname, 'uplink_util_pct', points, device.role),
        ]);
        if (c.points.length || m.points.length || u.points.length) {
          setRemoteRows([{
            id: device.id,
            hostname: device.hostname,
            role: device.role,
            platform: device.platform,
            fabric_id: device.fabric_id || 'croc-dc1-pod1',
            cpu_pct: c.points,
            memory_pct: m.points,
            uplink_util_pct: u.points,
          }]);
        }
      } catch {
        // Use local fallback
      }
    }
  }, [mode, device, fabricId, points]);

  useEffect(() => {
    void loadTelemetry();
    const timer = window.setInterval(() => void loadTelemetry(), 15000);
    return () => window.clearInterval(timer);
  }, [loadTelemetry]);

  const seriesList: ChartSeries[] = useMemo(() => {
    const calcStats = (arr: number[]) => {
      if (!arr.length) return { last: 0, avg: 0, max: 0 };
      const last = arr[arr.length - 1];
      const sum = arr.reduce((a, b) => a + b, 0);
      return {
        last: Number(last.toFixed(1)),
        avg: Number((sum / arr.length).toFixed(1)),
        max: Number(Math.max(...arr).toFixed(1)),
      };
    };

    if (mode === 'device' && (device || hostname)) {
      const targetHost = device?.hostname || hostname || '';
      const match = remoteRows.find((r) => r.hostname === targetHost);
      const fb = device ? buildFallbackSeries(device, points) : { cpu_pct: [], memory_pct: [], uplink_util_pct: [] };
      const cpuPts = (match?.cpu_pct?.length ? match.cpu_pct : match?.series?.cpu_pct?.length ? match.series.cpu_pct : fb.cpu_pct);
      const memPts = (match?.memory_pct?.length ? match.memory_pct : match?.series?.memory_pct?.length ? match.series.memory_pct : fb.memory_pct);
      const upPts = (match?.uplink_util_pct?.length ? match.uplink_util_pct : match?.series?.uplink_util_pct?.length ? match.series.uplink_util_pct : fb.uplink_util_pct);
      return [
        { id: 'cpu', label: 'CPU Utilization %', color: '#38bdf8', points: cpuPts, ...calcStats(cpuPts) },
        { id: 'mem', label: 'Memory (RAM) %', color: '#10b981', points: memPts, ...calcStats(memPts) },
        { id: 'uplink', label: 'Uplink Traffic %', color: '#f59e0b', points: upPts, ...calcStats(upPts) },
      ];
    }

    const pool = devices.length > 0 ? devices : [];
    const byHost = new Map(remoteRows.map((r) => [r.hostname, r]));
    const items = pool.length > 0 ? pool : remoteRows.map((r, i) => ({
      id: r.id ?? i + 1,
      hostname: r.hostname,
      role: r.role as Device['role'],
      platform: r.platform as Device['platform'],
      fabric_id: r.fabric_id,
      sparkline: r.cpu_pct,
    } as Device));

    return items.slice(0, 12).map((dev, idx) => {
      const rem = byHost.get(dev.hostname);
      const fb = buildFallbackSeries(dev, points);
      const raw = rem
        ? (rem[selectedMetric] || rem.series?.[selectedMetric] || fb[selectedMetric])
        : fb[selectedMetric];
      const pts = raw && raw.length > 0 ? raw : fb[selectedMetric];
      return {
        id: dev.hostname,
        label: dev.hostname.split('.')[0],
        color: STAND_PALETTE[idx % STAND_PALETTE.length],
        points: pts,
        ...calcStats(pts),
      };
    });
  }, [mode, device, devices, remoteRows, points, selectedMetric]);

  const timeLabels = useMemo(() => {
    const count = Math.max(6, ...seriesList.map((s) => s.points.length), points);
    if (timestamps.length >= count) return timestamps.slice(-count);
    const now = Date.now();
    return Array.from({ length: count }, (_, idx) => {
      const d = new Date(now - (count - 1 - idx) * 15000);
      return d.toTimeString().slice(0, 5);
    });
  }, [seriesList, timestamps, points]);

  const maxDataVal = useMemo(() => {
    const allVals = seriesList.flatMap((s) => s.points);
    const peak = allVals.length > 0 ? Math.max(...allVals) : 30;
    return Math.min(100, Math.max(40, Math.ceil((peak * 1.25) / 10) * 10));
  }, [seriesList]);

  const host = typeof window !== 'undefined' ? window.location.hostname || 'localhost' : 'localhost';
  const promExpr = mode === 'device' && device
    ? `{hostname="${device.hostname}"}`
    : METRIC_META[selectedMetric].promExpr;
  const vmuiUrl = `http://${host}:8428/vmui/?#/?g0.expr=${encodeURIComponent(promExpr)}`;
  const grafanaUrl = `http://${host}:3000`;

  const W = 760;
  const H = compact ? 150 : 185;
  const PAD_L = 38;
  const PAD_R = 12;
  const PAD_T = 10;
  const PAD_B = 22;
  const plotW = W - PAD_L - PAD_R;
  const plotH = H - PAD_T - PAD_B;
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((t) => Math.round(maxDataVal * t));

  return (
    <div
      data-testid="grafana-telemetry-chart"
      className="rounded-xl border border-white/[0.08] bg-[#0b0d13] p-3.5 space-y-2.5 select-none"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Activity className="w-3.5 h-3.5 text-cyan-400 shrink-0" />
          <span className="text-xs font-semibold text-zinc-100 truncate">
            {title || (mode === 'device' && device
              ? `Телеметрия узла ${device.hostname.split('.')[0]} (CPU / RAM / Uplink)`
              : METRIC_META[selectedMetric].label)}
          </span>
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            VictoriaMetrics TSDB
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {mode === 'stand' && (
            <div className="inline-flex rounded-lg bg-[#08090c] p-0.5 border border-white/[0.07]">
              {(['cpu_pct', 'memory_pct', 'uplink_util_pct'] as MetricKey[]).map((mKey) => (
                <button
                  key={mKey}
                  type="button"
                  onClick={() => setSelectedMetric(mKey)}
                  data-testid={`telemetry-metric-${mKey}`}
                  className={`px-2 py-0.5 rounded-md text-[10px] font-mono transition-colors cursor-pointer ${
                    selectedMetric === mKey
                      ? 'bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 font-semibold'
                      : 'text-zinc-400 hover:text-zinc-200'
                  }`}
                >
                  {METRIC_META[mKey].short}
                </button>
              ))}
            </div>
          )}

          <button
            type="button"
            onClick={() => void loadTelemetry()}
            title="Обновить серию"
            className="p-1 rounded-md bg-white/[0.03] hover:bg-white/[0.08] border border-white/[0.07] text-zinc-400 hover:text-zinc-100 cursor-pointer"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin text-cyan-400' : ''}`} />
          </button>

          <a
            href={vmuiUrl}
            target="_blank"
            rel="noreferrer"
            data-testid="open-vmui-link"
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-violet-500/10 hover:bg-violet-500/20 border border-violet-500/25 text-[10px] font-mono text-violet-300 transition-colors"
            title="Открыть прямой PromQL-график в VictoriaMetrics VMUI (:8428)"
          >
            <span>VMUI :8428</span>
            <ExternalLink className="w-2.5 h-2.5" />
          </a>

          <a
            href={grafanaUrl}
            target="_blank"
            rel="noreferrer"
            data-testid="open-grafana-link"
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/25 text-[10px] font-mono text-amber-300 transition-colors"
            title="Открыть дашборд CROC DREAM NOC в Grafana (:3000)"
          >
            <span>Grafana :3000</span>
            <ExternalLink className="w-2.5 h-2.5" />
          </a>
        </div>
      </div>

      <div className="relative w-full overflow-hidden">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto overflow-visible">
          {yTicks.map((val) => {
            const y = PAD_T + plotH - (val / maxDataVal) * plotH;
            return (
              <g key={val}>
                <line
                  x1={PAD_L}
                  y1={y}
                  x2={W - PAD_R}
                  y2={y}
                  stroke="rgba(255,255,255,0.06)"
                  strokeDasharray={val === 0 ? undefined : '3 3'}
                />
                <text
                  x={PAD_L - 6}
                  y={y + 3}
                  textAnchor="end"
                  fontSize="9"
                  fontFamily="monospace"
                  fill="#71717a"
                >
                  {val}%
                </text>
              </g>
            );
          })}

          {timeLabels.map((lbl, idx) => {
            const step = Math.max(1, Math.floor((timeLabels.length - 1) / 5));
            if (idx % step !== 0 && idx !== timeLabels.length - 1) return null;
            const x = PAD_L + (idx / Math.max(1, timeLabels.length - 1)) * plotW;
            return (
              <text
                key={`${lbl}-${idx}`}
                x={x}
                y={H - 4}
                textAnchor="middle"
                fontSize="8.5"
                fontFamily="monospace"
                fill="#71717a"
              >
                {lbl}
              </text>
            );
          })}

          {seriesList.map((s) => {
            if (!s.points.length) return null;
            const coords = s.points.map((v, idx) => ({
              x: PAD_L + (idx / Math.max(1, s.points.length - 1)) * plotW,
              y: PAD_T + plotH - (Math.min(maxDataVal, Math.max(0, v)) / maxDataVal) * plotH,
            }));
            const linePath = buildSmoothSvgPath(coords);
            const areaPath = `${linePath} L${coords[coords.length - 1].x.toFixed(1)},${(PAD_T + plotH).toFixed(1)} L${coords[0].x.toFixed(1)},${(PAD_T + plotH).toFixed(1)} Z`;
            return (
              <g key={s.id}>
                <path d={areaPath} fill={s.color} fillOpacity="0.14" />
                <path d={linePath} fill="none" stroke={s.color} strokeWidth="1.8" />
              </g>
            );
          })}
        </svg>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-1 border-t border-white/[0.06] text-[10px] font-mono">
        {seriesList.map((s) => (
          <div key={s.id} className="inline-flex items-center gap-1.5 text-zinc-300">
            <span className="w-2.5 h-1 rounded-sm shrink-0" style={{ backgroundColor: s.color }} />
            <span className="font-semibold text-zinc-100">{s.label}</span>
            <span className="text-zinc-400">
              last <strong className="text-zinc-200">{s.last}%</strong> · avg {s.avg}% · max {s.max}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};
