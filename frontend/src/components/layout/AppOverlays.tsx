import React, { Suspense, lazy } from 'react';
import type { Device, StandRead, UserRole } from '../../api';
import type { Locale } from '../../i18n';
import type { AppTheme } from './Header';

const CopilotPanel = lazy(() => import('../modals/CopilotPanel').then((m) => ({ default: m.CopilotPanel })));
const DryRunModal = lazy(() => import('../modals/DryRunModal').then((m) => ({ default: m.DryRunModal })));
const WelcomeModal = lazy(() => import('../modals/WelcomeModal').then((m) => ({ default: m.WelcomeModal })));
const ShareModal = lazy(() => import('../modals/ShareModal').then((m) => ({ default: m.ShareModal })));
const ShortcutsModal = lazy(() => import('../modals/ShortcutsModal').then((m) => ({ default: m.ShortcutsModal })));
const AccessCodeModal = lazy(() => import('../modals/AccessCodeModal').then((m) => ({ default: m.AccessCodeModal })));
const StandsModal = lazy(() => import('../modals/StandsModal').then((m) => ({ default: m.StandsModal })));

interface AppOverlaysProps {
  copilotOpen: boolean;
  onCloseCopilot: () => void;
  activeTab: string;
  deviceId: number | null;
  userRole: UserRole;
  locale: Locale;
  setLocale: (l: Locale) => void;
  theme: AppTheme;
  setTheme: (t: AppTheme) => void;
  copilotRequest: { text: string; n: number } | null;
  onRunDryRun: (targetIds: number[]) => void;
  onOpenDiff: (targetDeviceId?: number) => void;
  onScanDrift: (deviceIds?: number[]) => void;
  onOpenDevice: (id: number) => void;
  onRemediate: (targetDeviceId: number) => void;
  onOpenTab: (tab: string) => void;
  dryRunModalOpen: boolean;
  onCloseDryRun: () => void;
  onConfirmDryRun: (deviceIds: number[]) => void;
  dryRunTargetIds: number[];
  devices: Device[];
  shareModalOpen: boolean;
  onCloseShare: () => void;
  welcomeModalOpen: boolean;
  isDedicatedRolePort: boolean;
  onWelcomeClose: (role: UserRole) => void;
  shortcutsModalOpen: boolean;
  onCloseShortcuts: () => void;
  accessCodeModalOpen: boolean;
  onAccessCodeSuccess: (role: UserRole) => void;
  standsModalOpen?: boolean;
  onCloseStandsModal?: () => void;
  activeStand?: string;
  onSelectStand?: (slug: string) => void;
  stands?: StandRead[];
  onRefreshStands?: () => Promise<void>;
}

export const AppOverlays: React.FC<AppOverlaysProps> = ({
  copilotOpen,
  onCloseCopilot,
  activeTab,
  deviceId,
  userRole,
  locale,
  setLocale,
  theme,
  setTheme,
  copilotRequest,
  onRunDryRun,
  onOpenDiff,
  onScanDrift,
  onOpenDevice,
  onRemediate,
  onOpenTab,
  dryRunModalOpen,
  onCloseDryRun,
  onConfirmDryRun,
  dryRunTargetIds,
  devices,
  shareModalOpen,
  onCloseShare,
  welcomeModalOpen,
  isDedicatedRolePort,
  onWelcomeClose,
  shortcutsModalOpen,
  onCloseShortcuts,
  accessCodeModalOpen,
  onAccessCodeSuccess,
  standsModalOpen = false,
  onCloseStandsModal,
  activeStand = 'all',
  onSelectStand,
  stands = [],
  onRefreshStands,
}) => (
  <Suspense fallback={null}>
    {copilotOpen && (
      <CopilotPanel
        open={copilotOpen}
        onClose={onCloseCopilot}
        deviceId={activeTab === 'device' ? deviceId : null}
        userRole={userRole}
        locale={locale}
        request={copilotRequest}
        activeScreen={activeTab}
        onRunDryRun={onRunDryRun}
        onOpenDiff={onOpenDiff}
        onScanDrift={onScanDrift}
        onOpenDevice={onOpenDevice}
        onRemediate={onRemediate}
        onOpenTab={onOpenTab}
      />
    )}

    {dryRunModalOpen && userRole !== 'viewer' && (
      <DryRunModal
        open={dryRunModalOpen}
        onClose={onCloseDryRun}
        onConfirm={onConfirmDryRun}
        deviceIds={dryRunTargetIds}
        devices={devices}
        locale={locale}
      />
    )}

    {shareModalOpen && (userRole === 'owner' || userRole === 'admin') && (
      <ShareModal
        isOpen={shareModalOpen}
        onClose={onCloseShare}
        userRole={userRole}
        locale={locale}
      />
    )}

    {welcomeModalOpen && !isDedicatedRolePort && (
      <WelcomeModal
        isOpen={welcomeModalOpen}
        onClose={onWelcomeClose}
        locale={locale}
        setLocale={setLocale}
        theme={theme}
        setTheme={setTheme}
      />
    )}

    {shortcutsModalOpen && (
      <ShortcutsModal
        isOpen={shortcutsModalOpen}
        onClose={onCloseShortcuts}
        locale={locale}
      />
    )}

    {accessCodeModalOpen && (
      <AccessCodeModal
        isOpen={accessCodeModalOpen}
        locale={locale}
        onSuccess={onAccessCodeSuccess}
      />
    )}

    {standsModalOpen && onCloseStandsModal && onSelectStand && onRefreshStands && (
      <StandsModal
        isOpen={standsModalOpen}
        onClose={onCloseStandsModal}
        userRole={userRole}
        locale={locale}
        activeStand={activeStand}
        onSelectStand={onSelectStand}
        stands={stands}
        onRefreshStands={onRefreshStands}
      />
    )}
  </Suspense>
);

