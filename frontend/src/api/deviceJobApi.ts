/**
 * @file api/deviceJobApi.ts
 * @description Device, Job, Drift, and Intent operations client.
 */

import { NetOpsStandsClient } from './standsApi';
import type {
  Device,
  DeviceDetail,
  DeviceEmergencyAction,
  DeviceProbeRequest,
  DeviceProbeResult,
  DriftReportItem,
  JobDetail,
  JobDiff,
  JobLog,
  JobStatus,
  JobSummary,
} from './types';

export class NetOpsDeviceJobClient extends NetOpsStandsClient {
  /** Дочитывает все страницы /devices (limit/offset), ответ API остаётся массивом. */
  async getDevices(fabricId?: string): Promise<Device[]> {
    const limit = 500;
    const all: Device[] = [];
    for (let offset = 0; ; offset += limit) {
      const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      if (fabricId) qs.set('fabric_id', fabricId);
      const page = await this.request<Device[]>(`/devices?${qs.toString()}`);
      all.push(...page);
      if (page.length < limit) return all;
    }
  }

  async getDevice(id: number): Promise<DeviceDetail> {
    return this.request<DeviceDetail>(`/devices/${id}`);
  }

  async probeDevice(data: DeviceProbeRequest): Promise<DeviceProbeResult> {
    return this.request<DeviceProbeResult>('/devices/probe', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async createDevice(
    data: Partial<Device> & {
      auth_profile?: string;
      username?: string;
      password?: string;
      proxy_jump?: string | null;
      hardware_specs?: string | null;
    }
  ): Promise<Device> {
    return this.request<Device>('/devices', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async promoteDevice(id: number): Promise<Device> {
    return this.request<Device>(`/devices/${id}/promote`, {
      method: 'POST',
    });
  }

  async deviceEmergencyAction(
    id: number,
    data: DeviceEmergencyAction
  ): Promise<{ status: string; message: string }> {
    return this.request<{ status: string; message: string }>(`/devices/${id}/emergency-action`, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async restoreDeviceBackup(
    id: number
  ): Promise<{ status: string; hostname: string; restored: boolean; message: string }> {
    return this.request<{ status: string; hostname: string; restored: boolean; message: string }>(
      `/devices/${id}/restore-backup`,
      {
        method: 'POST',
      }
    );
  }

  async deleteDevice(id: number): Promise<void> {
    return this.request<void>(`/devices/${id}`, {
      method: 'DELETE',
    });
  }

  async syncInventory(): Promise<{ created: string[]; updated: string[]; unchanged: string[] }> {
    return this.request('/inventory/sync', { method: 'POST' });
  }

  async createDryRun(deviceIds: number[]): Promise<{ job_id: string; status: JobStatus }> {
    return this.request('/jobs/dry-run', {
      method: 'POST',
      body: JSON.stringify({ device_ids: deviceIds, intent_source: 'git_main' }),
    });
  }

  async createDeploy(
    jobId: string,
    confirmedBy: string = 'operator'
  ): Promise<{ job_id: string; status: JobStatus }> {
    return this.request('/jobs/deploy', {
      method: 'POST',
      body: JSON.stringify({ job_id: jobId, confirmed_by: confirmedBy }),
    });
  }

  async getJobs(limit = 20): Promise<JobSummary[]> {
    return this.request<JobSummary[]>(`/jobs?limit=${limit}`);
  }

  async getJob(id: string): Promise<JobDetail> {
    return this.request<JobDetail>(`/jobs/${id}`);
  }

  async getJobLogs(id: string, afterId = 0): Promise<JobLog[]> {
    return this.request<JobLog[]>(`/jobs/${id}/logs?after_id=${afterId}`);
  }

  async getJobDiff(id: string): Promise<JobDiff> {
    return this.request<JobDiff>(`/jobs/${id}/diff`);
  }

  async scanDrift(deviceIds?: number[]): Promise<{ job_id: string; status: JobStatus }> {
    const validIds = deviceIds && deviceIds.length > 0 ? deviceIds : null;
    return this.request('/drift/scan', {
      method: 'POST',
      body: JSON.stringify({ device_ids: validIds }),
    });
  }

  async getDriftReport(): Promise<DriftReportItem[]> {
    return this.request<DriftReportItem[]>('/drift/report');
  }

  async remediateDrift(deviceId: number): Promise<{ job_id: string; status: JobStatus }> {
    return this.request('/drift/remediate', {
      method: 'POST',
      body: JSON.stringify({ device_id: deviceId }),
    });
  }

  async lintIntent(): Promise<{
    issues: Array<{ source: string; field: string; message: string; severity?: string }>;
  }> {
    return this.request('/intent/lint');
  }

  async injectChaos(scenario: string): Promise<{ status: string; message: string }> {
    return this.request('/system/chaos', {
      method: 'POST',
      body: JSON.stringify({ scenario }),
    });
  }
}
