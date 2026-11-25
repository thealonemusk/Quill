'use client';

import { useEffect } from 'react';
import { apiBase } from '@/lib/api';

const PING_EVERY_MS = 10 * 60 * 1000;
const PING_LIMIT = 12;

function isRemoteApi(base: string): boolean {
  return /^https?:\/\//.test(base) && !/^https?:\/\/(localhost|127\.0\.0\.1)(:|\/|$)/.test(base);
}

export function KeepAlive() {
  useEffect(() => {
    const base = apiBase();
    const onDeployedSite = window.location.hostname === 'quill-nikhil.vercel.app';
    if (!onDeployedSite && !isRemoteApi(base)) return;
    let sent = 0;
    const id = setInterval(() => {
      sent += 1;
      fetch(`${base}/api/scan/status`, { cache: 'no-store' }).catch(() => {});
      if (sent >= PING_LIMIT) clearInterval(id);
    }, PING_EVERY_MS);
    return () => clearInterval(id);
  }, []);
  return null;
}
