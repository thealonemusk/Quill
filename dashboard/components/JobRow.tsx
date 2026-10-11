'use client';

import clsx from 'clsx';
import { motion } from 'framer-motion';
import { Check, ExternalLink, Send } from 'lucide-react';
import { Job } from '@/lib/types';
import { ApplyStatusBadge, AtsBadge, PlatformBadge, ScoreBadge, VisitedBadge } from '@/components/ui/Badge';
import { safeHref } from '@/lib/safe-url';

interface JobRowProps {
  job: Job;
  index?: number;
  applying?: boolean;
  applyBusy?: boolean;
  onVisit: (id: string) => void;
  onApply: (id: string) => void;
  alwaysShowOpen?: boolean;
}

/**
 * The same job as a card, for phones and iPad portrait.
 *
 * A six-column table cannot be made to work at 390px — horizontal scrolling a
 * table you also have to scroll vertically is the worst of both. The card
 * carries the same information and the same actions, stacked, and the table
 * is kept for `md` and up where the columns genuinely help scanning.
 */
export function JobCard({
  job,
  index = 0,
  applying = false,
  applyBusy = false,
  onVisit,
  onApply,
  alwaysShowOpen = false,
}: JobRowProps) {
  const visited = Boolean(job.visited_at);
  const alreadyApplied = job.status === 'applied' || job.apply_status === 'applied';
  const date = job.discovered_at
    ? new Date(job.discovered_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    : '—';

  const statusBadge = applying ? 'applying' : job.apply_status;
  const showError = job.apply_error && job.apply_status && job.apply_status !== 'applied';

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.02, duration: 0.2 }}
      className={clsx(
        'rounded-2xl border bg-[var(--surface)] p-3.5 shadow-[var(--shadow-card)] transition-colors',
        visited
          ? 'border-[var(--border)] bg-[var(--surface-2)]/50'
          : 'border-[var(--border)]',
      )}
    >
      {/* The score leads: it is the one number that decides whether the rest
          of the card is worth reading, so it gets the left edge rather than
          being parked in a far corner. */}
      <div className="flex items-start gap-3">
        <span className="mt-0.5 shrink-0">
          <ScoreBadge score={job.relevance_score} />
        </span>
        <div className="min-w-0 flex-1">
          <p
            className={clsx(
              'line-clamp-2 text-[15px] font-semibold leading-snug',
              visited ? 'text-[var(--text-muted)]' : 'text-[var(--text-primary)]',
            )}
          >
            {job.title}
          </p>
          <p className="mt-1 truncate text-[13px] text-[var(--text-secondary)]">{job.company}</p>
          {job.location && (
            <p className="truncate text-xs text-[var(--text-muted)]">{job.location}</p>
          )}
        </div>
      </div>

      {(visited || statusBadge) && (
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
          {visited && <VisitedBadge />}
          <ApplyStatusBadge status={statusBadge} />
        </div>
      )}

      {showError && (
        <p className="mt-2 line-clamp-2 text-[11px] leading-relaxed text-[var(--danger)]">
          {job.apply_error}
        </p>
      )}

      {/* Provenance and actions share the footer, divided off from the content
          above so the card reads as "what it is" then "what you can do". */}
      <div className="mt-3 flex items-center gap-2 border-t border-[var(--border)] pt-3">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
          <PlatformBadge platform={job.platform} />
          <AtsBadge ats={job.ats_type} />
          <span className="text-[11px] text-[var(--text-muted)]">{date}</span>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            disabled={applyBusy || alreadyApplied}
            onClick={() => onApply(job.id)}
            aria-label={alreadyApplied ? 'Already applied' : 'Auto-apply'}
            className={clsx(
              // 40px square: comfortable on touch, where the desktop row's
              // hover-to-reveal affordance does not exist at all.
              'inline-flex h-10 w-10 items-center justify-center rounded-xl border transition-colors',
              alreadyApplied
                ? 'border-transparent bg-[var(--success-bg)] text-[var(--success)]'
                : applyBusy
                  ? 'border-[var(--border)] text-[var(--text-muted)] opacity-40'
                  : 'border-[var(--border)] bg-[var(--surface-2)] text-[var(--text-secondary)] active:bg-[var(--surface-3)]',
            )}
          >
            {alreadyApplied ? <Check className="h-4 w-4" /> : <Send className="h-4 w-4" />}
          </button>
          <a
            href={safeHref(job.url)}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => !visited && onVisit(job.id)}
            aria-label={alwaysShowOpen ? 'Open and fill manually' : 'Open posting'}
            className={clsx(
              'inline-flex h-10 items-center justify-center gap-1.5 rounded-xl border px-3 text-[13px] font-medium transition-colors',
              visited
                ? 'border-[var(--border)] bg-[var(--surface-2)] text-[var(--text-secondary)]'
                : 'bg-signal border-transparent active:brightness-110',
            )}
          >
            <ExternalLink className="h-4 w-4" />
            Open
          </a>
        </div>
      </div>
    </motion.div>
  );
}

