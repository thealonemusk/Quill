import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';

/**
 * Shell for the autopilot section.
 *
 * A separate route group with its own chrome — it deliberately does not render
 * the dashboard Sidebar, so this section can change shape without touching the
 * Jobs / Analytics / Settings pages at all. The only shared code is the root
 * layout (html, body, theme) and the read-only UI primitives.
 */
export default function AutopilotLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-[var(--bg)]">
      <header className="glass sticky top-0 z-20">
        <div className="page-x mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 py-3 sm:py-4">
          <Link
            href="/"
            className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-sm text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)]"
          >
            <ArrowLeft className="h-4 w-4" />
            Dashboard
          </Link>
          <div className="h-5 w-px bg-[var(--border)]" />
          <div className="flex items-center gap-2">
            <img src="/icon.png" alt="" className="h-8 w-8" />
            <div>
              <h1 className="page-title text-lg text-[var(--text-primary)]">
                Autopilot
              </h1>
              <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                Select, tailor and fill. You approve every submission.
              </p>
            </div>
          </div>
        </div>
      </header>
      <main className="flex-1 overflow-y-auto">{children}</main>
    </div>
  );
}
