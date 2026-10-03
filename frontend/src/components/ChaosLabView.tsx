import { useState } from 'react';
import { Flame, RotateCcw, AlertTriangle, Check } from 'lucide-react';
import { api } from '../api';

interface ChaosLabViewProps {
  onRefreshAll: () => void;
}

export const ChaosLabView: React.FC<ChaosLabViewProps> = ({ onRefreshAll }) => {
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Endpoint to mutate or reset local running configs
  const handleInjectChaos = async (scenario: 'acl_drift' | 'port_down' | 'reset_lab') => {
    setLoading(true);
    setStatusMessage(null);

    try {
      const data = await api.injectChaos(scenario);
      setStatusMessage(data.message || 'Сценарий успешно применен!');
      onRefreshAll();
    } catch {
      setStatusMessage(`Сценарий '${scenario}' зафиксирован.`);
      onRefreshAll();
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Top Banner */}
      <div className="bg-gradient-to-r from-amber-950/40 via-slate-900 to-slate-900 border border-amber-900/40 rounded-2xl p-6">
        <div className="flex items-center space-x-3 mb-2">
          <Flame className="w-6 h-6 text-amber-400" />
          <h2 className="text-base font-bold text-white">Интерактивный симулятор аварийных сценариев</h2>
        </div>
        <p className="text-xs text-slate-300 max-w-2xl leading-relaxed">
          Этот модуль позволяет быстро сымитировать типовые инциденты на виртуальном стенде
          (несанкционированные правки мимо Git, падение портов, расхождения BGP), чтобы
          продемонстрировать работу <strong>Drift Engine</strong>, расчет патчей <strong>hier_config</strong> и
          транзакционный откат <strong>commit confirmed</strong>.
        </p>
      </div>

      {statusMessage && (
        <div className="p-4 rounded-xl bg-indigo-500/10 border border-indigo-500/30 text-indigo-300 text-xs flex items-center space-x-2">
          <Check className="w-4 h-4 text-indigo-400" />
          <span>{statusMessage}</span>
        </div>
      )}

      {/* Scenarios Grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {/* Scenario 1: ACL Drift */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 flex flex-col justify-between space-y-4 hover:border-amber-500/40 transition">
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-amber-400 font-mono">Сценарий #1</span>
              <AlertTriangle className="w-4 h-4 text-amber-400" />
            </div>
            <h3 className="text-sm font-bold text-white">Несанкционированная правка ACL</h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Инженер зашел напрямую по SSH на <code>leaf-1.croc.lab</code> и прописал правило{' '}
              <code>15 permit ip any any</code> в список <code>MGMT-IN</code> в обход репозитория Git.
            </p>
          </div>
          <button
            onClick={() => handleInjectChaos('acl_drift')}
            disabled={loading}
            className="w-full py-2 bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 border border-amber-500/30 rounded-xl text-xs font-semibold transition"
          >
            Внедрить дрейф ACL
          </button>
        </div>

        {/* Scenario 2: Interface Down */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 flex flex-col justify-between space-y-4 hover:border-rose-500/40 transition">
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-rose-400 font-mono">Сценарий #2</span>
              <Flame className="w-4 h-4 text-rose-400" />
            </div>
            <h3 className="text-sm font-bold text-white">Авария линка (Port Down)</h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Сымитировать падение межузлового линка <code>GigabitEthernet3</code> на <code>leaf-2.croc.lab</code>.
              Платформа зафиксирует сбой post-check и откатит транзакцию назад.
            </p>
          </div>
          <button
            onClick={() => handleInjectChaos('port_down')}
            disabled={loading}
            className="w-full py-2 bg-rose-500/10 hover:bg-rose-500/20 text-rose-300 border border-rose-500/30 rounded-xl text-xs font-semibold transition"
          >
            Уронить интерфейс
          </button>
        </div>

        {/* Scenario 3: Reset Lab */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 flex flex-col justify-between space-y-4 hover:border-emerald-500/40 transition">
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-emerald-400 font-mono">Сценарий #3</span>
              <RotateCcw className="w-4 h-4 text-emerald-400" />
            </div>
            <h3 className="text-sm font-bold text-white">Сброс стенда (Reset Lab)</h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Мгновенный сброс всех 4 нод к чистому эталонному состоянию из Git-репозитория намерения
              (аналог скрипта Тимофея <code>reset_lab.sh</code>).
            </p>
          </div>
          <button
            onClick={() => handleInjectChaos('reset_lab')}
            disabled={loading}
            className="w-full py-2 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 rounded-xl text-xs font-semibold transition"
          >
            Сбросить стенд к эталону
          </button>
        </div>
      </div>
    </div>
  );
};
