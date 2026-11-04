import React from 'react';
import { Bot } from 'lucide-react';
import type { RiskExplanation } from '../../api';
import type { Locale } from '../../i18n';

interface DiffAiRiskCardProps {
  analyzingAi: boolean;
  aiAnalysis: RiskExplanation | null;
  locale?: Locale;
  onAnalyzeWithAI: () => void;
}

export const DiffAiRiskCard: React.FC<DiffAiRiskCardProps> = ({
  analyzingAi,
  aiAnalysis,
  locale = 'ru',
  onAnalyzeWithAI,
}) => {
  const isEn = locale === 'en';
  return (
    <div className="p-4 rounded-2xl bg-[var(--bg-card)] border border-[var(--border-subtle)] space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center space-x-1.5 min-w-0">
          <Bot className="w-4 h-4 text-[var(--text-secondary)] shrink-0" />
          <span className="text-xs font-bold font-mono text-[var(--text-primary)] truncate">LLM Risk Assistant</span>
        </div>
        <button
          onClick={onAnalyzeWithAI}
          disabled={analyzingAi}
          className="px-2.5 py-1 rounded-lg bg-[var(--bg-elevated)] hover:bg-[var(--bg-subtle)] text-[var(--text-primary)] text-[11px] font-mono border border-[var(--border-subtle)] hover:border-[var(--border-hover)] transition flex items-center space-x-1 cursor-pointer shrink-0"
        >
          <span>{analyzingAi ? (isEn ? 'Analyzing...' : 'Анализ...') : isEn ? 'Assess Risks' : 'Оценить риски'}</span>
        </button>
      </div>

      {aiAnalysis && (
        <div className="space-y-2.5 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-[var(--text-secondary)] text-[11px] font-mono">
              {isEn ? 'Risk Level:' : 'Уровень риска:'}
            </span>
            <span
              className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold ${
                aiAnalysis.risk_level === 'HIGH' || aiAnalysis.risk_level === 'CRITICAL'
                  ? 'bg-rose-500/15 text-rose-600 dark:text-rose-300 border border-rose-500/30'
                  : aiAnalysis.risk_level === 'MEDIUM'
                    ? 'bg-amber-500/15 text-amber-600 dark:text-amber-300 border border-amber-500/30'
                    : 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border border-emerald-500/30'
              }`}
            >
              {aiAnalysis.risk_level} RISK
            </span>
          </div>

          <p className="text-[var(--text-primary)] text-[11px] leading-relaxed">{aiAnalysis.summary}</p>

          <div className="space-y-1 pt-2 border-t border-[var(--border-subtle)]">
            <span className="text-[10px] font-mono uppercase tracking-wider text-[var(--text-muted)] block">
              {isEn ? 'Risk Factors:' : 'Факторы риска:'}
            </span>
            {aiAnalysis.key_points.map((pt, i) => (
              <div key={i} className="flex items-start space-x-1.5 text-[11px] text-[var(--text-secondary)]">
                <span className="text-[var(--text-muted)] mt-0.5">•</span>
                <span>{pt}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

