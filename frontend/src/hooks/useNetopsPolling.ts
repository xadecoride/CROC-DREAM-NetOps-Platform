import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { allConsolePorts, api, getErrorMessage, isLocalOwnerConsole } from '../api';
import type { Device, JobSummary, StandRead, UserRole } from '../api';
import { useJobActions } from './useJobActions';

function areDevicesEqual(a: Device[], b: Device[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const da = a[i];
    const db = b[i];
    if (
      da.id !== db.id ||
      da.hostname !== db.hostname ||
      da.status !== db.status ||
      da.oper_status !== db.oper_status ||
      da.management_mode !== db.management_mode ||
      da.platform !== db.platform ||
      da.role !== db.role ||
      da.management_ip !== db.management_ip ||
      da.fabric_id !== db.fabric_id ||
      da.site !== db.site ||
      da.last_checked_at !== db.last_checked_at ||
      da.updated_at !== db.updated_at ||
      da.sparkline?.length !== db.sparkline?.length ||
      da.sparkline?.at(-1) !== db.sparkline?.at(-1)
    ) {
      return false;
    }
  }
  return true;
}

function areJobsEqual(a: JobSummary[], b: JobSummary[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const ja = a[i];
    const jb = b[i];
    if (
      ja.id !== jb.id ||
      ja.status !== jb.status ||
      ja.progress !== jb.progress ||
      ja.started_at !== jb.started_at ||
      ja.finished_at !== jb.finished_at
    ) {
      return false;
    }
  }
  return true;
}

function alignDevicesWithStands(list: Device[], stands: StandRead[]): Device[] {
  if (list.length === 0 || stands.length !== 1) return list;
  const slug = stands[0].slug;
  if (!slug || list.some((d) => d.fabric_id === slug || d.site === slug)) return list;
  return list.map((d) => ({ ...d, site: slug }));
}

interface UseNetopsPollingParams {
  isDedicatedRolePort: boolean;
  userRole: UserRole;
  locale: 'ru' | 'en';
  setActiveTab: React.Dispatch<React.SetStateAction<string>>;
  setUserRole: React.Dispatch<React.SetStateAction<UserRole>>;
  setWelcomeModalOpen: React.Dispatch<React.SetStateAction<boolean>>;
  setAccessCodeModalOpen: React.Dispatch<React.SetStateAction<boolean>>;
  showToast: (message: string, type?: 'success' | 'error' | 'info') => void;
  activeStand?: string;
  setActiveStand?: React.Dispatch<React.SetStateAction<string>>;
}

