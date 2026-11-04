import { useCallback } from 'react';
import { api, getErrorMessage } from '../api';
import type { Device, JobSummary, UserRole } from '../api';

export interface UseJobActionsParams {
  jobs: JobSummary[];
  userRole: UserRole;
  locale: 'ru' | 'en';
  setActiveTab: React.Dispatch<React.SetStateAction<string>>;
  setSelectedJobId: React.Dispatch<React.SetStateAction<string | null>>;
  setDevices: React.Dispatch<React.SetStateAction<Device[]>>;
  fetchDevices: (forceStands?: boolean) => Promise<void>;
  fetchJobs: () => Promise<void>;
  showToast: (message: string, type?: 'success' | 'error' | 'info') => void;
}

export interface UseJobActionsReturn {
  handleExecuteDryRun: (deviceIds: number[]) => Promise<void>;
  handleDeploy: (jobId: string) => Promise<void>;
  handleScanDrift: (deviceIds?: number[]) => Promise<void>;
  handleRemediate: (targetDeviceId: number) => Promise<void>;
  handleSyncInventory: () => Promise<void>;
  handleLintIntent: () => Promise<void>;
  handleDeleteDevice: (id: number, returnToList?: boolean) => Promise<void>;
}

export function useJobActions({
  jobs,
  userRole,
  locale,
  setActiveTab,
  setSelectedJobId,
  setDevices,
  fetchDevices,
  fetchJobs,
  showToast,
}: UseJobActionsParams): UseJobActionsReturn {
  const handleExecuteDryRun = useCallback(
    async (deviceIds: number[]) => {
      try {
        showToast('Запуск холостого прогона (Dry-Run)...', 'info');
        const res = await api.createDryRun(deviceIds);
        setSelectedJobId(res.job_id);
        setActiveTab('jobs');
        showToast(`Задача ${res.job_id.slice(0, 8)} поставлена в очередь!`, 'success');
        await fetchJobs();
      } catch (err: unknown) {
        showToast(`Ошибка запуска Dry-Run: ${getErrorMessage(err)}`, 'error');
      }
    },
    [fetchJobs, setActiveTab, showToast, setSelectedJobId]
  );

  const handleDeploy = useCallback(
    async (jobId: string) => {
      try {
        const targetJob = jobs.find((j) => j.id === jobId);
        if (targetJob && targetJob.type !== 'DRY_RUN' && targetJob.type !== 'DRIFT_SCAN') {
          showToast('Синхронизация с эталонным конфигом (Remediation / Deploy)...', 'info');
          const diff = await api.getJobDiff(jobId);
          const changed = diff.devices.filter((d) => Boolean(d.remediation_patch?.trim()));
          for (const dev of changed) {
            const patch = dev.remediation_patch || '';
            if (dev.device_id) {
              try {
                const rem = await api.remediateDrift(dev.device_id);
                setSelectedJobId(rem.job_id);
              } catch {
                await api.applyEmergencyPatch(
                  dev.hostname,
                  patch,
                  'Деплой эталонного конфига из Diff & Config'
                );
              }
            } else {
              await api.applyEmergencyPatch(
                dev.hostname,
                patch,
                'Деплой эталонного конфига из Diff & Config'
              );
            }
          }
          const scan = await api.scanDrift(
            changed.map((d) => d.device_id).filter((id): id is number => typeof id === 'number')
          );
          setSelectedJobId(scan.job_id);
          setActiveTab('jobs');
          showToast(`Эталонная конфигурация применена (${changed.length} узл.)!`, 'success');
          await Promise.all([fetchJobs(), fetchDevices()]);
          return;
        }
        showToast('Запуск транзакционного деплоя (commit confirmed)...', 'info');
        const res = await api.createDeploy(jobId, userRole);
        setSelectedJobId(res.job_id);
        setActiveTab('jobs');
        showToast(`Деплой ${res.job_id.slice(0, 8)} запущен!`, 'success');
        await fetchJobs();
      } catch (err: unknown) {
        showToast(`Ошибка деплоя: ${getErrorMessage(err)}`, 'error');
      }
    },
    [fetchDevices, fetchJobs, jobs, setActiveTab, showToast, userRole, setSelectedJobId]
  );

  const handleScanDrift = useCallback(
    async (deviceIds?: number[]) => {
      try {
        showToast('Запуск сканирования дрейфа конфигураций...', 'info');
        const targetIds = deviceIds && deviceIds.length > 0 ? deviceIds : undefined;
        const res = await api.scanDrift(targetIds);
        setSelectedJobId(res.job_id);
        setActiveTab('jobs');
        showToast('Внеочередной скан дрейфа запущен!', 'success');
        await fetchJobs();
      } catch (err: unknown) {
        showToast(`Ошибка сканирования дрейфа: ${getErrorMessage(err)}`, 'error');
      }
    },
    [fetchJobs, setActiveTab, showToast, setSelectedJobId]
  );

  const handleRemediate = useCallback(
    async (targetDeviceId: number) => {
      try {
        showToast('Запуск компенсирующего патча (Remediate)...', 'info');
        const res = await api.remediateDrift(targetDeviceId);
        setSelectedJobId(res.job_id);
        setActiveTab('jobs');
        showToast('Устранение дрейфа поставлено в очередь!', 'success');
        await fetchJobs();
      } catch (err: unknown) {
        showToast(`Ошибка устранения дрейфа: ${getErrorMessage(err)}`, 'error');
      }
    },
    [fetchJobs, setActiveTab, showToast, setSelectedJobId]
  );

  const handleSyncInventory = useCallback(async () => {
    try {
      showToast('Синхронизация inventory.yaml из Git...', 'info');
      const res = await api.syncInventory();
      showToast(
        `Инвентарь синхронизирован! Создано: ${res.created.length}, обновлено: ${res.updated.length}`,
        'success'
      );
      await fetchDevices();
    } catch (err: unknown) {
      showToast(`Ошибка синхронизации инвентаря: ${getErrorMessage(err)}`, 'error');
    }
  }, [fetchDevices, showToast]);

  const handleLintIntent = useCallback(async () => {
    try {
      const res = await api.lintIntent();
      if (res.issues && res.issues.length > 0) {
        showToast(`Найдено ${res.issues.length} предупреждений в моделях Intent.`, 'error');
      } else {
        showToast('Pre-flight lint успешен: все модели Pydantic и связность валидны!', 'success');
      }
    } catch (err: unknown) {
      showToast(`Ошибка линтинга: ${getErrorMessage(err)}`, 'error');
    }
  }, [showToast]);

  const handleDeleteDevice = useCallback(
    async (id: number, returnToList = false) => {
      try {
        await api.deleteDevice(id);
        setDevices((prev) => prev.filter((d) => d.id !== id));
        showToast(
          locale === 'en' ? 'Device deleted successfully' : 'Устройство успешно удалено',
          'success'
        );
        await Promise.all([fetchDevices(), fetchJobs()]);
        if (returnToList) {
          setActiveTab('devices');
        }
      } catch (err: unknown) {
        showToast(
          `${locale === 'en' ? 'Delete error' : 'Ошибка удаления'}: ${getErrorMessage(err)}`,
          'error'
        );
      }
    },
    [fetchDevices, fetchJobs, locale, setActiveTab, setDevices, showToast]
  );

  return {
    handleExecuteDryRun,
    handleDeploy,
    handleScanDrift,
    handleRemediate,
    handleSyncInventory,
    handleLintIntent,
    handleDeleteDevice,
  };
}
