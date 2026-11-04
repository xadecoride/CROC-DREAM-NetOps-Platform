/**
 * @file api/domainTypes.ts
 * @description Extended domain types (users, integrations, bootstrap, discovery, collectors) for CROC DREAM NetOps Platform.
 */

import type { GitStatusInfo, UserRole } from './types';

export interface UserAccount {
  id: number | null;
  username: string;
  role: 'viewer' | 'operator' | 'admin';
  is_active: boolean;
  active?: boolean;
  allowed_system: string | null;
  bound_client_id: string | null;
  bound_ip: string | null;
  bound_user_agent: string | null;
  bound_at: string | null;
  last_seen_at: string | null;
  expires_at?: string | null;
  telegram_chat_id?: string | null;
  telegram_username?: string | null;
  telegram_verified_at?: string | null;
  verification_code?: string | null;
  verification_expires_at?: string | null;
  created_at: string | null;
  updated_at?: string | null;
}

export interface UserInviteCreated extends UserAccount {
  token: string;
}

export interface TelegramBindingInfo {
  chat_id: number;
  username: string;
  tg_username?: string;
  first_name?: string;
  role: string;
  token?: string;
}

export interface SecurityEventEntry {
  timestamp: string;
  type: string;
  severity: 'info' | 'warning' | 'error' | 'critical';
  message: string;
  username?: string | null;
  client_id?: string | null;
  client_ip?: string | null;
  telegram_chat_id?: string | null;
}

export interface IntegrationsStatus {
  sessions?: UserAccount[];
  user_tokens?: Record<string, string>;
  security_events?: SecurityEventEntry[];
  access_registry?: Record<string, unknown>;
  database: {
    engine: 'postgresql' | 'sqlite';
    status: string;
    counts: {
      devices: number;
      jobs: number;
      users: number;
      bound_sessions: number;
    };
  };
  github_sot: GitStatusInfo;
  netbox: {
    configured: boolean;
    url: string | null;
    mode: string;
  };
  prometheus: {
    configured: boolean;
    reachable?: boolean;
    url: string | null;
    scrape_endpoint: string;
    collector_interval_sec: number;
    active_targets?: number;
    up_targets?: number;
    stored_samples?: number;
    jobs?: string[];
  };
  telegram: {
    configured: boolean;
    bot_token_set: boolean;
    bot_username?: string;
    chat_ids: number[];
    bindings?: TelegramBindingInfo[];
    commands: string[];
  };
}

export interface DeviceHardwareSpecs {
  os_version?: string;
  cpu_cores?: number | string;
  cpu_model?: string;
  ram_gb?: number | string;
  disk_gb?: number | string;
  uptime?: string;
  serial?: string;
  interfaces?: Array<{
    name: string;
    ip?: string;
    status?: string;
  }>;
  containers?: Array<{
    name: string;
    image: string;
    status: string;
    ports?: string;
  }>;
  lldp_neighbors?: Array<{
    local_port: string;
    neighbor: string;
    remote_port: string;
  }>;
}

export interface BootstrapMetricsConfig {
  metrics_interval_seconds: number;
  drift_scan_interval_seconds: number;
  commit_confirm_timeout_seconds: number;
  max_ping_loss_percent: number;
}

export interface BootstrapExposureInfo {
  mode: 'local_only' | 'public_tunnel';
  public_tunnel_enabled: boolean;
  local_url: string;
  public_url: string | null;
  public_host?: string | null;
  role_urls?: Record<string, string>;
  websocket_enabled: boolean;
  websocket_auth_scheme: string;
}

export interface BootstrapStatusResponse {
  initialized: boolean;
  bootstrap_completed: boolean;
  caller_is_bound_owner: boolean;
  has_populated_db?: boolean;
  can_transfer_owner?: boolean;
  cluster_name: string;
  exposure: BootstrapExposureInfo;
  owner: {
    exists: boolean;
    username: string;
    bound_client_id: string | null;
    bound_ip: string | null;
    recovery_key?: string | null;
    telegram_linked: boolean;
    telegram_username?: string | null;
    telegram_chat_id?: string | null;
  };
  github: GitStatusInfo & {
    repo_connected?: boolean;
    auto_commit_on_diff?: boolean;
    yaml_devices_count: number;
    webhook_endpoint: string;
    webhook_registered: boolean;
    sync_mode: string;
  };
  telegram: {
    bot_token_set: boolean;
    configured: boolean;
    bot_username: string;
    chat_ids: number[];
    deep_link_claim: string;
    deep_link_recover: string;
  };
  server: {
    network_driver: string;
    clab_host: string;
    clab_port: number;
    clab_user: string;
    devices_count: number;
    in_sync_count: number;
    devices: Array<{
      hostname: string;
      platform: string;
      role: string;
      ip: string;
      status: string;
    }>;
  };
  ai: {
    groq_configured: boolean;
    gemini_configured: boolean;
    ollama_url: string;
    cascade?: LlmCascadeConfig[];
  };
  metrics_config?: BootstrapMetricsConfig;
}

