import React from 'react';
import { ArrowLeft, ArrowRight } from 'lucide-react';
import { primaryBtnClass, secondaryBtnClass } from './WelcomeStepStyles';

export const Lbl: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <label className="block text-[11px] font-mono text-[var(--text-secondary)] mb-1">{children}</label>
);

interface StepNavProps {
  onBack: () => void;
  onNext: () => void;
  disabled?: boolean;
  nextLabel?: string;
}

/** Back/Next footer shared by all wizard steps (icons instead of words). */
export const StepNav: React.FC<StepNavProps> = ({ onBack, onNext, disabled, nextLabel }) => (
  <div className="flex items-center justify-between pt-1">
    <button type="button" onClick={onBack} className={secondaryBtnClass} aria-label="Back">
      <ArrowLeft className="w-3.5 h-3.5" />
    </button>
    <button type="button" onClick={onNext} disabled={disabled} className={primaryBtnClass} aria-label="Next">
      {nextLabel && <span>{nextLabel}</span>}
      <ArrowRight className="w-3.5 h-3.5" />
    </button>
  </div>
);
