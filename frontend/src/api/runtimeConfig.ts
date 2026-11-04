/**
 * @file api/runtimeConfig.ts
 * @description Порты консолей и роли берутся с бэкенда (config/constants.yaml), а не зашиты в сборку.
 * Конфиг загружается один раз до первого рендера (см. main.tsx).
 */

import type { UserRole } from './types';

export interface ConsoleConfig {
  owner_port: number;
  role_ports: Record<string, number>;
  loopback_hosts: string[];
  local_owner_login_token: string;
}

let loaded: ConsoleConfig | null = null;

export async function loadConsoleConfig(): Promise<ConsoleConfig> {
  const res = await fetch('/api/v1/config/console', { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Не удалось загрузить конфигурацию консолей: HTTP ${res.status}`);
  loaded = (await res.json()) as ConsoleConfig;
  return loaded;
}

export function consoleConfig(): ConsoleConfig {
  if (!loaded) throw new Error('Конфигурация консолей ещё не загружена');
  return loaded;
}

/** Порт (строкой) → закреплённая роль, например '5174' → 'admin'. */
export function portRoleMap(): Record<string, UserRole> {
  return Object.fromEntries(
    Object.entries(consoleConfig().role_ports).map(([role, port]) => [String(port), role as UserRole]),
  );
}

/** Все порты консолей (owner + ролевые), строками. */
export function allConsolePorts(): string[] {
  const { owner_port, role_ports } = consoleConfig();
  return [owner_port, ...Object.values(role_ports)].map(String);
}

export function rolePortList(): string[] {
  return Object.values(consoleConfig().role_ports).map(String);
}