export interface LlmCascadeConfig {
  name: string;
  base_url: string;
  model: string;
  api_key?: string;
  timeout_seconds?: number;
}

export interface BootstrapConfigureRequest {
  cluster_name?: string;
  exposure_mode?: 'local_only' | 'public_tunnel';
  websocket_enabled?: boolean;
  github_repo_url?: string;
  github_branch?: string;
  auto_commit_on_diff?: boolean;
  github_pat?: string;
  github_webhook_secret?: string;
  webhook_public_url?: string;
  telegram_bot_token?: string;
  telegram_chat_id?: string;
  groq_api_key?: string;
  gemini_api_key?: string;
  ollama_url?: string;
  llm_cascade?: LlmCascadeConfig[];
  bootstrap_completed?: boolean;
  metrics_interval_seconds?: number;
  drift_scan_interval_seconds?: number;
  commit_confirm_timeout_seconds?: number;
  max_ping_loss_percent?: number;
}

export interface RecoverOwnerResponse {
  status: string;
  sent?: boolean;
  message?: string;
  username?: string;
  role?: UserRole;
  token?: string;
  bound_client_id?: string;
  bound_ip?: string;
}

export interface BootstrapConfigureResponse {
  status: string;
  exposure: {
    active: boolean;
    mode: 'local_only' | 'public_tunnel';
    public_url: string | null;
    local_url: string;
    public_host?: string | null;
    role_urls?: Record<string, string>;
  };
  websocket_enabled: boolean;
  webhook_status: string;
  bot_verified: boolean;
  bot_username: string;
  deep_link_claim: string;
  git: GitStatusInfo;
  runtime_keys: string[];
  metrics_config?: BootstrapMetricsConfig;
}

export interface DiscoveredSwitchItem {
  hostname: string;
  platform: string;
  role: string;
  endpoint: string;
  reachable: boolean;
  banner?: string;
}

export interface ServerDiscoveryResponse {
  status: 'ok' | 'error' | string;
  mode?: 'ssh' | 'offline';
  message?: string;
  server_reachable?: boolean;
  discovered_containers?: Array<{
    name: string;
    image: string;
    status: string;
    ports: string;
  }>;
  discovered_switches?: DiscoveredSwitchItem[];
  synced_total?: number;
  created?: number;
  updated?: number;
}

export interface ProtocolSupportInfo {
  supported: boolean;
  port?: number;
  verified_by?: string | null;
  tls?: boolean | null;
  cli_supported?: boolean;
  tcp_open?: boolean;
  path?: string;
  snmp_agent?: string[];
}

export interface CollectorDevicePlan {
  protocol: string;
  selected_mode?: string;
  port: number;
  host: string;
  platform: string;
  fabric_id: string;
  verified_by?: string | null;
  supported_protocols?: Record<string, ProtocolSupportInfo>;
}

export interface CollectorFallbackEvent {
  ts: string;
  hostname: string;
  from: string;
  to: string;
  reason: string;
}

export interface CollectorPlanResponse {
  refreshed_at: string | null;
  protocols: Record<string, number>;
  devices: Record<string, CollectorDevicePlan>;
  overrides?: Record<string, string>;
  events?: CollectorFallbackEvent[];
}

export interface StandRead {
  id: number;
  slug: string;
  name: string;
  site?: string | null;
  driver: string;
  jump_host?: string | null;
  jump_port: number;
  jump_user?: string | null;
  has_password: boolean;
  port_map?: Record<string, string> | null;
  topology_path?: string | null;
  queue_name: string;
  status: string;
  vitality_state?: 'UP' | 'DOWN' | string;
  down_reason?: string | null;
  ai_diagnosis?: string | null;
  using_backup_fallback?: boolean;
  last_backup_at?: string | null;
  backup_available?: boolean;
  description?: string | null;
  total_devices: number;
  in_sync_devices: number;
  drifted_devices: number;
  unreachable_devices: number;
  is_default?: boolean;
  is_active?: boolean;
}

export interface StandCreatePayload {
  slug: string;
  name: string;
  site?: string | null;
  driver?: string;
  jump_host?: string | null;
  jump_port?: number;
  jump_user?: string | null;
  jump_password?: string | null;
  port_map?: Record<string, any> | null;
  topology_path?: string | null;
  queue_name?: string;
  status?: string;
  description?: string | null;
}

export interface StandProbeResponse {
  reachable: boolean;
  jump_host?: string | null;
  jump_port: number;
  latency_ms?: number | null;
  discovered_nodes: { hostname: string; endpoint: string }[];
  message: string;
}


