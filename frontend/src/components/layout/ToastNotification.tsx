import React from 'react';
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react';

export interface ToastData {
  message: string;
  type: 'success' | 'error' | 'info';
}

export interface ToastNotificationProps {
  toast: ToastData;
  onClose: () => void;
}

export const ToastNotification: React.FC<ToastNotificationProps> = ({ toast, onClose }) => {
  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed bottom-6 right-6 z-[80] flex items-center space-x-3 px-4 py-3 rounded-xl bg-zinc-900 border border-white/[0.1] shadow-2xl animate-fade-in text-xs"
    >
      {toast.type === 'success' && <CheckCircle2 className="w-4 h-4 text-emerald-400" />}
      {toast.type === 'error' && <AlertCircle className="w-4 h-4 text-rose-400" />}
      {toast.type === 'info' && <Info className="w-4 h-4 text-cyan-400" />}
      <span className="text-zinc-200">{toast.message}</span>
      <button
        type="button"
        onClick={onClose}
        aria-label="Закрыть уведомление"
        className="text-zinc-400 hover:text-zinc-200 cursor-pointer"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
};
