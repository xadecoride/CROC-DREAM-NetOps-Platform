import React from 'react';
import {
  Sparkles,
  RefreshCw,
  Send,
} from 'lucide-react';
import type { DeviceDetail } from '../../../api';
import { PLATFORM_CLI_PRESETS } from '../../../constants';

const renderFormattedText = (text: string) => {
  return text.split('\n').map((line, lineIdx) => {
    const parts = line.split(/(\*\*.*?\*\*|`.*?`)/g);
    return (
      <div key={lineIdx} className={line.trim() === '' ? 'h-2' : 'min-h-[1.25rem]'}>
        {parts.map((part, partIdx) => {
          if (part.startsWith('**') && part.endsWith('**')) {
            return (
              <strong key={partIdx} className="font-bold text-slate-900 dark:text-white">
                {part.slice(2, -2)}
              </strong>
            );
          }
          if (part.startsWith('`') && part.endsWith('`')) {
            return (
              <code
                key={partIdx}
                className="px-1.5 py-0.5 rounded bg-slate-200/80 dark:bg-white/[0.1] text-indigo-700 dark:text-cyan-300 font-mono text-[11px] font-semibold"
              >
                {part.slice(1, -1)}
              </code>
            );
          }
          return <span key={partIdx}>{part}</span>;
        })}
      </div>
    );
  });
};

interface DeviceSshTerminalTabProps {
  device: DeviceDetail;
  isServer: boolean;
  isManaged: boolean;
  terminalRunning: boolean;
  terminalOutput: Array<{ command: string; output: string; time: string }>;
  commandInput: string;
  setCommandInput: (v: string) => void;
  onRunCommand: (cmd?: string) => void;
}

export const DeviceSshTerminalTab: React.FC<DeviceSshTerminalTabProps> = ({
  device,
  isServer,
  isManaged,
  terminalRunning,
  terminalOutput,
  commandInput,
  setCommandInput,
  onRunCommand,
}) => (
  <div className="rounded-lg border border-[var(--border-subtle)] bg-[#07090e] overflow-hidden flex flex-col h-[550px]">
    <div className="px-4 py-2.5 bg-white/[0.03] border-b border-white/[0.06] flex items-center justify-between">
      <div className="flex items-center gap-2">
        <div className="flex gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-rose-500/80" />
          <span className="w-2.5 h-2.5 rounded-full bg-amber-500/80" />
          <span className="w-2.5 h-2.5 rounded-full bg-emerald-500/80" />
        </div>
        <span className="text-xs font-mono text-zinc-400 ml-2">
          ssh://{isServer ? 'root' : 'admin'}@{device.management_ip}:{device.management_port}
          {device.proxy_jump ? ` (via ${device.proxy_jump})` : ''}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-mono text-zinc-300 flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
          LIVE SSH
        </span>
        <button
          onClick={() => onRunCommand('clear')}
          className="px-2 py-0.5 rounded text-[11px] text-zinc-400 hover:text-white hover:bg-white/[0.06]"
        >
          Clear
        </button>
      </div>
    </div>

    <div className="px-4 py-2 bg-white/[0.01] border-b border-white/[0.04] flex items-center gap-2 overflow-x-auto text-xs">
      <span className="text-zinc-500 text-[11px] shrink-0">⚡</span>
      {(isServer
        ? PLATFORM_CLI_PRESETS.linux_server
        : PLATFORM_CLI_PRESETS[device.platform] || PLATFORM_CLI_PRESETS.cisco_iosxe
      ).map((cmd) => (
        <button
          key={cmd}
          onClick={() => onRunCommand(cmd)}
          disabled={terminalRunning}
          className="px-2 py-0.5 rounded bg-white/[0.04] hover:bg-white/[0.08] text-zinc-300 font-mono text-[11px] disabled:opacity-50 cursor-pointer"
        >
          {cmd}
        </button>
      ))}
    </div>

    <div className="flex-1 p-4 font-mono text-xs overflow-y-auto space-y-3 text-zinc-300">
      {terminalOutput.map((item, idx) => (
        <div key={idx} className="space-y-1">
          <div className="flex items-center gap-2 text-zinc-400">
            <span className="text-zinc-600">[{item.time}]</span>
            <span>{device.hostname}#</span>
            <span className="text-white font-semibold">{item.command}</span>
          </div>
          <pre className="text-zinc-300 whitespace-pre-wrap pl-4 border-l border-white/[0.06] text-[12px]">
            {item.output}
          </pre>
        </div>
      ))}
      {terminalRunning && (
        <div className="flex items-center gap-2 text-amber-400 py-2">
          <span className="w-2 h-2 rounded-full bg-amber-400 animate-ping" />
          <span className="text-xs">…</span>
        </div>
      )}
    </div>

    <div className="p-3 bg-white/[0.02] border-t border-white/[0.06] flex items-center gap-2">
      <span className="font-mono text-xs text-zinc-400 pl-1">{device.hostname}#</span>
      <input
        type="text"
        value={commandInput}
        disabled={terminalRunning}
        onChange={(e) => setCommandInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') onRunCommand();
        }}
        placeholder={
          terminalRunning
            ? '…'
            : !isManaged
            ? 'MONITORING_ONLY: read-only'
            : 'команду / command'
        }
        className="flex-1 bg-transparent text-xs text-zinc-100 font-mono focus:outline-none disabled:opacity-50"
      />
      <button
        onClick={() => onRunCommand()}
        disabled={terminalRunning}
        className="px-3 py-1 rounded bg-[var(--text-primary)] text-[var(--bg-main)] hover:opacity-90 text-xs font-mono font-medium transition-colors cursor-pointer disabled:opacity-50"
      >
        {terminalRunning ? '…' : '↵'}
      </button>
    </div>
  </div>
);

