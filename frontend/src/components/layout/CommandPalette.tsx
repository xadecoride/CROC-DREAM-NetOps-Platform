import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Search, Network, ShieldCheck, FlaskConical, Activity, Server } from 'lucide-react';
import type { Device, UserRole } from '../../api';
import { PLATFORM_NAMES, ROLE_NAMES, getVisibleTabs } from '../../constants';
import { translations, type Locale } from '../../i18n';

interface Props {
  open: boolean;
  onClose: () => void;
  devices: Device[];
  onOpenDevice: (id: number) => void;
  onOpenTab: (tab: string) => void;
  userRole?: UserRole;
  locale?: Locale;
}

export const CommandPalette: React.FC<Props> = ({
  open,
  onClose,
  devices,
  onOpenDevice,
  onOpenTab,
  userRole = 'viewer',
  locale = 'ru',
}) => {
  const [query, setQuery] = useState('');
  const [index, setIndex] = useState(0);
  const [prevOpen, setPrevOpen] = useState(open);
  const input = useRef<HTMLInputElement>(null);
  const t = translations[locale];

  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) {
      setQuery('');
      setIndex(0);
    }
  }

  const items = useMemo(() => {
    const visibleTabs = getVisibleTabs(userRole);
    const quickActions = [
      {
        key: 'qa-topology',
        label: locale === 'en' ? 'Open Topology Graph & Path Tracer' : 'Открыть карту топологии и трассировку (Topology)',
        hint: 'Быстрое действие · dashboard',
        searchText: 'topology топология граф связность trace ecmp checkpoint снимок dashboard',
        Icon: Network,
        run: () => onOpenTab('dashboard'),
      },
      {
        key: 'qa-drift',
        label: locale === 'en' ? 'Audit Configuration Drift' : 'Аудит и устранение конфигурационного дрейфа (Drift)',
        hint: 'Быстрое действие · drift',
        searchText: 'drift дрейф отклонения конфиг сверка безопасность audit',
        Icon: ShieldCheck,
        run: () => onOpenTab('drift'),
      },
      {
        key: 'qa-chaos',
        label: locale === 'en' ? 'Open Chaos Lab & Digital Twin' : 'Открыть полигон испытаний (Chaos Lab / Digital Twin)',
        hint: 'Быстрое действие · lab',
        searchText: 'chaos lab хаос полигон симуляция сбой тест twin',
        Icon: FlaskConical,
        run: () => onOpenTab('lab'),
      },
      {
        key: 'qa-jobs',
        label: locale === 'en' ? 'Open Automation Jobs & Pipeline' : 'Журнал задач автоматизации и транзакций (Jobs)',
        hint: 'Быстрое действие · jobs',
        searchText: 'jobs задачи деплой пайплайн лог dry-run транзакции',
        Icon: Activity,
        run: () => onOpenTab('jobs'),
      },
    ];

    const all = [
      ...devices.map((d) => {
        const bgpHint = d.role === 'spine' ? 'AS65000' : d.role === 'leaf' ? `AS6500${d.id} VXLAN` : 'AS65099';
        const portsHint = 'eth1 eth2 Ethernet1 Ethernet2 GE1/0/1 GE1/0/2 xe-0/0/0 Loopback0';
        return {
          key: `d${d.id}`,
          label: d.hostname,
          hint: `${PLATFORM_NAMES[d.platform] ?? d.platform} · ${d.management_ip}:${d.management_port}${d.fabric_id ? ` · ${d.fabric_id}` : ''}`,
          searchText: `${d.hostname} ${d.management_ip} ${d.management_port} ${d.platform} ${PLATFORM_NAMES[d.platform] ?? ''} ${d.role} ${ROLE_NAMES[d.role] ?? ''} ${d.status} ${d.site ?? ''} ${d.fabric_id ?? ''} ${d.rack ?? ''} ${bgpHint} ${portsHint}`.toLowerCase(),
          Icon: Server,
          run: () => onOpenDevice(d.id),
        };
      }),
      ...quickActions,
      ...visibleTabs.map((tab) => ({
        key: tab.id,
        label: t[tab.labelKey],
        hint: locale === 'en' ? 'Section' : 'Раздел',
        searchText: `${t[tab.labelKey]} ${tab.id} ${tab.groupKey}`.toLowerCase(),
        Icon: tab.Icon,
        run: () => onOpenTab(tab.id),
      })),
    ];
    const q = query.trim().toLowerCase();
    if (!q) return all.slice(0, 10);
    const tokens = q.split(/\s+/).filter(Boolean);
    return all
      .filter((item) => tokens.every((tok) => item.searchText.includes(tok)))
      .slice(0, 10);
  }, [query, devices, onOpenDevice, onOpenTab, userRole, t, locale]);

  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);

  if (!open) return null;

  const pick = (i: number) => {
    items[i]?.run();
    onClose();
  };

  return (
    <div className="fixed inset-0 z-[60] bg-black/60 flex items-start justify-center pt-24" onClick={onClose}>
      <div
        data-testid="command-palette-modal"
        className="w-[560px] max-w-[92vw] rounded-xl bg-[#0c0e14] border border-white/[0.1] shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-4 border-b border-white/[0.06]">
          <Search className="w-4 h-4 text-zinc-500" />
          <input
            ref={input}
            value={query}
            data-testid="command-palette-input"
            onChange={(e) => {
              setQuery(e.target.value);
              setIndex(0);
            }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') setIndex((i) => Math.min(i + 1, items.length - 1));
              else if (e.key === 'ArrowUp') setIndex((i) => Math.max(i - 1, 0));
              else if (e.key === 'Enter') pick(index);
              else if (e.key === 'Escape') onClose();
            }}
            placeholder="Поиск: устройство, IP, порт (GE1/0/1), BGP (AS65000), роль (spine/leaf), стенд..."
            className="flex-1 bg-transparent py-3 text-sm text-zinc-100 focus:outline-none"
          />
        </div>
        <ul className="py-1 max-h-[65vh] overflow-y-auto">
          {items.map((it, i) => {
            const Icon = it.Icon;
            return (
              <li key={it.key}>
                <button
                  onMouseEnter={() => setIndex(i)}
                  onClick={() => pick(i)}
                  className={`w-full flex items-center justify-between gap-2 px-4 py-2 text-[13px] cursor-pointer ${
                    i === index ? 'bg-white/[0.06] text-white' : 'text-zinc-300'
                  }`}
                >
                  <span className="flex items-center gap-2 truncate">
                    <Icon className="w-3.5 h-3.5 text-cyan-400 shrink-0" />
                    <span className="truncate">{it.label}</span>
                  </span>
                  <span className="text-xs text-zinc-500 font-mono shrink-0">{it.hint}</span>
                </button>
              </li>
            );
          })}
          {items.length === 0 && (
            <li className="px-4 py-6 text-center text-sm text-zinc-500">Ничего не найдено</li>
          )}
        </ul>
      </div>
    </div>
  );
};
