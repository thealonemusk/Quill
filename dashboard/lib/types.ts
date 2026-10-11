export type JobStatus =
  | 'discovered'
  | 'scored'
  | 'tailored'
  | 'applied'
  | 'interviewing'
  | 'rejected'
  | 'offer'
  | 'ignored';

export interface Job {
  id: string;
  title: string;
  company: string;
  platform: string;
  url: string;
  location: string;
  experience_required: string;
  salary_range: string;
  relevance_score: number;
  fit_reason: string;
  missing_skills: string;
  seniority_level: string;
  is_engineering_role: boolean;
  easy_apply: boolean;
  recruiter_name: string;
  recruiter_profile: string;
  status: JobStatus;
  discovered_at: string | null;
  visited_at: string | null;
  applied_at: string | null;
  ats_type?: string;
  apply_status?: string;
  apply_error?: string;
  tier?: string;
  tier_value?: number;
}

export interface Analytics {
  total_jobs: number;
  by_status: Record<string, number>;
  by_platform: Record<string, number>;
  top_companies: { company: string; score: number }[];
}

export interface SettingsData {
  target_roles: string[];
  experience_years: number;
  preferred_locations: string[];
  min_relevance_score: number;
  user_full_name: string;
  has_gemini_key: boolean;
  has_linkedin: boolean;
  has_naukri: boolean;
  has_indeed: boolean;
  has_instahyre: boolean;
  supported_platforms: string[];
  excluded_companies: string[];
}

export interface ApplyStatus {
  running: boolean;
  last_result: Record<string, unknown> | null;
  current_job: string | null;
}

export interface ApplicantProfile {
  first_name: string;
  last_name: string;
  full_name: string;
  email: string;
  phone: string;
  city: string;
  state: string;
  country: string;
  postal_code: string;
  address_line1: string;
  address_line2: string;
  linkedin: string;
  github: string;
  portfolio: string;
  current_title: string;
  current_company: string;
  years_experience: string;
  notice_period: string;
  earliest_start: string;
  salary_expectation: string;
  authorized_to_work: boolean;
  require_sponsorship: boolean;
  how_heard: string;
  skills: string;
  cover_letter_template: string;
  auto_submit: boolean;
  education: { school: string; degree: string; major: string; start_year: string; end_year: string }[];
  missing: string[];
  has_resume: boolean;
  ready_to_apply: boolean;
}

export interface ScanStatus {
  running: boolean;
  last_result: Record<string, unknown> | null;
  /** Seconds since this scan started, 0 when idle. */
  elapsed_seconds?: number;
  /** Running far longer than any real scan takes — it is wedged, not slow. */
  stalled?: boolean;
}