interface DeviceCopilotTabProps {
  device: DeviceDetail;
  copilotHistory: Array<{ role: 'user' | 'assistant'; text: string }>;
  copilotInput: string;
  setCopilotInput: (v: string) => void;
  copilotLoading: boolean;
  onAskCopilot: (text: string) => void;
}

export const DeviceCopilotTab: React.FC<DeviceCopilotTabProps> = ({
  device,
  copilotHistory,
  copilotInput,
  setCopilotInput,
  copilotLoading,
  onAskCopilot,
}) => (
  <div className="space-y-4">
    <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-card)] p-5 md:p-6 space-y-4">
      <div className="flex items-center gap-3 pb-3 border-b border-[var(--border-subtle)]">
        <div className="w-9 h-9 rounded-md bg-[var(--bg-subtle)] border border-[var(--border-subtle)] flex items-center justify-center text-[var(--text-primary)]">
          <Sparkles className="w-4 h-4" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-[var(--text-primary)] flex items-center gap-2">
            <span>Copilot</span>
            <span className="font-mono text-xs px-2 py-0.5 rounded bg-[var(--bg-subtle)] border border-[var(--border-subtle)] text-[var(--text-secondary)]">
              {device.hostname}
            </span>
          </h3>
        </div>
      </div>

      <div>
        <div className="text-[11px] font-mono uppercase tracking-wider text-[var(--text-muted)] font-semibold mb-2">
          ⚡
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => onAskCopilot(`Потянет ли этот узел дополнительную нагрузку +30%?`)}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--bg-subtle)] hover:bg-[var(--bg-elevated)] border border-[var(--border-subtle)] hover:border-[var(--border-hover)] text-[var(--text-primary)] transition-colors cursor-pointer text-left flex items-center gap-2"
          >
            <span>Нагрузка +30%?</span>
          </button>
          <button
            onClick={() => onAskCopilot(`Как безопасно вывести узел ${device.hostname} на техобслуживание (Drain)?`)}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--bg-subtle)] hover:bg-[var(--bg-elevated)] border border-[var(--border-subtle)] hover:border-[var(--border-hover)] text-[var(--text-primary)] transition-colors cursor-pointer text-left flex items-center gap-2"
          >
            <span>Drain</span>
          </button>
          <button
            onClick={() => onAskCopilot(`Проанализируй связность BGP и проверь риск сплит-брейна.`)}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--bg-subtle)] hover:bg-[var(--bg-elevated)] border border-[var(--border-subtle)] hover:border-[var(--border-hover)] text-[var(--text-primary)] transition-colors cursor-pointer text-left flex items-center gap-2"
          >
            <span>BGP split-brain</span>
          </button>
        </div>
      </div>

      <div className="space-y-3 min-h-[180px] max-h-[420px] overflow-y-auto p-4 md:p-5 rounded-lg bg-[var(--bg-subtle)] border border-[var(--border-subtle)] flex flex-col">
        {copilotHistory.length === 0 && (
          <div className="m-auto text-center text-xs text-[var(--text-muted)] py-12 font-mono flex flex-col items-center gap-2">
            <Sparkles className="w-5 h-5 text-[var(--text-muted)]" />
            <span>?</span>
          </div>
        )}
        {copilotHistory.map((msg, i) => (
          <div
            key={i}
            className={`flex flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'} w-full`}
          >
            <div className="flex items-center gap-1.5 mb-1 px-1">
              <span className="text-[10px] font-mono font-bold uppercase tracking-wider text-[var(--text-muted)]">
                {msg.role === 'user' ? 'Вы' : 'NetOps Copilot'}
              </span>
            </div>
            <div
              className={`rounded-lg text-xs leading-relaxed max-w-[85%] md:max-w-[75%] px-4 py-3 ${
                msg.role === 'user'
                  ? 'bg-[var(--text-primary)] text-[var(--bg-main)]'
                  : 'bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[var(--text-primary)]'
              }`}
            >
              {renderFormattedText(msg.text)}
            </div>
          </div>
        ))}
        {copilotLoading && (
          <div className="self-start flex items-center gap-2.5 p-3 rounded-lg bg-[var(--bg-card)] border border-[var(--border-subtle)] text-xs text-[var(--text-secondary)]">
            <RefreshCw className="w-3.5 h-3.5 animate-spin" />
            <span className="font-mono">…</span>
          </div>
        )}
      </div>

      <div className="flex gap-2 pt-1">
        <input
          type="text"
          value={copilotInput}
          onChange={(e) => setCopilotInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') onAskCopilot(copilotInput);
          }}
          placeholder="Вопрос…"
          className="flex-1 bg-[var(--bg-subtle)] border border-[var(--border-subtle)] focus:border-[var(--text-secondary)] rounded-md px-4 py-2 text-xs text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none transition-colors"
        />
        <button
          onClick={() => onAskCopilot(copilotInput)}
          disabled={copilotLoading || !copilotInput.trim()}
          className="px-4 py-2 rounded-md bg-[var(--text-primary)] text-[var(--bg-main)] hover:opacity-90 text-xs font-semibold flex items-center gap-2 transition-opacity disabled:opacity-40 cursor-pointer"
        >
          <Send className="w-3.5 h-3.5" />
          <span>↵</span>
        </button>
      </div>
    </div>
  </div>
);

