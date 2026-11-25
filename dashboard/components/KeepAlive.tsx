'use client';

import { useEffect } from 'react';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';
const PING_EVERY_MS = 10 * 60 * 1000;
const PING_LIMIT = 12;

function isRemoteApi(base: string): boolean {
  return /^https?:\/\//.test(base) && !/^https?:\/\/(localhost|127\.0\.0\.1)(:|\/|$)/.test(base);
}

export function KeepAlive() {
  useEffect(() => {
    if (!isRemoteApi(API_BASE)) return;
    let sent = 0;
    const id = setInterval(() => {
      sent += 1;
      fetch(`${API_BASE}/api/scan/status`, { cache: 'no-store' }).catch(() => {});
      if (sent >= PING_LIMIT) clearInterval(id);
    }, PING_EVERY_MS);
    return () => clearInterval(id);
  }, []);
  return null;
}
