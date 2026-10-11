/**
 * Autopilot API client.
 *
 * Deliberately separate from `lib/api.ts`: the autopilot section is self
 * contained, so nothing here can change behaviour the existing dashboard
 * depends on. The request wrapper mirrors the one in api.ts (same FastAPI
 * `detail` unwrapping) rather than importing it, to keep the two independent.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    let message = `API ${res.status}: ${path}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === 'string') message = body.detail;
    } catch {
      /* keep the fallback */
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

export type Tier = 'FAANG' | 'ELITE' | 'STRONG' | 'KNOWN' | 'UNKNOWN' | 'EXCLUDED';
export type RouteKind = 'auto' | 'login' | 'manual';

export interface Candidate {
  job_id: string;
  title: string;
  company: string;
  location: string;
  url: string;
  apply_url: string;
  tier: Tier;
  tier_value: number;
  route: RouteKind;
  route_reason: string;
  ats: string;
  score: number;
  reasons: string[];
  rejected: string;
}

export interface CandidateStats {
  pool: number;
  selected: number;
  eligible: number;
  by_tier: Record<string, number>;
  by_route: Record<string, number>;
}

export interface CandidateResponse {
  selected: Candidate[];
  passed_over: Candidate[];
  stats: CandidateStats;
}

export interface AutopilotStatus {
  running: boolean;
  stage: string;
  done: number;
  total: number;
  current: string;
  started_at: string | null;
  last_result: {
    tailored?: { job_id: string; ok: boolean; error?: string; coverage?: number }[];
    apply?: Record<string, unknown>;
    errors?: string[];
  } | null;
}

export interface QueueItem {
  job_id: string;
  title: string;
  company: string;
  location: string;
  url: string;
  ats: string;
  apply_status: string;
  reason: string;
  has_screenshot: boolean;
  has_tailored_resume: boolean;
}

export interface CallbackBucket {
  callback: number;
  rejected: number;
  no_reply: number;
  pending: number;
  sent: number;
  settled: number;
  rate: number | null;
  enough_data: boolean;
}

export interface CallbackStats {
  overall: CallbackBucket;
  by: Record<'variant' | 'llm_used' | 'first_glance', Record<string, CallbackBucket>>;
  rules: { callback: string[]; no_reply_after_days: number; min_sample: number };
}

export const autopilot = {
  callbacks: () => request<CallbackStats>('/api/autopilot/callbacks'),

  candidates: (limit = 30, minTier = 'UNKNOWN', autoOnly = false) =>
    request<CandidateResponse>(
      `/api/autopilot/candidates?limit=${limit}&min_tier=${minTier}&auto_only=${autoOnly}`,
    ),

  run: (jobIds: string[], tailor = true) =>
    request<{ message: string; running: boolean; jobs: number }>('/api/autopilot/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // auto_submit is never sent as true: the run always stops for review.
      body: JSON.stringify({ job_ids: jobIds, tailor, auto_submit: false }),
    }),

  status: () => request<AutopilotStatus>('/api/autopilot/status'),

  stop: () =>
    request<{ message: string; running: boolean }>('/api/autopilot/stop', { method: 'POST' }),

  queue: () => request<{ items: QueueItem[]; count: number }>('/api/autopilot/queue'),

  decide: (jobId: string, status: 'applied' | 'ignored', notes = '') =>
    request<{ success: boolean; status: string }>(`/api/autopilot/queue/${jobId}/decide`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, notes }),
    }),

  screenshotUrl: (jobId: string) => `${API_BASE}/api/autopilot/screenshot/${jobId}`,
  resumeUrl: (jobId: string) => `${API_BASE}/api/autopilot/resume/${jobId}`,
};

export const TIER_STYLE: Record<Tier, { label: string; color: string; bg: string }> = {
  FAANG: { label: 'FAANG', color: 'var(--tier-faang)', bg: 'var(--tier-faang-bg)' },
  ELITE: { label: 'Elite', color: 'var(--signal)', bg: 'var(--signal-soft)' },
  STRONG: { label: 'Strong', color: 'var(--success)', bg: 'var(--success-bg)' },
  KNOWN: { label: 'Known', color: 'var(--tier-known)', bg: 'var(--tier-known-bg)' },
  UNKNOWN: { label: 'Unknown', color: 'var(--tier-unknown)', bg: 'var(--tier-unknown-bg)' },
  EXCLUDED: { label: 'Excluded', color: 'var(--danger)', bg: 'var(--danger-bg)' },
};

export const ROUTE_STYLE: Record<RouteKind, { label: string; hint: string }> = {
  auto: { label: 'Auto', hint: 'public form, the bot can fill and you approve' },
  login: { label: 'Sign-in', hint: 'needs your account for this employer' },
  manual: { label: 'Manual', hint: 'apply by hand through the extension' },
};
