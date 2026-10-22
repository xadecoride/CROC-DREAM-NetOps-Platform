/**
 * @file api/http.ts
 * @description Base HTTP dispatcher with CSRF, X-Client-ID device fingerprint,
 * and automatic session recovery exclusively for the local hardware Owner console.
 */

import { getPortRole, isLocalOwnerConsole } from './auth';
import { consoleConfig } from './runtimeConfig';
import type { UserRole } from './types';

export const API_BASE = '/api/v1';

export class NetOpsHttpClient {
  /** Resolves once the initial invite/session bootstrap has finished. */
  protected ready: Promise<void> = Promise.resolve();
  private reloginInFlight: Promise<void> | null = null;

  /** Delays API calls until the initial cookie-session exchange completes. */
  setReady(ready: Promise<void>): void {
    this.ready = ready;
  }

  /**
   * Exchanges an Owner-issued access token or 6-digit code for an HttpOnly session cookie.
   */
  async login(token: string): Promise<{ username: string; role: UserRole; csrf_token: string }> {
    return this.request('/auth/login', { method: 'POST', body: JSON.stringify({ token: token.trim() }) }, false, false);
  }

  /** Destroys the server-side session and clears role state. */
  async logout(): Promise<void> {
    await this.request<void>('/auth/logout', { method: 'POST' }, true, false);
    localStorage.removeItem('netops_user_role');
    localStorage.removeItem('netops_static_viewer_token');
    try {
      sessionStorage.removeItem('netops_invite_session');
    } catch {
      // ignore
    }
  }

  /** Reads the (non-HttpOnly) CSRF cookie set alongside the session cookie. */
  protected getCsrfToken(): string {
    if (typeof document === 'undefined') return '';
    const match = document.cookie.match(/(?:^|;\s*)netops_csrf=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  /**
   * Returns a persistent unique client device fingerprint stored in localStorage.
   */
  getClientId(): string {
    if (typeof window === 'undefined') return 'ssr-client';
    let clientId = localStorage.getItem('netops_client_id');
    if (!clientId) {
      const rand =
        typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
          ? crypto.randomUUID().slice(0, 12)
          : Math.random().toString(36).slice(2, 10) + '-' + Date.now().toString(36);
      clientId = `dev-${rand}`;
      localStorage.setItem('netops_client_id', clientId);
    }
    return clientId;
  }

  /**
   * Generic HTTP request dispatcher.
   * Auto-recovers 401 on the local hardware Owner console (`localhost:5173`)
   * or when holding an eternal static QR viewer token across server restarts.
   */
  protected async request<T>(
    endpoint: string,
    options: RequestInit = {},
    waitReady = true,
    allowAutoRelogin = true
  ): Promise<T> {
    if (waitReady) await this.ready;

    const doFetch = async (): Promise<Response> => {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'X-Client-ID': this.getClientId(),
        'X-CSRF-Token': this.getCsrfToken(),
        ...((options.headers as Record<string, string>) || {}),
      };
      return fetch(`${API_BASE}${endpoint}`, {
        ...options,
        headers,
        credentials: 'same-origin',
      });
    };

    let res = await doFetch();

    // Auto-relogin on local hardware Owner console or eternal QR viewer if Redis/server restarted
    const savedQrToken = typeof window !== 'undefined' ? localStorage.getItem('netops_static_viewer_token') : null;
    if (res.status === 401 && allowAutoRelogin && !getPortRole() && (isLocalOwnerConsole() || savedQrToken)) {
      if (!this.reloginInFlight) {
        const reloginTok = isLocalOwnerConsole()
          ? consoleConfig().local_owner_login_token
          : savedQrToken!;
        this.reloginInFlight = this.login(reloginTok)
          .then((authRes) => {
            localStorage.setItem('netops_user_role', authRes.role || (isLocalOwnerConsole() ? 'owner' : 'viewer'));
          })
          .catch(() => {
            if (!isLocalOwnerConsole()) {
              localStorage.removeItem('netops_static_viewer_token');
            }
          })
          .finally(() => {
            this.reloginInFlight = null;
          });
      }
      await this.reloginInFlight;
      res = await doFetch();
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      let message = res.statusText || `HTTP ${res.status}`;
      if (typeof err.detail === 'string') {
        message = err.detail;
      } else if (Array.isArray(err.detail)) {
        message = err.detail
          .map((d: { loc?: string[]; msg?: string }) => `${d.loc ? d.loc.slice(1).join('.') + ': ' : ''}${d.msg || d}`)
          .join('; ');
      } else if (err.detail) {
        message = JSON.stringify(err.detail);
      }
      throw new Error(message);
    }

    if (res.status === 204) {
      return undefined as T;
    }

    return res.json();
  }
}
