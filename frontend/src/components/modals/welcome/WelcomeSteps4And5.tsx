import React from 'react';
import {
  Check,
  CheckCircle2,
  Cpu,
  GitBranch,
  Globe,
  Key,
  Moon,
  RefreshCw,
  Send,
  Server,
  ShieldCheck,
  Sun,
} from 'lucide-react';
import type { DiscoveredSwitchItem, LlmCascadeConfig, ServerDiscoveryResponse } from '../../../api';
import type { AppTheme } from '../../layout/Header';
import { translations, type Locale } from '../../../i18n';
import {
  LLM_PRESETS,
  inputClass,
  secondaryBtnClass,
  utilityPillClass,
  type WizardStep,
} from './WelcomeStepStyles';
import { Lbl, StepNav } from './WelcomeParts';

interface WelcomeHeaderProps {
  isEn: boolean;
  locale: Locale;
  setLocale: (l: Locale) => void;
  theme: AppTheme;
  setTheme: (t: AppTheme) => void;
  wizardStep: WizardStep;
  setWizardStep: (step: WizardStep) => void;
  onSkip: () => void;
  ownerToken: string;
  copiedKey: boolean;
  setCopiedKey: (v: boolean) => void;
}

export const WelcomeHeader: React.FC<WelcomeHeaderProps> = ({
  isEn,
  locale,
  setLocale,
  theme,
  setTheme,
  wizardStep,
  setWizardStep,
  onSkip,
  ownerToken,
  copiedKey,
  setCopiedKey,
}) => {
  const t = translations[locale];
  const stepsMeta: { id: WizardStep; title: string; icon: React.ReactNode }[] = [
    { id: 1, title: isEn ? '1. Owner' : '1. Владелец', icon: <Key className="w-3.5 h-3.5 shrink-0" /> },
    { id: 2, title: '2. GitHub', icon: <GitBranch className="w-3.5 h-3.5 shrink-0" /> },
    { id: 3, title: '3. Telegram', icon: <Send className="w-3.5 h-3.5 shrink-0" /> },
    { id: 4, title: isEn ? '4. AI' : '4. ИИ', icon: <Cpu className="w-3.5 h-3.5 shrink-0" /> },
    { id: 5, title: isEn ? '5. Server' : '5. Сервер', icon: <Server className="w-3.5 h-3.5 shrink-0" /> },
  ];

  return (
    <>
      <div className="px-3.5 sm:px-5 py-3 border-b border-[var(--border-subtle)] bg-[var(--bg-elevated)]/60 shrink-0">
        <div className="flex items-center justify-between gap-2 mb-2.5">
          <span className="inline-flex items-center gap-2 text-xs font-mono font-semibold">
            <span className="w-2 h-2 rounded-full bg-emerald-500" />
            <span>{isEn ? 'Cluster Setup' : 'Настройка кластера'}</span>
          </span>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              className={utilityPillClass}
            >
              {theme === 'dark' ? (
                <>
                  <Sun className="w-3.5 h-3.5 text-amber-400" />
                  <span className="hidden sm:inline">{t.themeLight}</span>
                </>
              ) : (
                <>
                  <Moon className="w-3.5 h-3.5 text-sky-600" />
                  <span className="hidden sm:inline">{t.themeDark}</span>
                </>
              )}
            </button>
            <button
              type="button"
              onClick={() => setLocale(locale === 'ru' ? 'en' : 'ru')}
              className={utilityPillClass}
            >
              <Globe className="w-3.5 h-3.5 text-[var(--text-muted)]" />
              <span className="uppercase font-semibold">{locale === 'ru' ? 'RU' : 'EN'}</span>
            </button>
            <button
              type="button"
              onClick={onSkip}
              className="h-7 px-2.5 rounded-md text-[11px] font-mono text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer"
            >
              {isEn ? 'Skip →' : 'Пропустить →'}
            </button>
          </div>
        </div>
        <div className="grid grid-cols-5 gap-1 sm:gap-1.5">
          {stepsMeta.map((s) => {
            const active = wizardStep === s.id;
            const completed = wizardStep > s.id;
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => setWizardStep(s.id)}
                className={`px-1.5 sm:px-2 py-1.5 sm:py-2 rounded-lg border text-[10px] sm:text-[11px] font-medium transition-all cursor-pointer flex items-center justify-center gap-1 ${
                  active
                    ? 'border-[var(--text-primary)] bg-[var(--bg-card)] text-[var(--text-primary)]'
                    : completed
                      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-500'
                      : 'border-[var(--border-subtle)] bg-[var(--bg-main)]/60 text-[var(--text-secondary)]'
                }`}
              >
                {completed ? <Check className="w-3.5 h-3.5 shrink-0" /> : s.icon}
                <span className="truncate">{s.title}</span>
              </button>
            );
          })}
        </div>
      </div>
      {ownerToken && (
        <div className="mx-3.5 sm:mx-5 mt-3 rounded-md border border-emerald-500/30 bg-emerald-500/10 p-2.5 space-y-1.5">
          <div className="text-xs font-semibold text-emerald-500 flex items-center gap-1.5">
            <ShieldCheck className="w-4 h-4 shrink-0" />
            <span>{isEn ? 'Owner Recovery Key:' : 'Резервный ключ Владельца:'}</span>
          </div>
          <div className="flex items-center gap-2">
            <code className="flex-1 bg-[var(--bg-main)] border border-[var(--border-subtle)] rounded px-2.5 py-1 text-xs font-mono text-emerald-500 select-all truncate">
              {ownerToken}
            </code>
            <button
              type="button"
              onClick={() => {
                navigator.clipboard.writeText(ownerToken);
                setCopiedKey(true);
                setTimeout(() => setCopiedKey(false), 2000);
              }}
              className={secondaryBtnClass}
            >
              {copiedKey ? 'OK' : isEn ? 'Copy' : 'Копировать'}
            </button>
          </div>
        </div>
      )}
    </>
  );
};

