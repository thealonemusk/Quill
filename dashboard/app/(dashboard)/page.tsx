'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Search, ChevronLeft, ChevronRight } from 'lucide-react';
import clsx from 'clsx';
import { api } from '@/lib/api';
import type { Job } from '@/lib/types';
import { ScanBar } from '@/components/ScanBar';
import { JobCard, JobRow } from '@/components/JobRow';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';

// A scan's whole result should be readable without paging through it.
const JOBS_PER_PAGE = 50;
const MANUAL_APPLY_STATUSES = new Set(['failed', 'needs_review', 'skipped']);

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState('');
  const [scanStalled, setScanStalled] = useState(false);
  const [applying, setApplying] = useState(false);
  const [search, setSearch] = useState('');
  const [sortOrder, setSortOrder] = useState<'desc' | 'asc'>('desc');
  const [sortBy, setSortBy] = useState<'popularity' | 'score' | 'date'>('popularity');
  const [currentPage, setCurrentPage] = useState(1);

  const loadJobs = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      setJobs(await api.getJobs());
    } catch {
      if (!quiet) setJobs([]);
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadJobs();
    api
      .getScanStatus()
      .then((s) => {
        setScanning(!!s.running);
        setScanStalled(!!s.stalled);
      })
      .catch(() => {});
    api.getApplyStatus().then((s) => setApplying(!!s.running)).catch(() => {});
  }, [loadJobs]);

  useEffect(() => {
    if (!applying) return;
    const id = setInterval(async () => {
      try {
        const status = await api.getApplyStatus();
        await loadJobs(true);
        if (!status.running) setApplying(false);
      } catch {}
    }, 2500);
    return () => clearInterval(id);
  }, [applying, loadJobs]);

  useEffect(() => {
    if (!scanning) return;
    const id = setInterval(async () => {
      try {
        const status = await api.getScanStatus();
        await loadJobs(true);
        setScanStalled(!!status.stalled);
        if (!status.running) {
          setScanning(false);
          setScanStalled(false);
          // A scan can finish "successfully" having been blocked by a
          // source — reporting only `error` made that look like zero results.
          const failure = status.last_result?.error ?? status.last_result?.warning;
          if (typeof failure === 'string') setScanError(failure);
        }
      } catch {}
    }, 3000);
    return () => clearInterval(id);
  }, [scanning, loadJobs]);

  const handleScan = async () => {
    if (scanning) return;
    setScanError('');
    try {
      setScanning(true);
      setCurrentPage(1);
      await api.startScan();
    } catch (err) {
      // Swallowing this is what made the button look dead: a scan already
      // running answers 409, the state reverted, and nothing said why.
      const message = err instanceof Error ? err.message : 'Could not start the scan';
      setScanning(false);
      setScanError(message);
    }
  };

  const handleStopScan = async () => {
    if (!scanning) return;
    try {
      const result = await api.stopScan();
      if (!result.running) {
        setScanning(false);
        setScanError('');
      }
    } catch (err) {
      setScanError(err instanceof Error ? err.message : 'Could not stop scan');
    }
  };

  /** Abandon a scan that has stopped responding, so a new one can start. */
  const handleForceStopScan = async () => {
    try {
      await api.stopScan(true);
      setScanning(false);
      setScanStalled(false);
      setScanError('');
    } catch (err) {
      setScanError(err instanceof Error ? err.message : 'Could not abandon the scan');
    }
  };

  const handleVisit = async (id: string) => {
    const visitedAt = new Date().toISOString();
    setJobs((prev) =>
      prev.map((j) => (j.id === id && !j.visited_at ? { ...j, visited_at: visitedAt } : j)),
    );
    try {
      await api.markJobVisited(id);
    } catch {
      // Keep optimistic visited state
    }
  };

  const handleRemoveVisited = async () => {
    const seen = jobs.filter((j) => j.visited_at).length;
    if (!seen) {
      setScanError('No visited jobs to remove.');
      return;
    }
    if (!confirm(`Remove ${seen} job${seen === 1 ? '' : 's'} you have already opened?`)) return;
    try {
      const { removed_count } = await api.purgeVisitedJobs();
      setCurrentPage(1);
      await loadJobs();
      setScanError(removed_count ? '' : 'Nothing was removed.');
    } catch (err) {
      setScanError(err instanceof Error ? err.message : 'Could not remove visited jobs');
    }
  };

  const handleClearJobs = async () => {
    const appliedCount = jobs.filter(
      (j) => j.status === 'applied' || j.apply_status === 'applied',
    ).length;
    const rest = jobs.length - appliedCount;
    const detail = appliedCount
      ? `Remove ${rest} pending and ${appliedCount} applied job${appliedCount === 1 ? '' : 's'} from the list?\n\n` +
        'Applied ones stay recorded in history, so they will not come back in a scan.'
      : `Remove ${rest} job${rest === 1 ? '' : 's'} from the list?`;
    if (!confirm(detail)) return;
    try {
      await api.clearDiscoveredJobs(true);
      setCurrentPage(1);
      await loadJobs();
      setScanError('');
    } catch (err) {
      setScanError(err instanceof Error ? err.message : 'Could not clear jobs');
    }
  };

  const handleApply = async (id: string) => {
    if (applying || scanning) return;
    try {
      setApplying(true);
      await api.applyJob(id);
    } catch (err) {
      setApplying(false);
      alert(err instanceof Error ? err.message : 'Could not start apply');
    }
  };

  const handleApplyAll = async () => {
    if (applying || scanning) return;
    const eligible = jobs.filter(
      (j) =>
        !['applied', 'interviewing', 'rejected', 'offer', 'ignored'].includes(j.status) &&
        j.apply_status !== 'applied' &&
        !MANUAL_APPLY_STATUSES.has(j.apply_status || ''),
    );
    if (!eligible.length) {
      alert('No eligible jobs to apply to.');
      return;
    }
    if (!confirm(`Auto-apply to ${eligible.length} job${eligible.length === 1 ? '' : 's'} using your saved profile?`)) {
      return;
    }
    try {
      setApplying(true);
      // The ids counted in the dialog above, so the run is exactly what was
      // confirmed. With none sent, the server picks its own set.
      await api.startApply(eligible.map((j) => j.id));
    } catch (err) {
      setApplying(false);
      alert(err instanceof Error ? err.message : 'Could not start apply. Check Settings → Applicant profile.');
    }
  };

  const handleStopApply = async () => {
    if (!applying) return;
    try {
      await api.stopApply();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Could not stop apply');
    }
  };

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return jobs.filter((j) => {
      if (!q) return true;
      return (
        j.title.toLowerCase().includes(q) ||
        j.company.toLowerCase().includes(q) ||
        j.location.toLowerCase().includes(q)
      );
    });
  }, [jobs, search]);

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      const aVisited = a.visited_at ? 1 : 0;
      const bVisited = b.visited_at ? 1 : 0;
      if (aVisited !== bVisited) return aVisited - bVisited;

      if (sortBy === 'popularity') {
        const diff = (b.tier_value ?? 0) - (a.tier_value ?? 0);
        if (diff !== 0) return sortOrder === 'desc' ? diff : -diff;
        const score = (b.relevance_score || 0) - (a.relevance_score || 0);
        if (score !== 0) return score;
      }
      if (sortBy === 'score') {
        const diff = (b.relevance_score || 0) - (a.relevance_score || 0);
        if (diff !== 0) return sortOrder === 'desc' ? diff : -diff;
      }
      const tA = a.discovered_at ? new Date(a.discovered_at).getTime() : 0;
      const tB = b.discovered_at ? new Date(b.discovered_at).getTime() : 0;
      return sortOrder === 'desc' ? tB - tA : tA - tB;
    });
  }, [filtered, sortOrder, sortBy]);

  const queueJobs = useMemo(
    () => sorted.filter((j) => !MANUAL_APPLY_STATUSES.has(j.apply_status || '')),
    [sorted],
  );
  const manualJobs = useMemo(
    () => sorted.filter((j) => MANUAL_APPLY_STATUSES.has(j.apply_status || '')),
    [sorted],
  );

  const totalPages = Math.max(1, Math.ceil(queueJobs.length / JOBS_PER_PAGE));
  const pageJobs = queueJobs.slice((currentPage - 1) * JOBS_PER_PAGE, currentPage * JOBS_PER_PAGE);

  useEffect(() => setCurrentPage(1), [search, sortOrder, sortBy]);

  const pendingStatuses: Job['status'][] = ['discovered', 'scored'];
  const pendingCount = jobs.filter((j) => pendingStatuses.includes(j.status)).length;
  const visitedCount = jobs.filter((j) => j.visited_at).length;

  return (
    <>
      <header className="page-x glass sticky top-0 z-20 py-4 sm:py-5">
        <div className="flex min-w-0 flex-wrap items-end justify-between gap-x-4 gap-y-3">
          <div className="min-w-0 max-w-full">
            <h1 className="page-title text-4xl text-[var(--text-primary)] sm:text-5xl">Jobs</h1>
            <div className="mt-3 flex flex-wrap gap-1.5">
              <span className="bg-signal rounded-full px-2.5 py-1 text-xs font-semibold">
                {jobs.length} total
              </span>
              {pendingCount > 0 && (
                <span className="rounded-full border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1 text-xs font-medium text-[var(--text-secondary)]">
                  {pendingCount} pending
                </span>
              )}
              {visitedCount > 0 && (
                <span className="rounded-full border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1 text-xs font-medium text-[var(--text-muted)]">
                  {visitedCount} visited
                </span>
              )}
              {manualJobs.length > 0 && (
                <span className="rounded-full border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1 text-xs font-medium text-[var(--text-secondary)]">
                  {manualJobs.length} manual
                </span>
              )}
              {scanning && (
                <span className="bg-signal rounded-full px-2.5 py-1 text-xs font-semibold">
                  Scanning
                </span>
              )}
              {applying && (
                <span className="rounded-full bg-[var(--ink)] px-2.5 py-1 text-xs font-semibold text-[var(--on-ink)]">
                  Applying
                </span>
              )}
            </div>
          </div>
          <ScanBar
            scanning={scanning}
            applying={applying}
            onScan={handleScan}
            onRemoveVisited={handleRemoveVisited}
            onClear={handleClearJobs}
            onApplyAll={handleApplyAll}
            onStopApply={handleStopApply}
            onStopScan={handleStopScan}
          />
        </div>

        {(scanError || scanStalled) && (
          <div
            className={clsx(
              'mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm',
              scanStalled
                ? 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300'
                : 'border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300'
            )}
          >
            <span>
              {scanStalled
                ? 'This scan has stopped responding. Abandon it to start a new one.'
                : scanError}
            </span>
            <div className="flex shrink-0 items-center gap-2">
              {scanStalled && (
                <button
                  type="button"
                  onClick={handleForceStopScan}
                  className="rounded-md border border-current px-2 py-1 text-xs font-medium"
                >
                  Abandon scan
                </button>
              )}
              <button
                type="button"
                onClick={() => setScanError('')}
                className="text-xs opacity-70 hover:opacity-100"
                aria-label="Dismiss"
              >
                ✕
              </button>
            </div>
          </div>
        )}
      </header>

      <div className="page-x pb-nav flex-1 overflow-y-auto pt-5 sm:pt-6">
        <div className="mx-auto max-w-6xl space-y-4">
          <div className="flex flex-wrap items-center gap-2 sm:gap-3">
            <div className="relative w-full min-w-0 sm:w-auto sm:min-w-[220px] sm:flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-muted)]" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search title or company"
                className="pl-10"
              />
            </div>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as 'popularity' | 'score' | 'date')}
              className="min-w-0 flex-1 rounded-xl border border-[var(--border)] bg-[var(--surface)] px-3 py-2.5 text-sm text-[var(--text-secondary)] shadow-[var(--shadow-rest)] outline-none focus:border-[var(--accent)] sm:flex-none"
            >
              <option value="popularity">Popularity</option>
              <option value="score">Best match</option>
              <option value="date">Date</option>
            </select>
            <select
              value={sortOrder}
              onChange={(e) => setSortOrder(e.target.value as 'desc' | 'asc')}
              className="min-w-0 flex-1 rounded-xl border border-[var(--border)] bg-[var(--surface)] px-3 py-2.5 text-sm text-[var(--text-secondary)] shadow-[var(--shadow-rest)] outline-none focus:border-[var(--accent)] sm:flex-none"
            >
              <option value="desc">
                {sortBy === 'popularity' ? 'Famous first' : sortBy === 'score' ? 'Highest score' : 'Newest'}
              </option>
              <option value="asc">
                {sortBy === 'popularity' ? 'Least known' : sortBy === 'score' ? 'Lowest score' : 'Oldest'}
              </option>
            </select>
          </div>

          <div className="md:overflow-hidden md:rounded-2xl md:border md:border-[var(--border)] md:bg-[var(--surface)] md:shadow-[var(--shadow-card)]">
            {loading ? (
              <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-10 text-center text-sm text-[var(--text-muted)] md:rounded-none md:border-0 md:p-16">Loading…</div>
            ) : queueJobs.length === 0 && manualJobs.length === 0 ? (
              <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-10 text-center text-sm text-[var(--text-muted)] md:rounded-none md:border-0 md:p-16">
                No jobs yet. Run a scan to discover roles.
              </div>
            ) : queueJobs.length === 0 ? (
              <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-10 text-center text-sm text-[var(--text-muted)] md:rounded-none md:border-0 md:p-16">
                No jobs left in the auto-apply queue. Check Manual apply below.
              </div>
            ) : (
              <>
                {/* Cards on phones and iPad portrait, the table from md up. */}
                <div className="space-y-2.5 md:hidden">
                  {pageJobs.map((job, i) => (
                    <JobCard
                      key={job.id}
                      job={job}
                      index={i}
                      applying={applying && (job.apply_status === 'applying' || job.apply_status === 'queued')}
                      applyBusy={applying}
                      onVisit={handleVisit}
                      onApply={handleApply}
                    />
                  ))}
                </div>
                <div className="hidden overflow-x-auto md:block">
                  <table className="w-full table-fixed text-left">
                    <thead>
                      <tr className="border-b border-[var(--border)] text-xs font-medium text-[var(--text-muted)]">
                        <th className="px-4 py-3">Role</th>
                        <th className="w-28 px-4 py-3 lg:w-32">Source</th>
                        <th className="w-16 px-4 py-3 text-center lg:w-20">Score</th>
                        <th className="w-16 px-4 py-3 lg:w-20">Found</th>
                        <th className="w-24 px-4 py-3" />
                      </tr>
                    </thead>
                    <tbody>
                      {pageJobs.map((job, i) => (
                        <JobRow
                          key={job.id}
                          job={job}
                          index={i}
                          applying={applying && (job.apply_status === 'applying' || job.apply_status === 'queued')}
                          applyBusy={applying}
                          onVisit={handleVisit}
                          onApply={handleApply}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="mt-3 flex flex-wrap items-center justify-between gap-2 px-1 py-1 text-sm text-[var(--text-muted)] md:mt-0 md:border-t md:border-[var(--border)] md:px-4 md:py-3">
                  <span>
                    {(currentPage - 1) * JOBS_PER_PAGE + 1}–{Math.min(currentPage * JOBS_PER_PAGE, queueJobs.length)} of{' '}
                    {queueJobs.length}
                  </span>
                  <div className="flex gap-2">
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={currentPage === 1}
                      onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={currentPage === totalPages}
                      onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                    >
                      <ChevronRight className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              </>
            )}
          </div>

          {!loading && manualJobs.length > 0 && (
            <div className="space-y-3 pt-4">
              <div>
                <h2 className="page-title text-2xl text-[var(--text-primary)]">Manual apply</h2>
                <p className="mt-0.5 text-sm text-[var(--text-muted)]">
                  Auto-fill could not finish these. Open the posting and complete them yourself, or retry apply.
                </p>
              </div>
              <div className="md:overflow-hidden md:rounded-2xl md:border md:border-[var(--border)] md:bg-[var(--surface)] md:shadow-[var(--shadow-card)]">
                <div className="space-y-2.5 md:hidden">
                  {manualJobs.map((job, i) => (
                    <JobCard
                      key={job.id}
                      job={job}
                      index={i}
                      applying={applying && (job.apply_status === 'applying' || job.apply_status === 'queued')}
                      applyBusy={applying}
                      alwaysShowOpen
                      onVisit={handleVisit}
                      onApply={handleApply}
                    />
                  ))}
                </div>
                <div className="hidden overflow-x-auto md:block">
                  <table className="w-full table-fixed text-left">
                    <thead>
                      <tr className="border-b border-[var(--border)] text-xs font-medium text-[var(--text-muted)]">
                        <th className="px-4 py-3">Role</th>
                        <th className="w-28 px-4 py-3 lg:w-32">Source</th>
                        <th className="w-16 px-4 py-3 text-center lg:w-20">Score</th>
                        <th className="w-16 px-4 py-3 lg:w-20">Found</th>
                        <th className="w-24 px-4 py-3" />
                      </tr>
                    </thead>
                    <tbody>
                      {manualJobs.map((job, i) => (
                        <JobRow
                          key={job.id}
                          job={job}
                          index={i}
                          applying={applying && (job.apply_status === 'applying' || job.apply_status === 'queued')}
                          applyBusy={applying}
                          alwaysShowOpen
                          onVisit={handleVisit}
                          onApply={handleApply}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

    </>
  );
}
