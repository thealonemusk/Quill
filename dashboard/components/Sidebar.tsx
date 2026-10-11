'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useTheme } from 'next-themes';
import { Briefcase, BarChart2, Settings, Moon, Sun } from 'lucide-react';
import clsx from 'clsx';
import { useEffect, useState } from 'react';

const NAV = [
  { href: '/', icon: Briefcase, label: 'Jobs' },
  { href: '/analytics', icon: BarChart2, label: 'Analytics' },
  { href: '/settings', icon: Settings, label: 'Settings' },
];

/**
 * Navigation, in two shapes.
 *
 * Below `lg` the sidebar becomes a bottom tab bar rather than a drawer. A
 * drawer needs a trigger, and the only place to put one is the page header —
 * which is already `sticky top-0`, so the two would fight over the same strip
 * and the same z-index. A tab bar owns the bottom edge, which nothing else
 * uses, needs no open/close state, and is the pattern iOS users already know.
 *
 * The breakpoint is `lg` (1024px) on purpose: a 15rem sidebar leaves an iPad
 * in portrait (820px) about 580px for a six-column table, which is not enough.
 * Portrait gets the tab bar and the full width; landscape gets the sidebar.
 *
 * The chrome is liquid glass in both themes: translucent, blurred, with a
 * bright top edge. The active item stays the signal color.
 */
export function Sidebar() {
  const path = usePathname();
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  const isDark = mounted && theme === 'dark';
  const toggleTheme = () => setTheme(isDark ? 'light' : 'dark');

  return (
    <>
      <aside className="liquid-glass sticky top-3 ml-3 mb-3 hidden h-[calc(100vh-1.5rem)] w-[var(--sidebar-width)] shrink-0 flex-col rounded-[28px] lg:flex">
        <div className="px-5 py-6">
          <div className="flex items-center gap-2.5">
            <img src="/icon.png" alt="" className="h-8 w-8" />
            <p className="text-lg font-bold tracking-tight">Quill</p>
          </div>
        </div>

        <nav className="flex-1 space-y-1 px-3">
          {NAV.map(({ href, icon: Icon, label }) => {
            const active = path === href;
            return (
              <Link
                key={href}
                href={href}
                className={clsx(
                  'flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-semibold transition-colors',
                  active
                    ? 'bg-signal'
                    : 'text-[var(--text-secondary)] hover:bg-[var(--glass-hover)] hover:text-[var(--text-primary)]',
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {label}
              </Link>
            );
          })}
        </nav>

        <div className="border-t border-[var(--border)] p-3">
          <button
            type="button"
            onClick={toggleTheme}
            className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-semibold text-[var(--text-secondary)] transition-colors hover:bg-[var(--glass-hover)] hover:text-[var(--text-primary)]"
          >
            {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            {isDark ? 'Light mode' : 'Dark mode'}
          </button>
        </div>
      </aside>

      {/* Mobile / tablet-portrait tab bar. */}
      <nav
        className="liquid-glass fixed inset-x-3 z-40 flex items-stretch rounded-[22px] lg:hidden"
        style={{ bottom: 'max(0.75rem, env(safe-area-inset-bottom, 0px))' }}
      >
        {NAV.map(({ href, icon: Icon, label }) => {
          const active = path === href;
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? 'page' : undefined}
              className={clsx(
                'flex flex-1 flex-col items-center justify-center gap-1 py-2.5 text-[11px] font-semibold transition-colors',
                active ? 'text-[var(--signal-ink)]' : 'text-[var(--text-secondary)]',
              )}
            >
              <Icon className="h-5 w-5" />
              {label}
            </Link>
          );
        })}
        <button
          type="button"
          onClick={toggleTheme}
          aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
          className="flex flex-1 flex-col items-center justify-center gap-1 py-2.5 text-[11px] font-semibold text-[var(--text-secondary)]"
        >
          {isDark ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
          Theme
        </button>
      </nav>
    </>
  );
}
