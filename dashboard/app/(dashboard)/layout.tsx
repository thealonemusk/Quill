import { Sidebar } from '@/components/Sidebar';

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen bg-[var(--bg)]">
      <Sidebar />
      {/* min-w-0 is what stops a wide table forcing the whole flex row wider
          than the screen — without it the page scrolls sideways on a phone. */}
      <main className="flex min-h-screen min-w-0 flex-1 flex-col lg:py-3 lg:pr-3">{children}</main>
    </div>
  );
}
