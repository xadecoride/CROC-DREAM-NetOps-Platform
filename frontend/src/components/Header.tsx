import React from 'react';
import { Network, Activity, ShieldCheck, UserCheck, Terminal, Layers } from 'lucide-react';
import type { UserRole } from '../api';

interface HeaderProps {
  activeTab: string;
  setActiveTab: (tab: string) => void;
  userRole: UserRole;
  setUserRole: (role: UserRole) => void;
  apiHealthy: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  activeTab,
  setActiveTab,
  userRole,
  setUserRole,
  apiHealthy,
}) => {
  return (
    <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-50 px-6 py-3.5">
      <div className="max-w-7xl mx-auto flex items-center justify-between">
        {/* Brand */}
        <div className="flex items-center space-x-3.5">
          <div className="h-10 w-10 rounded-xl bg-gradient-to-tr from-indigo-600 via-indigo-500 to-cyan-400 flex items-center justify-center shadow-lg shadow-indigo-500/20">
            <Network className="h-5 w-5 text-white" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <span className="font-bold text-lg tracking-tight text-white">CROC DREAM</span>
              <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                NetOps Platform
              </span>
            </div>
            <p className="text-xs text-slate-400">Heterogeneous CLOS Automation • Cisco & Arista</p>
          </div>
        </div>

        {/* Navigation Tabs */}
        <nav className="flex items-center space-x-1 bg-slate-950/60 p-1 rounded-xl border border-slate-800">
          <button
            onClick={() => setActiveTab('3d')}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center space-x-2 ${
              activeTab === '3d'
                ? 'bg-gradient-to-r from-indigo-600 to-cyan-500 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <span className="text-cyan-400 font-bold">✨</span>
            <span>3D Топология</span>
          </button>
          <button
            onClick={() => setActiveTab('devices')}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center space-x-2 ${
              activeTab === 'devices'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <Layers className="w-3.5 h-3.5" />
            <span>Устройства</span>
          </button>
          <button
            onClick={() => setActiveTab('jobs')}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center space-x-2 ${
              activeTab === 'jobs'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <Activity className="w-3.5 h-3.5" />
            <span>Пайплайны</span>
          </button>
          <button
            onClick={() => setActiveTab('diff')}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center space-x-2 ${
              activeTab === 'diff'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <Terminal className="w-3.5 h-3.5" />
            <span>Monaco Diff & AI</span>
          </button>
          <button
            onClick={() => setActiveTab('drift')}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center space-x-2 ${
              activeTab === 'drift'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <ShieldCheck className="w-3.5 h-3.5" />
            <span>Контроль дрейфа</span>
          </button>
          <button
            onClick={() => setActiveTab('lab')}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center space-x-2 ${
              activeTab === 'lab'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <span className="text-amber-400 font-bold">🧪</span>
            <span>Тестовый стенд</span>
          </button>
        </nav>

        {/* Status & User Selector */}
        <div className="flex items-center space-x-4">
          {/* API Health */}
          <div className="flex items-center space-x-1.5 text-xs text-slate-400">
            <span
              className={`w-2 h-2 rounded-full ${
                apiHealthy ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]' : 'bg-rose-500 animate-pulse'
              }`}
            />
            <span className="hidden sm:inline">{apiHealthy ? 'API Active' : 'API Offline'}</span>
          </div>

          {/* Role selector */}
          <div className="flex items-center space-x-2 bg-slate-800/70 border border-slate-700/60 rounded-lg px-2.5 py-1">
            <UserCheck className="w-3.5 h-3.5 text-indigo-400" />
            <select
              value={userRole}
              onChange={(e) => setUserRole(e.target.value as UserRole)}
              className="bg-transparent text-xs font-medium text-slate-200 focus:outline-none cursor-pointer"
            >
              <option value="admin" className="bg-slate-900 text-slate-200">
                admin (Полный доступ)
              </option>
              <option value="operator" className="bg-slate-900 text-slate-200">
                operator (Оператор деплоя)
              </option>
              <option value="viewer" className="bg-slate-900 text-slate-200">
                viewer (Только чтение)
              </option>
            </select>
          </div>
        </div>
      </div>
    </header>
  );
};
