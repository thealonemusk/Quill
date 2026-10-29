'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import clsx from 'clsx';
import {
  AlertTriangle, CheckCircle2, ExternalLink, FileText, Image as ImageIcon,
  Loader2, RefreshCw, Sparkles, XCircle,
} from 'lucide-react';

import { Button } from '@/components/ui/Button';
import { Card, CardDescription, CardTitle } from '@/components/ui/Card';
import {
  autopilot, ROUTE_STYLE, TIER_STYLE,
  type AutopilotStatus, type CallbackBucket, type CallbackStats, type Candidate,
  type CandidateStats, type QueueItem, type Tier,
} from '@/lib/autopilot';
import { safeHref } from '@/lib/safe-url';

const TIER_ORDER: Tier[] = ['UNKNOWN', 'KNOWN', 'STRONG', 'ELITE', 'FAANG'];

export default function AutopilotPage() {
  const [tab, setTab] = useState<'select' | 'review'>('select');

  const [selected, setSelected] = useState<Candidate[]>([]);
  const [passedOver, setPassedOver] = useState<Candidate[]>([]);
  const [stats, setStats] = useState<CandidateStats | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [limit, setLimit] = useState(30);
  const [minTier, setMinTier] = useState<Tier>('UNKNOWN');
  const [autoOnly, setAutoOnly] = useState(false);
  const [showPassed, setShowPassed] = useState(false);

  const [status, setStatus] = useState<AutopilotStatus | null>(null);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [callbacks, setCallbacks] = useState<CallbackStats | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await autopilot.candidates(limit, minTier, autoOnly);
      setSelected(data.selected);
      setPassedOver(data.passed_over);
      setStats(data.stats);
      // Default to everything the bot can actually act on.
      setChecked(new Set(data.selected.filter((c) => c.route !== 'manual').map((c) => c.job_id)));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load candidates');
    } finally {
      setLoading(false);
    }
  }, [limit, minTier, autoOnly]);

  const loadQueue = useCallback(async () => {
    try {
      const data = await autopilot.queue();
      setQueue(data.items);
    } catch {
      /* the queue is secondary; a failure here must not blank the page */
    }
    autopilot.callbacks().then(setCallbacks).catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    loadQueue();
    autopilot.status().then(setStatus).catch(() => {});
  }, [loadQueue]);

  // Poll only while a run is in flight, matching the dashboard's scan pattern.
  useEffect(() => {
    if (!status?.running) return;
    const id = setInterval(async () => {
      try {
        const next = await autopilot.status();
        setStatus(next);
        if (!next.running) {
          await loadQueue();
          await load();
        }
      } catch {
        /* keep polling */
      }
    }, 2500);
    return () => clearInterval(id);
  }, [status?.running, load, loadQueue]);

  const toggle = (jobId: string) =>
    setChecked((prev) => {
      const next = new Set(prev);
      next.has(jobId) ? next.delete(jobId) : next.add(jobId);
      return next;
    });

  const runnable = useMemo(
    () => selected.filter((c) => checked.has(c.job_id) && c.route !== 'manual'),
    [selected, checked],
  );

  const start = async () => {
    if (!runnable.length) return;
    const ok = window.confirm(
      `Tailor a resume and fill the form for ${runnable.length} job(s)?\n\n` +
        'Nothing is submitted — each application stops for your review.',
    );
    if (!ok) return;
    try {
      await autopilot.run(runnable.map((c) => c.job_id));
      setStatus(await autopilot.status());
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Could not start');
    }
  };

  const decide = async (jobId: string, outcome: 'applied' | 'ignored') => {
    try {
      await autopilot.decide(jobId, outcome);
      setQueue((prev) => prev.filter((q) => q.job_id !== jobId));
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Could not record that');
    }
  };

  return (
    <div className="page-x mx-auto max-w-6xl space-y-5 pb-16 pt-5 sm:pt-6">
      {status?.running && (
        <div className="flex items-center gap-3 rounded-xl border border-[var(--accent)] bg-[var(--surface)] px-5 py-3.5">
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-[var(--signal-ink)]" />
          <div className="flex-1 text-sm">
            <span className="font-medium text-[var(--text-primary)] capitalize">{status.stage}</span>
            {status.total > 0 && (
              <span className="text-[var(--text-muted)]">
                {' '}· {status.done}/{status.total}
              </span>
            )}
            {status.current && (
              <span className="text-[var(--text-muted)]"> · {status.current}</span>
            )}
          </div>
          <Button variant="secondary" size="sm" onClick={() => autopilot.stop().catch(() => {})}>
            Stop
          </Button>
        </div>
      )}

      <div className="flex items-center gap-2">
        {(['select', 'review'] as const).map((key) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={clsx(
              'rounded-full px-4 py-1.5 text-sm font-semibold transition-colors',
              tab === key
                ? 'bg-[var(--ink)] text-[var(--on-ink)]'
                : 'bg-[var(--surface)] text-[var(--text-secondary)] shadow-[var(--shadow-rest)] ring-1 ring-[var(--border)] hover:ring-[var(--text-primary)]',
            )}
          >
            {key === 'select' ? 'Select' : `Review${queue.length ? ` (${queue.length})` : ''}`}
          </button>
        ))}
      </div>

      {tab === 'select' && (
        <>
          <Card>
            <CardTitle>Criteria</CardTitle>
            <CardDescription>
              Ranked on company tier, role fit, location, and whether the application can
              actually be reached.
            </CardDescription>
            <div className="mt-5 flex flex-wrap items-end gap-4">
              <label className="text-sm">
                <span className="mb-1.5 block font-medium text-[var(--text-secondary)]">
                  How many
                </span>
                <input
                  type="number"
                  min={1}
                  max={200}
                  value={limit}
                  onChange={(e) => setLimit(Math.max(1, Number(e.target.value) || 1))}
                  className="w-24 rounded-xl border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm outline-none focus:border-[var(--accent)]"
                />
              </label>
              <label className="text-sm">
                <span className="mb-1.5 block font-medium text-[var(--text-secondary)]">
                  Minimum tier
                </span>
                <select
                  value={minTier}
                  onChange={(e) => {
                    const next = TIER_ORDER.find((t) => t === e.target.value);
                    if (next) setMinTier(next);
                  }}
                  className="rounded-xl border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text-secondary)] outline-none"
                >
                  {TIER_ORDER.map((t) => (
                    <option key={t} value={t}>
                      {TIER_STYLE[t].label} and above
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-2 pb-2.5 text-sm text-[var(--text-secondary)]">
                <input
                  type="checkbox"
                  checked={autoOnly}
                  onChange={(e) => setAutoOnly(e.target.checked)}
                />
                Only jobs the bot can fill
              </label>
              <Button variant="secondary" onClick={load} disabled={loading} className="ml-auto">
                <RefreshCw className={clsx('h-4 w-4', loading && 'animate-spin')} />
                Refresh
              </Button>
            </div>

            {stats && (
              <div className="mt-5 flex flex-wrap gap-2 text-xs">
                <Stat label="pool" value={stats.pool} />
                <Stat label="eligible" value={stats.eligible} />
                <Stat label="selected" value={stats.selected} />
                {Object.entries(stats.by_route).map(([k, v]) => (
                  <Stat key={k} label={ROUTE_STYLE[k as keyof typeof ROUTE_STYLE]?.label ?? k} value={v} />
                ))}
              </div>
            )}
          </Card>

          {error && (
            <div className="flex items-center gap-2 rounded-xl border border-[var(--danger-border)] bg-[var(--danger-bg)] px-4 py-3 text-sm text-[var(--danger)]">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              {error}
            </div>
          )}

          <div className="flex items-center justify-between">
            <p className="text-sm text-[var(--text-muted)]">
              {checked.size} of {selected.length} chosen
              {runnable.length !== checked.size && (
                <span> · {checked.size - runnable.length} are manual-only</span>
              )}
            </p>
            <Button onClick={start} disabled={!runnable.length || status?.running}>
              <Sparkles className="h-4 w-4" />
              Tailor &amp; fill {runnable.length || ''}
            </Button>
          </div>

          <div className="space-y-2">
            {loading && !selected.length && (
              <p className="py-10 text-center text-sm text-[var(--text-muted)]">Loading…</p>
            )}
            {!loading && !selected.length && (
              <Card>
                <p className="text-sm text-[var(--text-muted)]">
                  Nothing matches yet. Most scanned jobs are LinkedIn listings with no reachable
                  application form — scan company career boards to fill this list.
                </p>
              </Card>
            )}
            {selected.map((c) => (
              <CandidateRow
                key={c.job_id}
                candidate={c}
                checked={checked.has(c.job_id)}
                onToggle={() => toggle(c.job_id)}
              />
            ))}
          </div>

          {passedOver.length > 0 && (
            <div>
              <button
                onClick={() => setShowPassed((v) => !v)}
                className="text-sm text-[var(--text-muted)] underline-offset-4 hover:underline"
              >
                {showPassed ? 'Hide' : 'Show'} {passedOver.length} passed over
              </button>
              {showPassed && (
                <div className="mt-3 space-y-1.5">
                  {passedOver.map((c) => (
                    <div
                      key={c.job_id}
                      className="flex items-center gap-3 rounded-xl border border-[var(--border)] px-4 py-2.5 text-sm opacity-70"
                    >
                      <span className="w-10 shrink-0 text-xs text-[var(--text-muted)]">
                        {c.score.toFixed(0)}
                      </span>
                      <span className="w-40 shrink-0 truncate font-medium text-[var(--text-secondary)]">
                        {c.company}
                      </span>
                      <span className="flex-1 truncate text-[var(--text-muted)]">{c.title}</span>
                      <span className="shrink-0 text-xs text-[var(--danger)]">{c.rejected}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {tab === 'review' && (
        <div className="space-y-3">
          <Card>
            <CardTitle>Waiting for you</CardTitle>
            <CardDescription>
              Each form was filled and left open for checking. Nothing has been submitted.
            </CardDescription>
          </Card>
          {!queue.length && (
            <p className="py-10 text-center text-sm text-[var(--text-muted)]">
              Nothing in the queue.
            </p>
          )}
          {queue.map((item) => (
            <QueueRow key={item.job_id} item={item} onDecide={decide} />
          ))}
          {callbacks && <CallbackCard stats={callbacks} />}
        </div>
      )}
    </div>
  );
}

const CALLBACK_GROUPS: { key: keyof CallbackStats['by']; label: string }[] = [
  { key: 'variant', label: 'Resume' },
  { key: 'llm_used', label: 'Wording' },
  { key: 'first_glance', label: 'First-glance match' },
];

function rateText(b: CallbackBucket): string {
  if (b.rate === null) return '—';
  return `${b.rate}%${b.enough_data ? '' : ' *'}`;
}

/** Whether tailoring pays, measured by callbacks rather than keyword scores. */
function CallbackCard({ stats }: { stats: CallbackStats }) {
  const { overall, rules } = stats;
  return (
    <Card>
      <CardTitle>Callback rate</CardTitle>
      <CardDescription>
        A callback is a job marked {rules.callback.join(' or ')}. No reply after{' '}
        {rules.no_reply_after_days} days counts as a no; younger applications are pending and
        left out. * fewer than {rules.min_sample} settled — too few to trust.
      </CardDescription>
      <div className="mt-3 flex flex-wrap gap-2 text-sm">
        <span className="rounded-lg bg-[var(--surface-2)] px-2.5 py-1 text-[var(--text-secondary)]">
          <b className="font-semibold text-[var(--text-primary)]">{rateText(overall)}</b> overall
        </span>
        <Stat label="callbacks" value={overall.callback} />
        <Stat label="settled" value={overall.settled} />
        <Stat label="pending" value={overall.pending} />
      </div>
      {overall.sent > 0 && (
        <div className="mt-4 space-y-3 text-sm">
          {CALLBACK_GROUPS.map(({ key, label }) => (
            <div key={key}>
              <p className="mb-1 text-xs uppercase tracking-wide text-[var(--text-muted)]">{label}</p>
              <div className="flex flex-wrap gap-2">
                {Object.entries(stats.by[key]).map(([name, b]) => (
                  <span
                    key={name}
                    className="rounded-lg border border-[var(--border)] px-2.5 py-1 text-[var(--text-secondary)]"
                  >
                    {name}: <b className="text-[var(--text-primary)]">{rateText(b)}</b>{' '}
                    <span className="text-[var(--text-muted)]">
                      ({b.callback}/{b.settled})
                    </span>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <span className="rounded-lg bg-[var(--surface-2)] px-2.5 py-1 text-[var(--text-secondary)]">
      <b className="font-semibold text-[var(--text-primary)]">{value}</b> {label}
    </span>
  );
}

function CandidateRow({
  candidate,
  checked,
  onToggle,
}: {
  candidate: Candidate;
  checked: boolean;
  onToggle: () => void;
}) {
  const tier = TIER_STYLE[candidate.tier];
  const route = ROUTE_STYLE[candidate.route];
  const manual = candidate.route === 'manual';

  return (
    <div
      className={clsx(
        'flex items-start gap-3 rounded-xl border px-4 py-3 transition-colors',
        checked ? 'border-[var(--accent)] bg-[var(--surface)]' : 'border-[var(--border)]',
        manual && 'opacity-70',
      )}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={onToggle}
        disabled={manual}
        className="mt-1"
        aria-label={`Select ${candidate.title}`}
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-[var(--text-primary)]">{candidate.company}</span>
          <span
            className="rounded-lg px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
            style={{ color: tier.color, background: tier.bg }}
          >
            {tier.label}
          </span>
          <span
            className="rounded-lg bg-[var(--surface-2)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-muted)]"
            title={route.hint}
          >
            {route.label}
          </span>
          <span className="ml-auto text-xs text-[var(--text-muted)]">
            {candidate.score.toFixed(0)}
          </span>
        </div>
        <p className="mt-0.5 truncate text-sm text-[var(--text-secondary)]">{candidate.title}</p>
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          {candidate.location && <>{candidate.location} · </>}
          {candidate.reasons.join(' · ')}
        </p>
        {manual && (
          <p className="mt-1 text-xs text-[var(--danger)]">{candidate.route_reason}</p>
        )}
      </div>
      <a
        href={safeHref(candidate.apply_url || candidate.url)}
        target="_blank"
        rel="noreferrer"
        className="mt-0.5 shrink-0 rounded-lg p-1.5 text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)]"
        aria-label="Open posting"
      >
        <ExternalLink className="h-4 w-4" />
      </a>
    </div>
  );
}

function QueueRow({
  item,
  onDecide,
}: {
  item: QueueItem;
  onDecide: (jobId: string, outcome: 'applied' | 'ignored') => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="font-medium text-[var(--text-primary)]">
            {item.company} <span className="text-[var(--text-muted)]">·</span>{' '}
            <span className="font-normal text-[var(--text-secondary)]">{item.title}</span>
          </p>
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">{item.reason}</p>
        </div>
        {item.has_tailored_resume && (
          <a
            href={autopilot.resumeUrl(item.job_id)}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-[var(--text-muted)] hover:bg-[var(--surface-2)]"
          >
            <FileText className="h-3.5 w-3.5" /> Resume
          </a>
        )}
        {item.has_screenshot && (
          <button
            onClick={() => setOpen((v) => !v)}
            className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-[var(--text-muted)] hover:bg-[var(--surface-2)]"
          >
            <ImageIcon className="h-3.5 w-3.5" /> {open ? 'Hide' : 'Screenshot'}
          </button>
        )}
        <a
          href={safeHref(item.url)}
          target="_blank"
          rel="noreferrer"
          className="rounded-lg p-1.5 text-[var(--text-muted)] hover:bg-[var(--surface-2)]"
          aria-label="Open posting"
        >
          <ExternalLink className="h-4 w-4" />
        </a>
        <Button size="sm" onClick={() => onDecide(item.job_id, 'applied')}>
          <CheckCircle2 className="h-4 w-4" /> Applied
        </Button>
        <Button size="sm" variant="secondary" onClick={() => onDecide(item.job_id, 'ignored')}>
          <XCircle className="h-4 w-4" /> Skip
        </Button>
      </div>
      {open && item.has_screenshot && (
        <img
          src={autopilot.screenshotUrl(item.job_id)}
          alt="Filled application form"
          className="mt-3 w-full rounded-xl border border-[var(--border)]"
        />
      )}
    </div>
  );
}
