import type { Analytics, ApplicantProfile, ApplyStatus, Job, ScanStatus, SettingsData } from './types';
import { DEFAULT_SCAN_PLATFORMS } from './platforms';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    let message = `API ${res.status}: ${path}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === 'string') message = body.detail;
    } catch {
      // keep fallback
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

export const api = {
  getJobs: () => request<Job[]>('/api/jobs'),

  getScanStatus: () => request<ScanStatus>('/api/scan/status'),

  startScan: (platforms = DEFAULT_SCAN_PLATFORMS, maxJobs = 400) =>
    request<{ message: string }>('/api/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        platforms,
        max_jobs_per_platform: maxJobs,
        headless: true,
      }),
    }),

  // `force` abandons a scan that has stopped responding. The stop flag is
  // cooperative, so a scraper blocked on a request that never returns will
  // never see it — without this the status stays stuck and every later scan
  // is refused.
  stopScan: (force = false) =>
    request<{ message: string; running: boolean; forced?: boolean }>(
      `/api/scan/stop${force ? '?force=true' : ''}`,
      { method: 'POST' }
    ),

  updateJobStatus: (id: string, status: string) =>
    request<{ success: boolean }>(`/api/jobs/${id}/status`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    }),

  markJobVisited: (id: string) =>
    request<{ success: boolean; visited_at: string }>(`/api/jobs/${id}/visit`, {
      method: 'POST',
    }),

  purgeVisitedJobs: () =>
    request<{ removed_count: number }>('/api/jobs/purge-visited', { method: 'POST' }),

  // `includeApplied` also removes rows already applied to. They stay in the
  // seen ledger, so clearing them does not offer the job back at the next scan.
  clearDiscoveredJobs: (includeApplied = false) =>
    request<{ deleted_count: number; applied_removed: number }>(
      `/api/jobs/clear${includeApplied ? '?include_applied=true' : ''}`,
      { method: 'DELETE' }
    ),

  purgeExperiencedJobs: () =>
    request<{ purged_count: number }>('/api/jobs/purge-experienced', { method: 'POST' }),

  getAnalytics: () => request<Analytics>('/api/analytics'),

  getSettings: () => request<SettingsData>('/api/settings'),

  saveSettings: (payload: Record<string, unknown>) =>
    request<{ success: boolean }>('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  getProfile: () => request<ApplicantProfile>('/api/profile'),

  saveProfile: (payload: Record<string, unknown>) =>
    request<ApplicantProfile>('/api/profile', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  uploadResume: async (file: File) => {
    const body = new FormData();
    body.append('file', file);
    const res = await fetch(`${API_BASE}/api/profile/resume`, { method: 'POST', body });
    if (!res.ok) throw new Error(`API ${res.status}: /api/profile/resume`);
    return res.json() as Promise<{ success: boolean; resume_path: string }>;
  },

  getApplyStatus: () => request<ApplyStatus>('/api/apply/status'),

  startApply: (jobIds?: string[]) =>
    request<{ message: string; running: boolean }>('/api/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        job_ids: jobIds || null,
        auto_submit: false, // ignored server-side: Quill never submits
        headless: false,
      }),
    }),

  stopApply: () =>
    request<{ message: string; running: boolean }>('/api/apply/stop', { method: 'POST' }),

  applyJob: (id: string) =>
    request<{ message: string; running: boolean }>(`/api/jobs/${id}/apply`, { method: 'POST' }),
};
