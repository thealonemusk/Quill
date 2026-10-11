import clsx from 'clsx';
import { atsLabel, platformLabel } from '@/lib/platforms';

export function PlatformBadge({ platform }: { platform: string }) {
  return (
    <span className="inline-flex items-center rounded-full border border-[var(--border)] bg-[var(--surface)] px-2 py-0.5 text-[11px] font-medium text-[var(--text-secondary)]">
      {platformLabel(platform)}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className="inline-flex items-center rounded-full bg-[var(--surface-2)] px-2 py-0.5 text-[11px] font-medium text-[var(--text-secondary)]">
      {status}
    </span>
  );
}

export function ScoreBadge({ score }: { score: number }) {
  const hot = score >= 75;
  const mid = score >= 55;
  return (
    <span
      className={clsx(
        'inline-flex h-7 min-w-[2.1rem] items-center justify-center rounded-full px-2 text-[13px] font-bold tabular-nums leading-none',
        hot
          ? 'bg-signal'
          : mid
            ? 'bg-signal-soft'
            : 'bg-[var(--surface-3)] text-[var(--text-muted)]',
      )}
    >
      {Math.round(score)}
    </span>
  );
}

export function VisitedBadge() {
  return (
    <span className="inline-flex items-center rounded-full bg-[var(--surface-3)] px-2 py-0.5 text-[10px] font-semibold text-[var(--text-muted)]">
      Visited
    </span>
  );
}

export function AtsBadge({ ats }: { ats?: string }) {
  if (!ats || ats === 'unknown') return null;
  return (
    <span className="inline-flex items-center rounded-full border border-[var(--border)] bg-[var(--surface)] px-2 py-0.5 text-[10px] font-medium text-[var(--text-muted)]">
      {atsLabel(ats)}
    </span>
  );
}

export function ApplyStatusBadge({ status }: { status?: string }) {
  if (!status) return null;
  const tone =
    status === 'applied'
      ? 'bg-[var(--success-bg)] text-[var(--success)]'
      : status === 'failed'
        ? 'bg-[var(--danger-bg)] text-[var(--danger)]'
        : status === 'needs_review' || status === 'applying' || status === 'queued'
          ? 'bg-signal'
          : 'bg-[var(--surface-2)] text-[var(--text-muted)]';
  return (
    <span className={clsx('inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold', tone)}>
      {status.replace('_', ' ')}
    </span>
  );
}
