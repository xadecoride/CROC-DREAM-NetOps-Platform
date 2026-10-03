import { useEffect, useState } from 'react';
import { api } from './api';
import type { Device, JobSummary, UserRole } from './api';
import { Header } from './components/Header';
import { DeviceList } from './components/DeviceList';
import { JobsView } from './components/JobsView';
import { DiffViewer } from './components/DiffViewer';
import { DriftView } from './components/DriftView';
import { ChaosLabView } from './components/ChaosLabView';
import { Topology3D } from './components/Topology3D';
import { DeviceDetailModal } from './components/DeviceDetailModal';
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react';

export function App() {
  const [activeTab, setActiveTab] = useState<string>('devices');
  const [userRole, setUserRole] = useState<UserRole>('admin');
  const [devices, setDevices] = useState<Device[]>([]);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [selectedModalDeviceId, setSelectedModalDeviceId] = useState<number | null>(null);
  const [apiHealthy, setApiHealthy] = useState<boolean>(true);
  const [loading, setLoading] = useState<boolean>(true);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' | 'info' } | null>(
    null
  );

  // Sync token whenever role changes
  useEffect(() => {
    const tokenMap: Record<UserRole, string> = {
      admin: 'dev-admin-token',
      operator: 'dev-operator-token',
      viewer: 'dev-viewer-token',
    };
    api.setToken(tokenMap[userRole]);
  }, [userRole]);

  // Toast helper
  const showToast = (message: string, type: 'success' | 'error' | 'info' = 'info') => {
    setToast({ message, type });
    setTimeout(() => {
      setToast(null);
    }, 4000);
  };

  const fetchDevices = async () => {
    try {
      const data = await api.getDevices();
      setDevices(data);
      setApiHealthy(true);
    } catch (err: any) {
      console.error('Failed to load devices', err);
      setApiHealthy(false);
    }
  };

  const fetchJobs = async () => {
    try {
      const data = await api.getJobs();
      setJobs(data);
      if (data.length > 0 && !selectedJobId) {
        setSelectedJobId(data[0].id);
      }
    } catch (err: any) {
      console.error('Failed to load jobs', err);
    }
  };

  const refreshAll = async () => {
    setLoading(true);
    await Promise.all([fetchDevices(), fetchJobs()]);
    setLoading(false);
  };

  useEffect(() => {
    refreshAll();
    const interval = setInterval(() => {
      fetchDevices();
      fetchJobs();
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  // Action Handlers
  const handleRunDryRun = async (deviceIds: number[]) => {
    try {
      showToast('Запуск холостого прогона (Dry-Run)...', 'info');
      const res = await api.createDryRun(deviceIds);
      setSelectedJobId(res.job_id);
      setActiveTab('jobs');
      showToast(`Задача ${res.job_id.slice(0, 8)} поставлена в очередь!`, 'success');
      await fetchJobs();
    } catch (err: any) {
      showToast(`Ошибка запуска Dry-Run: ${err.message}`, 'error');
    }
  };

  const handleDeploy = async (jobId: string) => {
    try {
      showToast('Запуск транзакционного деплоя (commit confirmed)...', 'info');
      const res = await api.createDeploy(jobId, userRole);
      setSelectedJobId(res.job_id);
      setActiveTab('jobs');
      showToast(`Деплой ${res.job_id.slice(0, 8)} запущен!`, 'success');
      await fetchJobs();
    } catch (err: any) {
      showToast(`Ошибка деплоя: ${err.message}`, 'error');
    }
  };

  const handleScanDrift = async (deviceIds?: number[]) => {
    try {
      showToast('Запуск сканирования дрейфа конфигураций...', 'info');
      const res = await api.scanDrift(deviceIds);
      setSelectedJobId(res.job_id);
      setActiveTab('jobs');
      showToast('Внеочередной скан дрейфа запущен!', 'success');
      await fetchJobs();
    } catch (err: any) {
      showToast(`Ошибка сканирования дрейфа: ${err.message}`, 'error');
    }
  };

  const handleRemediate = async (deviceId: number) => {
    try {
      showToast('Запуск компенсирующего патча (Remediate)...', 'info');
      const res = await api.remediateDrift(deviceId);
      setSelectedJobId(res.job_id);
      setActiveTab('jobs');
      showToast('Устранение дрейфа поставлено в очередь!', 'success');
      await fetchJobs();
    } catch (err: any) {
      showToast(`Ошибка устранения дрейфа: ${err.message}`, 'error');
    }
  };

  const handleSyncInventory = async () => {
    try {
      showToast('Синхронизация inventory.yaml из Git...', 'info');
      const res = await api.syncInventory();
      showToast(
        `Инвентарь синхронизирован! Создано: ${res.created.length}, обновлено: ${res.updated.length}`,
        'success'
      );
      await fetchDevices();
    } catch (err: any) {
      showToast(`Ошибка синхронизации инвентаря: ${err.message}`, 'error');
    }
  };

  const handleLintIntent = async () => {
    try {
      const res = await api.lintIntent();
      if (res.issues && res.issues.length > 0) {
        showToast(`Найдено ${res.issues.length} предупреждений в моделях Intent.`, 'error');
      } else {
        showToast('Pre-flight lint успешен: все модели Pydantic и связность валидны!', 'success');
      }
    } catch (err: any) {
      showToast(`Ошибка линтинга: ${err.message}`, 'error');
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans">
      <Header
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        userRole={userRole}
        setUserRole={setUserRole}
        apiHealthy={apiHealthy}
      />

      {/* Toast Notification */}
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 flex items-center space-x-3 px-4 py-3 rounded-2xl bg-slate-900 border border-slate-700 shadow-2xl animate-fade-in text-xs">
          {toast.type === 'success' && <CheckCircle2 className="w-4 h-4 text-emerald-400" />}
          {toast.type === 'error' && <AlertCircle className="w-4 h-4 text-rose-400" />}
          {toast.type === 'info' && <Info className="w-4 h-4 text-indigo-400" />}
          <span className="text-slate-200">{toast.message}</span>
          <button onClick={() => setToast(null)} className="text-slate-400 hover:text-slate-200">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Main Container */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-6 space-y-6">
        {activeTab === '3d' && (
          <div className="space-y-6">
            <Topology3D
              devices={devices}
              onSelectDevice={(id) => setSelectedModalDeviceId(id)}
              onRunDryRun={handleRunDryRun}
              isJobRunning={jobs.some((j) => j.status === 'RUNNING')}
            />
            {/* Quick Actions Panel beneath 3D view */}
            <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 flex flex-wrap items-center justify-between gap-4">
              <div className="text-xs text-slate-400">
                💡 <span className="text-slate-200 font-semibold">Управление 3D моделью:</span> Зажмите левую кнопку мыши для вращения угла обзора, используйте колесико для зума. Кликните на узел для открытия меню.
              </div>
              <div className="flex items-center space-x-2">
                <button
                  onClick={() => handleRunDryRun(devices.map((d) => d.id))}
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-semibold shadow-lg shadow-indigo-600/20 transition"
                >
                  Холостой прогон (Все ноды)
                </button>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'devices' && (
          <DeviceList
            devices={devices}
            loading={loading}
            onRefresh={refreshAll}
            onRunDryRun={handleRunDryRun}
            onScanDrift={handleScanDrift}
            onSyncInventory={handleSyncInventory}
            onLintIntent={handleLintIntent}
          />
        )}

        {activeTab === 'jobs' && (
          <JobsView
            jobs={jobs}
            loading={loading}
            selectedJobId={selectedJobId}
            onSelectJob={(id) => setSelectedJobId(id)}
            onDeploy={handleDeploy}
            onOpenDiff={(id) => {
              setSelectedJobId(id);
              setActiveTab('diff');
            }}
            onRefresh={fetchJobs}
          />
        )}

        {activeTab === 'diff' && (
          <DiffViewer
            jobs={jobs}
            selectedJobId={selectedJobId}
            onSelectJob={(id) => setSelectedJobId(id)}
            onDeploy={handleDeploy}
          />
        )}

        {activeTab === 'drift' && (
          <DriftView
            onRemediate={handleRemediate}
            onScanDrift={() => handleScanDrift()}
          />
        )}

        {activeTab === 'lab' && (
          <ChaosLabView onRefreshAll={refreshAll} />
        )}

        {/* Modal for inspecting node parameters from 3D view */}
        {selectedModalDeviceId !== null && (
          <DeviceDetailModal
            deviceId={selectedModalDeviceId}
            onClose={() => setSelectedModalDeviceId(null)}
          />
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-900 bg-slate-950 py-4 px-6 text-center text-[11px] text-slate-500">
        CROC DREAM — NetOps Platform • Хранилище SoT: Git + YAML • Движок: hier_config & Jinja2 • Бэкенд: FastAPI, SQLite, Celery
      </footer>
    </div>
  );
}

export default App;
