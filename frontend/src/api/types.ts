/**
 * @file api/types.ts
 * @description Core domain types, interfaces, and error helpers for CROC DREAM NetOps Platform.
 */

export type Platform =
  | 'cisco_iosxe'
  | 'arista_eos'
  | 'huawei_vrp'
  | 'juniper_junos'
  | 'eltex_mes'
  | 'eltex_esr'
  | 'yadro_kornfe'
  | 'linux_server';

export type DeviceRole = 'spine' | 'leaf' | 'border' | 'border_firewall' | 'server';

export type DeviceStatus = 'UNKNOWN' | 'IN_SYNC' | 'DRIFT_DETECTED' | 'UNREACHABLE' | 'IN_PROGRESS';

export type DriftStatus = 'IN_SYNC' | 'DRIFT_DETECTED' | 'UNREACHABLE';

export type JobType = 'DRY_RUN' | 'DEPLOY' | 'DRIFT_SCAN' | 'DRIFT_REMEDIATE';

export type JobStatus = 'PENDING' | 'RUNNING' | 'SUCCESS' | 'FAILED';

export type TargetStatus = 'PENDING' | 'SUCCESS' | 'FAILED' | 'SKIPPED' | 'ROLLED_BACK';

export type LogLevel = 'INFO' | 'WARNING' | 'ERROR';

export type UserRole = 'viewer' | 'operator' | 'admin' | 'owner';

export type OperStatus = 'UP' | 'DOWN' | 'DEGRADED';

export interface Device {
  id: number;
  hostname: string;
  management_ip: string;
  management_port: number;
  platform: Platform;
  role: DeviceRole;
  auth_profile: string;
  status: DeviceStatus;
  fabric_id?: string | null;
  site?: string | null;
  rack?: string | null;
  unit?: number | null;
  management_mode?: 'MONITORING_ONLY' | 'MANAGED';
  proxy_jump?: string | null;
  hardware_specs?: string | null;
  oper_status?: OperStatus;
  vitality_state?: 'UP' | 'DOWN';
  down_reason?: string | null;
  ai_diagnosis?: string | null;
  using_backup_fallback?: boolean;
  last_backup_at?: string | null;
  backup_available?: boolean;
  sparkline?: number[];
  state_timeline?: string[];
  last_checked_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface DeviceProbeRequest {
  management_ip: string;
  management_port?: number;
  username?: string;
  password?: string;
  proxy_jump?: string;
}

export interface DeviceProbeResult {
  reachable: boolean;
  detected_platform: Platform;
  detected_role: DeviceRole;
  hostname: string;
  os_version?: string;
  cpu_cores?: number;
  ram_gb?: number;
  disk_gb?: number;
  interfaces?: string[];
  lldp_neighbors?: Array<{ local_port: string; remote_chassis: string; remote_port: string }>;
  message: string;
}

export interface DeviceEmergencyAction {
  action: 'rollback_last_commit' | 'reset_bgp_sessions' | 'restart_services' | 'reboot';
  force?: boolean;
  reason?: string;
}

export interface RiskExplanation {
  risk_level: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  summary: string;
  key_points: string[];
  recommendations: string[];
  is_safe: boolean;
  provider: string;
}

export interface InterfaceIntent {
  name: string;
  description?: string;
  enabled: boolean;
  mode: 'l2' | 'l3';
  ipv4_address?: string;
  access_vlan?: number | null;
  ospf_area?: number | null;
  mtu: number;
}

export interface BgpNeighborIntent {
  peer_ip: string;
  remote_asn: number;
  description?: string;
  password?: string;
  announced_prefixes?: string[];
}

export interface BgpIntent {
  asn: number;
  router_id: string;
  neighbors: BgpNeighborIntent[];
  evpn?: boolean;
  vxlan_vni?: number | null;
}

export interface AclRule {
  sequence: number;
  action: 'permit' | 'deny';
  protocol: 'ip' | 'tcp' | 'udp' | 'icmp';
  source: string;
  destination: string;
}

export interface AclIntent {
  name: string;
  rules: AclRule[];
}

export interface DeviceIntent {
  hostname: string;
  interfaces: InterfaceIntent[];
  bgp?: BgpIntent;
  acls: AclIntent[];
}

export interface DeviceDetail extends Device {
  intent: DeviceIntent | null;
  intent_issues: Array<{ source: string; field: string; message: string }>;
}

export interface JobTarget {
  device_id: number | null;
  hostname: string;
  status: TargetStatus;
  error: string | null;
  has_changes: boolean;
}

export interface JobLog {
  id: number;
  created_at: string;
  level: LogLevel;
  step: string;
  hostname: string | null;
  message: string;
}

export interface JobSummary {
  id: string;
  type: JobType;
  status: JobStatus;
  progress: number;
  intent_source: string | null;
  parent_job_id: string | null;
  created_by: string;
  confirmed_by: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobDetail extends JobSummary {
  targets: JobTarget[];
  logs: JobLog[];
}

export interface DeviceDiff {
  device_id: number | null;
  hostname: string;
  status: TargetStatus;
  error: string | null;
  running_config: string | null;
  intended_config: string | null;
  remediation_patch: string | null;
  rollback_patch: string | null;
}

export interface IntentSource {
  hostname: string;
  exists: boolean;
  yaml: string;
  editable: boolean;
}

export interface IntentSaveResult {
  saved: boolean;
  hostname: string;
  issues: Array<{ source: string; field: string; message: string }>;
}

export interface JobDiff {
  job_id: string;
  job_type: JobType;
  devices: DeviceDiff[];
}

export interface DriftReportItem {
  device_id: number;
  hostname: string;
  status: DriftStatus;
  checked_at: string;
  job_id: string | null;
  unauthorized_lines: string[];
  missing_lines: string[];
  remediation_patch: string | null;
  error: string | null;
  safe_lines?: string[];
  risky_lines?: string[];
  risk_level?: 'LOW' | 'HIGH';
}

export interface CopilotAction {
  type: 'dry_run' | 'remediate' | 'open_device' | 'scan_drift' | 'open_diff';
  label: string;
  device_id?: number | null;
  device_ids?: number[];
  payload?: Record<string, unknown>;
}

export interface CopilotReplyWithAction {
  answer: string;
  provider: string;
  action?: CopilotAction | null;
}


export interface EmergencyAuditLog {
  timestamp: string;
  user: string;
  action: string;
  device: string;
  payload: string;
  reason: string;
  success: boolean;
  output_preview: string;
}

export interface AiGuardResponse {
  hostname: string;
  healthy: boolean;
  metric: string;
  observed_value: number;
  expected_range: [number, number];
  deviation_pct: number;
  problem: string | null;
  message: string;
  provider: string;
  injected_simulation: string | null;
}


export interface GitStatusInfo {
  repo_path: string;
  github_repo_url: string;
  is_git_repo: boolean;
  branch?: string;
  commit_sha?: string;
  commit_short?: string;
  last_commit_message?: string;
  last_commit_at?: string;
  remote_url?: string;
  working_tree_clean?: boolean;
  modified_files?: string[];
  webhook_configured: boolean;
  auto_sync_on_push: boolean;
}

export * from './domainTypes';

export function getErrorMessage(err: unknown, fallback = 'Unexpected error'): string {
  if (err instanceof Error && err.message) {
    return err.message;
  }
  if (typeof err === 'string' && err.trim()) {
    return err;
  }
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) {
      return msg;
    }
  }
  return fallback;
}