interface WelcomeAiStepProps {
  isEn: boolean;
  llmCascade: LlmCascadeConfig[];
  setLlmCascade: React.Dispatch<React.SetStateAction<LlmCascadeConfig[]>>;
  aiSavedMsg: string;
  savingAi: boolean;
  onSaveAiCluster: () => void;
  setWizardStep: (step: WizardStep) => void;
}

export const WelcomeAiStep: React.FC<WelcomeAiStepProps> = ({
  llmCascade,
  setLlmCascade,
  aiSavedMsg,
  savingAi,
  onSaveAiCluster,
  setWizardStep,
}) => (
  <div className="space-y-3">
    <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)]/60 p-3 space-y-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-mono font-semibold">LLM Cascade ({llmCascade.length})</span>
        <button
          type="button"
          onClick={() =>
            setLlmCascade((prev) => [
              ...prev,
              { name: '', base_url: '', model: '', api_key: '', timeout_seconds: 2.5 },
            ])
          }
          className="px-2 py-0.5 rounded border border-emerald-500/40 bg-emerald-500/10 text-[10px] font-mono text-emerald-500 cursor-pointer"
        >
          + Provider
        </button>
      </div>

      <div className="flex items-center gap-1 flex-wrap">
        {LLM_PRESETS.map((preset) => (
          <button
            key={preset.name}
            type="button"
            onClick={() =>
              setLlmCascade((prev) => {
                const row: LlmCascadeConfig = { ...preset, api_key: '' };
                const isEmptyFirst =
                  prev.length === 1 && !prev[0].name.trim() && !(prev[0].api_key || '').trim();
                return isEmptyFirst ? [row] : [...prev, row];
              })
            }
            className="px-2 py-0.5 rounded border border-[var(--border-subtle)] bg-[var(--bg-card)] text-[10px] font-mono text-[var(--text-secondary)] hover:text-emerald-500 cursor-pointer"
          >
            {preset.label}
          </button>
        ))}
      </div>

      <div className="space-y-1.5 max-h-48 overflow-y-auto">
        {llmCascade.map((item, idx) => (
          <div
            key={idx}
            className="rounded border border-[var(--border-subtle)] bg-[var(--bg-card)] p-1.5 flex items-center gap-1.5"
          >
            <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-500 text-[10px] font-mono font-bold">
              #{idx + 1}
            </span>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5 flex-1">
              <input
                type="text"
                value={item.name}
                onChange={(e) =>
                  setLlmCascade((prev) =>
                    prev.map((p, i) => (i === idx ? { ...p, name: e.target.value } : p))
                  )
                }
                placeholder="Name"
                className={inputClass}
              />
              <input
                type="text"
                value={item.model}
                onChange={(e) =>
                  setLlmCascade((prev) =>
                    prev.map((p, i) => (i === idx ? { ...p, model: e.target.value } : p))
                  )
                }
                placeholder="Model"
                className={inputClass}
              />
              <input
                type="text"
                value={item.base_url}
                onChange={(e) =>
                  setLlmCascade((prev) =>
                    prev.map((p, i) => (i === idx ? { ...p, base_url: e.target.value } : p))
                  )
                }
                placeholder="Base URL"
                className={inputClass}
              />
              <input
                type="password"
                value={item.api_key || ''}
                onChange={(e) =>
                  setLlmCascade((prev) =>
                    prev.map((p, i) => (i === idx ? { ...p, api_key: e.target.value } : p))
                  )
                }
                placeholder="API Key"
                className={inputClass}
              />
            </div>
            <button
              type="button"
              onClick={() => setLlmCascade((prev) => prev.filter((_, i) => i !== idx))}
              className="px-1.5 py-1 text-xs font-mono text-rose-500 cursor-pointer"
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </div>

    {aiSavedMsg && (
      <div className="text-xs font-mono text-rose-500 bg-rose-500/10 border border-rose-500/20 rounded px-3 py-1.5">
        {aiSavedMsg}
      </div>
    )}

    <StepNav onBack={() => setWizardStep(3)} onNext={onSaveAiCluster} disabled={savingAi} />
  </div>
);

interface WelcomeServerStepProps {
  isEn: boolean;
  serverHost: string;
  setServerHost: (v: string) => void;
  serverPort: number;
  setServerPort: (v: number) => void;
  serverUser: string;
  setServerUser: (v: string) => void;
  serverPassword: string;
  setServerPassword: (v: string) => void;
  discoveringServer: boolean;
  discoveryResult: ServerDiscoveryResponse | null;
  onDiscoverServer: () => void;
  onDiscoverOffline: () => void;
  onFinishWizard: () => void;
  setWizardStep: (step: WizardStep) => void;
}

export const WelcomeServerStep: React.FC<WelcomeServerStepProps> = ({
  isEn,
  serverHost,
  setServerHost,
  serverPort,
  setServerPort,
  serverUser,
  setServerUser,
  serverPassword,
  setServerPassword,
  discoveringServer,
  discoveryResult,
  onDiscoverServer,
  onDiscoverOffline,
  onFinishWizard,
  setWizardStep,
}) => (
  <div className="space-y-3.5">
    <div className="grid grid-cols-3 gap-2.5">
      <div className="col-span-2">
        <Lbl>{isEn ? 'Host' : 'Хост'}</Lbl>
        <input
          type="text"
          value={serverHost}
          onChange={(e) => setServerHost(e.target.value)}
          placeholder="5.228.243.54"
          className={inputClass}
        />
      </div>
      <div>
        <Lbl>{isEn ? 'SSH Port' : 'SSH Порт'}</Lbl>
        <input
          type="number"
          value={serverPort}
          onChange={(e) => setServerPort(Number(e.target.value) || 221)}
          placeholder="221"
          className={inputClass}
        />
      </div>
    </div>

    <div>
      <Lbl>{isEn ? 'SSH User' : 'Пользователь SSH'}</Lbl>
      <input
        type="text"
        value={serverUser}
        onChange={(e) => setServerUser(e.target.value)}
        className={inputClass}
      />
    </div>

    <div>
      <Lbl>{isEn ? 'SSH Password' : 'Пароль SSH'}</Lbl>
      <div className="flex flex-wrap sm:flex-nowrap gap-2">
        <input
          type="password"
          value={serverPassword}
          onChange={(e) => setServerPassword(e.target.value)}
          placeholder="SSH password"
          className={inputClass}
        />
        <button
          type="button"
          onClick={onDiscoverServer}
          disabled={discoveringServer}
          className="px-3.5 py-1.5 rounded-md bg-emerald-500 hover:bg-emerald-400 text-zinc-950 text-xs font-semibold flex items-center gap-1.5 cursor-pointer shrink-0"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${discoveringServer ? 'animate-spin' : ''}`} />
          <span>{discoveringServer ? '...' : isEn ? 'Connect SSH' : 'Подключить'}</span>
        </button>
        <button
          type="button"
          onClick={onDiscoverOffline}
          disabled={discoveringServer}
          className={secondaryBtnClass}
        >
          <span>{isEn ? 'Offline (Git SoT)' : 'Офлайн-стенд (Git SoT)'}</span>
        </button>
      </div>
    </div>

    {discoveryResult?.discovered_switches && (
      <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-2.5 space-y-2">
        <div className="text-xs font-semibold text-emerald-500 flex items-center gap-1.5">
          <CheckCircle2 className="w-3.5 h-3.5" />
          <span>
            {isEn ? 'Nodes:' : 'Узлов:'} {discoveryResult.synced_total}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          {discoveryResult.discovered_switches.map((sw: DiscoveredSwitchItem) => (
            <div
              key={sw.hostname}
              className="rounded border border-[var(--border-subtle)] bg-[var(--bg-main)] px-2.5 py-1.5 flex items-center justify-between text-[11px] font-mono"
            >
              <span className="truncate font-semibold">{sw.hostname}</span>
              <span className={sw.reachable ? 'text-emerald-500' : 'text-rose-500'}>
                {sw.reachable ? 'SSH OK' : 'OFFLINE'}
              </span>
            </div>
          ))}
        </div>
      </div>
    )}

    {discoveryResult?.status === 'error' && discoveryResult.message && (
      <div className="text-xs font-mono text-rose-500 bg-rose-500/10 border border-rose-500/20 rounded px-3 py-1.5">
        {discoveryResult.message}
      </div>
    )}

    <StepNav onBack={() => setWizardStep(4)} onNext={onFinishWizard} nextLabel={isEn ? 'Launch Console' : 'Открыть пульт'} />
  </div>
);
