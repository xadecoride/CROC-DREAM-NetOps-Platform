/**
 * @file api/client.ts
 * @description Domain REST API client methods for CROC DREAM NetOps Platform.
 */

import {
  bootstrapAuthSession,
  getPublicConsoleUrls,
  isLocalOwnerConsole,
  isLoopbackUrl,
  resolveCurrentRole,
  resolveShareBaseUrl,
  type PublicConsoleUrls,
} from './auth';
import { NetOpsDeviceJobClient } from './deviceJobApi';
import type {
  AiGuardResponse,
  BootstrapConfigureRequest,
  BootstrapConfigureResponse,
  BootstrapExposureInfo,
  BootstrapStatusResponse,
  CollectorPlanResponse,
  CopilotReplyWithAction,
  EmergencyAuditLog,
  GitStatusInfo,
  IntentSaveResult,
  IntentSource,
  IntegrationsStatus,
  RecoverOwnerResponse,
  RiskExplanation,
  ServerDiscoveryResponse,
  UserAccount,
  UserInviteCreated,
  UserRole,
} from './types';

export interface StaticViewerStatus {
  enabled: boolean;
  token: string;
  username: string;
  role: string;
  allowed_system?: string | null;
}

export class NetOpsApiClient extends NetOpsDeviceJobClient {
  // --------------------------------------------------------------------------
  // AI Copilot & Risk Assessment
  // --------------------------------------------------------------------------

  async explainDiff(jobId: string, hostname?: string): Promise<RiskExplanation> {
    const query = hostname ? `?hostname=${encodeURIComponent(hostname)}` : '';
    return this.request<RiskExplanation>(`/jobs/${jobId}/explain${query}`, {
      method: 'POST',
    });
  }

  async askCopilot(message: string, deviceId?: number | null, screen?: string | null): Promise<CopilotReplyWithAction> {
    return this.request<CopilotReplyWithAction>('/copilot/chat', {
      method: 'POST',
      body: JSON.stringify({
        message,
        device_id: deviceId ?? null,
        screen: screen ?? null,
      }),
    });
  }

  async getAiGuardOverview(): Promise<AiGuardResponse[]> {
    return this.request<AiGuardResponse[]>('/ai-guard/overview');
  }

  // --------------------------------------------------------------------------
  // Emergency Hub / Manual Intervention (Zero-Trust RBAC)
  // --------------------------------------------------------------------------

  async executeEmergencyCommand(device: string, command: string): Promise<{
    status: string;
    device: string;
    command: string;
    output: string;
    failed?: boolean;
  }> {
    return this.request('/emergency/command', {
      method: 'POST',
      body: JSON.stringify({ device, command }),
    });
  }

  async applyEmergencyPatch(device: string, patch: string, reason?: string): Promise<{
    status: string;
    device: string;
    applied_lines: string[];
    output: string;
    reason?: string;
  }> {
    return this.request('/emergency/patch', {
      method: 'POST',
      body: JSON.stringify({ device, patch, reason: reason || 'Manual emergency patch' }),
    });
  }

  async softResetFabric(): Promise<{ status: string; message: string; details: string }> {
    return this.request('/emergency/soft-reset', { method: 'POST' });
  }

  async hardResetFabric(): Promise<{ status: string; message: string; details: string }> {
    return this.request('/emergency/hard-reset', { method: 'POST' });
  }

  async getEmergencyAudit(): Promise<EmergencyAuditLog[]> {
    return this.request<EmergencyAuditLog[]>('/emergency/audit');
  }

  // --------------------------------------------------------------------------
  // Session, Sharing, Git SoT & Integrations Utilities
  // --------------------------------------------------------------------------

  async getMe(): Promise<UserAccount> {
    return this.request<UserAccount>('/users/me');
  }

  async getUsers(): Promise<UserAccount[]> {
    return this.request<UserAccount[]>('/users');
  }

  async createUserInvite(
    username: string,
    role: 'viewer' | 'operator' | 'admin',
    allowedSystem?: string | null,
    expiresInMinutes?: number | null
  ): Promise<UserInviteCreated> {
    return this.request<UserInviteCreated>('/users', {
      method: 'POST',
      body: JSON.stringify({
        username,
        role,
        allowed_system: allowedSystem || null,
        expires_in_minutes: expiresInMinutes && expiresInMinutes > 0 ? expiresInMinutes : null,
      }),
    });
  }

  async createBatchInvites(
    prefix: string,
    role: 'viewer' | 'operator' | 'admin',
    count: number,
    allowedSystem?: string | null,
    expiresInMinutes?: number | null
  ): Promise<UserInviteCreated[]> {
    return this.request<UserInviteCreated[]>('/users/batch', {
      method: 'POST',
      body: JSON.stringify({
        prefix,
        role,
        count,
        allowed_system: allowedSystem || null,
        expires_in_minutes: expiresInMinutes && expiresInMinutes > 0 ? expiresInMinutes : null,
      }),
    });
  }