export function useNetopsPolling({
  isDedicatedRolePort,
  userRole,
  locale,
  setActiveTab,
  setUserRole,
  setWelcomeModalOpen,
  setAccessCodeModalOpen,
  showToast,
  activeStand,
  setActiveStand,
}: UseNetopsPollingParams) {
  const [devices, setDevices] = useState<Device[]>([]);
  const [stands, setStands] = useState<StandRead[]>([]);
  const [activeStandSlug, setActiveStandSlug] = useState<string>(activeStand || 'all');
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [apiHealthy, setApiHealthy] = useState<boolean>(true);
  const [loading, setLoading] = useState<boolean>(true);
  const authFailedRef = useRef<boolean>(false);
  const devicesInFlightRef = useRef<boolean>(false);
  const jobsInFlightRef = useRef<boolean>(false);
  const standsRef = useRef<StandRead[]>([]);
  const lastFocusFetchRef = useRef<number>(0);
  const lastDevicePollRef = useRef<number>(0);
  const prevHasActiveJobRef = useRef<boolean>(false);

  const clearAuthFailed = useCallback(() => {
    authFailedRef.current = false;
  }, []);

  useEffect(() => {
    if (activeStand && activeStand !== activeStandSlug) {
      setActiveStandSlug(activeStand);
    }
  }, [activeStand, activeStandSlug]);

  useEffect(() => {
    if (devices.length === 0 || activeStandSlug === 'all') return;
    const matched = devices.filter(
      (d) => d.fabric_id === activeStandSlug || d.site === activeStandSlug
    );
    if (matched.length === 0) {
      const fabrics = Array.from(new Set(devices.map((d) => d.fabric_id).filter(Boolean)));
      const fallbackSlug = fabrics.length === 1 && fabrics[0] ? fabrics[0] : 'all';
      setActiveStandSlug(fallbackSlug);
      setActiveStand?.(fallbackSlug);
    }
  }, [devices, activeStandSlug, setActiveStand]);

  useEffect(() => {
    if (isDedicatedRolePort) return;
    const params = new URLSearchParams(window.location.search);
    const hasInviteToken = Boolean(params.get('token') || params.get('role'));
    const localOwner = isLocalOwnerConsole();
    api
      .getBootstrapStatus()
      .then((st) => {
        if (localOwner && st && st.bootstrap_completed === false) {
          localStorage.removeItem('netops_onboarding_done');
          setWelcomeModalOpen(true);
        } else if (!hasInviteToken && st && st.bootstrap_completed && (st.caller_is_bound_owner || localOwner)) {
          localStorage.setItem('netops_onboarding_done', 'true');
          setWelcomeModalOpen(false);
        }
      })
      .catch(() => {});
  }, [isDedicatedRolePort, setWelcomeModalOpen]);

  const isAuthError = (err: unknown): boolean => {
    const msg = getErrorMessage(err, '').toLowerCase();
    return msg.includes('401') || msg.includes('учетные данные') || msg.includes('unauthorized');
  };

  const applyDevicesAndStands = useCallback(
    (data: Device[] | unknown, standData: StandRead[] | null) => {
      if (Array.isArray(standData) && standData.length > 0) {
        standsRef.current = standData;
        setStands(standData);
      }
      if (Array.isArray(data)) {
        const aligned = alignDevicesWithStands(data, standsRef.current);
        setDevices((prev) => {
          if (aligned.length === 0 && prev.length > 0) return prev;
          return areDevicesEqual(prev, aligned) ? prev : aligned;
        });
      }
    },
    []
  );

  const applyJobsList = useCallback((data: JobSummary[] | unknown) => {
    if (!Array.isArray(data)) return;
    setJobs((prev) => {
      if (data.length === 0 && prev.length > 0) return prev;
      return areJobsEqual(prev, data) ? prev : data;
    });
    setSelectedJobId((current) => {
      if (current && data.some((j) => j.id === current)) return current;
      const diffJob = data.find(
        (j) => j.type === 'DRY_RUN' || j.type === 'DRIFT_SCAN' || j.type === 'DEPLOY'
      );
      return diffJob?.id ?? data[0]?.id ?? current;
    });
  }, []);

  const fetchDevices = useCallback(async (forceStands = false) => {
    if (devicesInFlightRef.current) return;
    devicesInFlightRef.current = true;
    try {
      const needStands = forceStands || standsRef.current.length === 0;
      const [data, standData] = await Promise.all([
        api.getDevices(),
        needStands ? api.getStands().catch(() => null) : Promise.resolve(null),
      ]);
      authFailedRef.current = false;
      applyDevicesAndStands(data, standData);
      setApiHealthy((prev) => (prev ? prev : true));
    } catch (err: unknown) {
      if (isAuthError(err)) {
        authFailedRef.current = true;
        if (isLocalOwnerConsole()) {
          localStorage.removeItem('netops_onboarding_done');
          setWelcomeModalOpen(true);
        } else {
          setAccessCodeModalOpen(true);
        }
      } else {
        setApiHealthy(false);
      }
    } finally {
      devicesInFlightRef.current = false;
    }
  }, [applyDevicesAndStands, setWelcomeModalOpen, setAccessCodeModalOpen]);

  const fetchJobs = useCallback(async () => {
    if (jobsInFlightRef.current) return;
    jobsInFlightRef.current = true;
    try {
      const data = await api.getJobs();
      authFailedRef.current = false;
      applyJobsList(data);
    } catch (err: unknown) {
      if (isAuthError(err)) {
        authFailedRef.current = true;
      }
    } finally {
      jobsInFlightRef.current = false;
    }
  }, [applyJobsList]);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    try {
      await Promise.all([fetchDevices(true), fetchJobs()]);
    } finally {
      setLoading(false);
    }
  }, [fetchDevices, fetchJobs]);

  const hasActiveJob = useMemo(() => {
    return jobs.some((j) => j.status === 'RUNNING' || j.status === 'PENDING');
  }, [jobs]);

  useEffect(() => {
    if (prevHasActiveJobRef.current && !hasActiveJob) {
      void fetchDevices();
    }
    prevHasActiveJobRef.current = hasActiveJob;
  }, [hasActiveJob, fetchDevices]);

  useEffect(() => {
    let active = true;
    (async () => {
      const localOwner = isLocalOwnerConsole();
      try {
        try {
          const me = await api.getMe();
          if (active && me.role) {
            authFailedRef.current = false;
            setAccessCodeModalOpen(false);
            const resolvedRole: UserRole =
              localOwner && me.role === 'viewer' ? 'owner' : (me.role as UserRole);
            setUserRole(resolvedRole);
            localStorage.setItem('netops_user_role', resolvedRole);
          }
        } catch (authErr: unknown) {
          if (active) {
            if (localOwner) {
              api.setRole('owner');
              setUserRole('owner');
            } else {
              authFailedRef.current = true;
              setAccessCodeModalOpen(true);
              const params = new URLSearchParams(window.location.search);
              if (params.get('token') || params.get('code')) {
                showToast(getErrorMessage(authErr, 'Ошибка авторизации кода/токена'), 'error');
              }
              return;
            }
          }
        }

        const [devs, jbs, standList] = await Promise.all([
          api.getDevices(),
          api.getJobs(),
          api.getStands().catch(() => null),
        ]);
        if (!active) return;
        authFailedRef.current = false;
        applyDevicesAndStands(devs, standList);
        applyJobsList(jbs);
        setApiHealthy(true);
      } catch (err: unknown) {
        if (!active) return;
        const msg = getErrorMessage(err, '');
        const isAuthErr =
          msg.includes('401') ||
          msg.toLowerCase().includes('учетные данные') ||
          msg.toLowerCase().includes('unauthorized');
        if (isAuthErr && localOwner) {
          authFailedRef.current = true;
          localStorage.removeItem('netops_onboarding_done');
          setWelcomeModalOpen(true);
        } else if (isAuthErr) {
          authFailedRef.current = true;
          setAccessCodeModalOpen(true);
        } else {
          console.error('Failed initial load', err);
        }
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [showToast, setAccessCodeModalOpen, setUserRole, setWelcomeModalOpen]);

  useEffect(() => {
    const canPoll = () =>
      !authFailedRef.current &&
      Boolean(
        localStorage.getItem('netops_onboarding_done') === 'true' ||
          localStorage.getItem('netops_auth_token') ||
          localStorage.getItem('netops_personal_token') ||
          document.cookie.includes('netops_csrf=') ||
          allConsolePorts().includes(window.location.port)
      );

    const onFocusOrVisible = () => {
      const now = Date.now();
      if (now - lastFocusFetchRef.current < 2000) return;
      if (document.visibilityState === 'visible' && canPoll()) {
        lastFocusFetchRef.current = now;
        fetchDevices();
        fetchJobs();
      }
    };
    window.addEventListener('focus', onFocusOrVisible);
    document.addEventListener('visibilitychange', onFocusOrVisible);

    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const schedule = () => {
      const base = hasActiveJob ? 1500 : 20000;
      const delay = base * (0.85 + Math.random() * 0.3);
      timer = setTimeout(async () => {
        if (stopped) return;
        if (document.visibilityState !== 'hidden' && canPoll()) {
          const now = Date.now();
          if (!hasActiveJob) {
            lastDevicePollRef.current = now;
            await Promise.all([fetchJobs(), fetchDevices()]);
          } else {
            const needDevicePoll = now - lastDevicePollRef.current >= 15000;
            if (needDevicePoll) {
              lastDevicePollRef.current = now;
              await Promise.all([fetchJobs(), fetchDevices()]);
            } else {
              await fetchJobs();
            }
          }
        }
        if (!stopped) schedule();
      }, delay);
    };
    schedule();

    return () => {
      window.removeEventListener('focus', onFocusOrVisible);
      document.removeEventListener('visibilitychange', onFocusOrVisible);
      stopped = true;
      clearTimeout(timer);
    };
  }, [hasActiveJob, fetchDevices, fetchJobs]);

  const {
    handleExecuteDryRun,
    handleDeploy,
    handleScanDrift,
    handleRemediate,
    handleSyncInventory,
    handleLintIntent,
    handleDeleteDevice,
  } = useJobActions({
    jobs,
    userRole,
    locale,
    setActiveTab,
    setSelectedJobId,
    setDevices,
    fetchDevices,
    fetchJobs,
    showToast,
  });

  return {
    devices,
    stands,
    setStands,
    activeStandSlug,
    setActiveStandSlug,
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
  };
}

