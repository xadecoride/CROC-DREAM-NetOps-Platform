import React from 'react';
import {
  Activity,
  LayoutDashboard,
  Layers,
  Network,
  ShieldCheck,
  ShieldAlert,
  Terminal,
} from 'lucide-react';

import type { UserRole } from './api';

export interface TabItem {
  groupKey: 'overview' | 'inventory' | 'automation' | 'testing';
  id: string;
  labelKey:
    | 'tabDashboard'
    | 'tabTopology'
    | 'tabDevices'
    | 'tabJobs'
    | 'tabDiff'
    | 'tabDrift'
    | 'tabLab'
    | 'tabEmergency';
  Icon: React.ComponentType<{ className?: string }>;
  minRole?: UserRole;
}

const ROLE_RANK: Record<UserRole, number> = {
  viewer: 1,
  operator: 2,
  admin: 3,
  owner: 4,
};

export const TAB_DEFINITIONS: TabItem[] = [
  { groupKey: 'overview', id: 'dashboard', labelKey: 'tabDashboard', Icon: LayoutDashboard, minRole: 'viewer' },
  { groupKey: 'overview', id: 'topology', labelKey: 'tabTopology', Icon: Network, minRole: 'viewer' },
  { groupKey: 'inventory', id: 'devices', labelKey: 'tabDevices', Icon: Layers, minRole: 'viewer' },
  { groupKey: 'automation', id: 'jobs', labelKey: 'tabJobs', Icon: Activity, minRole: 'viewer' },
  { groupKey: 'automation', id: 'diff', labelKey: 'tabDiff', Icon: Terminal, minRole: 'viewer' },
  { groupKey: 'automation', id: 'drift', labelKey: 'tabDrift', Icon: ShieldCheck, minRole: 'viewer' },
  { groupKey: 'testing', id: 'emergency', labelKey: 'tabEmergency', Icon: ShieldAlert, minRole: 'admin' },
];

export function getVisibleTabs(role: UserRole = 'viewer'): TabItem[] {
  const rank = ROLE_RANK[role] ?? 1;
  return TAB_DEFINITIONS.filter((item) => rank >= (ROLE_RANK[item.minRole ?? 'viewer'] ?? 1));
}

export const PLATFORM_NAMES: Record<string, string> = {
  arista_eos: 'Arista EOS',
  cisco_iosxe: 'Cisco IOS-XE',
  huawei_vrp: 'Huawei VRP',
  juniper_junos: 'Juniper Junos',
  eltex_mes: 'Eltex MES',
  eltex_esr: 'Eltex ESR',
  yadro_kornfe: 'YADRO Kornfe',
  linux_server: 'Linux Server',
};

export const ROLE_NAMES: Record<string, string> = {
  spine: 'Spine',
  leaf: 'Leaf',
  border: 'Border',
  border_firewall: 'Border FW',
  server: 'Server / Compute',
};

export const PLATFORM_CLI_PRESETS: Record<string, string[]> = {
  arista_eos: ['show ip bgp summary', 'show ip route', 'show interfaces status', 'show running-config'],
  cisco_iosxe: ['show ip bgp summary', 'show ip interface brief', 'show ip route', 'show version'],
  huawei_vrp: ['display bgp peer', 'display ip interface brief', 'display ip routing-table', 'display current-configuration'],
  juniper_junos: ['show bgp summary', 'show interfaces terse', 'show route', 'show configuration'],
  eltex_mes: ['show ip bgp summary', 'show ip interface brief', 'show interfaces status', 'show running-config'],
  eltex_esr: ['show ip bgp summary', 'show ip interfaces', 'show ip route', 'show running-config'],
  yadro_kornfe: ['show ip bgp summary', 'show interface status', 'show ip route', 'show runningconfiguration all'],
  linux_server: ['uptime', 'free -m', 'ip -br a', 'ss -tunlp'],
};

