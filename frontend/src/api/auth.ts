/**
 * @file api/auth.ts
 * @description Local hardware Owner detection, public internet tunnel URL resolution,
 * and Owner-issued personal code/token session bootstrap.
 */

import type { BootstrapExposureInfo, UserRole } from './types';

import { consoleConfig, portRoleMap } from './runtimeConfig';

const loopbackHosts = (): Set<string> => new Set(consoleConfig().loopback_hosts);

/**
 * Checks whether the current browser hostname is local hardware (loopback).
 */
export function isLocalHardwareHost(hostname?: string): boolean {
  if (typeof window === 'undefined' && !hostname) return false;
  const host = (hostname ?? window.location.hostname).trim().toLowerCase();
  return loopbackHosts().has(host);
}

/**
 * Checks whether a URL string points to a loopback address (`localhost` / `127.0.0.1`).
 */
export function isLoopbackUrl(url?: string | null): boolean {
  if (!url) return true;
  try {
    const parsed = new URL(url);
    return loopbackHosts().has(parsed.hostname.toLowerCase());
  } catch {
    return url.includes('127.0.0.1') || url.includes('localhost');
  }
}

/**
 * Returns the fixed role for dedicated local Docker ports (localhost:5174..5176),
 * or null when running on the main console or over the public internet tunnel.
 */
export function getPortRole(port?: string): UserRole | null {
  if (typeof window === 'undefined' && !port) return null;
  if (!isLocalHardwareHost()) return null;
  const currentPort = port ?? window.location.port;
  return portRoleMap()[currentPort] ?? null;
}

/**
 * Checks whether the current tab was opened via an explicit `?token=...` participant invite link.
 */
export function isInviteSessionActive(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return sessionStorage.getItem('netops_invite_session') === '1';
  } catch {
    return false;
  }
}

/**
 * True when the user is on local hardware (localhost/127.0.0.1) on the primary console (5173)
 * and has not explicitly opened a participant invite link (`?token=...`).
 */
export function isLocalOwnerConsole(): boolean {
  if (typeof window === 'undefined') return false;
  if (getPortRole()) return false;
  if (!isLocalHardwareHost()) return false;
  const params = new URLSearchParams(window.location.search);
  if (params.has('token')) return false;
  return !isInviteSessionActive();
}

/**
 * Resolves the effective UI role for the current browser session.
 * Local hardware on port 5173 always resolves to `'owner'`.
 */
export function resolveCurrentRole(): UserRole {
  if (typeof window === 'undefined') return 'viewer';
  const portRole = getPortRole();
  if (portRole) {
    return portRole;
  }
  if (isLocalOwnerConsole()) {
    localStorage.setItem('netops_user_role', 'owner');
    return 'owner';
  }
  return (localStorage.getItem('netops_user_role') as UserRole) || 'viewer';
}

/**
 * Resolves the public internet tunnel base URL for Owner-issued invite links.
 */
export function resolveShareBaseUrl(baseOverride?: string): string {
  if (baseOverride && baseOverride.trim()) {
    return baseOverride.trim().replace(/\/$/, '');
  }
  if (typeof window !== 'undefined') {
    const savedBase = localStorage.getItem('netops_share_base_url');
    if (savedBase && !isLoopbackUrl(savedBase)) {
      return savedBase.replace(/\/$/, '');
    }
    if (!isLocalHardwareHost()) {
      return window.location.origin.replace(/\/$/, '');
    }
    if (savedBase) {
      return savedBase.replace(/\/$/, '');
    }
    return window.location.origin.replace(/\/$/, '');
  }
  return '';
}

export interface PublicConsoleUrls {
  publicHost: string;
  publicGateUrl: string;
}

/**
 * Computes the public internet gate URL where participants enter with their Owner-issued code or link.
 */
export function getPublicConsoleUrls(exposure?: BootstrapExposureInfo | null): PublicConsoleUrls {
  const base = resolveShareBaseUrl(exposure?.public_url || undefined);
  let host = exposure?.public_host || 'localhost';
  try {
    if (base) {
      host = new URL(base).hostname;
    }
  } catch {
    // ignore
  }
  return {
    publicHost: host,
    publicGateUrl: base || (typeof window !== 'undefined' ? window.location.origin : ''),
  };
}

/**
 * Bootstraps the browser authentication session on initial load:
 * 1. Local hardware on `:5173` ALWAYS logs in as `'owner'` (`local-owner-console`).
 * 2. Participant invite links (`?token=<crk_token_or_6digit_code>`) exchange the Owner-issued code for an HttpOnly session cookie.
 * 3. External internet visitors without a token/session must enter their Owner-issued code in AccessCodeModal.
 */
export function bootstrapAuthSession(
  loginFn: (token: string) => Promise<{ username: string; role: UserRole; csrf_token: string }>
): Promise<void> {
  if (typeof window === 'undefined') return Promise.resolve();

  const savedShare = localStorage.getItem('netops_share_base_url');
  if (savedShare && isLoopbackUrl(savedShare)) {
    localStorage.removeItem('netops_share_base_url');
  }

  const portRole = getPortRole();
  if (portRole) {
    localStorage.setItem('netops_user_role', portRole);
    localStorage.setItem('netops_onboarding_done', 'true');
    return Promise.resolve();
  }

  const url = new URL(window.location.href);
  const urlToken = url.searchParams.get('token') || url.searchParams.get('code');
  const legacyToken = localStorage.getItem('netops_token');
  const savedQrToken = localStorage.getItem('netops_static_viewer_token');
  localStorage.removeItem('netops_token');

  if (urlToken) {
    const cleanUrlToken = urlToken.trim();
    url.searchParams.delete('token');
    url.searchParams.delete('code');
    window.history.replaceState(null, '', url.pathname + url.search + url.hash);
    localStorage.setItem('netops_onboarding_done', 'true');
    if (cleanUrlToken.startsWith('crk_viewer_')) {
      localStorage.setItem('netops_static_viewer_token', cleanUrlToken);
    }
    try {
      sessionStorage.setItem('netops_invite_session', '1');
    } catch {
      // ignore storage errors
    }
    return loginFn(cleanUrlToken)
      .then((res) => {
        localStorage.setItem('netops_user_role', res.role);
        localStorage.setItem('netops_onboarding_done', 'true');
        if (res.username === 'static_viewer') {
          localStorage.setItem('netops_static_viewer_token', cleanUrlToken);
        }
      })
      .catch(() => {
        localStorage.removeItem('netops_static_viewer_token');
      });
  }

  // Local hardware on main console (localhost:5173): always authenticate as Owner
  if (isLocalOwnerConsole()) {
    localStorage.setItem('netops_user_role', 'owner');
    const tokenToUse = legacyToken || consoleConfig().local_owner_login_token;
    return loginFn(tokenToUse)
      .then((res) => {
        localStorage.setItem('netops_user_role', res.role || 'owner');
      })
      .catch(() => {
        localStorage.setItem('netops_user_role', 'owner');
      });
  }

  const persistentToken = savedQrToken || legacyToken;
  if (persistentToken) {
    return loginFn(persistentToken)
      .then((res) => {
        localStorage.setItem('netops_user_role', res.role);
        localStorage.setItem('netops_onboarding_done', 'true');
      })
      .catch(() => {
        localStorage.removeItem('netops_static_viewer_token');
      });
  }

  return Promise.resolve();
}
