/**
 * @file api/standsApi.ts
 * @description Multi-stand / multi-fabric CRUD & Topology Graph client mixin for CROC DREAM NetOps Platform.
 */

import { NetOpsHttpClient } from './http';
import type { StandCreatePayload, StandProbeResponse, StandRead } from './types';

export interface TopologyGraphLink {
  id: string;
  source_id: number;
  target_id: number;
  source_hostname: string;
  target_hostname: string;
  source_port: string;
  target_port: string;
  source_ip?: string | null;
  target_ip?: string | null;
  subnet?: string | null;
  layer: 'L2_LLDP' | 'L3_SUBNET' | 'BGP_EVPN' | 'INTER_STAND' | string;
  layers: string[];
  protocol: string;
  bgp_asn?: string | null;
  vxlan_vni?: number | null;
  mtu?: number;
  acl_in?: string | null;
  is_inter_stand: boolean;
  source_stand: string;
  target_stand: string;
  load_pct: number;
  admin_state: 'UP' | 'SHUTDOWN';
  oper_status: 'UP' | 'DOWN' | 'DEGRADED' | 'SHUTDOWN';
  status_reason?: string | null;
  is_custom?: boolean;
}

export interface TopologyGraphNode {
  id: number;
  hostname: string;
  platform: string;
  role: string;
  status: string;
  oper_status: string;
  management_ip: string;
  management_port: number;
  fabric_id: string;
  site: string;
  rack?: string | null;
  unit?: number | null;
  load_pct: number;
  telemetry_protocol?: string;
  telemetry_mode?: string;
  telemetry_port?: number;
  bgp?: {
    asn: number;
    router_id: string;
    evpn: boolean;
    vxlan_vni?: number | null;
    neighbors_count: number;
  } | null;
  interfaces: Array<{
    name: string;
    ipv4_address?: string | null;
    mode: string;
    mtu?: number;
    enabled: boolean;
    ospf_area?: string | null;
    acl_in?: string | null;
    description?: string | null;
  }>;
  acls: string[];
  drained?: boolean;
  drain_reason?: string | null;
  total_links?: number;
  shutdown_links?: number;
}

export interface TopologyCheckpoint {
  id: string;
  name: string;
  created_at: string;
  links_count: number;
  drained_count: number;
  configs_count: number;
}

export interface TopologyGraphResponse {
  nodes: TopologyGraphNode[];
  links: TopologyGraphLink[];
  stands: Array<{
    slug: string;
    name: string;
    site: string;
    jump_host?: string | null;
    jump_port?: number | null;
    driver: string;
    status: string;
    device_count: number;
  }>;
}

export interface TopologyTraceHop {
  step: number;
  device_id: number;
  hostname: string;
  role: string;
  platform: string;
  fabric_id: string;
  ingress_port: string;
  egress_port: string;
  subnet?: string | null;
  pipeline_stages: string[];
}

export interface TopologyTraceResponse {
  reachable: boolean;
  source?: string;
  target?: string;
  ecmp_paths_count: number;
  ecmp_paths?: string[];
  disabled_links: string[];
  hops: TopologyTraceHop[];
  links_used: string[];
  message: string;
}

export interface StandDeviceTelemetrySeries {
  id?: number;
  hostname: string;
  role: string;
  platform: string;
  fabric_id: string;
  status?: string;
  oper_status?: string;
  cpu_pct: number[];
  memory_pct: number[];
  uplink_util_pct: number[];
  series?: {
    cpu_pct?: number[];
    memory_pct?: number[];
    uplink_util_pct?: number[];
  };
}

export interface StandTelemetrySeriesResponse {
  fabric_id?: string | null;
  points?: number;
  step_seconds?: number;
  timestamps?: string[];
  devices: StandDeviceTelemetrySeries[] | Record<string, StandDeviceTelemetrySeries>;
}

export class NetOpsStandsClient extends NetOpsHttpClient {
  async getStandTelemetrySeries(
    fabricId?: string,
    points = 24
  ): Promise<StandTelemetrySeriesResponse> {
    const qs = `?points=${points}${
      fabricId && fabricId !== 'all' ? `&fabric_id=${encodeURIComponent(fabricId)}` : ''
    }`;
    return this.request<StandTelemetrySeriesResponse>(`/telemetry/stand-series${qs}`);
  }

  async getDeviceTelemetrySeries(
    hostname: string,
    metric: 'cpu_pct' | 'memory_pct' | 'uplink_util_pct' = 'cpu_pct',
    points = 24,
    role = 'leaf'
  ): Promise<{ hostname: string; metric: string; points: number[]; unit: string; threshold: number }> {
    return this.request(
      `/telemetry/series/${encodeURIComponent(hostname)}?metric=${metric}&points=${points}&role=${encodeURIComponent(role)}`
    );
  }

  async getStands(): Promise<StandRead[]> {
    return this.request<StandRead[]>('/stands');
  }

