import type { LlmCascadeConfig } from '../../../api';

export type WizardStep = 1 | 2 | 3 | 4 | 5;

export const LLM_PRESETS: Array<{
  label: string;
  name: string;
  base_url: string;
  model: string;
  timeout_seconds: number;
}> = [
  {
    label: '+ Groq',
    name: 'Groq',
    base_url: 'https://api.groq.com/openai/v1',
    model: 'llama-3.3-70b-versatile',
    timeout_seconds: 2.5,
  },
  {
    label: '+ Gemini',
    name: 'Gemini',
    base_url: 'https://generativelanguage.googleapis.com/v1beta/openai/',
    model: 'gemini-2.0-flash',
    timeout_seconds: 3.0,
  },
  {
    label: '+ DeepSeek',
    name: 'DeepSeek',
    base_url: 'https://api.deepseek.com/v1',
    model: 'deepseek-chat',
    timeout_seconds: 3.5,
  },
  {
    label: '+ OpenRouter',
    name: 'OpenRouter',
    base_url: 'https://openrouter.ai/api/v1',
    model: 'deepseek/deepseek-chat',
    timeout_seconds: 3.5,
  },
  {
    label: '+ Ollama',
    name: 'Ollama',
    base_url: 'http://localhost:11434/v1',
    model: 'qwen2.5-coder:7b',
    timeout_seconds: 4.0,
  },
];

export function inferProviderDefaults(
  name: string,
  apiKey: string
): Pick<LlmCascadeConfig, 'name' | 'base_url' | 'model'> {
  const n = name.trim().toLowerCase();
  const k = apiKey.trim();
  if (n.includes('groq') || k.startsWith('gsk_')) {
    return { name: name || 'Groq', base_url: 'https://api.groq.com/openai/v1', model: 'llama-3.3-70b-versatile' };
  }
  if (n.includes('gemini') || k.startsWith('AIza')) {
    return {
      name: name || 'Gemini',
      base_url: 'https://generativelanguage.googleapis.com/v1beta/openai/',
      model: 'gemini-2.0-flash',
    };
  }
  if (n.includes('openrouter') || k.startsWith('sk-or-')) {
    return {
      name: name || 'OpenRouter',
      base_url: 'https://openrouter.ai/api/v1',
      model: 'deepseek/deepseek-chat',
    };
  }
  if (n.includes('deepseek')) {
    return { name: name || 'DeepSeek', base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' };
  }
  if (n.includes('ollama') || n.includes('local')) {
    return { name: name || 'Ollama', base_url: 'http://localhost:11434/v1', model: 'qwen2.5-coder:7b' };
  }
  return { name: name || 'OpenAI', base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' };
}

export const inputClass =
  'w-full bg-[var(--bg-main)] border border-[var(--border-subtle)] focus:border-[var(--border-hover)] rounded-md px-3 py-1.5 text-xs font-mono text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none transition-colors';

export const primaryBtnClass =
  'px-4 py-2 rounded-md bg-[var(--text-primary)] hover:opacity-90 text-[var(--bg-main)] text-xs font-semibold flex items-center gap-1.5 cursor-pointer transition-opacity disabled:opacity-50';

export const secondaryBtnClass =
  'px-3 py-1.5 rounded-md border border-[var(--border-subtle)] hover:border-[var(--border-hover)] bg-[var(--bg-elevated)] text-xs font-mono text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer transition-colors';

export const utilityPillClass =
  'h-7 flex items-center gap-1.5 px-2.5 rounded-md border border-[var(--border-subtle)] hover:border-[var(--border-hover)] bg-[var(--bg-card)] hover:bg-[var(--bg-elevated)] text-[11px] font-mono text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors cursor-pointer';
