import type { Metadata, Viewport } from 'next';
import './globals.css';
import { ThemeProvider } from '@/components/ThemeProvider';

export const metadata: Metadata = {
  title: 'Quill',
  description: 'Job discovery for early-career engineers',
  icons: { icon: '/icon.png' },
};

// `viewportFit: 'cover'` lets the page paint into the iPhone's safe areas; the
// bottom nav then pads itself back out with env(safe-area-inset-bottom) so it
// clears the home indicator instead of sitting under it.
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // `suppressHydrationWarning` is required by next-themes: it sets the theme
    // class on <html> before React hydrates, so server and client markup
    // differ by design on the first paint.
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen antialiased">
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