export function JobRow({
  job,
  index = 0,
  applying = false,
  applyBusy = false,
  onVisit,
  onApply,
  alwaysShowOpen = false,
}: JobRowProps) {
  const visited = Boolean(job.visited_at);
  const alreadyApplied = job.status === 'applied' || job.apply_status === 'applied';
  const date = job.discovered_at
    ? new Date(job.discovered_at).toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
      })
    : '—';

  return (
    <motion.tr
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.02, duration: 0.2 }}
      className={clsx(
        'group border-b border-[var(--border)] transition-colors',
        visited
          ? 'bg-[var(--surface-2)]/60 opacity-75'
          : 'hover:bg-[var(--surface-2)]',
      )}
    >
      <td className="px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <p
              className={clsx(
                'truncate text-sm font-semibold',
                visited ? 'text-[var(--text-muted)]' : 'text-[var(--text-primary)]',
              )}
            >
              {job.title}
            </p>
            {visited && <VisitedBadge />}
            <ApplyStatusBadge status={applying ? 'applying' : job.apply_status} />
          </div>
          <p className="truncate text-xs text-[var(--text-muted)]">
            {job.company}
            {job.location ? ` · ${job.location}` : ''}
          </p>
          {job.apply_error && job.apply_status && job.apply_status !== 'applied' && (
            <p className="mt-0.5 max-w-md truncate text-[11px] text-[var(--danger)]" title={job.apply_error}>
              {job.apply_error}
            </p>
          )}
        </div>
      </td>
      <td className="px-4 py-3">
        <div className="flex flex-col items-start gap-1">
          <PlatformBadge platform={job.platform} />
          <AtsBadge ats={job.ats_type} />
        </div>
      </td>
      <td className="px-4 py-3 text-center">
        <ScoreBadge score={job.relevance_score} />
      </td>
      <td className="px-4 py-3 text-xs text-[var(--text-muted)]">{date}</td>
      <td className="px-4 py-3 text-right">
        <div className="inline-flex items-center gap-1">
          <button
            type="button"
            disabled={applyBusy || alreadyApplied}
            onClick={() => onApply(job.id)}
            title={alreadyApplied ? 'Already applied' : 'Auto-apply'}
            className={clsx(
              'inline-flex rounded-lg p-2 transition-all',
              alreadyApplied
                ? 'text-[var(--success)]'
                : applyBusy
                  ? 'text-[var(--text-muted)] opacity-40'
                  : 'text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--accent)] lg:opacity-0 lg:group-hover:opacity-100',
            )}
          >
            <Send className="h-4 w-4" />
          </button>
          <a
            href={safeHref(job.url)}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => !visited && onVisit(job.id)}
            className={clsx(
              'inline-flex rounded-lg p-2 transition-all',
              visited
                ? 'text-[var(--success)] opacity-100'
                : alwaysShowOpen
                  ? 'text-[var(--accent)] opacity-100 hover:bg-[var(--surface-3)]'
                  : 'text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--accent)] lg:opacity-0 lg:group-hover:opacity-100',
            )}
            title={alwaysShowOpen ? 'Open and fill manually' : visited ? 'Already visited' : 'Open posting'}
          >
            {visited ? <Check className="h-4 w-4" /> : <ExternalLink className="h-4 w-4" />}
          </a>
        </div>
      </td>
    </motion.tr>
  );
}
