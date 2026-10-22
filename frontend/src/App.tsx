import { Suspense, lazy, useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { api, isLocalOwnerConsole, rolePortList, type UserRole } from './api';
import { Header, type AppTheme } from './components/layout/Header';
import { Sidebar } from './components/layout/Sidebar';
import { Dashboard } from './components/views/Dashboard';
import { TopologyMap } from './components/charts/TopologyMap';
import { CommandPalette } from './components/layout/CommandPalette';
import { MobileNav } from './components/layout/MobileNav';
import { AppOverlays } from './components/layout/AppOverlays';
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts';
import { useNetopsPolling } from './hooks/useNetopsPolling';
import { translations, type Locale } from './i18n';
import { ToastNotification } from './components/layout/ToastNotification';

// Lazy-loaded secondary views to isolate heavy dependencies and code-split bundles
const DevicePage = lazy(() => import('./components/views/DevicePage').then((m) => ({ default: m.DevicePage })));
const DeviceList = lazy(() => import('./components/views/DeviceList').then((m) => ({ default: m.DeviceList })));
const JobsView = lazy(() => import('./components/views/JobsView').then((m) => ({ default: m.JobsView })));
const DiffViewer = lazy(() => import('./components/views/DiffViewer').then((m) => ({ default: m.DiffViewer })));
const DriftView = lazy(() => import('./components/views/DriftView').then((m) => ({ default: m.DriftView })));
const ChaosLabView = lazy(() => import('./components/views/ChaosLabView').then((m) => ({ default: m.ChaosLabView })));
const EmergencyHub = lazy(() => import('./components/views/EmergencyHub').then((m) => ({ default: m.EmergencyHub })));

function LoadingFallback() {
  return (
    <div className="flex items-center justify-center p-16 text-zinc-500 font-mono text-xs">
      <div className="w-5 h-5 rounded-full border-2 border-zinc-700 border-t-zinc-300 animate-spin mr-3" />
      <span>Загрузка модуля...</span>
    </div>
  );
}

export function App() {
  const [activeTab, setActiveTab] = useState<string>('dashboard');
  const [locale, setLocale] = useState<Locale>(() => (localStorage.getItem('netops_lang') as Locale) || 'ru');
  const [theme, setTheme] = useState<AppTheme>('dark');
  const [userRole, setUserRole] = useState<UserRole>(() => api.getRole());
  const isDedicatedRolePort =
    typeof window !== 'undefined' && rolePortList().includes(window.location.port);
  const [welcomeModalOpen, setWelcomeModalOpen] = useState<boolean>(() => {
    if (isDedicatedRolePort) return false;
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      if (params.get('setup') || params.get('welcome')) {
        localStorage.removeItem('netops_onboarding_done');
        return true;
      }
      if (params.get('token') || params.get('role')) {
        return false;
      }
      if (!isLocalOwnerConsole()) {
        return false;
      }
    }
    return !localStorage.getItem('netops_onboarding_done');
  });
  const [shareModalOpen, setShareModalOpen] = useState<boolean>(false);
  const [accessCodeModalOpen, setAccessCodeModalOpen] = useState<boolean>(false);
  const [activeStand, setActiveStand] = useState<string>('all');
  const [standsModalOpen, setStandsModalOpen] = useState<boolean>(false);
  const [deviceId, setDeviceId] = useState<number | null>(null);
  const [copilotOpen, setCopilotOpen] = useState(false);
  const [copilotRequest, setCopilotRequest] = useState<{ text: string; n: number } | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [dryRunModalOpen, setDryRunModalOpen] = useState(false);
  const [dryRunTargetIds, setDryRunTargetIds] = useState<number[]>([]);
  const [shortcutsModalOpen, setShortcutsModalOpen] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' | 'info' } | null>(
    null
  );

  const toastTimerRef = useRef<number | null>(null);
  const showToast = useCallback((message: string, type: 'success' | 'error' | 'info' = 'info') => {
    if (toastTimerRef.current !== null) {
      window.clearTimeout(toastTimerRef.current);
    }
    setToast({ message, type });
    toastTimerRef.current = window.setTimeout(() => {
      setToast(null);
      toastTimerRef.current = null;
    }, 4000);
  }, []);

  const {
    devices,
    stands,
    setStands,
    jobs,
    selectedJobId,
    setSelectedJobId,
    apiHealthy,
    loading,
    fetchDevices,
    fetchJobs,
    refreshAll,
    clearAuthFailed,
    handleExecuteDryRun,
    handleDeploy,
    handleScanDrift,
    handleRemediate,
    handleSyncInventory,
    handleLintIntent,
    handleDeleteDevice,
  } = useNetopsPolling({
    isDedicatedRolePort,
    userRole,
    locale,
    setActiveTab,
    setUserRole,
    setWelcomeModalOpen,
    setAccessCodeModalOpen,
    showToast,
  });

  const fetchStands = useCallback(async () => {
    try {
      const data = await api.getStands();
      if (Array.isArray(data)) {
        setStands(data);
      }
    } catch {
      // Ignore if stands endpoint is unauthenticated or offline during initial load
    }
  }, [setStands]);

  const handleRefreshStandsAndDevices = useCallback(async () => {
    await fetchStands();
    await refreshAll();
  }, [fetchStands, refreshAll]);

  useEffect(() => {
    const onTopoMutated = () => {
      void fetchDevices();
    };
    window.addEventListener('netops-topology-mutated', onTopoMutated);
    return () => window.removeEventListener('netops-topology-mutated', onTopoMutated);
  }, [fetchDevices]);

  const filteredDevices = useMemo(
    () =>
      devices.filter(
        (d) => activeStand === 'all' || d.fabric_id === activeStand || d.site === activeStand
      ),
    [devices, activeStand]
  );

  useKeyboardShortcuts({
    shortcutsModalOpen,
    setShortcutsModalOpen,
    standsModalOpen,
    setStandsModalOpen,
    accessCodeModalOpen,
    setAccessCodeModalOpen,
    shareModalOpen,
    setShareModalOpen,
    welcomeModalOpen,
    setWelcomeModalOpen,
    dryRunModalOpen,
    setDryRunModalOpen,
    searchOpen,
    setSearchOpen,
    copilotOpen,
    setCopilotOpen,
  });

  const handleSetLocale = useCallback((newLocale: Locale) => {
    setLocale(newLocale);
    localStorage.setItem('netops_lang', newLocale);
  }, []);

  const handleSetTheme = useCallback((newTheme: AppTheme) => {
    setTheme(newTheme);
    localStorage.setItem('netops_theme', newTheme);
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    document.documentElement.className = theme === 'dark' ? 'theme-dark dark' : 'theme-light';
  }, [theme]);

  const openDevice = useCallback((id: number) => {
    setDeviceId(id);
    setActiveTab('device');
  }, []);

  const openDiff = useCallback((targetDeviceId?: number) => {
    if (targetDeviceId) {
      setDeviceId(targetDeviceId);
    }
    setSelectedJobId((current) => {
      const currentJob = jobs.find((j) => j.id === current);
      if (currentJob) {
        return current;
      }
      const diffJob = jobs.find((j) => j.type === 'DRY_RUN' || j.type === 'DEPLOY') || jobs[0];
      return diffJob?.id ?? current;
    });
    setActiveTab('diff');
  }, [jobs, setSelectedJobId]);

  const handleOpenDiffJob = useCallback((jobId?: string) => {
    if (jobId) setSelectedJobId(jobId);
    setActiveTab('diff');
  }, [setSelectedJobId]);

  const openDryRunModal = useCallback((targetIds: number[]) => {
    setDryRunTargetIds(
      targetIds.length > 0
        ? targetIds
        : filteredDevices.filter((d) => d.role !== 'server' && d.platform !== 'linux_server').map((d) => d.id)
    );
    setDryRunModalOpen(true);
  }, [filteredDevices]);

  const askCopilot = useCallback((text: string) => {
    setCopilotOpen(true);
    setCopilotRequest({ text, n: Date.now() });
  }, []);

  const handleBackFromDevice = useCallback(() => { setActiveTab('devices'); fetchDevices(); }, [fetchDevices]);
  const handleDeleteDeviceFromPage = useCallback((id: number) => handleDeleteDevice(id, true), [handleDeleteDevice]);
  const handleDeleteDeviceFromList = useCallback((id: number) => handleDeleteDevice(id, false), [handleDeleteDevice]);
  const handleScanDriftScoped = useCallback(
    (ids?: number[]) =>
      handleScanDrift(
        ids && ids.length > 0
          ? ids
          : activeStand === 'all'
            ? undefined
            : filteredDevices.map((d) => d.id)
      ),
    [handleScanDrift, activeStand, filteredDevices]
  );
  const handleScanDriftAll = useCallback(() => handleScanDriftScoped(), [handleScanDriftScoped]);
  const handleToggleCopilot = useCallback(() => setCopilotOpen((o) => !o), []);
  const handleOpenWelcomeModal = useCallback(() => setWelcomeModalOpen(true), []);
  const handleOpenShareModal = useCallback(() => setShareModalOpen(true), []);
  const handleOpenStandsModal = useCallback(() => setStandsModalOpen(true), []);
  const handleCloseStandsModal = useCallback(() => setStandsModalOpen(false), []);
  const handleCloseCopilot = useCallback(() => setCopilotOpen(false), []);
  const handleCloseSearch = useCallback(() => setSearchOpen(false), []);
  const handleCloseDryRun = useCallback(() => setDryRunModalOpen(false), []);
  const handleCloseShare = useCallback(() => setShareModalOpen(false), []);
  const handleOpenSearch = useCallback(() => setSearchOpen(true), []);
  const handleSelectJob = useCallback((id: string) => setSelectedJobId(id), [setSelectedJobId]);

  const handleWelcomeClose = useCallback(async (role: UserRole) => {
    clearAuthFailed();
    setUserRole(role);
    setWelcomeModalOpen(false);
    const t = translations[locale];
    showToast(role === 'owner' ? t.toastOwnerSuccess : t.toastConnectSuccess, 'success');
    await refreshAll();
  }, [clearAuthFailed, locale, showToast, refreshAll]);

  return (
    <div className="min-h-dvh overflow-x-hidden bg-[var(--bg-main)] text-[var(--text-primary)] flex flex-col font-sans selection:bg-cyan-500/30 selection:text-cyan-200 transition-colors">
      <Header
        userRole={userRole}
        apiHealthy={apiHealthy}
        onToggleCopilot={handleToggleCopilot}
        copilotOpen={copilotOpen}
        locale={locale}
        setLocale={handleSetLocale}
        theme={theme}
        setTheme={handleSetTheme}
        onOpenWelcomeModal={handleOpenWelcomeModal}
        onOpenShareModal={handleOpenShareModal}
        activeStand={activeStand}
        standsCount={stands.length || 1}
        onOpenStandsModal={handleOpenStandsModal}
      />

      {!apiHealthy && (
        <div className="bg-rose-500/15 border-b border-rose-500/30 px-4 py-2 flex items-center justify-between text-xs text-rose-300 font-mono animate-fade-in">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-rose-400 animate-ping shrink-0" />
            <span>
              {locale === 'en'
                ? 'Connection to NetOps API lost. Reconnecting automatically...'
                : 'Потеряна связь с NetOps API. Автоматическое переподключение...'}
            </span>
          </div>
          <button
            onClick={refreshAll}
            className="px-2.5 py-1 rounded bg-rose-500/20 hover:bg-rose-500/30 text-rose-200 border border-rose-500/30 transition-colors cursor-pointer text-[11px]"
          >
            {locale === 'en' ? 'Retry Now' : 'Повторить попытку'}
          </button>
        </div>
      )}

      {toast && <ToastNotification toast={toast} onClose={() => setToast(null)} />}

      <div className="flex flex-1">
        <Sidebar
          activeTab={activeTab === 'device' ? 'devices' : activeTab}
          setActiveTab={setActiveTab}
          userRole={userRole}
          locale={locale}
        />
        <main className="flex-1 min-w-0 w-full max-w-[2560px] mx-auto p-2.5 sm:p-4 lg:p-6 2xl:p-8 space-y-3 sm:space-y-5 pb-24 lg:pb-8">
          <Suspense fallback={<LoadingFallback />}>
            {activeTab === 'dashboard' && (
              <Dashboard
                devices={filteredDevices}
                stands={stands}
                activeStand={activeStand}
                jobs={jobs}
                userRole={userRole}
                locale={locale}
                onOpenTab={setActiveTab}
                onOpenDevice={openDevice}
                onRunDryRun={openDryRunModal}
                onOpenDiff={handleOpenDiffJob}
                onSyncInventory={handleSyncInventory}
              />
            )}

            {activeTab === 'topology' && (
              <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-card)] p-4 sm:p-5 space-y-3 animate-fade-in">
                <div className="flex flex-wrap items-center justify-between gap-2 pb-2 border-b border-[var(--border-subtle)]">
                  <div>
                    <h1 className="text-base font-semibold text-[var(--text-primary)] tracking-tight">
                      {locale === 'en' ? 'Network Topology & Commutation' : 'Топология сети и управление связями'}
                    </h1>
                    <p className="text-xs text-[var(--text-muted)]">
                      {locale === 'en'
                        ? 'Click a node to inspect or drain traffic · Click a link to toggle UP / SHUTDOWN'
                        : 'Нажмите на устройство для управления трафиком или на линию связи для её включения / отключения'}
                    </p>
                  </div>
                </div>
                <TopologyMap
                  devices={devices}
                  stands={stands}
                  activeStand={activeStand}
                  locale={locale}
                  compact={false}
                  userRole={userRole}
                  onSelectDevice={openDevice}
                  onRunDryRun={userRole !== 'viewer' ? openDryRunModal : undefined}
                  onSyncStands={handleRefreshStandsAndDevices}
                  onOpenStandsModal={handleOpenStandsModal}
                />
              </div>
            )}

            {activeTab === 'device' && deviceId !== null && (
              <DevicePage
                key={deviceId}
                deviceId={deviceId}
                userRole={userRole}
                locale={locale}
                onBack={handleBackFromDevice}
                onRunDryRun={openDryRunModal}
                onScanDrift={handleScanDrift}
                onAskCopilot={askCopilot}
                onDeleteDevice={handleDeleteDeviceFromPage}
              />
            )}

            {activeTab === 'devices' && (
              <DeviceList
                devices={filteredDevices}
                loading={loading}
                userRole={userRole}
                locale={locale}
                onRefresh={refreshAll}
                onRunDryRun={openDryRunModal}
                onScanDrift={handleScanDriftScoped}
                onSyncInventory={handleSyncInventory}
                onLintIntent={handleLintIntent}
                onOpenDevice={openDevice}
                onOpenDiff={openDiff}
                onDeleteDevice={handleDeleteDeviceFromList}
              />
            )}

            {activeTab === 'jobs' && (
              <JobsView
                jobs={jobs}
                selectedJobId={selectedJobId}
                userRole={userRole}
                onSelectJob={handleSelectJob}
                onDeploy={handleDeploy}
                onOpenDiff={handleOpenDiffJob}
                onRefresh={fetchJobs}
                locale={locale}
              />
            )}

            {activeTab === 'diff' && (
              <DiffViewer
                jobs={jobs}
                selectedJobId={selectedJobId}
                targetDeviceId={deviceId}
                userRole={userRole}
                onSelectJob={handleSelectJob}
                onDeploy={handleDeploy}
                locale={locale}
                theme={theme}
              />
            )}

            {activeTab === 'drift' && (
              <DriftView
                userRole={userRole}
                devices={filteredDevices}
                onRemediate={handleRemediate}
                onScanDrift={handleScanDriftAll}
                locale={locale}
              />
            )}

            {activeTab === 'lab' && userRole !== 'viewer' && (
              <ChaosLabView onRefreshAll={refreshAll} locale={locale} />
            )}

            {activeTab === 'emergency' && (userRole === 'admin' || userRole === 'owner') && (
              <EmergencyHub
                currentRole={userRole === 'admin' || userRole === 'owner' ? 'admin' : 'operator'}
              />
            )}
          </Suspense>
        </main>
      </div>

      <AppOverlays
        copilotOpen={copilotOpen}
        onCloseCopilot={handleCloseCopilot}
        activeTab={activeTab}
        deviceId={deviceId}
        userRole={userRole}
        locale={locale}
        setLocale={handleSetLocale}
        theme={theme}
        setTheme={handleSetTheme}
        copilotRequest={copilotRequest}
        onRunDryRun={openDryRunModal}
        onOpenDiff={openDiff}
        onScanDrift={handleScanDriftScoped}
        onOpenDevice={openDevice}
        onRemediate={handleRemediate}
        onOpenTab={setActiveTab}
        dryRunModalOpen={dryRunModalOpen}
        onCloseDryRun={handleCloseDryRun}
        onConfirmDryRun={handleExecuteDryRun}
        dryRunTargetIds={dryRunTargetIds}
        devices={filteredDevices}
        shareModalOpen={shareModalOpen}
        onCloseShare={handleCloseShare}
        welcomeModalOpen={welcomeModalOpen}
        isDedicatedRolePort={isDedicatedRolePort}
        onWelcomeClose={handleWelcomeClose}
        shortcutsModalOpen={shortcutsModalOpen}
        onCloseShortcuts={() => setShortcutsModalOpen(false)}
        accessCodeModalOpen={accessCodeModalOpen}
        onAccessCodeSuccess={(role) => {
          clearAuthFailed();
          setAccessCodeModalOpen(false);
          setUserRole(role);
          void refreshAll();
        }}
        standsModalOpen={standsModalOpen}
        onCloseStandsModal={handleCloseStandsModal}
        activeStand={activeStand}
        onSelectStand={setActiveStand}
        stands={stands}
        onRefreshStands={handleRefreshStandsAndDevices}
      />

      <CommandPalette
        open={searchOpen}
        onClose={handleCloseSearch}
        devices={filteredDevices}
        onOpenDevice={openDevice}
        onOpenTab={setActiveTab}
        userRole={userRole}
        locale={locale}
      />

      <MobileNav
        activeTab={activeTab === 'device' ? 'devices' : activeTab}
        setActiveTab={setActiveTab}
        locale={locale}
        theme={theme}
        setTheme={handleSetTheme}
        userRole={userRole}
        setLocale={handleSetLocale}
        onToggleCopilot={handleToggleCopilot}
        onOpenSearch={handleOpenSearch}
        onOpenShare={handleOpenShareModal}
      />
    </div>
  );
}

export default App;

