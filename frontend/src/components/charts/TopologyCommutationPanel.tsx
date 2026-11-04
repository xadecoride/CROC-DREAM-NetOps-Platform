import React, { useState } from 'react';
import {
  Activity, AlertTriangle, ArrowRight, CheckCircle2, Clock, GitCommit,
  Layers, Plus, Power, PowerOff, Route, Shield, Trash2, X,
} from 'lucide-react';
import {
  api, getErrorMessage, type Device, type TopologyGraphLink, type TopologyTraceResponse,
} from '../../api';
import type { Locale } from '../../i18n';
import { useTranslation } from './topologyLayout';

interface Props {
  devices: Device[];
  links: TopologyGraphLink[];
  selectedLink: TopologyGraphLink | null;
  onSelectLink: (link: TopologyGraphLink | null) => void;
  traceResult: TopologyTraceResponse | null;
  traceSrc: number;
  traceDst: number;
  onChangeTrace: (srcId: number, dstId: number) => void;
  onRefreshGraph: () => Promise<void>;
  userRole?: string;
  locale?: Locale;
}

export const TopologyCommutationPanel: React.FC<Props> = ({
  devices, links, selectedLink, onSelectLink, traceResult, traceSrc, traceDst,
  onChangeTrace, onRefreshGraph, userRole, locale: propLocale,
}) => {
  const { isEn } = useTranslation(propLocale);
  const isViewer = userRole === 'viewer';
  const readOnlyTitle = isEn ? 'Unavailable in read-only mode' : 'Недоступно в режиме просмотра';
  const [toggling, setToggling] = useState(false);
  const [autoRevert60s, setAutoRevert60s] = useState(false);
  const [spofConfirm, setSpofConfirm] = useState<{
    link: TopologyGraphLink;
    isolatedNodes: string[];
    message: string;
  } | null>(null);
  const [autoRevertNotice, setAutoRevertNotice] = useState<string | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [srcId, setSrcId] = useState<number>(devices[0]?.id ?? 0);
  const [dstId, setDstId] = useState<number>(devices[1]?.id ?? devices[0]?.id ?? 0);
  const [srcPort, setSrcPort] = useState('GE1/0/2');
  const [dstPort, setDstPort] = useState('Ethernet3');
  const [layer, setLayer] = useState('L3_SUBNET');
  const [subnet, setSubnet] = useState('10.10.99.0/31');
  const [bgpAsn] = useState('AS65001 ↔ AS65000');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleToggleLink = async (link: TopologyGraphLink, force = false) => {
    if (isViewer) return;
    setToggling(true); setErrorMsg(null); setAutoRevertNotice(null);
    const nextState = link.admin_state === 'SHUTDOWN' ? 'UP' : 'SHUTDOWN';
    try {
      const res = await api.toggleTopologyLink(
        link.id,
        nextState,
        nextState === 'SHUTDOWN'
          ? isEn ? 'Disabled by operator on topology map' : 'Отключено оператором на карте топологии'
          : isEn ? 'Restored by operator' : 'Восстановлено оператором',
        nextState === 'SHUTDOWN'
          ? { force, strictSpofGuard: !force, autoRevertSeconds: autoRevert60s ? 60 : undefined }
          : undefined,
      );
      setSpofConfirm(null);
      if (res.spof_warning && res.isolated_nodes?.length) {
        setSpofConfirm({
          link,
          isolatedNodes: res.isolated_nodes,
          message: isEn
            ? `SPOF Warning: disabling link isolates nodes: ${res.isolated_nodes.join(', ')}`
            : `Внимание (SPOF): отключение линка изолирует узлы: ${res.isolated_nodes.join(', ')}`,
        });
      }
      if (res.auto_revert_at) {
        setAutoRevertNotice(isEn ? 'Commit Confirmed: 60s link auto-revert timer active' : 'Commit Confirmed: авто-возврат линка через 60с активен');
      }
      await onRefreshGraph();
    } catch (err: unknown) {
      const msg = getErrorMessage(err, isEn ? 'Failed to toggle link state' : 'Не удалось переключить состояние связи');
      if (nextState === 'SHUTDOWN' && !force) {
        setSpofConfirm({
          link,
          isolatedNodes: [link.source_hostname.split('.')[0], link.target_hostname.split('.')[0]],
          message: msg,
        });
      } else {
        setErrorMsg(msg);
      }
    } finally {
      setToggling(false);
    }
  };

  const handleDeleteCustom = async (link: TopologyGraphLink) => {
    if (isViewer) return;
    setToggling(true); setErrorMsg(null);
    try {
      await api.deleteTopologyLink(link.id);
      onSelectLink(null);
      await onRefreshGraph();
    } catch (err: unknown) {
      setErrorMsg(getErrorMessage(err, isEn ? 'Error deleting link' : 'Ошибка удаления связи'));
    } finally {
      setToggling(false);
    }
  };

  const handleCreateLink = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isViewer) return;
    if (!srcId || !dstId || srcId === dstId) {
      setErrorMsg(isEn ? 'Select two different nodes to connect' : 'Выберите два разных узла для коммутации');
      return;
    }
    setToggling(true); setErrorMsg(null);
    try {
      await api.createTopologyLink({
        source_id: srcId, target_id: dstId, source_port: srcPort, target_port: dstPort,
        layer, subnet, protocol: layer === 'INTER_STAND' ? 'DCI eBGP EVPN Interconnect' : 'LLDP + OSPF + eBGP',
        bgp_asn: bgpAsn,
      });
      setShowAddForm(false);
      await onRefreshGraph();
    } catch (err: unknown) {
      setErrorMsg(getErrorMessage(err, isEn ? 'Failed to create link' : 'Не удалось создать связь'));
    } finally {
      setToggling(false);
    }
  };

  const selectCls =
    'px-2 py-1 rounded bg-[var(--bg-base)] border border-[var(--border-subtle)] text-[11px] font-mono text-[var(--text-primary)] outline-none focus:border-[var(--border-strong)]';

  return (
    <div className="mt-3 pt-3 border-t border-[var(--border-subtle)] space-y-3" data-testid="commutation-panel">
      {errorMsg && (
        <div className="px-3 py-2 rounded bg-rose-500/10 border border-rose-500/30 text-[11px] font-mono text-rose-600 dark:text-rose-300 flex items-center justify-between">
          <span>{errorMsg}</span>
          <button onClick={() => setErrorMsg(null)} className="text-rose-500 hover:text-[var(--text-primary)] cursor-pointer">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {spofConfirm && (
        <div
          data-testid="spof-warning-banner"
          className="px-3 py-2.5 rounded-lg bg-amber-500/10 border border-amber-500/40 text-[11px] font-mono text-amber-700 dark:text-amber-200 flex flex-wrap items-center justify-between gap-2 animate-fade-in"
        >
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0" />
            <div>
              <div className="font-semibold text-amber-700 dark:text-amber-300">
                {isEn ? 'Connectivity Loss Protection (Cut-Edge / SPOF Guard)' : 'Защита от потери связности (Cut-Edge / SPOF Guard)'}
              </div>
              <div className="text-[10px] opacity-90">
                {spofConfirm.message} ({isEn ? 'Isolated nodes' : 'Изолируемые узлы'}: {spofConfirm.isolatedNodes.join(', ')})
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              disabled={toggling || isViewer}
              data-testid="force-shutdown-btn"
              onClick={() => void handleToggleLink(spofConfirm.link, true)}
              title={isViewer ? readOnlyTitle : undefined}
              className={`px-2.5 py-1 rounded bg-rose-500/20 border border-rose-500/40 text-rose-700 dark:text-rose-200 font-semibold ${
                isViewer ? 'opacity-40 cursor-not-allowed' : 'hover:bg-rose-500/30 cursor-pointer'
              }`}
            >
              {isEn ? 'Force SHUTDOWN' : 'Принудительно отключить (Force SHUTDOWN)'}
            </button>
            <button type="button" onClick={() => setSpofConfirm(null)} className="p-1 text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer">
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}

      {autoRevertNotice && (
        <div className="px-3 py-1.5 rounded bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-[11px] font-mono text-[var(--text-primary)] flex items-center justify-between">
          <span className="inline-flex items-center gap-1.5">
            <Clock className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
            {autoRevertNotice}
          </span>
          <button onClick={() => setAutoRevertNotice(null)} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] cursor-pointer">
            <X className="w-3 h-3" />
          </button>
        </div>
      )}

      {selectedLink && (
        <div className="p-3 rounded-lg bg-[var(--bg-surface)] border border-[var(--border-strong)] space-y-2.5 animate-fade-in" data-testid="selected-link-inspector">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-xs font-mono font-semibold text-[var(--text-primary)]">
              <GitCommit className="w-4 h-4 text-[var(--text-muted)]" />
              <span>{selectedLink.source_hostname.split('.')[0]} [{selectedLink.source_port}]</span>
              <ArrowRight className="w-3.5 h-3.5 text-[var(--text-muted)]" />
              <span>{selectedLink.target_hostname.split('.')[0]} [{selectedLink.target_port}]</span>
              <span className={`px-1.5 py-0.5 rounded text-[10px] border ${selectedLink.admin_state === 'SHUTDOWN' ? 'bg-rose-500/15 text-rose-600 dark:text-rose-400 border-rose-500/30' : 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'}`}>
                {selectedLink.admin_state}
              </span>
              {selectedLink.is_inter_stand && (
                <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--bg-elevated)] text-[var(--text-secondary)] border border-[var(--border-subtle)]">
                  DCI: {selectedLink.source_stand} ↔ {selectedLink.target_stand}
                </span>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {selectedLink.admin_state !== 'SHUTDOWN' && (
                <label data-testid="auto-revert-60s-toggle" className="inline-flex items-center gap-1.5 px-2 py-1 rounded bg-[var(--bg-base)] border border-[var(--border-subtle)] text-[10px] font-mono text-[var(--text-secondary)] cursor-pointer">
                  <input type="checkbox" checked={autoRevert60s} onChange={(e) => setAutoRevert60s(e.target.checked)} className="accent-zinc-500" />
                  <Clock className="w-3 h-3" />
                  <span>{isEn ? 'Auto-revert 60s' : 'Авто-возврат 60с'}</span>
                </label>
              )}

              <button
                type="button"
                disabled={toggling || isViewer}
                onClick={() => void handleToggleLink(selectedLink)}
                data-testid="toggle-link-state-btn"
                title={isViewer ? readOnlyTitle : undefined}
                className={`px-2.5 py-1 rounded text-[11px] font-mono font-medium flex items-center gap-1.5 border transition-colors ${
                  isViewer ? 'opacity-40 cursor-not-allowed ' : 'cursor-pointer '
                }${
                  selectedLink.admin_state === 'SHUTDOWN'
                    ? 'bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-700 dark:text-emerald-300 border-emerald-500/30'
                    : 'bg-rose-500/15 hover:bg-rose-500/25 text-rose-700 dark:text-rose-300 border-rose-500/30'
                }`}
              >
                {selectedLink.admin_state === 'SHUTDOWN' ? (
                  <><Power className="w-3 h-3" /><span>{isEn ? 'Enable Link' : 'Включить линк'}</span></>
                ) : (
                  <><PowerOff className="w-3 h-3" /><span>{isEn ? 'Disable Link' : 'Выключить линк'}</span></>
                )}
              </button>
              {selectedLink.is_custom && (
                <button
                  type="button"
                  disabled={toggling || isViewer}
                  onClick={() => void handleDeleteCustom(selectedLink)}
                  data-testid="delete-custom-link-btn"
                  className={`p-1 rounded bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20 ${
                    isViewer ? 'opacity-40 cursor-not-allowed' : 'hover:bg-rose-500/20 cursor-pointer'
                  }`}
                  title={isViewer ? readOnlyTitle : isEn ? 'Delete custom link' : 'Удалить связь'}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              )}
              <button type="button" onClick={() => onSelectLink(null)} className="p-1 rounded text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer">
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px] font-mono">
            <div className="p-2 rounded bg-[var(--bg-base)] border border-[var(--border-subtle)]">
              <div className="text-[10px] text-[var(--text-muted)]">{isEn ? 'Subnet' : 'Подсеть'}</div>
              <div className="text-[var(--text-primary)] truncate">{selectedLink.subnet || 'L2 / LLDP'}</div>
            </div>
            <div className="p-2 rounded bg-[var(--bg-base)] border border-[var(--border-subtle)]">
              <div className="text-[10px] text-[var(--text-muted)]">BGP / VXLAN</div>
              <div className="text-[var(--text-primary)] truncate">
                {selectedLink.bgp_asn || 'L2/L3'}{selectedLink.vxlan_vni ? ` · VNI ${selectedLink.vxlan_vni}` : ''}
              </div>
            </div>
            <div className="p-2 rounded bg-[var(--bg-base)] border border-[var(--border-subtle)]">
              <div className="text-[10px] text-[var(--text-muted)]">ACL</div>
              <div className="text-[var(--text-primary)] flex items-center gap-1">
                <Shield className="w-3 h-3 text-emerald-500 shrink-0" />
                <span>{selectedLink.acl_in || 'Permit'}</span>
              </div>
            </div>
            <div className="p-2 rounded bg-[var(--bg-base)] border border-[var(--border-subtle)]">
              <div className="text-[10px] text-[var(--text-muted)]">{isEn ? 'MTU / Load' : 'MTU / Нагрузка'}</div>
              <div className="text-[var(--text-primary)]">MTU {selectedLink.mtu || 1500} · {selectedLink.load_pct}%</div>
            </div>
          </div>
        </div>
      )}

      <div className="p-3 rounded-lg bg-[var(--bg-surface)] border border-[var(--border-subtle)] space-y-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Route className="w-4 h-4 text-[var(--text-muted)]" />
            <span className="text-xs font-semibold text-[var(--text-primary)]">
              {isEn ? 'Packet Path Tracer' : 'Маршрут пакета'}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1.5 text-[11px] font-mono">
              <span className="text-[var(--text-muted)]">{isEn ? 'From:' : 'Откуда:'}</span>
              <select value={traceSrc} onChange={(e) => onChangeTrace(Number(e.target.value), traceDst)} className={selectCls} data-testid="trace-src-select">
                {devices.map((d) => <option key={d.id} value={d.id}>{d.hostname.split('.')[0]}</option>)}
              </select>
              <ArrowRight className="w-3 h-3 text-[var(--text-muted)]" />
              <span className="text-[var(--text-muted)]">{isEn ? 'To:' : 'Куда:'}</span>
              <select value={traceDst} onChange={(e) => onChangeTrace(traceSrc, Number(e.target.value))} className={selectCls} data-testid="trace-dst-select">
                {devices.map((d) => <option key={d.id} value={d.id}>{d.hostname.split('.')[0]}</option>)}
              </select>
            </div>

            {!isViewer && (
              <button
                type="button"
                onClick={() => setShowAddForm((v) => !v)}
                data-testid="open-add-link-btn"
                className="px-2 py-1 rounded bg-[var(--bg-elevated)] hover:bg-[var(--bg-panel)] border border-[var(--border-subtle)] hover:border-[var(--border-strong)] text-[11px] font-mono text-[var(--text-primary)] flex items-center gap-1 cursor-pointer"
              >
                <Plus className="w-3 h-3" />
                <span>{isEn ? 'Link' : 'Связь'}</span>
              </button>
            )}
          </div>
        </div>

        {showAddForm && !isViewer && (
          <form onSubmit={handleCreateLink} data-testid="add-topology-link-form" className="p-2.5 rounded bg-[var(--bg-elevated)] border border-[var(--border-subtle)] grid grid-cols-2 sm:grid-cols-7 gap-2 items-end text-[11px] font-mono">
            <div>
              <label className="block text-[10px] text-[var(--text-muted)] mb-0.5">{isEn ? 'Node A' : 'Узел А'}</label>
              <select value={srcId} onChange={(e) => setSrcId(Number(e.target.value))} className={`${selectCls} w-full`}>
                {devices.map((d) => <option key={d.id} value={d.id}>{d.hostname.split('.')[0]}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-[10px] text-[var(--text-muted)] mb-0.5">{isEn ? 'Port A' : 'Порт А'}</label>
              <input value={srcPort} onChange={(e) => setSrcPort(e.target.value)} className={`${selectCls} w-full`} />
            </div>
            <div>
              <label className="block text-[10px] text-[var(--text-muted)] mb-0.5">{isEn ? 'Node B' : 'Узел Б'}</label>
              <select value={dstId} onChange={(e) => setDstId(Number(e.target.value))} className={`${selectCls} w-full`}>
                {devices.map((d) => <option key={d.id} value={d.id}>{d.hostname.split('.')[0]}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-[10px] text-[var(--text-muted)] mb-0.5">{isEn ? 'Port B' : 'Порт Б'}</label>
              <input value={dstPort} onChange={(e) => setDstPort(e.target.value)} className={`${selectCls} w-full`} />
            </div>
            <div>
              <label className="block text-[10px] text-[var(--text-muted)] mb-0.5">{isEn ? 'Layer' : 'Слой'}</label>
              <select value={layer} onChange={(e) => setLayer(e.target.value)} className={`${selectCls} w-full`}>
                <option value="L3_SUBNET">{isEn ? 'Network (IP)' : 'Сеть (IP)'}</option>
                <option value="BGP_EVPN">BGP / VXLAN</option>
                <option value="L2_LLDP">{isEn ? 'Physical (L2)' : 'Физика (L2)'}</option>
                <option value="INTER_STAND">{isEn ? 'Stands (DCI)' : 'Стенды (DCI)'}</option>
              </select>
            </div>
            <div>
              <label className="block text-[10px] text-[var(--text-muted)] mb-0.5">{isEn ? 'Subnet' : 'Подсеть'}</label>
              <input value={subnet} onChange={(e) => setSubnet(e.target.value)} placeholder="10.10.99.0/31" className={`${selectCls} w-full`} />
            </div>
            <button type="submit" disabled={toggling} className="px-2.5 py-1 rounded bg-[var(--text-primary)] text-[var(--bg-base)] font-semibold hover:opacity-90 cursor-pointer">
              {isEn ? 'Create' : 'Создать'}
            </button>
          </form>
        )}

        {traceResult && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] font-mono">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border ${traceResult.reachable ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/25' : 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/25'}`}>
                  <CheckCircle2 className="w-3 h-3" />
                  {traceResult.reachable ? (isEn ? 'Path Reachable' : 'Связь активна') : isEn ? 'Path Broken' : 'Связь прервана'}
                </span>
                {traceResult.ecmp_paths && traceResult.ecmp_paths.length > 0 && (
                  <span className="px-2 py-0.5 rounded bg-[var(--bg-elevated)] text-[var(--text-secondary)] border border-[var(--border-subtle)]">
                    {isEn ? 'Paths' : 'Пути'}: {traceResult.ecmp_paths.join('  |  ')}
                  </span>
                )}
              </div>
              {traceResult.disabled_links.length > 0 && (
                <span className="text-amber-500">{isEn ? 'Disabled links' : 'Отключено связей'}: {traceResult.disabled_links.length}</span>
              )}
            </div>

            {traceResult.hops.length > 0 && (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                {traceResult.hops.map((hop) => (
                  <div key={`${hop.step}-${hop.device_id}`} className="p-2 rounded bg-[var(--bg-base)] border border-[var(--border-subtle)] flex items-center justify-between gap-2 text-[11px] font-mono">
                    <div>
                      <div className="font-semibold text-[var(--text-primary)]">
                        {hop.step}. {hop.hostname.split('.')[0]}
                      </div>
                      <div className="text-[10px] text-[var(--text-secondary)]">
                        {hop.ingress_port} → {hop.egress_port}
                      </div>
                    </div>
                    <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-[var(--text-secondary)] uppercase">
                      {hop.role}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {links.length > 0 && (
          <div className="pt-1 space-y-1.5">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] font-mono text-[var(--text-muted)] inline-flex items-center gap-1">
                <Layers className="w-3 h-3" />
                {isEn ? 'Links' : 'Связи'} ({links.length}):
              </span>
              {links.some((l) => l.admin_state === 'SHUTDOWN') && (
                <button
                  type="button"
                  disabled={toggling}
                  onClick={async () => {
                    setToggling(true); setErrorMsg(null);
                    try {
                      const down = links.filter((l) => l.admin_state === 'SHUTDOWN');
                      await Promise.all(down.map((l) => api.toggleTopologyLink(l.id, 'UP', isEn ? 'Restore all links' : 'Восстановление всех связей')));
                      await onRefreshGraph();
                    } catch (err: unknown) {
                      setErrorMsg(getErrorMessage(err, isEn ? 'Failed to enable links' : 'Не удалось включить связи'));
                    } finally {
                      setToggling(false);
                    }
                  }}
                  className="px-2 py-0.5 rounded text-[10px] font-mono font-medium bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-700 dark:text-emerald-300 border border-emerald-500/35 flex items-center gap-1 cursor-pointer disabled:opacity-50"
                >
                  <Power className="w-2.5 h-2.5" />
                  <span>{isEn ? 'Enable All' : 'Включить все'} ({links.filter((l) => l.admin_state === 'SHUTDOWN').length})</span>
                </button>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-1 max-h-28 overflow-y-auto pr-1">
              {links.map((lnk) => {
                const isSel = selectedLink?.id === lnk.id;
                const isDown = lnk.admin_state === 'SHUTDOWN';
                return (
                  <button
                    key={lnk.id}
                    type="button"
                    onClick={() => onSelectLink(isSel ? null : lnk)}
                    data-testid={`link-chip-${lnk.id}`}
                    className={`px-2 py-0.5 rounded text-[10px] font-mono border transition-colors flex items-center gap-1 cursor-pointer ${
                      isSel
                        ? 'bg-[var(--bg-panel)] text-[var(--text-primary)] border-[var(--border-strong)] font-semibold'
                        : isDown
                        ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/30 line-through'
                        : 'bg-[var(--bg-base)] text-[var(--text-secondary)] border-[var(--border-subtle)] hover:border-[var(--border-strong)] hover:text-[var(--text-primary)]'
                    }`}
                  >
                    <Activity className="w-2.5 h-2.5" />
                    <span>{lnk.source_hostname.split('.')[0]} ↔ {lnk.target_hostname.split('.')[0]}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
