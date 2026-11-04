import React, { useState, useEffect, useCallback } from 'react';
import { api, getErrorMessage, isLoopbackUrl, type UserRole, type ServerDiscoveryResponse, type LlmCascadeConfig, consoleConfig } from '../../api';
import type { AppTheme } from '../layout/Header';
import type { Locale } from '../../i18n';
import { inferProviderDefaults, type WizardStep } from './welcome/WelcomeStepStyles';
import { WelcomeOwnerStep, WelcomeGithubStep, WelcomeTelegramStep } from './welcome/WelcomeSteps1To3';
import { WelcomeHeader, WelcomeAiStep, WelcomeServerStep } from './welcome/WelcomeSteps4And5';

interface WelcomeModalProps {
  isOpen: boolean;
  onClose: (role: UserRole) => void;
  locale: Locale;
  setLocale: (l: Locale) => void;
  theme: AppTheme;
  setTheme: (t: AppTheme) => void;
}

export const WelcomeModal: React.FC<WelcomeModalProps> = ({ isOpen, onClose, locale, setLocale, theme, setTheme }) => {
  const [wizardStep, setWizardStep] = useState<WizardStep>(1);
  const [clusterName, setClusterName] = useState('');
  const [exposureMode, setExposureMode] = useState<'local_only' | 'public_tunnel'>('public_tunnel');
  const [ownerToken, setOwnerToken] = useState('');
  const [copiedKey, setCopiedKey] = useState(false);
  const [recoverMode, setRecoverMode] = useState(false);
  const [recoveryKeyInput, setRecoveryKeyInput] = useState('');
  const [telegramCodeInput, setTelegramCodeInput] = useState('');
  const [recoveryStatusMsg, setRecoveryStatusMsg] = useState('');
  const [claimingOwner, setClaimingOwner] = useState(false);
  const [sendingTelegramCode, setSendingTelegramCode] = useState(false);
  const [canTransferOwnerFlag, setCanTransferOwnerFlag] = useState(false);

  const [githubRepoUrl, setGithubRepoUrl] = useState('');
  const [githubBranch, setGithubBranch] = useState('main');
  const [githubPat, setGithubPat] = useState('');
  const [githubConnected, setGithubConnected] = useState(false);
  const [githubCommitShort, setGithubCommitShort] = useState('HEAD');
  const [githubError, setGithubError] = useState(false);
  const [savingGithub, setSavingGithub] = useState(false);
  const [githubSavedMsg, setGithubSavedMsg] = useState('');

  const [telegramBotToken, setTelegramBotToken] = useState('');
  const [telegramChatId, setTelegramChatId] = useState('');
  const [telegramBotUsername, setTelegramBotUsername] = useState('');
  const [telegramClaimLink, setTelegramClaimLink] = useState('');
  const [telegramRecoverLink, setTelegramRecoverLink] = useState('');
  const [telegramLinked, setTelegramLinked] = useState(false);
  const [telegramLinkedUser, setTelegramLinkedUser] = useState('');
  const [savingTelegram, setSavingTelegram] = useState(false);
  const [telegramSavedMsg, setTelegramSavedMsg] = useState('');

  const [llmCascade, setLlmCascade] = useState<LlmCascadeConfig[]>([
    { name: '', base_url: '', model: '', api_key: '', timeout_seconds: 2.5 },
  ]);
  const [savingAi, setSavingAi] = useState(false);
  const [aiSavedMsg, setAiSavedMsg] = useState('');

  const [serverHost, setServerHost] = useState('');
  const [serverPort, setServerPort] = useState(221);
  const [serverUser, setServerUser] = useState('');
  const [serverPassword, setServerPassword] = useState('');
  const [discoveringServer, setDiscoveringServer] = useState(false);
  const [discoveryResult, setDiscoveryResult] = useState<ServerDiscoveryResponse | null>(null);

  const isEn = locale === 'en';

  const refreshBootstrapStatus = useCallback(async () => {
    try {
      const st = await api.getBootstrapStatus();
      setCanTransferOwnerFlag(Boolean(st.can_transfer_owner || st.initialized));
      if (st.cluster_name) setClusterName(st.cluster_name);
      if (st.exposure?.mode === 'public_tunnel' || st.exposure?.mode === 'local_only') {
        setExposureMode(st.exposure.mode);
      }
      const remoteUrl = st.github?.remote_url || st.github?.github_repo_url || '';
      setGithubRepoUrl((prev) => prev || remoteUrl);
      setGithubBranch((prev) => prev || st.github?.branch || 'main');
      setGithubConnected(Boolean(st.github?.repo_connected || remoteUrl));
      setGithubCommitShort(st.github?.commit_short || 'HEAD');
      setTelegramBotUsername(st.telegram?.bot_username || '');
      setTelegramClaimLink(st.telegram?.deep_link_claim || '');
      setTelegramRecoverLink(st.telegram?.deep_link_recover || '');
      setTelegramLinked(Boolean(st.owner?.telegram_linked));
      setTelegramLinkedUser(st.owner?.telegram_username || '');
      setTelegramChatId(st.owner?.telegram_chat_id || '');
      setOwnerToken(st.owner?.recovery_key || '');
      setServerHost(st.server?.clab_host || '');
      if (st.server?.clab_port) setServerPort(st.server.clab_port);
      if (st.server?.clab_user) setServerUser(st.server.clab_user);
      setLlmCascade(
        Array.isArray(st.ai?.cascade) && st.ai.cascade.length > 0
          ? st.ai.cascade
          : [{ name: '', base_url: '', model: '', api_key: '', timeout_seconds: 2.5 }]
      );
      if (st.initialized && st.caller_is_bound_owner === false) setRecoverMode(true);
      else if (!st.initialized) setRecoverMode(false);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    const t0 = setTimeout(() => void refreshBootstrapStatus(), 0);
    return () => clearTimeout(t0);
  }, [isOpen, refreshBootstrapStatus]);

  useEffect(() => {
    if (!isOpen || wizardStep !== 3) return;
    const timer = setInterval(() => void refreshBootstrapStatus(), 2000);
    return () => clearInterval(timer);
  }, [isOpen, wizardStep, refreshBootstrapStatus]);

  if (!isOpen) return null;

  const handleClaimOwner = async () => {
    setClaimingOwner(true);
    setRecoveryStatusMsg('');
    try {
      const res = await api.claimOwnerBootstrap({
        cluster_name: clusterName.trim() || '',
        recovery_key: recoveryKeyInput.trim() || undefined,
      });
      const cfg = await api.configureBootstrapLayers({
        cluster_name: clusterName.trim() || '',
        exposure_mode: exposureMode,
        websocket_enabled: true,
        auto_commit_on_diff: true,
      });
      if (cfg?.exposure?.public_url && !isLoopbackUrl(cfg.exposure.public_url)) {
        localStorage.setItem('netops_share_base_url', cfg.exposure.public_url);
      } else if (serverHost.trim()) {
        localStorage.setItem('netops_share_base_url', `http://${serverHost.trim()}:${consoleConfig().owner_port}`);
      } else {
        localStorage.removeItem('netops_share_base_url');
      }
      setOwnerToken(res.token);
      localStorage.setItem('netops_user_role', 'owner');
      localStorage.setItem('netops_cluster_name', res.cluster_name);
      setRecoverMode(false);
      setWizardStep(2);
    } catch (err: unknown) {
      setRecoverMode(true);
      setRecoveryStatusMsg(getErrorMessage(err, 'Кластер уже привязан к другому ПК'));
    } finally {
      setClaimingOwner(false);
    }
  };

  const handleRequestTelegramRecoveryCode = async () => {
    setSendingTelegramCode(true);
    setRecoveryStatusMsg('');
    try {
      const res = await api.recoverOwnerAccess({ action: 'request_telegram_code' });
      setRecoveryStatusMsg(
        res.message || (isEn ? '6-digit code sent to Owner in Telegram' : '6-значный код отправлен Владельцу в Telegram')
      );
    } catch (err: unknown) {
      setRecoveryStatusMsg(getErrorMessage(err, isEn ? 'Failed to send code via Telegram' : 'Не удалось отправить код в Telegram'));
    } finally {
      setSendingTelegramCode(false);
    }
  };

  const handleVerifyOwnerRecovery = async () => {
    setClaimingOwner(true);
    try {
      const res = await api.recoverOwnerAccess({
        action: 'verify',
        recovery_key: recoveryKeyInput.trim() || undefined,
        telegram_code: telegramCodeInput.trim() || undefined,
      });
      if (res.token) setOwnerToken(res.token);
      localStorage.setItem('netops_user_role', 'owner');
      localStorage.setItem('netops_cluster_name', clusterName.trim() || '');
      localStorage.setItem('netops_onboarding_done', 'true');
      setRecoverMode(false);
      onClose('owner');
    } catch (err: unknown) {
      setRecoveryStatusMsg(getErrorMessage(err, 'Неверный ключ или код'));
    } finally {
      setClaimingOwner(false);
    }
  };

  const handleConnectGithub = async (advance: boolean) => {
    const targetUrl = githubRepoUrl.trim();
    setSavingGithub(true);
    setGithubError(false);
    setGithubSavedMsg('');
    try {
      if (targetUrl) {
        const res = await api.connectGitRepo({
          repo_url: targetUrl,
          branch: githubBranch.trim() || 'main',
          pat: githubPat.trim() || undefined,
          cluster_name: clusterName.trim(),
          exposure_mode: exposureMode,
        });
        const gitInfo = res.git as (typeof res.git & { connected?: boolean; message?: string }) | undefined;
        if (gitInfo?.connected === false) {
          setGithubError(true);
          setGithubConnected(false);
          setGithubSavedMsg(gitInfo.message || 'Ошибка подключения к репозиторию');
          return;
        }
        const br = gitInfo?.branch || githubBranch.trim() || 'main';
        const sha = gitInfo?.commit_short || 'HEAD';
        setGithubBranch(br);
        setGithubCommitShort(sha);
        setGithubConnected(true);
        const syncRes = await api.syncGitSot().catch(() => null);
        const syncSummary = syncRes
          ? ` · Узлов: +${syncRes.sync.created.length} / ~${syncRes.sync.updated.length}`
          : '';
        setGithubSavedMsg(`Подключён: ${br}@${sha}${syncSummary}`);
      } else {
        const syncRes = await api.syncGitSot();
        setGithubBranch(syncRes.git?.branch || 'main');
        setGithubCommitShort(syncRes.git?.commit_short || 'HEAD');
        setGithubConnected(true);
        setGithubSavedMsg(
          `Локальный SoT: ${syncRes.git?.branch || 'main'}@${syncRes.git?.commit_short || 'HEAD'} (+${syncRes.sync.created.length} / ~${syncRes.sync.updated.length})`
        );
      }
      await refreshBootstrapStatus();
      if (advance) setWizardStep(3);
    } catch (err: unknown) {
      setGithubError(true);
      setGithubSavedMsg(getErrorMessage(err, 'Ошибка синхронизации GitHub'));
    } finally {
      setSavingGithub(false);
    }
  };

  const handleSaveTelegram = async (advance: boolean) => {
    setSavingTelegram(true);
    setTelegramSavedMsg('');
    try {
      if (telegramBotToken.trim()) {
        const res = await api.configureBootstrapLayers({ telegram_bot_token: telegramBotToken.trim() });
        if (res.bot_username) setTelegramBotUsername(res.bot_username);
        setTelegramSavedMsg('Токен бота сохранён');
      }
      await refreshBootstrapStatus();
      if (advance) setWizardStep(4);
    } catch (err: unknown) {
      setTelegramSavedMsg(getErrorMessage(err, 'Ошибка сохранения Telegram'));
    } finally {
      setSavingTelegram(false);
    }
  };

  const handleUnlinkTelegram = async () => {
    try {
      await api.unlinkOwnerTelegram();
      setTelegramLinked(false);
      setTelegramLinkedUser('');
      setTelegramChatId('');
    } catch (e) {
      setTelegramSavedMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const handleSaveAiCluster = async () => {
    setSavingAi(true);
    setAiSavedMsg('');
    try {
      const cleaned = llmCascade
        .filter((p) => p.name.trim() || p.base_url.trim() || (p.api_key || '').trim())
        .map((p) => {
          const def = inferProviderDefaults(p.name, p.api_key || '');
          return {
            name: p.name.trim() || def.name,
            base_url: p.base_url.trim() || def.base_url,
            model: p.model.trim() || def.model,
            api_key: (p.api_key || '').trim(),
            timeout_seconds: Number(p.timeout_seconds) || 2.5,
          };
        });
      await api.configureBootstrapLayers({ llm_cascade: cleaned });
      await refreshBootstrapStatus();
      setWizardStep(5);
    } catch (err: unknown) {
      setAiSavedMsg(getErrorMessage(err, 'Ошибка сохранения ИИ'));
    } finally {
      setSavingAi(false);
    }
  };

  const handleDiscoverServer = async () => {
    if (!serverHost.trim()) {
      setDiscoveryResult({ status: 'error', message: 'Введите IP или хост сервера' });
      return;
    }
    setDiscoveringServer(true);
    try {
      const res = await api.discoverAndAttachServer({
        mode: 'ssh',
        host: serverHost.trim(),
        port: Number(serverPort) || 221,
        username: serverUser.trim(),
        password: serverPassword || undefined,
      });
      setDiscoveryResult(res);
    } catch (err: unknown) {
      setDiscoveryResult({ status: 'error', message: getErrorMessage(err, 'Сервер недоступен') });
    } finally {
      setDiscoveringServer(false);
    }
  };

  const handleDiscoverOffline = async () => {
    setDiscoveringServer(true);
    try {
      const res = await api.discoverAndAttachServer({ mode: 'offline' });
      setDiscoveryResult(res);
    } catch (err: unknown) {
      setDiscoveryResult({ status: 'error', message: getErrorMessage(err, 'Ошибка подключения офлайн-стенда') });
    } finally {
      setDiscoveringServer(false);
    }
  };

  const handleFinishWizard = async () => {
    try {
      if (!ownerToken && !localStorage.getItem('netops_auth_token')) {
        const res = await api.claimOwnerBootstrap({ cluster_name: clusterName.trim() || '' });
        setOwnerToken(res.token);
      }
      await api.configureBootstrapLayers({
        cluster_name: clusterName.trim() || '',
        exposure_mode: exposureMode,
        bootstrap_completed: true,
      });
      if (!discoveryResult?.synced_total) {
        await api.syncInventory().catch(() => {});
      }
    } catch {
      // ignore
    }
    localStorage.setItem('netops_user_role', 'owner');
    localStorage.setItem('netops_cluster_name', clusterName.trim() || '');
    localStorage.setItem('netops_onboarding_done', 'true');
    onClose('owner');
  };

  const displayPublicHost = serverHost.trim() || window.location.hostname;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-2 sm:p-4 bg-[var(--bg-main)]/95 backdrop-blur-xl overflow-y-auto">
      <div className="relative z-10 w-full max-w-xl max-h-[94dvh] rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-card)] shadow-2xl text-[var(--text-primary)] flex flex-col overflow-hidden">
        <WelcomeHeader
          isEn={isEn}
          locale={locale}
          setLocale={setLocale}
          theme={theme}
          setTheme={setTheme}
          wizardStep={wizardStep}
          setWizardStep={setWizardStep}
          onSkip={handleFinishWizard}
          ownerToken={ownerToken}
          copiedKey={copiedKey}
          setCopiedKey={setCopiedKey}
        />

        <div className="p-3.5 sm:p-5 space-y-4 overflow-y-auto flex-1">

          {wizardStep === 1 && (
            <WelcomeOwnerStep
              isEn={isEn}
              recoverMode={recoverMode}
              setRecoverMode={setRecoverMode}
              clusterName={clusterName}
              setClusterName={setClusterName}
              exposureMode={exposureMode}
              setExposureMode={setExposureMode}
              displayPublicHost={displayPublicHost}
              canTransferOwnerFlag={canTransferOwnerFlag}
              claimingOwner={claimingOwner}
              onClaimOwner={handleClaimOwner}
              sendingTelegramCode={sendingTelegramCode}
              onRequestTelegramRecoveryCode={handleRequestTelegramRecoveryCode}
              telegramRecoverLink={telegramRecoverLink}
              telegramCodeInput={telegramCodeInput}
              setTelegramCodeInput={setTelegramCodeInput}
              recoveryKeyInput={recoveryKeyInput}
              setRecoveryKeyInput={setRecoveryKeyInput}
              recoveryStatusMsg={recoveryStatusMsg}
              onVerifyOwnerRecovery={handleVerifyOwnerRecovery}
            />
          )}

          {wizardStep === 2 && (
            <WelcomeGithubStep
              isEn={isEn}
              githubRepoUrl={githubRepoUrl}
              setGithubRepoUrl={setGithubRepoUrl}
              githubPat={githubPat}
              setGithubPat={setGithubPat}
              githubBranch={githubBranch}
              setGithubBranch={setGithubBranch}
              githubConnected={githubConnected}
              githubCommitShort={githubCommitShort}
              githubError={githubError}
              savingGithub={savingGithub}
              githubSavedMsg={githubSavedMsg}
              onConnectGithub={(adv) => void handleConnectGithub(adv)}
              setWizardStep={setWizardStep}
            />
          )}

          {wizardStep === 3 && (
            <WelcomeTelegramStep
              isEn={isEn}
              telegramBotToken={telegramBotToken}
              setTelegramBotToken={setTelegramBotToken}
              savingTelegram={savingTelegram}
              telegramLinked={telegramLinked}
              telegramLinkedUser={telegramLinkedUser}
              telegramChatId={telegramChatId}
              telegramBotUsername={telegramBotUsername}
              telegramClaimLink={telegramClaimLink}
              telegramSavedMsg={telegramSavedMsg}
              onSaveTelegram={(adv) => void handleSaveTelegram(adv)}
              onUnlinkTelegram={() => void handleUnlinkTelegram()}
              setWizardStep={setWizardStep}
            />
          )}

          {wizardStep === 4 && (
            <WelcomeAiStep
              isEn={isEn}
              llmCascade={llmCascade}
              setLlmCascade={setLlmCascade}
              aiSavedMsg={aiSavedMsg}
              savingAi={savingAi}
              onSaveAiCluster={handleSaveAiCluster}
              setWizardStep={setWizardStep}
            />
          )}

          {wizardStep === 5 && (
            <WelcomeServerStep
              isEn={isEn}
              serverHost={serverHost}
              setServerHost={setServerHost}
              serverPort={serverPort}
              setServerPort={setServerPort}
              serverUser={serverUser}
              setServerUser={setServerUser}
              serverPassword={serverPassword}
              setServerPassword={setServerPassword}
              discoveringServer={discoveringServer}
              discoveryResult={discoveryResult}
              onDiscoverServer={handleDiscoverServer}
              onDiscoverOffline={handleDiscoverOffline}
              onFinishWizard={handleFinishWizard}
              setWizardStep={setWizardStep}
            />
          )}
        </div>
      </div>
    </div>
  );
};