  async createStand(payload: StandCreatePayload): Promise<StandRead> {
    return this.request<StandRead>('/stands', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateStand(slug: string, payload: Partial<StandCreatePayload>): Promise<StandRead> {
    return this.request<StandRead>(`/stands/${encodeURIComponent(slug)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  }

  async deleteStand(slug: string): Promise<void> {
    return this.request<void>(`/stands/${encodeURIComponent(slug)}`, {
      method: 'DELETE',
    });
  }

  async probeStand(payload: {
    jump_host?: string | null;
    jump_port?: number;
    port_map?: Record<string, string> | null;
  }): Promise<StandProbeResponse> {
    return this.request<StandProbeResponse>('/stands/probe', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async discoverStand(slug: string, seedDemo = false): Promise<StandProbeResponse> {
    const qs = seedDemo ? '?seed_demo=true' : '';
    return this.request<StandProbeResponse>(`/stands/${encodeURIComponent(slug)}/discover${qs}`, {
      method: 'POST',
    });
  }

  async syncAllStands(): Promise<StandRead[]> {
    return this.request<StandRead[]>('/stands/sync-all', {
      method: 'POST',
    });
  }

  async backupStand(slug: string): Promise<{
    status: string;
    slug: string;
    count: number;
    last_backup_at: string | null;
    message: string;
  }> {
    return this.request(`/stands/${encodeURIComponent(slug)}/backup`, {
      method: 'POST',
    });
  }


  async getTopologyGraph(fabricId?: string): Promise<TopologyGraphResponse> {
    const qs = fabricId && fabricId !== 'all' ? `?fabric_id=${encodeURIComponent(fabricId)}` : '';
    return this.request<TopologyGraphResponse>(`/topology/graph${qs}`);
  }

  async traceTopologyPath(sourceId: number, targetId: number): Promise<TopologyTraceResponse> {
    return this.request<TopologyTraceResponse>(
      `/topology/trace?source_id=${sourceId}&target_id=${targetId}`
    );
  }

  async createTopologyLink(payload: {
    source_id: number;
    target_id: number;
    source_port?: string;
    target_port?: string;
    layer?: string;
    subnet?: string;
    protocol?: string;
    bgp_asn?: string;
  }): Promise<TopologyGraphLink> {
    return this.request<TopologyGraphLink>('/topology/links', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async toggleTopologyLink(
    linkId: string,
    adminState?: 'UP' | 'SHUTDOWN',
    reason?: string,
    options?: { force?: boolean; strictSpofGuard?: boolean; autoRevertSeconds?: number }
  ): Promise<{
    link_id: string;
    admin_state: 'UP' | 'SHUTDOWN';
    reason: string;
    auto_revert_at?: string | null;
    spof_warning?: boolean;
    isolated_nodes?: string[];
  }> {
    return this.request(`/topology/links/${encodeURIComponent(linkId)}/toggle`, {
      method: 'PATCH',
      body: JSON.stringify({
        admin_state: adminState,
        reason,
        force: options?.force,
        strict_spof_guard: options?.strictSpofGuard,
        auto_revert_seconds: options?.autoRevertSeconds,
      }),
    });
  }

  async deleteTopologyLink(linkId: string): Promise<{ deleted: boolean; link_id: string }> {
    return this.request(`/topology/links/${encodeURIComponent(linkId)}`, {
      method: 'DELETE',
    });
  }

  async toggleNodeDrain(
    deviceId: number,
    drained?: boolean,
    reason?: string
  ): Promise<{ device_id: number; drained: boolean; reason: string }> {
    return this.request(`/topology/nodes/${deviceId}/drain`, {
      method: 'POST',
      body: JSON.stringify({ drained, reason }),
    });
  }

  async getTopologyCheckpoints(): Promise<{ checkpoints: TopologyCheckpoint[] }> {
    return this.request<{ checkpoints: TopologyCheckpoint[] }>('/topology/checkpoints');
  }

  async createTopologyCheckpoint(name: string): Promise<TopologyCheckpoint> {
    return this.request<TopologyCheckpoint>('/topology/checkpoints', {
      method: 'POST',
      body: JSON.stringify({ name }),
    });
  }

  async restoreTopologyCheckpoint(
    id: string
  ): Promise<{ restored: boolean; checkpoint: TopologyCheckpoint }> {
    return this.request<{ restored: boolean; checkpoint: TopologyCheckpoint }>(
      `/topology/checkpoints/${encodeURIComponent(id)}/restore`,
      { method: 'POST' }
    );
  }

  async deleteTopologyCheckpoint(
    id: string
  ): Promise<{ deleted: boolean; checkpoint_id: string }> {
    return this.request<{ deleted: boolean; checkpoint_id: string }>(
      `/topology/checkpoints/${encodeURIComponent(id)}`,
      { method: 'DELETE' }
    );
  }
}