  async updateUserInvite(
    userId: number,
    patch: {
      role?: 'viewer' | 'operator' | 'admin';
      is_active?: boolean;
      allowed_system?: string | null;
      expires_in_minutes?: number | null;
    }
  ): Promise<UserAccount> {
    return this.request<UserAccount>(`/users/${userId}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    });
  }

  async generateUserVerifyCode(userId: number): Promise<UserAccount> {
    return this.request<UserAccount>(`/users/${userId}/verify-code`, {
      method: 'POST',
    });
  }

  async rotateUserToken(userId: number): Promise<UserInviteCreated> {
    return this.request<UserInviteCreated>(`/users/${userId}/token`, {
      method: 'POST',
    });
  }

  async unbindUserDevice(userId: number): Promise<UserAccount> {
    return this.request<UserAccount>(`/users/${userId}/unbind`, {
      method: 'POST',
    });
  }

  async deleteUserInvite(userId: number): Promise<void> {
    return this.request<void>(`/users/${userId}`, {
      method: 'DELETE',
    });
  }

  async getGitStatus(): Promise<GitStatusInfo> {
    return this.request<GitStatusInfo>('/git/status');
  }

  async syncGitSot(): Promise<{
    status: string;
    pull_output: string;
    sync: { created: string[]; updated: string[]; unchanged: string[] };
    git: GitStatusInfo;
  }> {
    return this.request('/git/sync', { method: 'POST' });
  }

  async getIntegrationsStatus(): Promise<IntegrationsStatus> {
    return this.request<IntegrationsStatus>('/system/integrations');
  }

  async sendTelegramSummary(customNote?: string): Promise<{
    status: string;
    sent: boolean;
    chat_ids: number[];
    message_preview: string;
    hint?: string;
  }> {
    return this.request('/system/telegram/test', {
      method: 'POST',
      body: JSON.stringify({ custom_note: customNote || null }),
    });
  }

  /**
   * Builds a direct share URL for any personal or preset token using the public internet URL.
   */
  buildInviteUrl(token: string, baseOverride?: string): string {
    const base = resolveShareBaseUrl(baseOverride);
    return `${base}/?token=${encodeURIComponent(token)}`;
  }

  /**
   * Returns ready-to-share public URLs for Jury (5176), Operator (5175), Admin (5174), and single-port links.
   */
  getPublicConsoles(exposure?: BootstrapExposureInfo | null): PublicConsoleUrls {
    return getPublicConsoleUrls(exposure);
  }

  getCanvasName(): string {
    return localStorage.getItem('netops_canvas_name') || 'Фабрика КРОК CLOS (Пром-кластер)';
  }

  setCanvasName(name: string): void {
    localStorage.setItem('netops_canvas_name', name);
  }

  getRole(): UserRole {
    return resolveCurrentRole();
  }

  setRole(role: UserRole): void {
    if (isLocalOwnerConsole() && role === 'viewer') {
      localStorage.setItem('netops_user_role', 'owner');
      return;
    }
    localStorage.setItem('netops_user_role', role);
  }

  async getBootstrapStatus(): Promise<BootstrapStatusResponse> {
    const res = await this.request<BootstrapStatusResponse>('/system/bootstrap/status');
    if (typeof window !== 'undefined' && res?.exposure?.public_url && !isLoopbackUrl(res.exposure.public_url)) {
      localStorage.setItem('netops_share_base_url', res.exposure.public_url);
    }
    return res;
  }

  async claimOwnerBootstrap(data: {
    cluster_name: string;
    owner_username?: string;
    recovery_key?: string;
  }): Promise<{
    status: string;
    username: string;
    role: UserRole;
    token: string;
    recovery_key: string;
    bound_client_id: string;
    bound_ip: string;
    cluster_name: string;
  }> {
    return this.request('/system/bootstrap/claim-owner', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async recoverOwnerAccess(data: {
    action: 'request_telegram_code' | 'verify';
    recovery_key?: string;
    telegram_code?: string;
  }): Promise<RecoverOwnerResponse> {
    return this.request<RecoverOwnerResponse>('/system/bootstrap/recover-owner', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async configureBootstrapLayers(data: BootstrapConfigureRequest): Promise<BootstrapConfigureResponse> {
    const res = await this.request<BootstrapConfigureResponse>('/system/bootstrap/configure', {
      method: 'POST',
      body: JSON.stringify(data),
    });
    if (typeof window !== 'undefined' && res?.exposure?.public_url && !isLoopbackUrl(res.exposure.public_url)) {
      localStorage.setItem('netops_share_base_url', res.exposure.public_url);
    }
    return res;
  }

  async commitDiffToGit(jobId: string): Promise<{
    status: string;
    committed: boolean;
    pushed: boolean;
    commit_short: string;
    commit_message?: string;
    branch: string;
    push_output?: string;
  }> {
    return this.request('/git/commit-diff', {
      method: 'POST',
      body: JSON.stringify({ job_id: jobId }),
    });
  }

  async discoverAndAttachServer(data: {
    mode: 'ssh' | 'offline';
    host?: string;
    port?: number;
    username?: string;
    password?: string;
    switch_password_huawei?: string;
    switch_password_arista?: string;
    switch_password_cisco?: string;
  }): Promise<ServerDiscoveryResponse> {
    return this.request<ServerDiscoveryResponse>('/system/bootstrap/discover-server', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async getFreezeStatus(): Promise<{
    frozen: boolean;
    reason?: string;
    frozen_by?: string;
    frozen_at?: string;
  }> {
    return this.request('/emergency/freeze', { method: 'GET' });
  }

  async setFreezeStatus(
    frozen: boolean,
    reason = 'Аварийная заморозка фабрики (Change Freeze)'
  ): Promise<{
    frozen: boolean;
    reason?: string;
    frozen_by?: string;
    frozen_at?: string;
  }> {
    return this.request('/emergency/freeze', {
      method: 'POST',
      body: JSON.stringify({ frozen, reason }),
    });
  }

  async unlinkOwnerTelegram(): Promise<{ unlinked: boolean }> {
    return this.request<{ unlinked: boolean }>('/system/owner/telegram/unlink', { method: 'POST' });
  }

  async connectGitRepo(data: {
    repo_url: string;
    branch?: string;
    pat?: string;
    cluster_name?: string;
    exposure_mode?: 'local_only' | 'public_tunnel';
    webhook_public_url?: string;
  }): Promise<BootstrapConfigureResponse> {
    return this.configureBootstrapLayers({
      github_repo_url: data.repo_url,
      github_branch: data.branch || 'main',
      github_pat: data.pat,
      cluster_name: data.cluster_name,
      exposure_mode: data.exposure_mode,
      webhook_public_url: data.webhook_public_url,
    });
  }

  async getStaticViewerStatus(): Promise<StaticViewerStatus> {
    return this.request<StaticViewerStatus>('/users/static-viewer');
  }

  async toggleStaticViewer(enabled: boolean): Promise<StaticViewerStatus> {
    return this.request<StaticViewerStatus>('/users/static-viewer', {
      method: 'PATCH',
      body: JSON.stringify({ enabled }),
    });
  }

  async rotateStaticViewer(): Promise<StaticViewerStatus> {
    return this.request<StaticViewerStatus>('/users/static-viewer/rotate', {
      method: 'POST',
    });
  }

  async getIntentSource(deviceId: number): Promise<IntentSource> {
    return this.request<IntentSource>(`/devices/${deviceId}/intent-source`);
  }

  async saveIntentSource(deviceId: number, yaml: string): Promise<IntentSaveResult> {
    return this.request<IntentSaveResult>(`/devices/${deviceId}/intent-source`, {
      method: 'PUT',
      body: JSON.stringify({ yaml }),
    });
  }

  async getPlatformCommands(): Promise<Record<string, Record<string, string>>> {
    return this.request<Record<string, Record<string, string>>>('/telemetry/platform-commands');
  }

  async getCollectorPlan(): Promise<CollectorPlanResponse> {
    return this.request<CollectorPlanResponse>('/telemetry/collectors');
  }

  async setDeviceCollectorProtocol(
    hostname: string,
    protocol: 'auto' | 'gnmi' | 'snmp' | 'prometheus_exporter' | 'ssh_cli'
  ): Promise<CollectorPlanResponse> {
    return this.request<CollectorPlanResponse>(`/telemetry/collectors/${encodeURIComponent(hostname)}`, {
      method: 'PUT',
      body: JSON.stringify({ protocol }),
    });
  }

  async refreshCollectorPlan(): Promise<{ status: string }> {
    return this.request<{ status: string }>('/telemetry/collectors/refresh', {
      method: 'POST',
    });
  }

  emitGitRefresh(): void {
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('netops:git-refresh'));
    }
  }
}

export const api = new NetOpsApiClient();
/** Вызывать только после loadConsoleConfig() (см. main.tsx). */
export function startApiSession(): void {
  api.setReady(bootstrapAuthSession((token) => api.login(token)));
}
