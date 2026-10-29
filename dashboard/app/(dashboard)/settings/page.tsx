'use client';

import { useEffect, useState } from 'react';
import { CheckCircle, X } from 'lucide-react';
import clsx from 'clsx';
import { api } from '@/lib/api';
import type { ApplicantProfile, SettingsData } from '@/lib/types';
import { Button } from '@/components/ui/Button';
import { Card, CardDescription, CardTitle } from '@/components/ui/Card';
import { Input, Label, Textarea } from '@/components/ui/Input';

export default function SettingsPage() {
  const [cfg, setCfg] = useState<SettingsData | null>(null);
  const [saved, setSaved] = useState(false);
  const [excludedCompanies, setExcludedCompanies] = useState<string[]>([]);
  const [companyInput, setCompanyInput] = useState('');
  const [profileSaved, setProfileSaved] = useState(false);
  const [resumeName, setResumeName] = useState('');
  const [profile, setProfile] = useState<Partial<ApplicantProfile>>({});
  const [form, setForm] = useState({
    target_roles_str: '',
    experience_years: '2',
    preferred_locations_str: '',
    min_relevance_score: '60',
    user_full_name: '',
    gemini_api_key: '',
    linkedin_email: '',
    linkedin_password: '',
  });

  useEffect(() => {
    api.getSettings().then((d) => {
      setCfg(d);
      setExcludedCompanies(d.excluded_companies || []);
      setForm((prev) => ({
        ...prev,
        target_roles_str: (d.target_roles || []).join(', '),
        experience_years: String(d.experience_years || 2),
        preferred_locations_str: (d.preferred_locations || []).join(', '),
        min_relevance_score: String(d.min_relevance_score || 60),
        user_full_name: d.user_full_name || '',
      }));
    }).catch(() => {});
    api.getProfile().then((p) => {
      setProfile(p);
      if (p.has_resume) setResumeName('resume on file');
    }).catch(() => {});
  }, []);

  const addExcludedCompany = () => {
    const name = companyInput.trim();
    if (!name) return;
    setExcludedCompanies((prev) => {
      const lower = name.toLowerCase();
      if (prev.some((c) => c.toLowerCase() === lower)) return prev;
      return [...prev, name];
    });
    setCompanyInput('');
  };

  const removeExcludedCompany = (name: string) => {
    setExcludedCompanies((prev) => prev.filter((c) => c !== name));
  };

  const handleSave = async () => {
    await api.saveSettings({
      target_roles: form.target_roles_str.split(',').map((s) => s.trim()).filter(Boolean),
      experience_years: parseInt(form.experience_years) || 2,
      preferred_locations: form.preferred_locations_str.split(',').map((s) => s.trim()).filter(Boolean),
      min_relevance_score: parseInt(form.min_relevance_score) || 60,
      excluded_companies: excludedCompanies,
      user_full_name: form.user_full_name,
      ...(form.gemini_api_key && { gemini_api_key: form.gemini_api_key }),
      ...(form.linkedin_email && { linkedin_email: form.linkedin_email }),
      ...(form.linkedin_password && { linkedin_password: form.linkedin_password }),
    });
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

  const pf = (key: keyof ApplicantProfile) => ({
    value: String(profile[key] ?? ''),
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setProfile((p) => ({ ...p, [key]: e.target.value })),
  });

  const handleSaveProfile = async () => {
    const savedProfile = await api.saveProfile({
      first_name: profile.first_name,
      last_name: profile.last_name,
      full_name: `${profile.first_name || ''} ${profile.last_name || ''}`.trim() || profile.full_name,
      email: profile.email,
      phone: profile.phone,
      city: profile.city,
      state: profile.state,
      country: profile.country,
      postal_code: profile.postal_code,
      address_line1: profile.address_line1,
      address_line2: profile.address_line2,
      linkedin: profile.linkedin,
      github: profile.github,
      portfolio: profile.portfolio,
      current_title: profile.current_title,
      current_company: profile.current_company,
      years_experience: profile.years_experience,
      notice_period: profile.notice_period,
      earliest_start: profile.earliest_start,
      salary_expectation: profile.salary_expectation,
      authorized_to_work: profile.authorized_to_work,
      require_sponsorship: profile.require_sponsorship,
      how_heard: profile.how_heard,
      skills: profile.skills,
      cover_letter_template: profile.cover_letter_template,
      auto_submit: profile.auto_submit,
      education: profile.education,
    });
    setProfile(savedProfile);
    setProfileSaved(true);
    setTimeout(() => setProfileSaved(false), 2500);
  };

  const handleResume = async (file: File | undefined) => {
    if (!file) return;
    await api.uploadResume(file);
    setResumeName(file.name);
    const p = await api.getProfile();
    setProfile(p);
  };

  const handleClearJobs = async () => {
    const place = cfg?.target_region === 'europe' ? 'Europe' : 'India';
    if (!confirm(`Clear discovered ${place} jobs that are not applied? The other region is left as it is.`)) return;
    await api.clearDiscoveredJobs();
  };

  const handlePurgeExperienced = async () => {
    const place = cfg?.target_region === 'europe' ? 'Europe' : 'India';
    if (!confirm(`Remove ${place} jobs that fail the 0-2 year or role filters? The other region is left as it is.`)) return;
    await api.purgeExperiencedJobs();
  };

  const f = (key: keyof typeof form) => ({
    value: form[key],
    onChange: (e: React.ChangeEvent<HTMLInputElement>) =>
      setForm((p) => ({ ...p, [key]: e.target.value })),
  });

  const connections = cfg
    ? [
        { label: 'Gemini', ok: cfg.has_gemini_key },
        { label: 'LinkedIn', ok: cfg.has_linkedin },
      ]
    : [];

  return (
    <>
      <header className="page-x glass sticky top-0 z-20 py-4 sm:py-5">
        <h1 className="page-title text-4xl text-[var(--text-primary)] sm:text-5xl">Settings</h1>
        <p className="mt-2 text-sm text-[var(--text-muted)]">Applicant profile, preferences, and credentials</p>
      </header>

      <div className="page-x pb-nav flex-1 overflow-y-auto pt-5 sm:pt-6">
        <div className="mx-auto max-w-2xl space-y-6">
          <Card>
            <CardTitle>Applicant profile</CardTitle>
            <CardDescription>
              Used to fill Greenhouse, Lever, Workday, and custom apply forms. Name, email, and phone are required. Upload a PDF resume if you have one; otherwise a resume is generated from this profile.
            </CardDescription>
            {profile.missing && profile.missing.length > 0 && (
              <p className="mt-3 text-xs text-[var(--danger)]">
                Missing: {profile.missing.join(', ')}
              </p>
            )}
            <div className="mt-5 space-y-4">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <Label>First name</Label>
                  <Input {...pf('first_name')} />
                </div>
                <div>
                  <Label>Last name</Label>
                  <Input {...pf('last_name')} />
                </div>
              </div>
              <div>
                <Label>Email</Label>
                <Input type="email" {...pf('email')} />
              </div>
              <div>
                <Label>Phone</Label>
                <Input {...pf('phone')} placeholder="+91 99399 64663" />
              </div>
              <div>
                <Label>Street address</Label>
                <Input {...pf('address_line1')} placeholder="Required for most Workday forms" />
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <div>
                  <Label>City</Label>
                  <Input {...pf('city')} />
                </div>
                <div>
                  <Label>State</Label>
                  <Input {...pf('state')} />
                </div>
                <div>
                  <Label>PIN / ZIP</Label>
                  <Input {...pf('postal_code')} />
                </div>
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <Label>LinkedIn</Label>
                  <Input {...pf('linkedin')} />
                </div>
                <div>
                  <Label>GitHub</Label>
                  <Input {...pf('github')} />
                </div>
              </div>
              <div>
                <Label>Portfolio</Label>
                <Input {...pf('portfolio')} />
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <Label>Current title</Label>
                  <Input {...pf('current_title')} />
                </div>
                <div>
                  <Label>Current company</Label>
                  <Input {...pf('current_company')} />
                </div>
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <Label>Years of experience</Label>
                  <Input {...pf('years_experience')} />
                </div>
                <div>
                  <Label>Notice period</Label>
                  <Input {...pf('notice_period')} />
                </div>
              </div>
              <div>
                <Label>School</Label>
                <Input
                  value={profile.education?.[0]?.school || ''}
                  onChange={(e) =>
                    setProfile((p) => ({
                      ...p,
                      education: [{
                        school: e.target.value,
                        degree: p.education?.[0]?.degree || 'Bachelor of Technology',
                        major: p.education?.[0]?.major || 'Computer Science',
                        start_year: p.education?.[0]?.start_year || '',
                        end_year: p.education?.[0]?.end_year || '',
                      }],
                    }))
                  }
                />
              </div>
              <div>
                <Label>Skills</Label>
                <Input {...pf('skills')} />
              </div>
              <div>
                <Label>Cover letter template</Label>
                <Textarea rows={4} {...pf('cover_letter_template')} />
              </div>
              <div>
                <Label>Resume (PDF)</Label>
                <Input
                  type="file"
                  accept=".pdf,.doc,.docx"
                  onChange={(e) => handleResume(e.target.files?.[0])}
                />
                {resumeName && (
                  <p className="mt-1 text-xs text-[var(--text-muted)]">{resumeName}</p>
                )}
              </div>
              <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                <input
                  type="checkbox"
                  checked={!!profile.authorized_to_work}
                  onChange={(e) => setProfile((p) => ({ ...p, authorized_to_work: e.target.checked }))}
                />
                Authorized to work in India
              </label>
              <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                <input
                  type="checkbox"
                  checked={!!profile.require_sponsorship}
                  onChange={(e) => setProfile((p) => ({ ...p, require_sponsorship: e.target.checked }))}
                />
                Require visa sponsorship
              </label>
              <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                <input
                  type="checkbox"
                  checked={profile.auto_submit !== false}
                  onChange={(e) => setProfile((p) => ({ ...p, auto_submit: e.target.checked }))}
                />
                Submit automatically when all required fields are filled
              </label>
            </div>
            <div className="mt-6 flex justify-end">
              <Button onClick={handleSaveProfile} className={profileSaved ? 'bg-[var(--success)]' : ''}>
                {profileSaved ? 'Saved' : 'Save profile'}
              </Button>
            </div>
          </Card>

          <Card>
            <CardTitle>Preferences</CardTitle>
            <CardDescription>Roles and locations used during scans</CardDescription>
            <div className="mt-5 space-y-4">
              <div>
                <Label>Name</Label>
                <Input {...f('user_full_name')} />
              </div>
              <div>
                <Label>Target roles</Label>
                <Input {...f('target_roles_str')} placeholder="Software Engineer, Backend Engineer" />
              </div>
              <div>
                <Label>Locations</Label>
                <Input {...f('preferred_locations_str')} placeholder="Bengaluru, Remote" />
              </div>
              <div>
                <Label>Excluded companies</Label>
                <div className="mt-1.5 flex gap-2">
                  <Input
                    value={companyInput}
                    onChange={(e) => setCompanyInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        addExcludedCompany();
                      }
                    }}
                    placeholder="TCS, Accenture, Wipro…"
                  />
                  <Button type="button" variant="secondary" onClick={addExcludedCompany}>
                    Add
                  </Button>
                </div>
                {excludedCompanies.length > 0 ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {excludedCompanies.map((name) => (
                      <span
                        key={name}
                        className="inline-flex items-center gap-1 rounded-full bg-[var(--surface-2)] px-3 py-1 text-xs font-medium text-[var(--text-secondary)]"
                      >
                        {name}
                        <button
                          type="button"
                          onClick={() => removeExcludedCompany(name)}
                          className="rounded-full p-0.5 text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text-primary)]"
                          aria-label={`Remove ${name}`}
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="mt-2 text-xs text-[var(--text-muted)]">
                    No companies excluded. Add names to skip them during scans.
                  </p>
                )}
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <Label>Max experience (years)</Label>
                  <Input type="number" {...f('experience_years')} />
                </div>
                <div>
                  <Label>Min score</Label>
                  <Input type="number" {...f('min_relevance_score')} />
                </div>
              </div>
            </div>
          </Card>

          <Card>
            <CardTitle>Connections</CardTitle>
            <CardDescription>API keys and LinkedIn sign-in</CardDescription>
            {connections.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-2">
                {connections.map((c) => (
                  <span
                    key={c.label}
                    className={clsx(
                      'inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium',
                      c.ok
                        ? 'bg-[var(--success-bg)] text-[var(--success)]'
                        : 'bg-[var(--surface-2)] text-[var(--text-muted)]',
                    )}
                  >
                    {c.ok && <CheckCircle className="h-3 w-3" />}
                    {c.label}
                  </span>
                ))}
              </div>
            )}
            <div className="mt-5 space-y-4">
              <div>
                <Label>Gemini API key</Label>
                <Input type="password" {...f('gemini_api_key')} placeholder="Leave blank to keep existing" />
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <Label>LinkedIn email</Label>
                  <Input {...f('linkedin_email')} />
                </div>
                <div>
                  <Label>LinkedIn password</Label>
                  <Input type="password" {...f('linkedin_password')} placeholder="Optional" />
                </div>
              </div>
            </div>
            <div className="mt-6 flex justify-end">
              <Button onClick={handleSave} className={saved ? 'bg-[var(--success)]' : ''}>
                {saved ? 'Saved' : 'Save changes'}
              </Button>
            </div>
          </Card>

          <Card className="border-[var(--danger-border)]">
            <CardTitle>Danger zone</CardTitle>
            <CardDescription>Destructive actions on your job list</CardDescription>
            <div className="mt-5 flex flex-wrap gap-3">
              <Button variant="danger" size="sm" onClick={handleClearJobs}>
                Clear discovered jobs
              </Button>
              <Button variant="danger" size="sm" onClick={handlePurgeExperienced}>
                Purge over-experienced
              </Button>
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}
