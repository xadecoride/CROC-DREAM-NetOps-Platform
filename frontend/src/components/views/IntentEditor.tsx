import React, { useEffect, useState, useMemo, useCallback } from 'react';
import { Editor } from '@monaco-editor/react';
import { Save, RotateCcw, AlertTriangle, CheckCircle2, Plus, GitBranch, RefreshCw } from 'lucide-react';
import { api, getErrorMessage } from '../../api';
import type { IntentSaveResult } from '../../api';
import type { Locale } from '../../i18n';
import type { AppTheme } from '../layout/Header';
import { useMonacoTheme } from './DiffViewerFallbacks';

interface Props {
  deviceId: number;
  canEdit: boolean;
  theme?: AppTheme;
  locale?: Locale;
}

const SNIPPETS: { labelRu: string; labelEn: string; section: 'interfaces' | 'bgp' | 'acls'; block: string }[] = [
  {
    labelRu: 'L3 Интерфейс',
    labelEn: 'L3 Interface',
    section: 'interfaces',
    block: [
      '  - name: Ethernet3',
      '    description: Uplink / Peer Link',
      '    enabled: true',
      '    mode: l3',
      '    ipv4_address: 10.10.99.0/31',
      '    mtu: 9216',
    ].join('\n'),
  },
  {
    labelRu: 'L2 VLAN Порт',
    labelEn: 'L2 VLAN Port',
    section: 'interfaces',
    block: [
      '  - name: Ethernet4',
      '    description: Server Access Port',
      '    enabled: true',
      '    mode: l2',
      '    access_vlan: 100',
      '    mtu: 1500',
    ].join('\n'),
  },
  {
    labelRu: 'Loopback',
    labelEn: 'Loopback',
    section: 'interfaces',
    block: [
      '  - name: Loopback1',
      '    description: VTEP / Service Loopback',
      '    enabled: true',
      '    mode: l3',
      '    ipv4_address: 10.255.255.101/32',
    ].join('\n'),
  },
  {
    labelRu: 'BGP Сосед',
    labelEn: 'BGP Neighbor',
    section: 'bgp',
    block: [
      '    - peer_ip: 10.10.99.1',
      '      remote_asn: 65199',
      '      description: Peer Router',
      '      announced_prefixes:',
      '        - 10.255.255.0/24',
    ].join('\n'),
  },
  {
    labelRu: 'ACL Фильтр',
    labelEn: 'ACL Filter',
    section: 'acls',
    block: [
      '  - name: PROTECT_CONTROL_PLANE',
      '    rules:',
      '      - sequence: 10',
      '        action: permit',
      '        protocol: tcp',
      '        source: 10.0.0.0/8',
      '        destination: any',
      '        dst_port: 179',
      '      - sequence: 20',
      '        action: permit',
      '        protocol: ip',
      '        source: any',
      '        destination: any',
    ].join('\n'),
  },
];

/** Редактор intent-YAML устройства (Git SoT). Сохранение валидируется на бэкенде. */
export const IntentEditor: React.FC<Props> = ({ deviceId, canEdit, theme = 'dark', locale = 'ru' }) => {
  const isEn = locale === 'en';
  const monacoTheme = useMonacoTheme(theme);
  const [original, setOriginal] = useState('');
  const [text, setText] = useState('');
  const [editable, setEditable] = useState(true);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<IntentSaveResult | null>(null);

  const loadSource = useCallback(async () => {
    try {
      const src = await api.getIntentSource(deviceId);
      setOriginal(src.yaml);
      setText(src.yaml);
      setEditable(src.editable);
      setError('');
      setResult(null);
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [deviceId]);

  useEffect(() => {
    setLoading(true);
    void loadSource();
  }, [loadSource]);

  const dirty = text !== original;
  const writable = canEdit && editable;

  const liveHint = useMemo(() => {
    if (!text.trim()) return isEn ? 'Empty YAML (hostname required)' : 'Пустой YAML (нужен hostname)';
    if (text.includes('\t')) return isEn ? 'Tabs are forbidden — use 2 spaces' : 'Табуляция (Tab) запрещена — используйте 2 пробела';
    if (!/^hostname:\s*\S+/m.test(text)) return isEn ? 'Specify root field hostname: <fqdn>' : 'Укажите корневое поле hostname: <fqdn>';
    return null;
  }, [text, isEn]);

  const insertSnippet = (snippet: (typeof SNIPPETS)[number]) => {
    setResult(null);
    setText((prev) => {
      const clean = prev.replace(/\s+$/, '');
      if (snippet.section === 'interfaces') {
        if (/^interfaces:\s*\[\s*\]/m.test(clean)) {
          return clean.replace(/^interfaces:\s*\[\s*\]/m, `interfaces:\n${snippet.block}`) + '\n';
        }
        if (/^interfaces:/m.test(clean)) {
          const lines = clean.split('\n');
          const idx = lines.findIndex((l) => /^interfaces:/.test(l));
          lines.splice(idx + 1, 0, snippet.block);
          return lines.join('\n') + '\n';
        }
        return `${clean}\n\ninterfaces:\n${snippet.block}\n`;
      }
      if (snippet.section === 'bgp') {
        if (/^\s+neighbors:/m.test(clean)) {
          const lines = clean.split('\n');
          const idx = lines.findIndex((l) => /^\s+neighbors:/.test(l));
          lines.splice(idx + 1, 0, snippet.block);
          return lines.join('\n') + '\n';
        }
        return `${clean}\n\nbgp:\n  asn: 65101\n  router_id: 10.255.0.1\n  neighbors:\n${snippet.block}\n`;
      }
      if (/^acls:\s*\[\s*\]/m.test(clean)) {
        return clean.replace(/^acls:\s*\[\s*\]/m, `acls:\n${snippet.block}`) + '\n';
      }
      if (/^acls:/m.test(clean)) {
        return `${clean}\n${snippet.block}\n`;
      }
      return `${clean}\n\nacls:\n${snippet.block}\n`;
    });
  };

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const res = await api.saveIntentSource(deviceId, text);
      setResult(res);
      if (res.saved) {
        setOriginal(text);
        api.emitGitRefresh();
      }
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const handleSyncGit = async () => {
    if (syncing) return;
    setSyncing(true);
    setError('');
    try {
      await api.syncGitSot();
      await loadSource();
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setSyncing(false);
    }
  };

  if (loading) {
    return (
      <div className="p-8 text-[var(--text-muted)] font-mono">
        {isEn ? 'Loading intent…' : 'Загрузка intent…'}
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full min-h-0 gap-2 p-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs font-mono">
        <span className="text-[var(--text-secondary)] inline-flex items-center gap-1.5">
          <GitBranch className="w-3.5 h-3.5 text-[var(--text-secondary)] shrink-0" />
          <span>
            {writable
              ? 'Git SoT · intent/devices/*.yaml'
              : editable
                ? isEn
                  ? 'Read-only (Admin / Owner required)'
                  : 'Только чтение (требуется Admin / Owner)'
                : isEn
                  ? 'Read-only (no template)'
                  : 'Только чтение (нет шаблона)'}
          </span>
        </span>
        {writable && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleSyncGit}
              disabled={syncing || saving}
              className="px-2.5 py-1 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-subtle)] hover:border-[var(--border-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-40 flex items-center gap-1.5 cursor-pointer transition"
              title={isEn ? 'Sync with Git SoT and reload YAML' : 'Синхронизировать с Git SoT и перечитать YAML'}
            >
              <RefreshCw className={`w-3.5 h-3.5 ${syncing ? 'animate-spin' : ''}`} />
              <span>{syncing ? 'Sync…' : 'Git Sync'}</span>
            </button>
            <button
              onClick={() => {
                setText(original);
                setResult(null);
              }}
              disabled={!dirty || saving}
              className="px-2.5 py-1 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-subtle)] hover:border-[var(--border-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-40 flex items-center gap-1.5 cursor-pointer transition"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>{isEn ? 'Reset' : 'Сбросить'}</span>
            </button>
            <button
              onClick={save}
              disabled={!dirty || saving || Boolean(liveHint)}
              className="px-3 py-1 rounded-lg bg-[var(--text-primary)] text-[var(--bg-main)] hover:opacity-90 disabled:opacity-40 font-medium flex items-center gap-1.5 cursor-pointer transition"
            >
              <Save className="w-3.5 h-3.5" />
              <span>{saving ? (isEn ? 'Saving…' : 'Сохранение…') : isEn ? 'Save to Git SoT' : 'Сохранить в Git SoT'}</span>
            </button>
          </div>
        )}
      </div>

      {writable && (
        <div className="flex flex-wrap items-center gap-1.5 py-1">
          <span className="text-[11px] font-mono text-[var(--text-muted)] mr-1">
            {isEn ? 'Snippets:' : 'Шаблоны:'}
          </span>
          {SNIPPETS.map((s) => (
            <button
              key={s.labelEn}
              type="button"
              onClick={() => insertSnippet(s)}
              className="px-2 py-0.5 rounded-md bg-[var(--bg-elevated)] hover:bg-[var(--bg-subtle)] border border-[var(--border-subtle)] hover:border-[var(--border-hover)] text-[11px] font-mono text-[var(--text-secondary)] hover:text-[var(--text-primary)] flex items-center gap-1 transition cursor-pointer"
            >
              <Plus className="w-3 h-3" />
              <span>{isEn ? s.labelEn : s.labelRu}</span>
            </button>
          ))}
        </div>
      )}

      {liveHint && (
        <div className="text-xs text-amber-600 dark:text-amber-400 font-mono flex items-center gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          <span>{liveHint}</span>
        </div>
      )}
      {error && <div className="text-xs text-rose-600 dark:text-rose-400 font-mono">{error}</div>}
      {result?.saved && (
        <div className="text-xs text-emerald-600 dark:text-emerald-400 font-mono flex items-center gap-1.5">
          <CheckCircle2 className="w-3.5 h-3.5" />
          <span>{isEn ? 'Saved to Git SoT · Run Dry-run' : 'Сохранено в Git SoT · Запустите Dry-run'}</span>
        </div>
      )}
      {result && !result.saved && (
        <div className="text-xs font-mono rounded-lg border border-amber-500/30 bg-amber-500/[0.06] p-2 space-y-1">
          <div className="flex items-center gap-1.5 text-amber-600 dark:text-amber-400">
            <AlertTriangle className="w-3.5 h-3.5" />
            <span>{isEn ? 'Validation errors:' : 'Ошибки валидации:'}</span>
          </div>
          {result.issues.map((i, idx) => (
            <div key={idx} className="text-[var(--text-primary)]">
              {i.field ? `${i.field}: ` : ''}
              {i.message}
            </div>
          ))}
        </div>
      )}

      <div className="flex-1 min-h-[300px] rounded-xl overflow-hidden border border-[var(--border-subtle)]">
        <Editor
          height="100%"
          theme={monacoTheme}
          language="yaml"
          value={text}
          onChange={(v) => setText(v ?? '')}
          options={{
            readOnly: !writable,
            minimap: { enabled: false },
            fontSize: 12,
            scrollBeyondLastLine: false,
            tabSize: 2,
          }}
        />
      </div>
    </div>
  );
};


